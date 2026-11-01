#Requires -Version 5.1

function Get-NvencArguments {
    param(
        [ValidateSet('p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7')][string]$NvencPreset = 'p6',
        [int]$Bitrate = 0,
        [int]$MaxBitrate = 0,
        [ValidateRange(0, 51)][int]$Quality = 18,
        [double]$FrameRate = 60,
        [switch]$Lossless
    )
    $options = @('-c:v', 'hevc_nvenc', '-gpu', '0', '-profile:v', 'main10',
        '-pix_fmt', 'p010le', '-preset', $NvencPreset, '-g', [string][int][math]::Max(1, [math]::Round(2 * $FrameRate)), '-aud', '1')
    if ($Lossless) {
        return $options + @('-tune', 'lossless', '-rc', 'constqp', '-qp', '0', '-bf', '0')
    }
    $options += @('-tune', 'hq', '-rc', 'vbr', '-multipass', 'fullres',
        '-rc-lookahead', '20', '-spatial_aq', '1', '-temporal_aq', '1', '-aq-strength', '8',
        '-bf', '3', '-b_ref_mode', 'middle')
    if ($Bitrate -gt 0) {
        if ($MaxBitrate -lt $Bitrate) { throw 'NVENC maximum bitrate must be at least the target bitrate.' }
        $options += @('-b:v', ('{0}k' -f $Bitrate), '-maxrate', ('{0}k' -f $MaxBitrate),
            '-bufsize', ('{0}k' -f (2 * $MaxBitrate)))
    }
    else { $options += @('-cq', [string]$Quality, '-b:v', '0') }
    return $options
}

function Test-NvencSupport {
    param([string]$Ffmpeg, [switch]$IncludeLossless)
    $modes = @($false)
    if ($IncludeLossless) { $modes += $true }
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        foreach ($losslessMode in $modes) {
            $options = @(Get-NvencArguments -Bitrate 28000 -MaxBitrate 30000 -Lossless:$losslessMode)
            $probeArgs = @('-hide_banner', '-v', 'error', '-nostdin', '-f', 'lavfi',
                '-i', 'color=black:size=256x144:rate=60', '-frames:v', '2', '-an') + $options + @('-f', 'null', '-')
            $probeOutput = & $Ffmpeg @probeArgs 2>&1
            if ($LASTEXITCODE -ne 0) {
                return @{ Ready = $false; Error = ($probeOutput -join "`n") }
            }
        }
        return @{ Ready = $true; Error = '' }
    }
    catch { return @{ Ready = $false; Error = $_.Exception.Message } }
    finally { $ErrorActionPreference = $savedPreference }
}
