# Fault injection only: no GPU or genuine media validation is claimed here.
param([string]$Root,[string]$Directory,[string]$Failure='mkv',[switch]$ExistingHDR10)
. (Join-Path $PSScriptRoot 'Load-Core.ps1') $Root
$PSScriptRoot=$Root
$script:Failure=$Failure
$script:WorkRoot=Join-Path $Directory 'work';$null=New-Item -ItemType Directory $script:WorkRoot -Force
$OutputDir=Join-Path $Directory 'output';$null=New-Item -ItemType Directory $OutputDir -Force
$source=Join-Path $Directory 'source.mov';[IO.File]::WriteAllText($source,'untouched source')
$Format='both';$Profile='8.4';$HdrMode='Original';$BrightnessMapping='Neutral';$BrightnessStrength=1.0
$Mp4AudioCodec='auto';$Force=$true;$Preset='ultrafast';$Encoder='x265';$WhitePoint='203';$BitrateMode='Quality'
$SkinToneCorrection=0.0
$script:Defaults=@{platform_target_kbps=28000;platform_max_kbps=30000};$script:BoundOptions=@{};$script:Results=@()
$script:DiskWatchPaths=@();$KeepHDR10=($Failure -eq 'rpu')
if ($KeepHDR10) {$HdrMode='TrueHDR';$TrueHDRLuminance=1000;$TrueHDRContrast=100;$TrueHDRSaturation=100;$TrueHDRMiddleGray=50}
function Write-Log {param($Message,$Level)}
function Write-TaskEvent {param($Type,$Data)}
function Get-IncomingVideoInfo {param($Path) return @{Path=$Path;Width=320;Height=180;Fps=30;AvgFps=30;FpsText='30';Duration=1;AudioStreams=@();SubtitleStreams=@();AttachmentStreams=@();InTransfer='bt709';InPrimaries='bt709';InMatrix='bt709';InRange='limited';SourceStream=@{}}}
function Get-CoreVideoRate {param($Path,$WorkDir,[switch]$Plan,[switch]$Packets) return @{timing=@{frames=30};video_bitrate=@{bps=1000000;method='test'};rate_plan=@{target_kbps=0;max_kbps=0}}}
function Invoke-MediaWithProgress {
    param($Exe,$Arguments,$WorkDir,$ArgumentPrefix,$LogBaseName,$What,$Label,$TotalSeconds,$ProcessWorkingDirectory)
    if ($LogBaseName -eq 'truehdr') {
        [IO.File]::WriteAllText((Join-Path $WorkDir 'truehdr-report.json'),'{"input_frames":30}')
        [IO.File]::WriteAllText((Join-Path $WorkDir 'truehdr_hlg.mkv'),'cache')
        [IO.File]::WriteAllText((Join-Path $WorkDir 'HDR10_video.mp4'),'pq')
    } else { [IO.File]::WriteAllText($Arguments[-1],'new encoded media') }
}
function Invoke-NativeQuiet {param($Exe,$Arguments,$What) if ($Arguments -contains '-o') {[IO.File]::WriteAllText($Arguments[-1],'injected')}}
function Get-HevcFrameCount {param($Path) return 30}
function Get-FinalHdrAnalysis {param($Path,$Info,$WorkDir) return @{frames=30;max_cll_nits=1000;max_fall_nits=70;shots=@()}}
function New-DvRpu {param($DoviExe,$FrameCount,$Stats,$WorkDir) if ($script:Failure -eq 'rpu') {throw 'INJECTED RPU failure'}; return @{RpuPath='test';MaxCll=1000;MaxFall=70}}
function New-Mp4Output {param($Info,$HevcPath,$OutputPath,$WorkDir,$AudioCodec,$Bitrate) [IO.File]::WriteAllText($OutputPath,'new mp4');return 0}
function New-MkvOutput {param($Info,$HevcPath,$OutputPath,$WorkDir) throw 'INJECTED MKV failure'}
function Test-DolbyVisionInMp4 {param($Path) return $true}
function Test-RpuInContainer {param($Path,$DoviExe,$ExpectedFrames,$WorkDir) return $true}
function Test-CoreAudio {param($Info,$Path,$WorkDir) return @{verification='test_double'}}
function Test-CoreVideoOutput {param($Info,$Record,$ExpectedFrames)}
function Get-MediaInfo {param($Path) return @{streams=@(@{codec_type='video';codec_name='hevc';profile='Main 10';color_transfer='smpte2084';color_primaries='bt2020';color_space='bt2020nc'})}}
function Save-CoreFailureDiagnostics {param($WorkDir,$Failure) return ''}
$suffix='_DV84';if ($KeepHDR10) {$suffix='_TrueHDR_DV84'}
$mp4=Join-Path $OutputDir ('source'+$suffix+'.mp4');[IO.File]::WriteAllText($mp4,'OLD finished mp4')
$mkv=Join-Path $OutputDir ('source'+$suffix+'.mkv');[IO.File]::WriteAllText($mkv,'OLD finished mkv')
$before=(Get-FileHash -LiteralPath $mp4).Hash
$hdr10=Join-Path $OutputDir 'source_TrueHDR_HDR10.mp4';$hdr10Before=$null
if ($ExistingHDR10) {[IO.File]::WriteAllText($hdr10,'OLD finished HDR10');$hdr10Before=(Get-FileHash -LiteralPath $hdr10).Hash}
try { $null=Convert-SingleFile $source; throw 'Expected failure missing' } catch { $errorMessage=$_.Exception.Message }
$hdr10Published=Test-Path -LiteralPath $hdr10
if ($hdr10Before) {$hdr10Published=((Get-FileHash -LiteralPath $hdr10).Hash -ne $hdr10Before)}
@{error=$errorMessage;old_mp4_unchanged=((Get-FileHash -LiteralPath $mp4).Hash -eq $before);hdr10_published=$hdr10Published;source=[IO.File]::ReadAllText($source)} | ConvertTo-Json -Compress
