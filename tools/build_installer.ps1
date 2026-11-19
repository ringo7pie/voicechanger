<#
.SYNOPSIS
インストーラー（out\installer\VoiceChanger-Setup-<版>.exe）を作る。

.DESCRIPTION
1. VB-CABLE の zip（VBCABLE_Driver_Pack46.zip）を config\tools_manifest.json の SHA-256 で照合し、out\installer-staging\vbcable に展開する
   （runtime\downloads\ に無ければ、マニフェストの URL から取得する）
2. PyInstaller で out\pyinstaller\dist\VoiceChanger を作る（packaging\voicechanger.spec）
3. Inno Setup の ISCC で packaging\VoiceChanger.iss をコンパイルする
版は voicechanger\__init__.py の __version__。最後に exe のパス・大きさ・SHA-256 を出す。

前提: .venv に requirements-build.txt（PyInstaller）、Inno Setup 6（winget install JRSoftware.InnoSetup）。

.EXAMPLE
pwsh -NoProfile -File .\tools\build_installer.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipPyInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
Import-Module (Join-Path -Path $PSScriptRoot -ChildPath 'InstallCommon.psm1') -Force

$venvPython = Join-Path -Path $repoRoot -ChildPath '.venv\Scripts\python.exe'
$outRoot = Join-Path -Path $repoRoot -ChildPath 'out'
$stagingRoot = Join-Path -Path $outRoot -ChildPath 'installer-staging'
$vbCableStaging = Join-Path -Path $stagingRoot -ChildPath 'vbcable'
$distRoot = Join-Path -Path $outRoot -ChildPath 'pyinstaller\dist'
$distDir = Join-Path -Path $distRoot -ChildPath 'VoiceChanger'
$workDir = Join-Path -Path $outRoot -ChildPath 'pyinstaller\work'
$installerDir = Join-Path -Path $outRoot -ChildPath 'installer'

# 版
$initText = Get-Content -LiteralPath (Join-Path -Path $repoRoot -ChildPath 'voicechanger\__init__.py') -Raw -Encoding utf8
$versionMatch = [regex]::Match($initText, '__version__\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw 'voicechanger\__init__.py に __version__ がありません'
}
$appVersion = $versionMatch.Groups[1].Value
Write-Output "版: $appVersion"

# Inno Setup
$isccCandidates = @(
    (Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $iscc) {
    throw 'Inno Setup 6 の ISCC.exe が見つかりません（winget install JRSoftware.InnoSetup）'
}

# 1. VB-CABLE
Write-Output '== VB-CABLE'
$manifest = Read-ToolManifest -Path (Join-Path -Path $repoRoot -ChildPath 'config\tools_manifest.json')
$vbItem = $manifest.items.vbcable
$vbZip = Join-Path -Path (Join-Path -Path $repoRoot -ChildPath 'runtime\downloads') -ChildPath $vbItem.file_pattern
if (-not (Test-Path -LiteralPath $vbZip)) {
    New-Item -ItemType Directory -Path (Split-Path -Path $vbZip -Parent) -Force | Out-Null
    Write-Output "取得: $($vbItem.url)"
    Invoke-WebRequest -Uri $vbItem.url -OutFile $vbZip
}
Compare-ManifestHash -Item $vbItem -ActualHash (Get-FileSha256 -Path $vbZip) | Out-Null
if (Test-Path -LiteralPath $vbCableStaging) {
    Remove-Item -LiteralPath $vbCableStaging -Recurse -Force
}
Expand-Archive -LiteralPath $vbZip -DestinationPath $vbCableStaging
if (-not (Test-Path -LiteralPath (Join-Path -Path $vbCableStaging -ChildPath 'VBCABLE_Setup_x64.exe'))) {
    throw "VB-CABLE の zip に VBCABLE_Setup_x64.exe がありません: $vbZip"
}

# 2. PyInstaller
if (-not $SkipPyInstaller) {
    Write-Output '== PyInstaller'
    & $venvPython -m PyInstaller (Join-Path -Path $repoRoot -ChildPath 'packaging\voicechanger.spec') --noconfirm --clean `
        --distpath $distRoot --workpath $workDir
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller が失敗しました（終了コード $LASTEXITCODE）"
    }
}
if (Test-Path -LiteralPath (Join-Path -Path $distDir -ChildPath '_internal\pylibrb')) {
    throw 'pylibrb が実行ファイルに入っています（GPL-2.0 のため同梱しない。spec の excludes を確認）'
}

# 3. Inno Setup
Write-Output '== Inno Setup'
& $iscc "/DAppVersion=$appVersion" "/DDistDir=$distDir" "/DVbCableDir=$vbCableStaging" "/DOutputDir=$installerDir" `
    (Join-Path -Path $repoRoot -ChildPath 'packaging\VoiceChanger.iss')
if ($LASTEXITCODE -ne 0) {
    throw "ISCC が失敗しました（終了コード $LASTEXITCODE）"
}
$installer = Join-Path -Path $installerDir -ChildPath "VoiceChanger-Setup-$appVersion.exe"
$item = Get-Item -LiteralPath $installer
Write-Output ('INSTALLER path={0} size_mb={1:N1} sha256={2}' -f $item.FullName, ($item.Length / 1MB), (Get-FileSha256 -Path $installer))
