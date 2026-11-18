<#
.SYNOPSIS
仮想オーディオデバイス VB-CABLE と OBS Studio を導入し、OBS を初めて起動する前の設定ファイルを用意する。

.DESCRIPTION
前段（このウィンドウ、管理者権限なし）:
  1. 今の Windows の既定デバイスを data\default_devices_before.json に記録する（導入後に比べるため。VB-CABLE が入る前は毎回記録し直す）
  2. VB-CABLE（VBCABLE_Driver_Pack46.zip）と OBS Studio 33.0.1 のインストーラを runtime\downloads\ に取得し、SHA-256 を照合する
     （VB-CABLE は公開ハッシュが無いので、初回の値を config\tools_manifest.json に記録する）
  3. OBS の設定フォルダ（%APPDATA%\obs-studio）が無ければ、初回起動の前に次を書く
       plugin_config\obs-websocket\config.json : websocket サーバを有効、認証あり、パスワードは乱数
       user.ini : [General] FirstRun=true（自動構成ウィザードと、既定デバイスを追う常設の音声入力を作らせない）
     既にあれば user.ini は触らず、websocket の設定だけを有効にする（元のファイルは .bak に残す）
     接続情報は data\obs_websocket.json にも書く（エンジンと setup_obs.ps1 が使う）
後段（管理者に昇格。UAC の確認が出る）:
  4. VB-CABLE が無ければセットアップ（VBCABLE_Setup_x64.exe）を起動する。画面の「Install Driver」を押す
  5. OBS が無ければインストーラを無人（/S）で実行する。入らなければ通常の画面で起動する
最後に PC の再起動を求める（VB-CABLE の説明: Reboot after installation）。

出力の最終行は AUDIO_TOOLS で始まる 1 行の要約。終了コード: 0 = 成功、1 = 失敗、2 = 昇格を断られた。

.PARAMETER SkipVbCable
VB-CABLE を扱わない。

.PARAMETER SkipObs
OBS を扱わない。

.EXAMPLE
pwsh -NoProfile -File .\tools\install_audio_tools.ps1 -WhatIf
pwsh -NoProfile -File .\tools\install_audio_tools.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$SkipVbCable,
    [switch]$SkipObs,
    [switch]$ElevatedPhase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
Import-Module -Name (Join-Path -Path $PSScriptRoot -ChildPath 'InstallCommon.psm1') -Force

$manifestPath = Join-Path -Path $repoRoot -ChildPath 'config\tools_manifest.json'
$runtimeRoot = Join-Path -Path $repoRoot -ChildPath 'runtime'
$downloadDirectory = Join-Path -Path $runtimeRoot -ChildPath 'downloads'
$logDirectory = Join-Path -Path $runtimeRoot -ChildPath 'logs'
$elevatedLogPath = Join-Path -Path $logDirectory -ChildPath 'install_audio_tools_elevated.log'
$dataDirectory = Join-Path -Path $repoRoot -ChildPath 'data'
$baselinePath = Join-Path -Path $dataDirectory -ChildPath 'default_devices_before.json'
$obsConnectionPath = Join-Path -Path $dataDirectory -ChildPath 'obs_websocket.json'
$obsExecutable = Join-Path -Path $env:ProgramFiles -ChildPath 'obs-studio\bin\64bit\obs64.exe'
$obsConfigRoot = Join-Path -Path $env:APPDATA -ChildPath 'obs-studio'
$websocketPort = 4455

function Test-VbCableInstalled {
    $cableDevices = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -like '*VB-Audio Virtual Cable*' })
    return $cableDevices.Count -gt 0
}

function New-RandomPassword {
    $alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24)
    return -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}

function Save-Utf8File {
    param([Parameter(Mandatory)] [string]$Path, [Parameter(Mandatory)] [string]$Content)
    $directory = Split-Path -Path $Path -Parent
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

# ======================================================================== 後段（管理者）
if ($ElevatedPhase) {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Start-Transcript -LiteralPath $elevatedLogPath -Force | Out-Null
    $elevatedFailed = $false
    try {
        $manifest = Read-ToolManifest -Path $manifestPath
        if (-not $SkipVbCable) {
            if (Test-VbCableInstalled) {
                Write-Output 'VB-CABLE: 既に入っています'
            } else {
                $setupPath = Join-Path -Path $repoRoot -ChildPath (($manifest.items.vbcable.extract_to -replace '/', '\') + '\VBCABLE_Setup_x64.exe')
                if (-not (Test-Path -LiteralPath $setupPath)) {
                    throw "VB-CABLE のセットアップが見つかりません: $setupPath"
                }
                Write-Output 'VB-CABLE: セットアップを起動します。画面の「Install Driver」を押し、終わったら閉じてください。'
                Write-Output '          Windows から「このデバイス ソフトウェアをインストールしますか」と聞かれたら「インストール」を押してください。'
                $setupProcess = Start-Process -FilePath $setupPath -WorkingDirectory (Split-Path -Path $setupPath -Parent) -PassThru -Wait
                Write-Output ('VB-CABLE: セットアップが終わりました（終了コード {0}）' -f $setupProcess.ExitCode)
                if (Test-VbCableInstalled) {
                    Write-Output 'VB-CABLE: デバイスを確認しました'
                } else {
                    Write-Output 'VB-CABLE: まだデバイスが見えません。再起動の後に確認します'
                }
            }
        }
        if (-not $SkipObs) {
            if (Test-Path -LiteralPath $obsExecutable) {
                Write-Output "OBS: 既に入っています ($obsExecutable)"
            } else {
                $installerPath = Join-Path -Path $downloadDirectory -ChildPath $manifest.items.obs.file_pattern
                Write-Output 'OBS: インストーラを無人（/S）で実行します（1〜2 分）'
                $installProcess = Start-Process -FilePath $installerPath -ArgumentList '/S' -PassThru -Wait
                Write-Output ('OBS: インストーラの終了コード {0}' -f $installProcess.ExitCode)
                if (-not (Test-Path -LiteralPath $obsExecutable)) {
                    Write-Output 'OBS: 無人では入らなかったので、通常の画面で起動します。既定のまま進めてください（最後の「OBS を起動」のチェックは外す）'
                    $installProcess = Start-Process -FilePath $installerPath -PassThru -Wait
                }
                if (Test-Path -LiteralPath $obsExecutable) {
                    Write-Output "OBS: 入りました ($obsExecutable)"
                } else {
                    throw "OBS が入りませんでした（$obsExecutable が無い）"
                }
            }
        }
    } catch {
        $elevatedFailed = $true
        Write-Output ('失敗: ' + $_.Exception.Message)
    } finally {
        Stop-Transcript | Out-Null
    }
    if ($elevatedFailed) {
        exit 1
    }
    exit 0
}

# ======================================================================== 前段（管理者権限なし）
$summary = [ordered]@{ baseline = 'skipped'; vbcable_download = 'skipped'; obs_download = 'skipped'; obs_config = 'skipped'; elevated = 'skipped' }
$manifest = Read-ToolManifest -Path $manifestPath
$manifestChanged = $false

# 1. 既定デバイスの記録（VB-CABLE を入れる直前の状態を残す。入った後は書き換えない）
if ((Test-Path -LiteralPath $baselinePath) -and (Test-VbCableInstalled)) {
    Write-Output "既定デバイスの記録: VB-CABLE が入った後なので、導入前の記録を残します ($baselinePath)"
    $summary.baseline = 'exists'
} elseif ($PSCmdlet.ShouldProcess($baselinePath, '今の既定デバイスを記録（導入前の状態として）')) {
    & pwsh -NoProfile -File (Join-Path -Path $PSScriptRoot -ChildPath 'check_audio_devices.ps1') -SaveBaseline $baselinePath | Select-Object -Last 2
    if ($LASTEXITCODE -ne 0) {
        throw "既定デバイスを記録できませんでした（終了コード $LASTEXITCODE）"
    }
    $summary.baseline = 'saved'
}

# 2. 取得と照合
function Get-VerifiedDownload {
    <# 取得して照合し、ファイルのパスだけを返す（-WhatIf のときは 'whatif'）。案内は情報ストリームに出す。 #>
    param([Parameter(Mandatory)] [string]$ItemKey)
    $InformationPreference = 'Continue'
    $item = $manifest.items.$ItemKey
    $targetPath = Join-Path -Path $downloadDirectory -ChildPath $item.file_pattern
    if (-not (Test-Path -LiteralPath $targetPath)) {
        if (-not $PSCmdlet.ShouldProcess($targetPath, ('{0} を取得（{1}）' -f $item.name, $item.url))) {
            return 'whatif'
        }
        New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
        $partialPath = $targetPath + '.partial'
        Write-Information ('取得: {0}' -f $item.url)
        Invoke-WebRequest -Uri $item.url -OutFile $partialPath -TimeoutSec 600
        Move-Item -LiteralPath $partialPath -Destination $targetPath -Force
    }
    $actualHash = Get-FileSha256 -Path $targetPath
    $hashState = Compare-ManifestHash -Item $item -ActualHash $actualHash
    if ($hashState -eq 'recorded') {
        $item.sha256 = $actualHash
        $script:manifestChanged = $true
        Write-Information ('SHA-256: {0} {1}（初回。記録します）' -f $item.file_pattern, $actualHash)
    } else {
        Write-Information ('SHA-256: {0} {1}（記録と一致）' -f $item.file_pattern, $actualHash)
    }
    return $targetPath
}

if (-not $SkipVbCable) {
    $vbcableZip = Get-VerifiedDownload -ItemKey 'vbcable'
    if ($vbcableZip -ne 'whatif') {
        $extractPath = Join-Path -Path $repoRoot -ChildPath ($manifest.items.vbcable.extract_to -replace '/', '\')
        if (-not (Test-PathUnderRoot -Path $extractPath -Root $runtimeRoot)) {
            throw "展開先が runtime\ の外です: $extractPath"
        }
        if ($PSCmdlet.ShouldProcess($extractPath, 'VB-CABLE を展開')) {
            if (Test-Path -LiteralPath $extractPath) {
                Remove-Item -LiteralPath $extractPath -Recurse -Force
            }
            Expand-Archive -LiteralPath $vbcableZip -DestinationPath $extractPath
            $summary.vbcable_download = 'ok'
        }
    } else {
        $summary.vbcable_download = 'whatif'
    }
}
if (-not $SkipObs) {
    $obsInstaller = Get-VerifiedDownload -ItemKey 'obs'
    $summary.obs_download = if ($obsInstaller -eq 'whatif') { 'whatif' } else { 'ok' }
}
if ($manifestChanged -and $PSCmdlet.ShouldProcess($manifestPath, 'SHA-256 を記録')) {
    Save-ToolManifest -Path $manifestPath -Manifest $manifest
}

# 3. OBS の初回起動前の設定
if (-not $SkipObs) {
    $websocketConfigPath = Join-Path -Path $obsConfigRoot -ChildPath 'plugin_config\obs-websocket\config.json'
    $userIniPath = Join-Path -Path $obsConfigRoot -ChildPath 'user.ini'
    $freshObs = -not (Test-Path -LiteralPath $obsConfigRoot)
    $password = $null
    if (Test-Path -LiteralPath $obsConnectionPath) {
        $password = [string](Get-Content -LiteralPath $obsConnectionPath -Raw -Encoding utf8 | ConvertFrom-Json).password
    }
    $websocketConfig = [ordered]@{ alerts_enabled = $false; auth_required = $true; first_load = $false; server_enabled = $true; server_password = ''; server_port = $websocketPort }
    if (Test-Path -LiteralPath $websocketConfigPath) {
        $existing = Get-Content -LiteralPath $websocketConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($property in $existing.PSObject.Properties) {
            $websocketConfig[$property.Name] = $property.Value
        }
        if (-not $password -and [string]$existing.server_password) {
            $password = [string]$existing.server_password
        }
        $websocketConfig.server_enabled = $true
        $websocketConfig.auth_required = $true
        $websocketConfig.first_load = $false
    }
    if (-not $password) {
        $password = New-RandomPassword
    }
    $websocketConfig.server_password = $password
    $websocketPort = [int]$websocketConfig.server_port
    if ($PSCmdlet.ShouldProcess($websocketConfigPath, 'OBS の websocket サーバを有効にする（認証あり）')) {
        if (Test-Path -LiteralPath $websocketConfigPath) {
            Copy-Item -LiteralPath $websocketConfigPath -Destination ($websocketConfigPath + '.bak-' + (Get-Date -Format 'yyyyMMddHHmmss'))
        }
        Save-Utf8File -Path $websocketConfigPath -Content (($websocketConfig | ConvertTo-Json) + "`n")
        Save-Utf8File -Path $obsConnectionPath -Content (([ordered]@{ host = '127.0.0.1'; port = $websocketPort; password = $password } | ConvertTo-Json) + "`n")
        $summary.obs_config = 'websocket'
    }
    if ($freshObs -and $PSCmdlet.ShouldProcess($userIniPath, 'OBS の初回ウィザードを出さない（FirstRun=true）')) {
        Save-Utf8File -Path $userIniPath -Content "[General]`r`nFirstRun=true`r`n"
        $summary.obs_config = 'websocket+user.ini'
    } elseif (-not $freshObs) {
        Write-Output "OBS の設定フォルダが既にあるので user.ini は触りません ($obsConfigRoot)"
    }
}

# 4〜5. 管理者で導入
$needsVbCable = (-not $SkipVbCable) -and -not (Test-VbCableInstalled)
$needsObs = (-not $SkipObs) -and -not (Test-Path -LiteralPath $obsExecutable)
if (-not ($needsVbCable -or $needsObs)) {
    Write-Output '管理者での導入: 必要なものは入っています'
    $summary.elevated = 'not_needed'
} elseif ($PSCmdlet.ShouldProcess('VB-CABLE / OBS', '管理者に昇格して導入（UAC の確認が出る）')) {
    $elevatedArguments = @('-NoProfile', '-File', $PSCommandPath, '-ElevatedPhase')
    if (-not $needsVbCable) {
        $elevatedArguments += '-SkipVbCable'
    }
    if (-not $needsObs) {
        $elevatedArguments += '-SkipObs'
    }
    Write-Output '管理者に昇格します。UAC の確認で「はい」を押してください。'
    try {
        $elevatedProcess = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList $elevatedArguments -Verb RunAs -PassThru -Wait
    } catch {
        Write-Output ('昇格できませんでした: ' + $_.Exception.Message)
        Write-Output 'AUDIO_TOOLS elevated=refused'
        exit 2
    }
    if (Test-Path -LiteralPath $elevatedLogPath) {
        Write-Output '--- 管理者側の記録'
        Get-Content -LiteralPath $elevatedLogPath -Encoding utf8 | Where-Object { $_ -notmatch '^\*{10}|^(Windows PowerShell|PowerShell) ' }
        Write-Output '---'
    }
    $summary.elevated = if ($elevatedProcess.ExitCode -eq 0) { 'ok' } else { 'failed' }
    if ($elevatedProcess.ExitCode -ne 0) {
        Write-Output ('AUDIO_TOOLS ' + (($summary.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }) -join ' '))
        exit 1
    }
}

Write-Output ''
if ($summary.elevated -eq 'ok') {
    Write-Output '次: PC を再起動してください。再起動の後、このフォルダで Claude Code を開き直して「続けて」と入力してください。'
}
Write-Output ('AUDIO_TOOLS ' + (($summary.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }) -join ' '))
exit 0
