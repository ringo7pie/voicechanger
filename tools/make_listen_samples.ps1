<#
.SYNOPSIS
自分の声の録音を、ピッチ・フォルマントの各プリセットと Beatrice の各声で変換し、聴き比べページ（out\listen\index.html）を作る。

.DESCRIPTION
入力は設定の self_voice_dir（既定 E:\nkdx\Games\voice_train\corpora\self\trimmed）の先頭から -Count 本。読むだけで書き換えない。
音量は声のある区間の RMS を -20 dBFS にそろえる（実際の音量は表に出す）。
出力の最終行は LISTEN_PAGE で始まる 1 行。

.PARAMETER Count
使う録音の数。

.PARAMETER NoOpen
作ったページをブラウザで開かない。

.EXAMPLE
pwsh -NoProfile -File .\tools\make_listen_samples.ps1
#>
param(
    [int]$Count = 2,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Output '.venv がありません。先に tools\install_engine_deps.ps1 を実行してください。'
    exit 1
}
Set-Location -LiteralPath $repoRoot
& $venvPython -m voicechanger listen-page --count $Count
if ($LASTEXITCODE -ne 0) {
    Write-Output "聴き比べページを作れませんでした（終了コード $LASTEXITCODE）"
    exit $LASTEXITCODE
}
$pagePath = Join-Path -Path $repoRoot -ChildPath 'out\listen\index.html'
if (-not $NoOpen) {
    Start-Process -FilePath $pagePath
}
exit 0
