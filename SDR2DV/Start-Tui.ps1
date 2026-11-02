#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Position=0)][string]$InputPath='', [switch]$NonInteractive,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference='Stop'
try {
    if ($NonInteractive) {
        $shell=(Get-Process -Id $PID).Path
        $forward=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'SDR2DV.ps1'),'-HdrMode','TrueHDR','-Bilibili')
        if ($InputPath) { $forward+=@('-InputPath',$InputPath) }
        & $shell @forward @Arguments
        exit $LASTEXITCODE
    }
    $python=Join-Path $PSScriptRoot '.venv-tui\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        & (Join-Path (Split-Path $PSScriptRoot -Parent) 'Deploy.ps1')
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) { throw 'Deployment failed.' }
    }
    $forward=@('-X','utf8','-B',(Join-Path $PSScriptRoot 'tui.py'))
    if ($InputPath) { $forward+=$InputPath }
    & $python @forward @Arguments
    exit $LASTEXITCODE
}
catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
