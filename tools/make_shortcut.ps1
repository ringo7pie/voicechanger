<#
.SYNOPSIS
デスクトップに VoiceChanger の GUI のショートカット（VoiceChanger.lnk）を作る。

.DESCRIPTION
宛先は .venv\Scripts\pythonw.exe -m voicechanger.gui（黒い窓を出さない）。作業フォルダはこのリポジトリ。
アイコン voicechanger\gui\voicechanger.ico が無ければ、GUI のコードから書き出す。
同じ名前で別の宛先のショートカットが既にあれば、上書きせずに終わる（終了コード 3）。

-Remove : このスクリプトが作ったショートカット（宛先が同じもの）を消す。

出力の最終行は SHORTCUT で始まる 1 行。終了コード: 0 = 成功、1 = 失敗、3 = 別のショートカットがあるので触らない。

.EXAMPLE
pwsh -NoProfile -File .\tools\make_shortcut.ps1 -WhatIf
pwsh -NoProfile -File .\tools\make_shortcut.ps1
pwsh -NoProfile -File .\tools\make_shortcut.ps1 -Remove
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$pythonw = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\pythonw.exe'
$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
$iconPath = Join-Path -Path $repoRoot -ChildPath 'voicechanger\gui\voicechanger.ico'
$arguments = '-m voicechanger.gui'
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$shortcutPath = Join-Path -Path $desktop -ChildPath 'VoiceChanger.lnk'

if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Output "SHORTCUT error=no_venv（先に tools\install_engine_deps.ps1 を実行してください）"
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$ourTarget = [System.IO.Path]::GetFullPath($pythonw)

function Test-OurShortcut {
    param([Parameter(Mandatory)] [string]$Path)
    $existing = $shell.CreateShortcut($Path)
    $target = if ($existing.TargetPath) { [System.IO.Path]::GetFullPath($existing.TargetPath) } else { '' }
    return ($target -ieq $ourTarget) -and ($existing.Arguments -eq $arguments)
}

if ($Remove) {
    if (-not (Test-Path -LiteralPath $shortcutPath)) {
        Write-Output "SHORTCUT removed=none path=$shortcutPath"
        exit 0
    }
    if (-not (Test-OurShortcut -Path $shortcutPath)) {
        Write-Output "SHORTCUT error=not_ours（宛先が違うので消しません） path=$shortcutPath"
        exit 3
    }
    if ($PSCmdlet.ShouldProcess($shortcutPath, 'ショートカットを消す')) {
        Remove-Item -LiteralPath $shortcutPath
        Write-Output "SHORTCUT removed=yes path=$shortcutPath"
    }
    exit 0
}

if ((Test-Path -LiteralPath $shortcutPath) -and -not (Test-OurShortcut -Path $shortcutPath)) {
    Write-Output "SHORTCUT error=exists（同じ名前で別の宛先のショートカットがあるので上書きしません） path=$shortcutPath"
    exit 3
}

if (-not (Test-Path -LiteralPath $iconPath)) {
    if ($PSCmdlet.ShouldProcess($iconPath, 'アイコンを書き出す')) {
        & $venvPython -m voicechanger.gui --write-icon $iconPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $iconPath)) {
            Write-Output "SHORTCUT error=icon（アイコンを書き出せません。PySide6 が入っているか確認してください）"
            exit 1
        }
    }
}

if ($PSCmdlet.ShouldProcess($shortcutPath, ('ショートカットを作る（{0} {1}）' -f $ourTarget, $arguments))) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $ourTarget
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = 'VoiceChanger（マイクの声をリアルタイムに変える）'
    $shortcut.Save()
    Write-Output "SHORTCUT created=yes path=$shortcutPath"
}
exit 0
