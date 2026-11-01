#Requires -Version 5.1
[CmdletBinding()]
param([string]$Python = '', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $PSScriptRoot '.venv-truehdr\Scripts\python.exe'
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($CheckOnly) { throw 'TrueHDR local environment is missing. Run Setup-TrueHDR.ps1 first.' }
        if ($Python) { & $Python -m venv (Join-Path $PSScriptRoot '.venv-truehdr') }
        else { & py -3.11 -m venv (Join-Path $PSScriptRoot '.venv-truehdr') }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.11 virtual environment.' }
    }
    if (-not $CheckOnly) {
        & $venvPython -m pip install nvidia-vfx==0.2.0.0 --index-url https://pypi.nvidia.com
        if ($LASTEXITCODE -ne 0) { throw 'Official NVIDIA VFX package installation failed.' }
        & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'truehdr-requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'CUDA array runtime installation failed.' }
        & $venvPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed.' }
    }
    & $venvPython (Join-Path $PSScriptRoot 'truehdr_backend.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'TrueHDR inference check failed. See the original SDK error above.' }
}
catch { Write-Error $_; exit 1 }
