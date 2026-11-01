#Requires -Version 5.1
<#
.SYNOPSIS
    Prepare SDR2DV dependencies without changing system PATH or starting conversion.
.PARAMETER Mode
    Auto enables TrueHDR setup when an NVIDIA adapter is present. Standard skips it.
.PARAMETER Portable
    Use project-local tools instead of discovering external tools.
.PARAMETER CheckOnly
    Verify existing tools and inference; do not download or write configuration.
#>
[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Standard', 'TrueHDR')][string]$Mode = 'Auto',
    [switch]$Portable,
    [switch]$CheckOnly,
    [string]$Mp4Muxer = '',
    [string]$Python = '',
    [string]$CacheDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\deploy-common.ps1')
$toolsDirectory = Join-Path $PSScriptRoot '.tools'
$configPath = Join-Path $toolsDirectory 'tools.json'
if (-not $CacheDirectory) { $CacheDirectory = Join-Path $toolsDirectory 'downloads' }
$assets = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'scripts\deploy-assets.json') -Raw | ConvertFrom-Json
try {
    if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'This deployment package requires Windows x64 and 64-bit PowerShell.'
    }
    $existing = $null
    if (Test-Path -LiteralPath $configPath) { $existing = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    $enableTrueHDR = ($Mode -eq 'TrueHDR')
    if ($Mode -eq 'Auto') {
        $enableTrueHDR = @((Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' })).Count -gt 0
    }
    Write-Host ('SDR2DV deployment: {0}; TrueHDR: {1}' -f $Mode, $enableTrueHDR)
    $resolved = [ordered]@{}
    foreach ($kind in @('ffmpeg', 'ffprobe', 'dovi_tool', 'mp4muxer')) {
        $candidates = @()
        if ($kind -eq 'mp4muxer' -and $Mp4Muxer) { $candidates += $Mp4Muxer }
        if ($existing -and $existing.tools.$kind) {
            $configured = [string]$existing.tools.$kind
            if (-not $Portable -or $configured.StartsWith($toolsDirectory + '\', [StringComparison]::OrdinalIgnoreCase)) { $candidates += $configured }
        }
        if ($kind -eq 'mp4muxer') { $candidates += (Join-Path $toolsDirectory 'mp4muxer\mp4muxer.exe') }
        if ($kind -eq 'ffprobe' -and $resolved.ffmpeg) { $candidates += (Join-Path ([IO.Path]::GetDirectoryName($resolved.ffmpeg)) 'ffprobe.exe') }
        if (-not $Portable) {
            $command = Get-Command ($kind + '.exe') -ErrorAction SilentlyContinue
            if ($command -and $command.Source) { $candidates += $command.Source }
            if ($kind -eq 'dovi_tool') { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\dovi_tool\dovi_tool.exe') }
            if ($kind -eq 'mp4muxer') { $candidates += (Join-Path $env:USERPROFILE 'Desktop\1\dubi\mp4muxer.exe') }
        }
        $path = Find-DeployTool -Kind $kind -Candidates $candidates
        if (-not $path) {
            if ($CheckOnly) { throw "Missing or incompatible $kind. Run Deploy.ps1 without -CheckOnly." }
            if ($kind -eq 'ffprobe') { throw 'FFmpeg installation did not provide a compatible ffprobe.' }
            $path = Install-DeployAsset -Kind $kind -Asset $assets.$kind -ToolsDirectory $toolsDirectory -CacheDirectory $CacheDirectory
        }
        $resolved[$kind] = $path
        Write-Host ('  {0}: {1}' -f $kind, $path)
    }
    if ($enableTrueHDR) {
        $venvPython = Join-Path $PSScriptRoot 'SDR2DV\.venv-truehdr\Scripts\python.exe'
        if ($CheckOnly) {
            & (Join-Path $PSScriptRoot 'SDR2DV\Setup-TrueHDR.ps1') -CheckOnly
        }
        else {
            $pythonPath = Find-DeployPython -Candidates @($venvPython, $Python)
            if (-not $pythonPath -and -not $Portable) {
                $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
                if ($launcher) {
                    $found = Invoke-DeployProbe $launcher.Source @('-3.11', '-c', 'import sys; print(sys.executable)')
                    if ($found.Code -eq 0) { $pythonPath = Find-DeployPython -Candidates @($found.Text.Trim()) }
                }
                if (-not $pythonPath) {
                    $command = Get-Command python.exe -ErrorAction SilentlyContinue
                    if ($command) { $pythonPath = Find-DeployPython -Candidates @($command.Source) }
                }
            }
            if (-not $pythonPath) {
                $uv = Install-DeployAsset -Kind uv -Asset $assets.uv -ToolsDirectory $toolsDirectory -CacheDirectory $CacheDirectory
                $pythonDir = Join-Path $toolsDirectory 'python'
                & $uv python install 3.11 --install-dir $pythonDir --no-bin --no-registry --no-config --system-certs --cache-dir (Join-Path $toolsDirectory 'uv-cache')
                if ($LASTEXITCODE -ne 0) { throw 'Managed Python installation failed.' }
                $pythonPath = Find-DeployPython -Candidates @(Get-ChildItem -LiteralPath $pythonDir -Recurse -File -Filter python.exe | ForEach-Object FullName)
                if (-not $pythonPath) { throw 'No compatible managed Python 3.11 executable found.' }
            }
            & (Join-Path $PSScriptRoot 'SDR2DV\Setup-TrueHDR.ps1') -Python $pythonPath
        }
        if ($LASTEXITCODE -ne 0) { throw 'TrueHDR setup or inference check failed.' }
    }
    if (-not $CheckOnly) {
        New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
        $config = [ordered]@{ schema = 1; truehdr_ready = $enableTrueHDR; tools = $resolved }
        [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))
    }
    Write-Host 'Ready. Drag a video onto an SDR2DV batch entrypoint to convert.'
    if (-not $enableTrueHDR) { Write-Host 'Standard mode ready; TrueHDR was not configured. Use -Mode TrueHDR to configure it.' }
}
catch { Write-Host ('Deployment failed: {0}' -f $_.Exception.Message) -ForegroundColor Red; exit 1 }
