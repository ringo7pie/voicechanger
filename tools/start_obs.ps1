<#
.SYNOPSIS
OBS Studio を VoiceChanger 用のプロファイルとシーンコレクションで起動する（-Stop で閉じる、-Status で状態）。

.DESCRIPTION
起動は Win32_Process.Create で行う（Claude Code の PowerShell ツールは Job の中で動き、子孫を片付けるため。
knowledge/10-silver/areas/env.md）。作業フォルダは OBS の bin\64bit にする（OBS はそこからでないと起動できない）。
websocket（data\obs_websocket.json のポート）が応答するまで待つ。
閉じるときはウィンドウを閉じる要求を送るだけで、強制終了はしない（強制終了すると次回 Safe Mode を勧められ、Safe Mode では websocket が動かない）。

-Plain : プロファイルとコレクションを指定せずに起動する（最初の設定用）。

出力の最終行は OBS_ で始まる 1 行。終了コード: 0 = 成功、1 = 失敗、2 = 起動していない（-Stop / -Status）。

.EXAMPLE
pwsh -NoProfile -File .\tools\start_obs.ps1
pwsh -NoProfile -File .\tools\start_obs.ps1 -Stop
#>
param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Plain
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$obsDirectory = Join-Path -Path $env:ProgramFiles -ChildPath 'obs-studio\bin\64bit'
$obsExecutable = Join-Path -Path $obsDirectory -ChildPath 'obs64.exe'
$connectionPath = Join-Path -Path $repoRoot -ChildPath 'data\obs_websocket.json'
$port = 4455
if (Test-Path -LiteralPath $connectionPath) {
    $port = [int](Get-Content -LiteralPath $connectionPath -Raw -Encoding utf8 | ConvertFrom-Json).port
}

function Test-WebsocketPort {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $port)
        return $task.Wait(500) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$running = @(Get-Process -Name 'obs64' -ErrorAction SilentlyContinue)

if ($Status) {
    if ($running.Count -eq 0) {
        Write-Output 'OBS_NOT_RUNNING'
        exit 2
    }
    Write-Output ('OBS_RUNNING pid={0} websocket={1}' -f ($running.Id -join ','), (Test-WebsocketPort))
    exit 0
}

if ($Stop) {
    if ($running.Count -eq 0) {
        Write-Output 'OBS_NOT_RUNNING'
        exit 2
    }
    foreach ($process in $running) {
        $process.CloseMainWindow() | Out-Null
    }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and @(Get-Process -Name 'obs64' -ErrorAction SilentlyContinue).Count -gt 0) {
        Start-Sleep -Milliseconds 300
    }
    if (@(Get-Process -Name 'obs64' -ErrorAction SilentlyContinue).Count -gt 0) {
        Write-Output 'OBS_STOP_TIMEOUT（確認の画面が出ていないか見てください。強制終了はしません）'
        exit 1
    }
    Write-Output 'OBS_STOPPED'
    exit 0
}

if ($running.Count -gt 0) {
    Write-Output ('OBS_ALREADY_RUNNING pid={0} websocket={1}' -f ($running.Id -join ','), (Test-WebsocketPort))
    exit 0
}
if (-not (Test-Path -LiteralPath $obsExecutable)) {
    Write-Output "OBS_NOT_INSTALLED $obsExecutable"
    exit 1
}
$arguments = '--disable-updater --disable-shutdown-check'
if (-not $Plain) {
    $arguments = '--profile VoiceChanger --collection VoiceChanger ' + $arguments
}
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = ('"{0}" {1}' -f $obsExecutable, $arguments)
    CurrentDirectory = $obsDirectory
}
if ($created.ReturnValue -ne 0) {
    Write-Output ('OBS_START_FAILED win32_return={0}' -f $created.ReturnValue)
    exit 1
}
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if ($null -eq (Get-Process -Id ([int]$created.ProcessId) -ErrorAction SilentlyContinue)) {
        Write-Output ('OBS_EXITED pid={0}' -f $created.ProcessId)
        exit 1
    }
    if (Test-WebsocketPort) {
        Start-Sleep -Seconds 1
        Write-Output ('OBS_READY pid={0} port={1}' -f $created.ProcessId, $port)
        exit 0
    }
}
Write-Output ('OBS_START_TIMEOUT pid={0}（websocket が応答しません）' -f $created.ProcessId)
exit 1
