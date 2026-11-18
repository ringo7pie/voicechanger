<#
.SYNOPSIS
OBS で試し録りをして、録画の中身（映像の符号化方式、音声 3 トラック、各トラックの音量）を検査する。

.DESCRIPTION
-InputWav を付けると、マイクの代わりに WAV を流すエンジンを起動して CABLE Input へ出す（試験用の設定ファイルを使い、
data\settings.json は書き換えない）。付けないときは、動いている VoiceChanger（GUI など）の声をそのまま録る。
OBS は tools\start_obs.ps1 で VoiceChanger のプロファイルとして起動しておく。

合格の基準: 映像が h264、音声が 3 本、トラック 1（合成）と 3（声）が無音でない（-50 dBFS より大きい）。
トラック 2（ゲーム音）は、ゲームを動かしていなければ無音で正しい（声が混ざっていないことの確認にもなる）。

出力の最終行は RECORD_TRIAL で始まる 1 行。終了コード: 0 = 合格、1 = 失敗、5 = 基準に合わない。

.EXAMPLE
pwsh -NoProfile -File .\tools\record_trial.ps1 -InputWav E:\nkdx\Games\voice_train\corpora\self\trimmed\EMOTION100_002.wav
#>
param(
    [string]$InputWav = '',
    [double]$Seconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
$controlPath = Join-Path -Path $repoRoot -ChildPath 'data\control.json'
$trialSettings = Join-Path -Path $repoRoot -ChildPath 'data\obs_trial_settings.json'
Set-Location -LiteralPath $repoRoot

$engineProcess = $null
if ($InputWav) {
    if (Test-Path -LiteralPath $controlPath) {
        $control = Get-Content -LiteralPath $controlPath -Raw -Encoding utf8 | ConvertFrom-Json
        if (Get-Process -Id ([int]$control.pid) -ErrorAction SilentlyContinue) {
            Write-Output 'RECORD_TRIAL failed=engine_running（VoiceChanger を閉じてから実行してください）'
            exit 1
        }
    }
    '{"mode": "dsp", "output_gain_db": 0.0, "dsp": {"semitones": 5.0, "formant_factor": 1.15}}' |
        Set-Content -LiteralPath $trialSettings -Encoding utf8NoBOM
    $engineArguments = @('-m', 'voicechanger', 'run', '--input-wav', (Resolve-Path -LiteralPath $InputWav).Path, '--loop-input',
        '--output-device', 'CABLE Input (VB-Audio Virtual Cable)', '--no-monitor', '--no-obs', '--settings-file', $trialSettings)
    # Start-Process は配列の要素を引用符なしでつなぐので、空白を含む要素は自分で囲む
    $argumentLine = ($engineArguments | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } }) -join ' '
    $engineProcess = Start-Process -FilePath $venvPython -ArgumentList $argumentLine -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(60)
    $engineReady = $false
    while ((Get-Date) -lt $deadline -and -not $engineProcess.HasExited) {
        if (Test-Path -LiteralPath $controlPath) {
            $engineReady = $true
            break
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $engineReady) {
        Write-Output ('RECORD_TRIAL failed=engine_start exited={0}（data\logs\engine.log を見てください）' -f $engineProcess.HasExited)
        exit 1
    }
    Start-Sleep -Seconds 2
}

try {
    $recordOutput = & $venvPython -m voicechanger.obs_setup record --seconds $Seconds
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'RECORD_TRIAL failed=record'
        exit 1
    }
} finally {
    if ($null -ne $engineProcess) {
        $control = Get-Content -LiteralPath $controlPath -Raw -Encoding utf8 -ErrorAction SilentlyContinue | ConvertFrom-Json
        if ($null -ne $control) {
            Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/api/shutdown' -f $control.port) -Method Post -ContentType 'application/json' `
                -Headers @{ 'X-VoiceChanger-Token' = [string]$control.token } -Body '{}' | Out-Null
        }
        $engineProcess.WaitForExit(15000) | Out-Null
    }
}

$recordLine = @($recordOutput | Where-Object { $_ -like 'OBS_RECORDED *' })[-1]
$outputPath = ($recordLine.Substring('OBS_RECORDED '.Length) | ConvertFrom-Json).output_path
Start-Sleep -Seconds 1
Write-Output "録画: $outputPath"

$probe = & ffprobe -v error -show_entries 'stream=index,codec_type,codec_name,width,height,avg_frame_rate,bit_rate' -show_entries 'format=duration,bit_rate' -of json $outputPath | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    Write-Output 'RECORD_TRIAL failed=ffprobe'
    exit 1
}
$video = @($probe.streams | Where-Object { $_.codec_type -eq 'video' })
$audio = @($probe.streams | Where-Object { $_.codec_type -eq 'audio' })
Write-Output ('映像: {0} {1}x{2} {3}fps / 音声: {4} 本 / 長さ {5:N1} 秒 / 全体 {6:N0} kbps' -f $video[0].codec_name, $video[0].width, $video[0].height,
    $video[0].avg_frame_rate, $audio.Count, [double]$probe.format.duration, ([double]$probe.format.bit_rate / 1000))

$trackLevels = @()
for ($trackIndex = 0; $trackIndex -lt $audio.Count; $trackIndex++) {
    $volumeLines = & ffmpeg -hide_banner -nostats -i $outputPath -map "0:a:$trackIndex" -af volumedetect -f null NUL 2>&1
    $maxVolume = [double](($volumeLines | Select-String -Pattern 'max_volume: (-?[\d.]+) dB').Matches[0].Groups[1].Value)
    $meanVolume = [double](($volumeLines | Select-String -Pattern 'mean_volume: (-?[\d.]+) dB').Matches[0].Groups[1].Value)
    $trackLevels += [pscustomobject]@{ Track = $trackIndex + 1; Max = $maxVolume; Mean = $meanVolume }
    Write-Output ('トラック {0}: 最大 {1:N1} dB / 平均 {2:N1} dB' -f ($trackIndex + 1), $maxVolume, $meanVolume)
}

$problems = @()
if ($video.Count -ne 1 -or $video[0].codec_name -ne 'h264') { $problems += 'video_not_h264' }
if ($audio.Count -ne 3) { $problems += "audio_tracks=$($audio.Count)" }
if ($audio.Count -ge 3) {
    if ($trackLevels[0].Max -le -50) { $problems += 'track1_silent' }
    if ($trackLevels[2].Max -le -50) { $problems += 'track3_silent' }
}
Write-Output ('RECORD_TRIAL ok={0} problems={1} path={2}' -f ($problems.Count -eq 0), ($problems -join ','), $outputPath)
if ($problems.Count -gt 0) {
    exit 5
}
exit 0
