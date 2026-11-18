<#
.SYNOPSIS
OBS Studio を VoiceChanger 用に設定する（専用のプロファイルとシーンコレクション「VoiceChanger」）。

.DESCRIPTION
1. OBS が起動していなければ起動する（websocket で操作するため）
2. python -m voicechanger.obs_setup configure で、プロファイル・映像（1920x1080 60fps）・録画（MKV、NVENC、音声 3 トラック）・
   入力（声＝CABLE Output、ゲームキャプチャ（音声つき）、画面キャプチャ）・トラックを設定し、
   プロファイルとシーンコレクションを一度切り替えて戻して反映・保存する
3. 録画エンコーダの詳細（recordEncoder.json）を書き、verify で設定を確かめる
既存のプロファイル・シーンコレクション（「無題」など）には触らない。
OBS を閉じて起動し直す方法は使わない（2026-11-18 に、閉じた後の起動で「クラッシュが検出されました」と出て、
シーンコレクションと出力の設定が保存されていなかった）。

出力の最終行は OBS_SETUP で始まる 1 行。終了コード: 0 = 成功、1 = 失敗、5 = 設定の確認で問題が見つかった。

.EXAMPLE
pwsh -NoProfile -File .\tools\setup_obs.ps1
#>
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
$startObs = Join-Path -Path $PSScriptRoot -ChildPath 'start_obs.ps1'
$profileDirectory = Join-Path -Path $env:APPDATA -ChildPath 'obs-studio\basic\profiles\VoiceChanger'

Set-Location -LiteralPath $repoRoot

$startArguments = @('-NoProfile', '-File', $startObs)
if (-not (Test-Path -LiteralPath $profileDirectory)) {
    $startArguments += '-Plain'
}
pwsh @startArguments | Select-Object -Last 1
if ($LASTEXITCODE -ne 0) {
    Write-Output 'OBS_SETUP failed=start'
    exit 1
}

& $venvPython -m voicechanger.obs_setup configure
if ($LASTEXITCODE -ne 0) {
    Write-Output 'OBS_SETUP failed=configure'
    exit 1
}

$encoderJson = (& $venvPython -m voicechanger.obs_setup encoder-json | Select-Object -Last 1)
[System.IO.File]::WriteAllText((Join-Path -Path $profileDirectory -ChildPath 'recordEncoder.json'), $encoderJson,
    [System.Text.UTF8Encoding]::new($false))
Write-Output "recordEncoder.json: $encoderJson"

& $venvPython -m voicechanger.obs_setup verify | Select-Object -Last 1
$verifyExit = $LASTEXITCODE
Write-Output ('OBS_SETUP verify_exit={0}' -f $verifyExit)
exit $verifyExit
