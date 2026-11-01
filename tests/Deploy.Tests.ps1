#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\deploy-common.ps1')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$parent = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
$testDirectory = Join-Path $parent ('SDR2DV_deploy_test_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
$passed = 0
try {
    $cache = Join-Path $testDirectory 'cache'
    New-Item -ItemType Directory -Path $cache | Out-Null
    $file = Join-Path $cache 'example.zip'
    [IO.File]::WriteAllText($file, 'verified cache data')
    $hash = (Get-FileHash -LiteralPath $file).Hash
    $asset = @{ file='example.zip';url='https://example.invalid/example.zip';sha256=$hash }
    if ((Get-DeployDownload -Asset $asset -CacheDirectory $cache) -ne $file) { throw 'Offline cache reuse failed.' }
    $passed++
    if ((Get-DeployDownload -Asset $asset -CacheDirectory ($cache + '\')) -ne $file) { throw 'Trailing-separator cache path failed.' }
    $passed++

    [IO.File]::WriteAllText($file, 'changed cache data')
    $rejected = $false
    try { $null = Get-DeployDownload -Asset $asset -CacheDirectory $cache } catch { $rejected = $_.Exception.Message -match 'SHA256 mismatch' }
    if (-not $rejected -or [IO.File]::ReadAllText($file) -ne 'changed cache data') { throw 'Corrupt cache was not rejected intact.' }
    $passed++

    $archive = Join-Path $testDirectory 'safe.zip'
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('folder/file.txt')
        $writer = New-Object IO.StreamWriter($entry.Open())
        try { $writer.Write('package contents') } finally { $writer.Dispose() }
    } finally { $zip.Dispose() }
    $destination = Join-Path $testDirectory 'safe'
    Expand-DeployZip -Archive $archive -Destination $destination
    if ([IO.File]::ReadAllText((Join-Path $destination 'folder\file.txt')) -ne 'package contents') { throw 'ZIP extraction failed.' }
    $passed++

    $unsafe = Join-Path $testDirectory 'unsafe.zip'
    $zip = [IO.Compression.ZipFile]::Open($unsafe, [IO.Compression.ZipArchiveMode]::Create)
    try { $null = $zip.CreateEntry('../escaped.txt') } finally { $zip.Dispose() }
    $rejected = $false
    try { Expand-DeployZip -Archive $unsafe -Destination (Join-Path $testDirectory 'unsafe') }
    catch { $rejected = $_.Exception.Message -match 'escapes extraction' }
    if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $testDirectory 'escaped.txt'))) { throw 'ZIP traversal was not rejected.' }
    $passed++
    Write-Host ("Passed {0} deployment tests." -f $passed)
}
finally {
    $absolute = [IO.Path]::GetFullPath($testDirectory)
    if ([IO.Path]::GetDirectoryName($absolute) -ne $parent -or [IO.Path]::GetFileName($absolute) -notlike 'SDR2DV_deploy_test_*' -or
        ((Get-Item -LiteralPath $absolute).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe test cleanup target.' }
    Remove-Item -LiteralPath $absolute -Recurse -Force
}
