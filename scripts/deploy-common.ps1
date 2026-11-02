#Requires -Version 5.1

function Invoke-DeployProbe {
    param([string]$Exe, [string[]]$Arguments)
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Exe @Arguments 2>&1
        return @{ Code = $LASTEXITCODE; Text = ($output -join "`n") }
    }
    catch { return @{ Code = -1; Text = $_.Exception.Message } }
    finally { $ErrorActionPreference = $savedPreference }
}

function Test-DeployTool {
    param([string]$Path, [string]$Kind)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    switch ($Kind) {
        'ffmpeg' {
            $version = Invoke-DeployProbe $Path @('-version')
            if ($version.Code -ne 0 -or $version.Text -notmatch 'ffmpeg version (\d+\.\d+)') { return $false }
            if ([version]$Matches[1] -lt [version]'7.1') { return $false }
            $filters = Invoke-DeployProbe $Path @('-hide_banner', '-filters')
            $encoders = Invoke-DeployProbe $Path @('-hide_banner', '-encoders')
            return ($filters.Code -eq 0 -and $filters.Text -match '\bzscale\b' -and
                $encoders.Code -eq 0 -and $encoders.Text -match '\blibx265\b' -and $encoders.Text -match '\balac\b')
        }
        'ffprobe' {
            $version = Invoke-DeployProbe $Path @('-version')
            return ($version.Code -eq 0 -and $version.Text -match 'ffprobe version (\d+\.\d+)' -and
                [version]$Matches[1] -ge [version]'7.1')
        }
        'dovi_tool' {
            $version = Invoke-DeployProbe $Path @('-V')
            return ($version.Code -eq 0 -and $version.Text -match 'dovi_tool (\d+\.\d+\.\d+)' -and
                [version]$Matches[1] -ge [version]'2.3.4')
        }
        'mp4muxer' {
            $help = Invoke-DeployProbe $Path @('--help')
            # Upstream prints its valid help text with exit code 1.
            return ($help.Code -in @(0, 1) -and $help.Text -match '\-\-dv-profile' -and
                $help.Text -match '\-\-dv-bl-compatible-id' -and $help.Text -match '\-\-hvc1flag')
        }
    }
    return $false
}

function Find-DeployTool {
    param([string]$Kind, [string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if (Test-DeployTool -Path $candidate -Kind $Kind) { return [IO.Path]::GetFullPath($candidate) }
    }
    return ''
}

function Get-DeployDownload {
    param([object]$Asset, [string]$CacheDirectory)
    $CacheDirectory = [IO.Path]::GetFullPath($CacheDirectory)
    if ($CacheDirectory.Length -gt [IO.Path]::GetPathRoot($CacheDirectory).Length) { $CacheDirectory = $CacheDirectory.TrimEnd('\') }
    if ([uri]$Asset.url -and ([uri]$Asset.url).Scheme -ne 'https') { throw 'Download URL must use HTTPS.' }
    if ([string]::IsNullOrWhiteSpace($Asset.file) -or $Asset.file -in @('.', '..') -or
        $Asset.file -ne [IO.Path]::GetFileName($Asset.file) -or $Asset.sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'Invalid download manifest.'
    }
    New-Item -ItemType Directory -Path $CacheDirectory -Force | Out-Null
    $destination = Join-Path $CacheDirectory $Asset.file
    if (Test-Path -LiteralPath $destination) {
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $Asset.sha256) {
            throw "Cached download SHA256 mismatch: $destination. Remove that cached archive and retry."
        }
        Write-Host ('Reuse verified download: {0}' -f $Asset.file)
        return $destination
    }
    $partial = $destination + '.' + [guid]::NewGuid().ToString('N') + '.partial'
    $savedProgress = $ProgressPreference
    try {
        $ProgressPreference = 'SilentlyContinue'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Write-Host ('Download: {0} (attempt {1}/3)' -f $Asset.file, $attempt)
                $request = @{ Uri = $Asset.url; OutFile = $partial; UseBasicParsing = $true;
                    TimeoutSec = 600; UserAgent = 'SDR2DV-deploy/2.0.0'; ErrorAction = 'Stop' }
                if ($env:HTTPS_PROXY) { $request.Proxy = $env:HTTPS_PROXY }
                Invoke-WebRequest @request
                break
            }
            catch {
                if ($attempt -eq 3) { throw }
                Start-Sleep -Seconds $attempt
            }
        }
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Asset.sha256) {
            throw "Downloaded archive SHA256 mismatch: $($Asset.file)"
        }
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($partial)) -ne $CacheDirectory -or
            [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($destination)) -ne $CacheDirectory) { throw 'Unsafe download publication path.' }
        Move-Item -LiteralPath $partial -Destination $destination
        return $destination
    }
    finally {
        $ProgressPreference = $savedProgress
        # Only the exact randomly named partial created by this invocation.
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
}

function Expand-DeployZip {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    if (Test-Path -LiteralPath $Destination) { throw "Extraction target already exists: $Destination" }
    New-Item -ItemType Directory -Path $Destination | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'ZIP entry escapes extraction directory.' }
            if (-not $entry.Name) { New-Item -ItemType Directory -Path $target -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $false)
        }
    }
    finally { $zip.Dispose() }
}

function Install-DeployAsset {
    param([string]$Kind, [object]$Asset, [string]$ToolsDirectory, [string]$CacheDirectory)
    $archive = Get-DeployDownload -Asset $Asset -CacheDirectory $CacheDirectory
    $packages = Join-Path $ToolsDirectory 'packages'
    New-Item -ItemType Directory -Path $packages -Force | Out-Null
    $target = Join-Path $packages ($Kind + '_' + [guid]::NewGuid().ToString('N').Substring(0,8))
    Expand-DeployZip -Archive $archive -Destination $target
    $executable = @((Get-ChildItem -LiteralPath $target -Recurse -File -Filter ($Kind + '.exe')))
    if ($executable.Count -ne 1) { throw "Expected exactly one $Kind.exe in archive." }
    if ($Kind -ne 'uv' -and -not (Test-DeployTool -Path $executable[0].FullName -Kind $Kind)) {
        throw "Downloaded $Kind failed its capability check."
    }
    return $executable[0].FullName
}

function Find-DeployPython {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $check = Invoke-DeployProbe $candidate @('-B', '-c', 'import sys; print(sys.executable); sys.exit(0 if sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32 else 1)')
        if ($check.Code -eq 0) { return [IO.Path]::GetFullPath($candidate) }
    }
    return ''
}
