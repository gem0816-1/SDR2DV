#Requires -Version 5.1
# Explicit recovery of an interrupted multi-file delivery; never run silently.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Directory)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'reliability.ps1')
$Directory=[IO.Path]::GetFullPath($Directory).TrimEnd('\')
Assert-NoReparsePath $Directory
if ([IO.Path]::GetFileName($Directory) -notmatch '^\.sdr2dv-txn-[a-f0-9]{32}$') { throw '不是 SDR2DV 交付暂存目录。' }
$parent=[IO.Path]::GetDirectoryName($Directory)
$data=Get-Content -LiteralPath (Join-Path $Directory 'delivery.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($data.schema -ne 1 -or $data.state -in @('committed','rolled_back')) { throw '日志版本不支持或交付已经完成；未更改文件。' }
$entries=@()
foreach ($e in @($data.entries)) {
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($e.path)) -ne $parent -or
        $e.staged -notmatch '^\d+\.staged$' -or $e.backup -notmatch '^\d+\.backup$' -or
        ($e.original_sha256 -and $e.original_sha256 -notmatch '^[A-Fa-f0-9]{64}$') -or
        ($e.new_sha256 -and $e.new_sha256 -notmatch '^[A-Fa-f0-9]{64}$')) { throw '恢复日志路径或 SHA256 非法。' }
    $entries+=@{path=$e.path;staged=$e.staged;backup=$e.backup;original_sha256=$e.original_sha256;new_sha256=$e.new_sha256}
}
$tx=@{Directory=$Directory;Locks=(Enter-OutputLocks @($entries | ForEach-Object {$_.path}));
    RecoveryRequired=$true;Journal=@{schema=1;state=$data.state;entries=$entries}}
try { Restore-DeliveryEntries $tx;Write-Host '已恢复交付前的文件集合。' }
finally { Close-OutputTransaction $tx }
