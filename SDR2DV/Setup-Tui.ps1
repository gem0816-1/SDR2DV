#Requires -Version 5.1
[CmdletBinding()]
param([string]$Python='', [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$localPython=Join-Path $PSScriptRoot '.venv-tui\Scripts\python.exe'
try {
    if (-not (Test-Path -LiteralPath $localPython -PathType Leaf)) {
        if ($CheckOnly) { throw 'Missing project TUI/core environment. Run deploy.bat.' }
        if (-not $Python) { throw 'Use deploy.bat to provision managed Python, or specify -Python.' }
        & $Python -X utf8 -m venv (Join-Path $PSScriptRoot '.venv-tui')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create project-local TUI environment.' }
    }
    $probe="import importlib.metadata as m,sys; v={'rich':'15.0.0','questionary':'2.1.1','numpy':'2.4.6'}; sys.exit(0 if all(m.version(k)==n for k,n in v.items()) else 1)"
    $old=$ErrorActionPreference
    try { $ErrorActionPreference='Continue'; $null=& $localPython -X utf8 -B -c $probe 2>&1; $ready=($LASTEXITCODE -eq 0) }
    finally { $ErrorActionPreference=$old }
    if (-not $ready) {
        if ($CheckOnly) { throw 'Pinned TUI/core dependencies missing. Run deploy.bat.' }
        & $localPython -X utf8 -m pip install -r (Join-Path $PSScriptRoot 'tui-requirements.txt') --cache-dir (Join-Path (Split-Path $PSScriptRoot -Parent) '.tools\pip-cache')
        if ($LASTEXITCODE -ne 0) { throw 'TUI dependency installation failed.' }
    } else { Write-Host 'Reuse pinned TUI/core environment; no installation needed.' }
    & $localPython -X utf8 -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'TUI dependency check failed.' }
}
catch { Write-Error $_; exit 1 }
