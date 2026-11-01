#Requires -Version 5.1
[CmdletBinding()]
param([string]$OutputDirectory = '', [string]$MSBuild = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deploy-common.ps1')
if (-not $OutputDirectory) { $OutputDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) '.build\mp4muxer' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$commit = 'dbc2a9721ae2724591a4b594d19a60423e511cd0'
$asset = @{ file='dlb_mp4base-source.zip'; sha256='BF3E2A91A94135F192B33C13B9160CB28DEF4CAA12A8C9008319DA0ED2BFD6F4';
    url="https://codeload.github.com/DolbyLaboratories/dlb_mp4base/zip/$commit" }
if (-not $MSBuild) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $MSBuild = @(& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find 'MSBuild\**\Bin\MSBuild.exe')[0]
}
if (-not $MSBuild -or -not (Test-Path -LiteralPath $MSBuild)) { throw 'Visual Studio 2022 C++ Build Tools and Windows SDK are required.' }
$archive = Get-DeployDownload -Asset $asset -CacheDirectory (Join-Path $OutputDirectory 'downloads')
$extract = Join-Path $OutputDirectory ('source_' + [guid]::NewGuid().ToString('N').Substring(0,8))
Expand-DeployZip -Archive $archive -Destination $extract
$source = Join-Path $extract ('dlb_mp4base-' + $commit)
$projects = @('make\mp4muxer\windows_amd64\mp4muxer_2010.vcxproj','make\libmp4base\windows_amd64\libmp4base_2010.vcxproj')
foreach ($relative in $projects) {
    $path = Join-Path $source $relative
    $text = [IO.File]::ReadAllText($path).Replace('<RuntimeLibrary>MultiThreadedDLL</RuntimeLibrary>', '<RuntimeLibrary>MultiThreaded</RuntimeLibrary>')
    [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false)))
}
$bin = Join-Path $OutputDirectory 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
& $MSBuild (Join-Path $source $projects[0]) /nologo /m:2 /p:Configuration=release /p:Platform=x64 /p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0 ('/p:OutDir=' + $bin + '\')
if ($LASTEXITCODE -ne 0 -or -not (Test-DeployTool -Path (Join-Path $bin 'mp4muxer.exe') -Kind mp4muxer)) { throw 'mp4muxer build/check failed.' }
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination (Join-Path $bin 'LICENSE.dlb_mp4base.txt')
Write-Host ('Built {0}; source commit {1}; static CRT.' -f (Join-Path $bin 'mp4muxer.exe'), $commit)
