<#
.SYNOPSIS
エンジンに必要なもの（Python の venv と依存、Beatrice 2 の公式 VST とモデル）を導入する。管理者権限は不要。

.DESCRIPTION
次の順に行う。済んでいる段は飛ばす。
  1. .venv を作る（E:\Program Files\Python314\python.exe -m venv）
  2. requirements.txt を入れる
  3. requirements-optional.txt（pylibrb）を入れる。失敗しても止めない
  4. Beatrice の VST とモデルの zip を探し、SHA-256 を照合して runtime\beatrice\ に展開する

Beatrice の zip は、先にブラウザで公式ページから取得しておく（配布サーバーはスクリプトからの直接取得を 403 で拒否する）。
  VST    : https://prj-beatrice.com/ の「Ver. 2.0.0-rc.3」の Windows 版（beatrice_2.0.0-rc.3_win.zip）
  モデル : https://prj-beatrice.com/2.0.0-rc.0-official-model-1-terms.html の規約を読み、
           同意したうえで同じページのリンクから取得（beatrice_2.0.0-rc.0_official_model_1.zip）
zip は Windows のダウンロードフォルダ、このリポジトリの Downloads\、runtime\downloads\ のどれかに置く。

ドライバ（VB-CABLE）と OBS は tools\install_audio_tools.ps1 で入れる。

出力の最終行は ENGINE_DEPS で始まる 1 行の要約。
終了コード: 0 = すべて済み、2 = Beatrice の zip が見つからず一部を飛ばした、1 = 失敗。

.PARAMETER DownloadFolder
Beatrice の zip を探すフォルダ。既定は Windows のダウンロードフォルダ（このリポジトリの Downloads\ と runtime\downloads\ も必ず探す）。

.PARAMETER SkipPython
venv と pip を飛ばす。

.PARAMETER SkipBeatrice
Beatrice の展開を飛ばす。

.EXAMPLE
pwsh -NoProfile -File .\tools\install_engine_deps.ps1 -WhatIf
pwsh -NoProfile -File .\tools\install_engine_deps.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$DownloadFolder = '',
    [switch]$SkipPython,
    [switch]$SkipBeatrice
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
Import-Module -Name (Join-Path -Path $PSScriptRoot -ChildPath 'InstallCommon.psm1') -Force

$globalPython = 'E:\Program Files\Python314\python.exe'
$venvRoot = Join-Path -Path $repoRoot -ChildPath '.venv'
$venvPython = Join-Path -Path $venvRoot -ChildPath 'Scripts\python.exe'
$requirementsPath = Join-Path -Path $repoRoot -ChildPath 'requirements.txt'
$optionalRequirementsPath = Join-Path -Path $repoRoot -ChildPath 'requirements-optional.txt'
$manifestPath = Join-Path -Path $repoRoot -ChildPath 'config\tools_manifest.json'
$runtimeRoot = Join-Path -Path $repoRoot -ChildPath 'runtime'
$runtimeDownloads = Join-Path -Path $runtimeRoot -ChildPath 'downloads'

$summary = [ordered]@{
    venv          = 'skipped'
    pip           = 'skipped'
    optional      = 'skipped'
    beatrice_vst  = 'skipped'
    beatrice_model = 'skipped'
}
$missingItems = [System.Collections.Generic.List[object]]::new()

# ---------------------------------------------------------------- 1〜3. Python
if (-not $SkipPython) {
    Write-Output '== Python の venv と依存'
    if (Test-Path -LiteralPath $venvPython) {
        Write-Output "venv: 既にあります ($venvPython)"
        $summary.venv = 'exists'
    } else {
        if (-not (Test-Path -LiteralPath $globalPython)) {
            throw "Python が見つかりません: $globalPython"
        }
        if ($PSCmdlet.ShouldProcess($venvRoot, 'venv を作成')) {
            & $globalPython -m venv $venvRoot
            if ($LASTEXITCODE -ne 0) {
                throw "venv の作成に失敗しました（終了コード $LASTEXITCODE）"
            }
            $summary.venv = 'created'
        } else {
            $summary.venv = 'whatif'
        }
    }

    if ($PSCmdlet.ShouldProcess($requirementsPath, 'pip install -r')) {
        & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw "pip install に失敗しました（終了コード $LASTEXITCODE）: $requirementsPath"
        }
        $summary.pip = 'ok'
    } else {
        $summary.pip = 'whatif'
    }

    if ($PSCmdlet.ShouldProcess($optionalRequirementsPath, 'pip install -r（失敗しても続ける）')) {
        & $venvPython -m pip install --disable-pip-version-check -r $optionalRequirementsPath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "任意の依存（pylibrb）を入れられませんでした（終了コード $LASTEXITCODE）。dsp モードは numpy の実装を使います。"
            $summary.optional = 'failed'
        } else {
            $summary.optional = 'ok'
        }
    } else {
        $summary.optional = 'whatif'
    }

    if ($summary.pip -eq 'ok') {
        $importCheck = 'import numpy, pedalboard, sounddevice, obsws_python, PySide6; from PySide6 import QtWidgets; ' +
            'print("import ok: numpy", numpy.__version__, "/ pedalboard", pedalboard.__version__, "/ sounddevice", sounddevice.__version__, "/ PySide6", PySide6.__version__)'
        & $venvPython -c $importCheck
        if ($LASTEXITCODE -ne 0) {
            throw "依存の import に失敗しました（終了コード $LASTEXITCODE）"
        }
    }
}

# ---------------------------------------------------------------- 4. Beatrice
if (-not $SkipBeatrice) {
    Write-Output '== Beatrice 2 の公式 VST とモデル'
    $manifest = Read-ToolManifest -Path $manifestPath
    $searchFolders = @()
    if ($DownloadFolder.Length -gt 0) {
        $searchFolders += $DownloadFolder
    } else {
        $searchFolders += Get-UserDownloadFolder
    }
    $searchFolders += (Join-Path -Path $repoRoot -ChildPath 'Downloads')
    $searchFolders += $runtimeDownloads
    Write-Output ('zip を探す場所: ' + ($searchFolders -join ' / '))

    $manifestChanged = $false
    $beatriceKeys = @(
        @{ Key = 'beatrice_vst'; SummaryName = 'beatrice_vst' },
        @{ Key = 'beatrice_model_1'; SummaryName = 'beatrice_model' }
    )
    foreach ($beatriceEntry in $beatriceKeys) {
        $item = $manifest.items.($beatriceEntry.Key)
        $zipFile = Find-DownloadedFile -Folders $searchFolders -Pattern $item.file_pattern
        if ($null -eq $zipFile) {
            Write-Output ("見つかりません: {0}（{1}）" -f $item.name, $item.file_pattern)
            $summary[$beatriceEntry.SummaryName] = 'missing'
            $missingItems.Add($item)
            continue
        }
        Write-Output ("見つかりました: {0}" -f $zipFile.FullName)

        $actualHash = Get-FileSha256 -Path $zipFile.FullName
        $hashState = Compare-ManifestHash -Item $item -ActualHash $actualHash
        Write-Output ("SHA-256: {0}（{1}）" -f $actualHash, $(if ($hashState -eq 'match') { '記録と一致' } else { '初回。記録します' }))
        if ($hashState -eq 'recorded') {
            $item.sha256 = $actualHash
            $manifestChanged = $true
        }

        $destination = Join-Path -Path $repoRoot -ChildPath ($item.extract_to -replace '/', '\')
        if (-not (Test-PathUnderRoot -Path $destination -Root $runtimeRoot)) {
            throw "展開先が runtime\ の外です: $destination"
        }
        if ($PSCmdlet.ShouldProcess($destination, ("{0} を展開（既存の中身は消す）" -f $zipFile.Name))) {
            if (Test-Path -LiteralPath $destination) {
                Remove-Item -LiteralPath $destination -Recurse -Force
            }
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            Expand-Archive -LiteralPath $zipFile.FullName -DestinationPath $destination
            $vst3Items = @(Get-ChildItem -LiteralPath $destination -Recurse -Filter '*.vst3' -ErrorAction SilentlyContinue)
            $tomlItems = @(Get-ChildItem -LiteralPath $destination -Recurse -Filter '*.toml' -File -ErrorAction SilentlyContinue)
            Write-Output ("展開しました: {0}（.vst3 {1} 件、.toml {2} 件）" -f $destination, $vst3Items.Count, $tomlItems.Count)
            foreach ($foundItem in ($vst3Items + $tomlItems)) {
                Write-Output ('  ' + $foundItem.FullName.Substring($repoRoot.Length + 1))
            }
            $summary[$beatriceEntry.SummaryName] = 'ok'
        } else {
            $summary[$beatriceEntry.SummaryName] = 'whatif'
        }
    }

    if ($manifestChanged -and $PSCmdlet.ShouldProcess($manifestPath, 'SHA-256 を記録')) {
        Save-ToolManifest -Path $manifestPath -Manifest $manifest
        Write-Output "SHA-256 を記録しました: $manifestPath"
    }

    if ($missingItems.Count -gt 0) {
        Write-Output ''
        Write-Output 'Beatrice の zip をブラウザで取得してから、もう一度このスクリプトを実行してください（済んだ段は飛ばします）。'
        foreach ($missingItem in $missingItems) {
            Write-Output ("  - {0}" -f $missingItem.name)
            Write-Output ("      ページ: {0}" -f $missingItem.page)
            Write-Output ("      取得法: {0}" -f $missingItem.acquire)
        }
    }
}

$summaryText = ($summary.GetEnumerator() | ForEach-Object { '{0}={1}' -f $_.Key, $_.Value }) -join ' '
Write-Output ''
Write-Output "ENGINE_DEPS $summaryText"
if ($missingItems.Count -gt 0) {
    exit 2
}
exit 0
