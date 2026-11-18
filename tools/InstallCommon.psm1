Set-StrictMode -Version Latest

<#
導入スクリプト（install_engine_deps.ps1 / install_audio_tools.ps1）で共用する関数。
ここの関数は何も書き換えない（Save-ToolManifest を除く）。ShouldProcess は呼び出し側で判定する。
#>

function Get-UserDownloadFolder {
    <# Windows の「ダウンロード」フォルダの実際の場所を返す。移動されていても追う。 #>
    $shellFoldersKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $downloadsValueName = '{374DE290-123F-4565-9164-39C4925E467B}'
    $properties = Get-ItemProperty -LiteralPath $shellFoldersKey -ErrorAction SilentlyContinue
    if ($null -ne $properties -and ($properties.PSObject.Properties.Name -contains $downloadsValueName)) {
        $rawPath = [string]$properties.$downloadsValueName
        if ($rawPath.Length -gt 0) {
            return [Environment]::ExpandEnvironmentVariables($rawPath)
        }
    }
    return (Join-Path -Path $env:USERPROFILE -ChildPath 'Downloads')
}

function Find-DownloadedFile {
    <# 複数のフォルダから、パターンに合う最新のファイルを 1 つ返す。無ければ $null。 #>
    param(
        [Parameter(Mandatory)] [string[]]$Folders,
        [Parameter(Mandatory)] [string]$Pattern
    )
    $candidates = foreach ($folder in $Folders) {
        if ($folder.Length -gt 0 -and (Test-Path -LiteralPath $folder -PathType Container)) {
            Get-ChildItem -LiteralPath $folder -Filter $Pattern -File -ErrorAction SilentlyContinue
        }
    }
    return ($candidates | Sort-Object -Property LastWriteTime -Descending | Select-Object -First 1)
}

function Get-FileSha256 {
    param([Parameter(Mandatory)] [string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-ToolManifest {
    param([Parameter(Mandatory)] [string]$Path)
    return (Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json)
}

function Save-ToolManifest {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] $Manifest
    )
    $jsonText = $Manifest | ConvertTo-Json -Depth 8
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $jsonText + "`n", $utf8NoBom)
}

function Test-PathUnderRoot {
    <# Path が Root の配下（Root 自身は含めない）なら $true。削除の前の安全確認に使う。 #>
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Root
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    return $fullPath.StartsWith($fullRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Compare-ManifestHash {
    <#
    取得物の SHA-256 をマニフェストと照合する。
    戻り値: 'recorded'（初回で未記録。呼び出し側が記録する）、'match'（一致）。不一致なら例外。
    #>
    param(
        [Parameter(Mandatory)] $Item,
        [Parameter(Mandatory)] [string]$ActualHash
    )
    $expectedHash = $Item.sha256
    if ($null -eq $expectedHash -or ([string]$expectedHash).Length -eq 0) {
        return 'recorded'
    }
    if (([string]$expectedHash).ToLowerInvariant() -ne $ActualHash) {
        throw ("SHA-256 が記録と一致しません: {0}`n  記録: {1}`n  実際: {2}`n" +
            "  別の版を取得した可能性があります。config\tools_manifest.json の版と入手先を確認してください。") -f $Item.name, $expectedHash, $ActualHash
    }
    return 'match'
}

Export-ModuleMember -Function Get-UserDownloadFolder, Find-DownloadedFile, Get-FileSha256, Read-ToolManifest,
    Save-ToolManifest, Test-PathUnderRoot, Compare-ManifestHash
