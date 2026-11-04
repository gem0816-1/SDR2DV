# Load production function definitions without executing the CLI or its exit.
param([string]$Root)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
. (Join-Path $Root 'encoding.ps1')
. (Join-Path $Root 'core.ps1')
if (Test-Path (Join-Path $Root 'reliability.ps1')) { . (Join-Path $Root 'reliability.ps1') }
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Root 'SDR2DV.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($definition in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($definition.Extent.Text.Replace('$PSScriptRoot',("'"+$Root.Replace("'","''")+"'"))))
}
