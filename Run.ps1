<#
.SYNOPSIS
ボイスチェンジャー（マイク → 変換 → 仮想マイク CABLE Output）を起動・停止・切り替えする。

.DESCRIPTION
引数なしで実行すると、このウィンドウでエンジンを起動し、ブラウザで操作画面を開く。止めるのは Ctrl+C か画面を閉じたあと -Stop。
起動中にもう一度実行すると、操作画面を開くだけ。

-Mode       : 起動中のエンジンのモードを切り替える（dsp / ai / mute / through）。
-Status     : 起動中のエンジンの状態を出す。
-Stop       : 起動中のエンジンを止める。
-Plan       : 何も起動せず、使う設定・デバイス・処理器の準備状況を出す。
-Background : 窓を出さずに裏で起動する（Claude Code の試験用。ログは data\logs\engine.log）。
-NoBrowser  : 操作画面を開かない。
-InputWav   : マイクの代わりに WAV を流す（試験用）。-LoopInput で繰り返す。
-OutputDevice : 出力デバイスをこの起動だけ変える（試験用）。-NoMonitor でモニターのストリームを開かない。

終了コード: 0 = 成功、1 = 失敗、2 = 起動していない、3 = デバイスが見つからない、4 = 既に起動している（-Background のとき）。

.EXAMPLE
pwsh -NoProfile -File .\Run.ps1
pwsh -NoProfile -File .\Run.ps1 -Mode ai
pwsh -NoProfile -File .\Run.ps1 -Status
pwsh -NoProfile -File .\Run.ps1 -Stop
pwsh -NoProfile -File .\Run.ps1 -Plan
#>
[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Mode', Mandatory)]
    [ValidateSet('dsp', 'ai', 'mute', 'through')]
    [string]$Mode,

    [Parameter(ParameterSetName = 'Status', Mandatory)]
    [switch]$Status,

    [Parameter(ParameterSetName = 'Stop', Mandatory)]
    [switch]$Stop,

    [Parameter(ParameterSetName = 'Plan', Mandatory)]
    [switch]$Plan,

    [Parameter(ParameterSetName = 'Start')]
    [switch]$Background,

    [Parameter(ParameterSetName = 'Start')]
    [switch]$NoBrowser,

    [Parameter(ParameterSetName = 'Start')]
    [string]$InputWav = '',

    [Parameter(ParameterSetName = 'Start')]
    [switch]$LoopInput,

    [Parameter(ParameterSetName = 'Start')]
    [string]$OutputDevice = '',

    [Parameter(ParameterSetName = 'Start')]
    [switch]$NoMonitor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
$controlPath = Join-Path -Path $repoRoot -ChildPath 'data\control.json'
$tokenHeader = 'X-VoiceChanger-Token'

function Get-EngineControl {
    <# 起動中なら data\control.json の中身を返す。PID が生きていなければ $null。 #>
    if (-not (Test-Path -LiteralPath $controlPath)) {
        return $null
    }
    $control = Get-Content -LiteralPath $controlPath -Raw -Encoding utf8 | ConvertFrom-Json
    $process = Get-Process -Id ([int]$control.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    return $control
}

function Invoke-EngineApi {
    param(
        [Parameter(Mandatory)] $Control,
        [Parameter(Mandatory)] [string]$Path,
        [hashtable]$Body = $null
    )
    $uri = 'http://127.0.0.1:{0}{1}' -f $Control.port, $Path
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 10
    }
    $headers = @{ $tokenHeader = [string]$Control.token }
    $jsonBody = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Uri $uri -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($jsonBody)) -TimeoutSec 10
}

function Write-EngineStatus {
    param([Parameter(Mandatory)] $EngineStatus)
    $buffers = $EngineStatus.buffers
    $processing = $EngineStatus.processing
    $devices = $null
    if ($EngineStatus.PSObject.Properties.Name -contains 'backend' -and $null -ne $EngineStatus.backend) {
        $devices = $EngineStatus.backend.devices
    }
    Write-Output ('mode={0} pending={1} monitor={2} aligned_latency_ms={3:N0}' -f $EngineStatus.mode, $EngineStatus.pending_mode, $EngineStatus.monitor, $EngineStatus.aligned_latency_ms)
    if ($null -ne $devices) {
        Write-Output ('input={0} / output={1} / monitor={2}' -f $devices.input, $devices.output, $devices.monitor)
    }
    Write-Output ('levels: in={0} dB out={1} dB' -f $EngineStatus.levels.input_peak_db, $EngineStatus.levels.output_peak_db)
    Write-Output ('processing: avg={0:N2} ms worst={1:N2} ms blocks={2}' -f $processing.average_block_ms, $processing.worst_block_ms, $buffers.blocks)
    Write-Output ('buffers: output_ms={0:N0} underruns={1} drift_drops={2} drift_inserts={3} forced_drops={4} processor_errors={5}' -f `
            $buffers.output_ms, $buffers.output_underruns, $buffers.drift_drops, $buffers.drift_inserts, $buffers.forced_drops, $buffers.processor_errors)
    if ($EngineStatus.PSObject.Properties.Name -contains 'obs' -and $null -ne $EngineStatus.obs) {
        Write-Output ('obs: connected={0} recording={1}' -f $EngineStatus.obs.connected, $EngineStatus.obs.recording)
    }
    if ([string]$EngineStatus.notice) {
        Write-Output ('notice: ' + $EngineStatus.notice)
    }
    foreach ($property in $EngineStatus.unavailable.PSObject.Properties) {
        Write-Output ('unavailable: {0}: {1}' -f $property.Name, $property.Value)
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Output '.venv がありません。先に次を実行してください: pwsh -NoProfile -File .\tools\install_engine_deps.ps1'
    exit 1
}

switch ($PSCmdlet.ParameterSetName) {
    'Plan' {
        Set-Location -LiteralPath $repoRoot
        & $venvPython -m voicechanger plan
        exit $LASTEXITCODE
    }
    'Status' {
        $control = Get-EngineControl
        if ($null -eq $control) {
            Write-Output 'VOICECHANGER_NOT_RUNNING'
            exit 2
        }
        Write-Output ('VOICECHANGER_RUNNING url={0} pid={1}' -f $control.url, $control.pid)
        Write-EngineStatus -EngineStatus (Invoke-EngineApi -Control $control -Path '/api/status')
        exit 0
    }
    'Mode' {
        $control = Get-EngineControl
        if ($null -eq $control) {
            Write-Output 'VOICECHANGER_NOT_RUNNING（先に .\Run.ps1 で起動してください）'
            exit 2
        }
        try {
            $response = Invoke-EngineApi -Control $control -Path '/api/mode' -Body @{ mode = $Mode }
        } catch {
            $message = $_.ErrorDetails.Message
            if (-not $message) {
                $message = $_.Exception.Message
            }
            Write-Output ('VOICECHANGER_MODE_REJECTED ' + $message)
            exit 1
        }
        Write-Output ('VOICECHANGER_MODE mode={0} pending={1}' -f $response.result.mode, $response.result.pending_mode)
        exit 0
    }
    'Stop' {
        $control = Get-EngineControl
        if ($null -eq $control) {
            Write-Output 'VOICECHANGER_NOT_RUNNING'
            exit 2
        }
        Invoke-EngineApi -Control $control -Path '/api/shutdown' -Body @{} | Out-Null
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and $null -ne (Get-Process -Id ([int]$control.pid) -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 200
        }
        if ($null -ne (Get-Process -Id ([int]$control.pid) -ErrorAction SilentlyContinue)) {
            Write-Output ('VOICECHANGER_STOP_TIMEOUT pid={0}' -f $control.pid)
            exit 1
        }
        Write-Output 'VOICECHANGER_STOPPED'
        exit 0
    }
    'Start' {
        $control = Get-EngineControl
        if ($null -ne $control) {
            Write-Output ('VOICECHANGER_ALREADY_RUNNING url={0} pid={1}' -f $control.url, $control.pid)
            if (-not $NoBrowser -and -not $Background) {
                Start-Process -FilePath ([string]$control.url)
            }
            if ($Background) {
                exit 4
            }
            exit 0
        }
        $runArguments = @('-m', 'voicechanger', 'run')
        if ($InputWav) {
            $resolvedWav = (Resolve-Path -LiteralPath $InputWav).Path
            $runArguments += @('--input-wav', $resolvedWav)
            if ($LoopInput) {
                $runArguments += '--loop-input'
            }
        }
        if ($OutputDevice) {
            $runArguments += @('--output-device', $OutputDevice)
        }
        if ($NoMonitor) {
            $runArguments += '--no-monitor'
        }
        if (-not $Background) {
            if (-not $NoBrowser) {
                $runArguments += '--open-browser'
            }
            Set-Location -LiteralPath $repoRoot
            & $venvPython @runArguments
            exit $LASTEXITCODE
        }

        # 裏で起動する。Claude Code の PowerShell ツールは Job の中で動き、子孫を片付けるので、Win32_Process.Create で外に出す
        $quotedArguments = ($runArguments | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } }) -join ' '
        $commandLine = '"{0}" {1}' -f $venvPython, $quotedArguments
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
        $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine               = $commandLine
            CurrentDirectory          = $repoRoot
            ProcessStartupInformation = $startup
        }
        if ($created.ReturnValue -ne 0) {
            Write-Output ('VOICECHANGER_START_FAILED win32_return={0}' -f $created.ReturnValue)
            exit 1
        }
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            if ($null -eq (Get-Process -Id ([int]$created.ProcessId) -ErrorAction SilentlyContinue)) {
                Write-Output ('VOICECHANGER_EXITED pid={0}（data\logs\engine.log を見てください）' -f $created.ProcessId)
                exit 3
            }
            $control = Get-EngineControl
            if ($null -ne $control) {
                try {
                    $engineStatus = Invoke-EngineApi -Control $control -Path '/api/status'
                    Write-Output ('VOICECHANGER_READY url={0} pid={1} mode={2}' -f $control.url, $control.pid, $engineStatus.mode)
                    exit 0
                } catch {
                    continue
                }
            }
        }
        Write-Output ('VOICECHANGER_START_TIMEOUT pid={0}' -f $created.ProcessId)
        exit 1
    }
}
