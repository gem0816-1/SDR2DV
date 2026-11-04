#Requires -Version 5.1
[CmdletBinding()]
param([string]$Python = '', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $PSScriptRoot '.venv-truehdr\Scripts\python.exe'
try {
    foreach ($module in @('encoding.ps1', 'brightness_mapping.py', 'hdr_math.py', 'pq_to_hlg.py', 'truehdr_backend.py', 'truehdr_stream.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $module) -PathType Leaf)) {
            throw "Incomplete TrueHDR package: missing $module. Extract the complete release ZIP again."
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($CheckOnly) { throw 'TrueHDR local environment is missing. Run Setup-TrueHDR.ps1 first.' }
        if ($Python) { & $Python -m venv (Join-Path $PSScriptRoot '.venv-truehdr') }
        else { & py -3.11 -m venv (Join-Path $PSScriptRoot '.venv-truehdr') }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.11 virtual environment.' }
    }
    $packageProbe = "import importlib.metadata as m,sys; from pathlib import Path; required=dict(x.strip().split('==') for x in Path(sys.argv[1]).read_text().splitlines() if x.strip() and not x.startswith('#')); required.update({'nvidia-vfx':'0.2.0.0','numpy':'2.4.6','cupy-cuda12x':'14.2.0'}); sys.exit(0 if sys.version_info[:2] == (3, 11) and all(m.version(k)==v for k,v in required.items()) else 1)"
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & $venvPython -B -c $packageProbe (Join-Path $PSScriptRoot 'truehdr-constraints.txt') 2>&1
        $packagesReady = ($LASTEXITCODE -eq 0)
    }
    finally { $ErrorActionPreference = $savedPreference }
    if (-not $packagesReady) {
        if ($CheckOnly) { throw 'Pinned TrueHDR dependencies are missing or incompatible. Run Setup-TrueHDR.ps1 without -CheckOnly.' }
        $cache = Join-Path (Split-Path $PSScriptRoot -Parent) '.tools\pip-cache'
        & $venvPython -m pip install nvidia-vfx==0.2.0.0 --index-url https://pypi.nvidia.com --cache-dir $cache
        if ($LASTEXITCODE -ne 0) { throw 'Official NVIDIA VFX package installation failed.' }
        & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'truehdr-requirements.txt') --cache-dir $cache
        if ($LASTEXITCODE -ne 0) { throw 'CUDA array runtime installation failed.' }
    }
    else { Write-Host 'Reuse pinned TrueHDR environment; no installation needed.' }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed.' }
    & $venvPython -B (Join-Path $PSScriptRoot 'brightness_mapping.py') --describe
    if ($LASTEXITCODE -ne 0) { throw 'Reference brightness/skin correction module check failed.' }
    . (Join-Path $PSScriptRoot 'reliability.ps1')
    $gpuLock=Enter-GpuTaskLock
    try {
        & $venvPython -B (Join-Path $PSScriptRoot 'truehdr_backend.py') --check
        if ($LASTEXITCODE -ne 0) { throw 'TrueHDR inference check failed. See the original SDK error above.' }
    } finally {$gpuLock.ReleaseMutex();$gpuLock.Dispose()}
}
catch { Write-Error $_; exit 1 }
