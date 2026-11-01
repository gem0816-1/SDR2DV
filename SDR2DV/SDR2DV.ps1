#Requires -Version 5.1
<#
.SYNOPSIS
    SDR 视频一键转杜比视界（PQ / Profile 8.1，或 HLG / Profile 8.4）

.DESCRIPTION
    流水线：
      1) SDR 标准映射或可选高光扩展，保留中低亮度基准
      2) SDR → PQ 或 HLG（BT.2020）10bit，NVENC 硬件或 x265 软件编码
      3) 按 PQ 域采样生成 RPU；CM v4.0 限制平均值下限，L6 MaxFALL 仍为近似
      4) 把 RPU 注入 HEVC
      5) 封装：MP4 用 Dolby mp4muxer（写入 DOVI 配置记录）；MKV 用 ffmpeg（音轨/字幕全保留）
      6) 校验输出

    说明：SDR 转 DV 不存在严格意义的“无损”。标准映射将 SDR 白点映射到 203 nit。
    -PeakNits 1000 另含高光扩展；P8.4 的亮度从编码后基础层回读测量。
    高光曲线不能恢复 SDR 已丢失的细节，编码重建也可能出现亮度超调。

.PARAMETER InputPath
    输入文件或文件夹。文件夹模式会批量处理其中的视频文件。

.PARAMETER Format
    输出容器：mp4（默认，电视/Apple TV 友好）、mkv（保留全部音轨字幕）、both。

.PARAMETER WhitePoint
    白点映射：203（方案二，默认，标准上变换）或 100（方案一，画面与 SDR 一致）。

.PARAMETER Profile
    8.1：PQ / HDR10 兼容基础层；8.4：HLG 兼容基础层。

.PARAMETER Bilibili
    B站投稿预设：Profile 8.4、HLG、目标 28000 kbps、VBV 30000 kbps，默认高光峰值 1000 nit。

.PARAMETER PeakNits
    1000：扩展最亮一段，中低亮度保持 WhitePoint 基准；0：关闭高光扩展。

.PARAMETER HdrMode
    Original 保留原转换；TrueHDR 使用官方 NVIDIA SDK，固定输出 P8.4，不叠加原高光曲线。

.PARAMETER BrightnessMapping
    TrueHDR 亮度曲线：Reference 默认匹配参考片的亮度风格；Neutral 保留旧的 SDK 直接映射。

.PARAMETER BrightnessStrength
    Reference 曲线强度，0..1，默认 1；0 相当于 Neutral。

.PARAMETER SkinToneCorrection
    Reference 模式暖色肤色去黄强度，0..1，默认 0.5；0 保持原色。使用柔和色相选择而非人脸检测。

.PARAMETER KeepHDR10
    TrueHDR 模式额外保留独立 HDR10/PQ MP4，音轨遵循 Mp4AudioCodec 策略。

.PARAMETER VideoBitrate
    视频目标码率（kbps），0 使用 CRF。正数使用 ABR；实际平均值依内容而变。

.PARAMETER MaxVideoBitrate
    VBV 码率上限（kbps），与 -VideoBitrate 同用；不是逐包瞬时码率保证。

.PARAMETER Preset
    x265 预设，默认 slow（质量优先）。嫌慢可改 medium / fast。

.PARAMETER Encoder
    Auto 默认在 Profile 8.4 且 NVENC 实测可用时启用硬件编码；否则使用 x265。
    NVENC 强制使用 NVIDIA 10bit HEVC，初始化失败会报错；x265 显式使用软件编码。

.PARAMETER NvencPreset
    NVENC 预设 p1..p7，默认 p6；仅 NVENC 使用。

.PARAMETER NvencCq
    NVENC 无目标码率时的 CQ，默认 18；和 x265 CRF 不是同一质量尺度。

.PARAMETER Crf
    x265 质量，默认 17（越低越好越大）。NVENC 请用 NvencCq。

.PARAMETER Lossless
    所选编码器的无损模式（体积巨大，仅测试/收藏用）。

.PARAMETER Mp4AudioCodec
    默认 auto：B站将 16/24bit 整数 PCM 无损改封为 ALAC，其余原样复制。
    copy 强制复制；alac 显式无损编码；eac3/ac3 仅显式指定时允许有损转码。

.PARAMETER AudioBitrate
    显式选择 eac3/ac3 时使用的音频转码码率（kbps），默认 640；copy 模式不使用。

.PARAMETER StatsFps
    亮度采样帧率，默认 4（每秒取 4 帧）。越高峰值越准，分析越慢。

.PARAMETER OutputDir
    输出目录，默认与输入同目录。

.PARAMETER KeepTemp
    显式保留本次中间文件、缓存和日志，便于排查；默认成功或失败均清理。

.PARAMETER Force
    输出已存在时覆盖。

.EXAMPLE
    .\SDR2DV.ps1 -InputPath "D:\Movies\movie.mkv"

.EXAMPLE
    .\SDR2DV.ps1 -InputPath "D:\Movies" -Format both -Preset medium

.EXAMPLE
    .\SDR2DV.ps1 -InputPath "D:\movie.mkv" -WhitePoint 100 -Crf 16

.EXAMPLE
    .\SDR2DV.ps1 -InputPath "D:\movie.mov" -HdrMode TrueHDR -Bilibili -Preset medium
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$InputPath = '',

    [ValidateSet('mp4', 'mkv', 'both')]
    [string]$Format = 'mp4',

    [ValidateSet('8.1', '8.4')]
    [string]$Profile = '8.1',

    [switch]$Bilibili,

    [ValidateSet('Original', 'TrueHDR')]
    [string]$HdrMode = 'Original',

    [ValidateRange(400, 2000)]
    [int]$TrueHDRLuminance = 1000,

    [ValidateRange(0, 200)]
    [int]$TrueHDRContrast = 100,

    [ValidateRange(0, 200)]
    [int]$TrueHDRSaturation = 100,

    [ValidateRange(10, 100)]
    [int]$TrueHDRMiddleGray = 50,

    [switch]$TrueHDRDebandingOff,

    [ValidateSet('Reference', 'Neutral')]
    [string]$BrightnessMapping = 'Reference',

    [ValidateRange(0.0, 1.0)]
    [double]$BrightnessStrength = 1.0,

    [ValidateRange(0.0, 1.0)]
    [double]$SkinToneCorrection = 0.5,

    [switch]$KeepHDR10,

    [string]$TrueHDRPython = '',

    [string]$TrueHDRCache = '',

    # kbps；0 使用 CRF，正数使用 ABR + VBV。
    [ValidateRange(0, 200000)]
    [int]$VideoBitrate = 0,

    [ValidateRange(0, 200000)]
    [int]$MaxVideoBitrate = 0,

    [ValidateSet('100', '203')]
    [string]$WhitePoint = '203',

    # 0：标准映射；1000：只扩展最高亮度一段，中间调仍按 WhitePoint。
    [ValidateSet(0, 1000)]
    [int]$PeakNits = 0,

    [ValidateSet('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow')]
    [string]$Preset = 'slow',

    [ValidateSet('Auto', 'NVENC', 'x265')]
    [string]$Encoder = 'Auto',

    [ValidateSet('p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7')]
    [string]$NvencPreset = 'p6',

    [ValidateRange(0, 51)]
    [int]$NvencCq = 18,

    [ValidateRange(0, 51)]
    [int]$Crf = 17,

    [switch]$Lossless,

    [ValidateSet('auto', 'copy', 'alac', 'eac3', 'ac3')]
    [string]$Mp4AudioCodec = 'auto',

    [ValidateRange(96, 1024)]
    [int]$AudioBitrate = 640,

    [ValidateRange(1, 30)]
    [int]$StatsFps = 4,

    [string]$OutputDir = '',

    [switch]$KeepTemp,

    [switch]$Force,

    [string]$DoviTool = '',

    [string]$Mp4Muxer = ''
)

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'
$script:ScriptVersion = '1.10.0'
. (Join-Path $PSScriptRoot 'encoding.ps1')

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
function Write-Log {
    param(
        [string]$Message,
        [ValidateSet('STEP', 'INFO', 'OK', 'WARN', 'ERR', 'DIM')]
        [string]$Level = 'INFO'
    )
    $prefix = '  -'
    $color = 'Gray'
    switch ($Level) {
        'STEP' { $prefix = '==>'; $color = 'Cyan' }
        'OK' { $prefix = '  +'; $color = 'Green' }
        'WARN' { $prefix = '  !'; $color = 'Yellow' }
        'ERR' { $prefix = '  x'; $color = 'Red' }
        'DIM' { $prefix = '   '; $color = 'DarkGray' }
        default { $prefix = '  -'; $color = 'Gray' }
    }
    Write-Host ("{0} {1}" -f $prefix, $Message) -ForegroundColor $color
}

function Format-Elapsed {
    param([TimeSpan]$Span)
    if ($Span.TotalHours -ge 1) {
        return ('{0:0}h {1:0}m {2:0}s' -f [math]::Floor($Span.TotalHours), $Span.Minutes, $Span.Seconds)
    }
    if ($Span.TotalMinutes -ge 1) {
        return ('{0:0}m {1:0}s' -f [math]::Floor($Span.TotalMinutes), $Span.Seconds)
    }
    return ('{0:0}s' -f [math]::Round($Span.TotalSeconds))
}

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:0.00} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:0.0} MB' -f ($Bytes / 1MB)) }
    return ('{0:0} KB' -f [math]::Max(1, [math]::Round($Bytes / 1KB)))
}

function Test-AsciiPath {
    param([string]$Path)
    if (-not $Path) { return $false }
    return ($Path -notmatch '[^\x00-\x7F]')
}

# Dolby mp4muxer 不支持非 ASCII 路径，中间目录必须选一个 ASCII 可写位置
function Get-AsciiWorkRoot {
    $candidates = @($env:TEMP, $env:PUBLIC, (Join-Path $env:SystemDrive 'ProgramData'), (Join-Path $env:SystemDrive 'Temp'))
    foreach ($base in $candidates) {
        if (-not $base) { continue }
        if (-not (Test-Path -LiteralPath $base)) { continue }
        if (-not (Test-AsciiPath $base)) { continue }
        $root = Join-Path $base 'SDR2DV'
        try {
            if (-not (Test-Path -LiteralPath $root)) { New-Item -ItemType Directory -Path $root -Force | Out-Null }
            $probe = Join-Path $root ('.write_' + [System.Guid]::NewGuid().ToString('N') + '.tmp')
            [System.IO.File]::WriteAllText($probe, 'x')
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
            return $root
        }
        catch { continue }
    }
    return (Join-Path $env:TEMP 'SDR2DV')
}

# ---------------------------------------------------------------------------
# 工具定位
# ---------------------------------------------------------------------------
function Resolve-ToolPath {
    param(
        [string]$Name,
        [string[]]$Candidates,
        [string]$Hint
    )
    foreach ($candidate in $Candidates) {
        if (-not $candidate) { continue }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    throw "找不到 $Name。$Hint"
}

# ---------------------------------------------------------------------------
# 外部程序调用
# ---------------------------------------------------------------------------
function Build-ArgumentString {
    param([string[]]$Arguments)
    $parts = @()
    foreach ($a in $Arguments) {
        if ($null -eq $a) { continue }
        $s = [string]$a
        if ($s -match '[\s"]') {
            $s = '"' + ($s -replace '"', '\"') + '"'
        }
        $parts += $s
    }
    return ($parts -join ' ')
}

function Invoke-MediaWithProgress {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$WorkDir,
        [string]$LogBaseName,
        [string]$What,
        [string]$Label = '处理',
        [double]$TotalSeconds = 0,
        [string[]]$ArgumentPrefix = @(),
        [string]$ProcessWorkingDirectory = ''
    )

    $errLog = Join-Path $WorkDir ($LogBaseName + '.log')
    $progLog = Join-Path $WorkDir ($LogBaseName + '.progress')
    if (Test-Path -LiteralPath $errLog) { Remove-Item -LiteralPath $errLog -Force }
    if (Test-Path -LiteralPath $progLog) { Remove-Item -LiteralPath $progLog -Force }

    $fullArgs = @($ArgumentPrefix) + @('-progress', $progLog, '-nostats') + @($Arguments)
    $argString = Build-ArgumentString -Arguments $fullArgs

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = $argString
    $psi.WorkingDirectory = $WorkDir
    if ($ProcessWorkingDirectory) { $psi.WorkingDirectory = $ProcessWorkingDirectory }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardError = $true
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    if (-not $proc.Start()) { throw "$What 启动失败" }
    $errTask = $proc.StandardError.ReadToEndAsync()
    try {
    $startTime = Get-Date
    $lastUpdate = $startTime
    $printed = $false

    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 500
        $now = Get-Date
        if (($now - $lastUpdate).TotalSeconds -lt 1) { continue }
        $lastUpdate = $now
        $workDrive = New-Object System.IO.DriveInfo([IO.Path]::GetPathRoot($WorkDir))
        if ($workDrive.AvailableFreeSpace -lt 1GB) { throw '临时盘可用空间低于 1 GB，已停止本次任务。' }
        if (-not (Test-Path -LiteralPath $progLog)) { continue }

        $text = ''
        try { $text = [System.IO.File]::ReadAllText($progLog) } catch { $text = '' }
        if (-not $text) { continue }

        $frame = 0
        $speed = ''
        $outSeconds = 0.0
        foreach ($line in ($text -split "`n")) {
            $t = $line.Trim()
            if ($t -match '^frame=(\d+)') { $frame = [int]$Matches[1] }
            elseif ($t -match '^speed=(.+)$') { $speed = $Matches[1].Trim() }
            elseif ($t -match '^out_time_us=(\d+)') { $outSeconds = [double]$Matches[1] / 1000000.0 }
        }

        $status = ('   {0} frame={1}' -f $Label, $frame)
        if ($TotalSeconds -gt 0 -and $outSeconds -gt 0) {
            $pct = [math]::Min(100.0, 100.0 * $outSeconds / $TotalSeconds)
            $status += ('  {0,5:0.0}%' -f $pct)
        }
        if ($speed) { $status += ('  speed={0}' -f $speed) }
        $status += ('  已用 {0}' -f (Format-Elapsed ([TimeSpan]::FromSeconds((($now - $startTime).TotalSeconds)))))
        Write-Host ("`r" + $status.PadRight(88)) -NoNewline
        $printed = $true
    }

    $proc.WaitForExit()
    $errText = ''
    try { $errText = $errTask.Result } catch { $errText = '' }
    if ($errText) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        try { [System.IO.File]::WriteAllText($errLog, $errText, $utf8NoBom) } catch { }
    }
    if ($printed) {
        $elapsed = (Get-Date) - $startTime
        Write-Host ("`r" + ('   {0} 完成，用时 {1}' -f $Label, (Format-Elapsed $elapsed)).PadRight(88))
    }

    $exitCode = -999
    try { $exitCode = $proc.ExitCode } catch { $exitCode = -999 }
    if ($exitCode -ne 0) {
        if ($errText) {
            $lines = @($errText -split "`r?`n" | Where-Object { $_ -ne '' })
            $tailCount = [math]::Min(20, $lines.Count)
            if ($tailCount -gt 0) { $lines[($lines.Count - $tailCount)..($lines.Count - 1)] | Out-Host }
        }
        throw ('{0} 失败（退出码 {1}）' -f $What, $exitCode)
    }
    }
    finally {
        if (-not $proc.HasExited) {
            # Only this invocation's process tree; includes Python's FFmpeg pipes.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $proc.Id /T /F 2>$null | Out-Null
        }
        try {
            if ($errTask.Wait(5000) -and $errTask.Result) {
                [IO.File]::WriteAllText($errLog, $errTask.Result, (New-Object Text.UTF8Encoding($false)))
            }
        } catch { }
        $proc.Dispose()
    }
}

function Invoke-NativeQuiet {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $savedPreference = $ErrorActionPreference
    try {
        # Windows PowerShell treats redirected native stderr as ErrorRecords;
        # capture the complete diagnostic before applying its actual exit code.
        $ErrorActionPreference = 'Continue'
        $output = & $Exe @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $savedPreference }
    if ($code -ne 0) {
        if ($output) { $output | Out-Host }
        throw "$What 失败（退出码 $code）"
    }
}

# ---------------------------------------------------------------------------
# 媒体探测
# ---------------------------------------------------------------------------
function Get-MediaInfo {
    param([string]$Path)
    $raw = & $script:Ffprobe -v error -print_format json -show_format -show_streams -i $Path 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        throw "ffprobe 无法读取输入文件：$Path"
    }
    return (($raw -join "`n") | ConvertFrom-Json)
}

function ConvertTo-FractionValue {
    param([string]$Text)
    if (-not $Text) { return 0.0 }
    if ($Text -match '^(\d+)/(\d+)$') {
        $num = [double]$Matches[1]
        $den = [double]$Matches[2]
        if ($den -le 0) { return 0.0 }
        return $num / $den
    }
    $value = 0.0
    if ([double]::TryParse($Text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$value)) {
        return $value
    }
    return 0.0
}

function ConvertTo-FpsArgument {
    param([string]$FpsText)
    if (-not $FpsText) { return '25' }
    if ($FpsText -match '^(\d+)/(\d+)$') {
        $den = [int]$Matches[2]
        $num = [int]$Matches[1]
        if ($den -eq 1) { return [string]$num }
        return $FpsText
    }
    return $FpsText
}

function Get-NumericValue {
    param($Value)
    if ($null -eq $Value) { return 0.0 }
    $parsed = 0.0
    $text = [string]$Value
    if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
        return $parsed
    }
    return 0.0
}

# ---------------------------------------------------------------------------
# 亮度 / PQ 计算
# ---------------------------------------------------------------------------
function Convert-PqSignalToNits {
    param([double]$Signal)
    if ($Signal -le 0.0) { return 0.0 }
    $m1 = 0.1593017578125
    $m2 = 78.84375
    $c1 = 0.8359375
    $c2 = 18.8515625
    $c3 = 18.6875
    $p = [math]::Pow($Signal, 1.0 / $m2)
    $num = $p - $c1
    if ($num -lt 0.0) { $num = 0.0 }
    $den = $c2 - $c3 * $p
    if ($den -le 0.0) { return 10000.0 }
    return 10000.0 * [math]::Pow($num / $den, 1.0 / $m1)
}

function Convert-YuvCodeToPqSignal {
    param([double]$Code)
    # 转换输出固定为 10bit limited，Y: 64..940
    $signal = ($Code - 64.0) / 876.0
    if ($signal -lt 0.0) { $signal = 0.0 }
    if ($signal -gt 1.0) { $signal = 1.0 }
    return $signal
}

# 在线性 BT.2020 RGB 域生成高光曲线。以 MaxRGB 等比缩放三通道，尽量保持色相。
# 最亮 10%（线性 MaxRGB > 0.9）平滑扩展至目标峰值；其余保持原白点映射。
function New-HighlightLut {
    param([string]$WorkDir, [int]$DiffuseWhite, [int]$Peak)
    $path = Join-Path $WorkDir 'highlight.cube'
    $size = 65
    $ratio = [double]$DiffuseWhite / $Peak
    $knee = 0.9
    # 为有损重建的高光超调保留 5% 余量；RPU 使用编码回读实测值。
    $ceiling = 0.95
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $writer = New-Object System.IO.StreamWriter($path, $false, (New-Object System.Text.UTF8Encoding($false)))
    try {
        $writer.WriteLine('TITLE "SDR highlight expansion; linear BT2020; normalized to target peak"')
        $writer.WriteLine('LUT_3D_SIZE 65')
        $writer.WriteLine('DOMAIN_MIN 0 0 0')
        $writer.WriteLine('DOMAIN_MAX 1 1 1')
        for ($b = 0; $b -lt $size; $b++) {
            for ($g = 0; $g -lt $size; $g++) {
                for ($r = 0; $r -lt $size; $r++) {
                    $maxRgb = [double][math]::Max($r, [math]::Max($g, $b)) / ($size - 1)
                    $scale = $ratio
                    if ($maxRgb -gt $knee) {
                        $t = ($maxRgb - $knee) / (1.0 - $knee)
                        $mapped = $ratio * $maxRgb + ($ceiling - $ratio) * $t * $t
                        $scale = $mapped / $maxRgb
                    }
                    $values = [object[]]@(($scale * $r / ($size - 1)), ($scale * $g / ($size - 1)), ($scale * $b / ($size - 1)))
                    $writer.WriteLine([string]::Format($culture, '{0:F9} {1:F9} {2:F9}', $values))
                }
            }
        }
    }
    finally { $writer.Dispose() }
    return $path
}

# ---------------------------------------------------------------------------
# 输入校验与参数提取
# ---------------------------------------------------------------------------
function New-ReferenceBrightnessFilter {
    param([string]$WorkDir, [ValidateSet('hlg', 'pq')][string]$Transfer,
        [ValidateSet('bt2020nc', 'gbr')][string]$InputMatrix = 'bt2020nc')
    $cubeName = 'reference_' + $Transfer + '.cube'
    $cubePath = Join-Path $WorkDir $cubeName
    if (-not (Test-Path -LiteralPath $cubePath)) {
        $mappingScript = Join-Path $PSScriptRoot 'brightness_mapping.py'
        $strength = $BrightnessStrength.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
        Invoke-NativeQuiet -Exe $script:TrueHDRPython -Arguments @('-B', $mappingScript,
            '--write-cube', $cubePath, '--transfer', $Transfer, '--strength', $strength,
            '--skin-strength', $SkinToneCorrection.ToString('R', [Globalization.CultureInfo]::InvariantCulture)) -What '生成参考亮度 LUT'
    }
    $trc = 'arib-std-b67'
    if ($Transfer -eq 'pq') { $trc = 'smpte2084' }
    $inputRange = 'limited'
    if ($InputMatrix -eq 'gbr') { $inputRange = 'full' }
    return ('zscale=tin={0}:pin=bt2020:min={1}:rin={2}:t={0}:p=bt2020:m=gbr:r=full:agamma=0,format=gbrpf32le,lut3d=file={3}:interp=tetrahedral,zscale=tin={0}:pin=bt2020:min=gbr:rin=full:t={0}:p=bt2020:m=bt2020nc:r=limited:agamma=0:dither=error_diffusion,format=yuv420p10le' -f $trc, $InputMatrix, $inputRange, $cubeName)
}

function Get-IncomingVideoInfo {
    param([string]$Path)

    $info = Get-MediaInfo -Path $Path
    $video = @($info.streams | Where-Object { $_.codec_type -eq 'video' })
    if ($video.Count -eq 0) { throw "文件里没有视频流：$Path" }

    $v = $video[0]
    $transfer = [string]$v.color_transfer
    $primaries = [string]$v.color_primaries
    $matrix = [string]$v.color_space
    $range = [string]$v.color_range

    if ($transfer -eq 'smpte2084' -or $transfer -eq 'arib-std-b67') {
        throw ("输入已经是 HDR（transfer={0}），不是 SDR，本工具只处理 SDR 源。" -f $transfer)
    }

    $knownTransfers = @('bt709', 'gamma22', 'gamma28', 'smpte170m', 'smpte240m', 'linear', 'iec61966_2_1', 'iec61966_2_4', 'bt2020_10', 'bt2020_12')
    $knownPrimaries = @('bt709', 'bt470m', 'bt470bg', 'smpte170m', 'smpte240m', 'film', 'bt2020', 'smpte428', 'smpte431', 'smpte432', 'jedec-p22')
    $knownMatrices = @('bt709', 'fcc', 'bt470bg', 'smpte170m', 'smpte240m', 'bt2020nc', 'bt2020c')

    $inTransfer = 'bt709'
    if ($knownTransfers -contains $transfer) { $inTransfer = $transfer }
    $inPrimaries = 'bt709'
    if ($knownPrimaries -contains $primaries) { $inPrimaries = $primaries }
    $inMatrix = 'bt709'
    if ($knownMatrices -contains $matrix) { $inMatrix = $matrix }

    $rangeMode = 'limited'
    if ($range -eq 'pc' -or $range -eq 'full' -or $range -eq 'jpeg') { $rangeMode = 'full' }

    $fpsText = [string]$v.r_frame_rate
    $fps = ConvertTo-FractionValue $fpsText
    if ($fps -le 0) {
        $fpsText = [string]$v.avg_frame_rate
        $fps = ConvertTo-FractionValue $fpsText
    }
    if ($fps -le 0) {
        $fps = 25.0
        $fpsText = '25'
    }
    $avgFps = ConvertTo-FractionValue ([string]$v.avg_frame_rate)
    if ($avgFps -le 0) { $avgFps = $fps }

    $audioStreams = @($info.streams | Where-Object { $_.codec_type -eq 'audio' })
    $subtitleStreams = @($info.streams | Where-Object { $_.codec_type -eq 'subtitle' })

    $duration = 0.0
    if ($info.format -and $info.format.duration) { $duration = Get-NumericValue $info.format.duration }

    return @{
        Path = $Path
        VideoCodec = [string]$v.codec_name
        Width = [int]$v.width
        Height = [int]$v.height
        PixelFormat = [string]$v.pix_fmt
        Fps = $fps
        AvgFps = $avgFps
        FpsText = $fpsText
        Duration = $duration
        InTransfer = $inTransfer
        InPrimaries = $inPrimaries
        InMatrix = $inMatrix
        InRange = $rangeMode
        AudioStreams = $audioStreams
        SubtitleStreams = $subtitleStreams
    }
}

# ---------------------------------------------------------------------------
# 亮度分析（解码 + 转 PQ 后采样）
# ---------------------------------------------------------------------------
function Measure-ContentBrightness {
    param(
        [string]$SourcePath,
        [string]$ZscaleChain,
        [string]$WorkDir,
        [int]$SampleFps,
        [double]$TotalSeconds = 0
    )

    $logName = 'stats.log'
    $logPath = Join-Path $WorkDir $logName
    if (Test-Path -LiteralPath $logPath) { Remove-Item -LiteralPath $logPath -Force }

    $filter = 'fps={1},{0},signalstats,metadata=print:file={2}' -f $ZscaleChain, $SampleFps, $logName
    $statsArgs = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', $SourcePath,
        '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', $filter, '-f', 'null', '-')

    Invoke-MediaWithProgress -Exe $script:Ffmpeg -Arguments $statsArgs -WorkDir $WorkDir `
        -LogBaseName 'brightness' -What '亮度分析' -Label '分析' -TotalSeconds $TotalSeconds

    if (-not (Test-Path -LiteralPath $logPath)) { throw '亮度分析没有产生统计数据' }

    $maxCode = 0.0
    $minCode = 2000.0
    $sumAvg = 0.0
    $samples = 0

    foreach ($line in [System.IO.File]::ReadLines($logPath)) {
        if ($line.Length -lt 24) { continue }
        if ($line.StartsWith('lavfi.signalstats.YMAX=')) {
            $value = ConvertTo-FractionValue ($line.Substring(23))
            if ($value -gt $maxCode) { $maxCode = $value }
        }
        elseif ($line.StartsWith('lavfi.signalstats.YMIN=')) {
            $value = ConvertTo-FractionValue ($line.Substring(23))
            if ($value -lt $minCode) { $minCode = $value }
        }
        elseif ($line.StartsWith('lavfi.signalstats.YAVG=')) {
            $sumAvg += (ConvertTo-FractionValue ($line.Substring(23)))
            $samples++
        }
    }

    if ($samples -lt 1) { throw '亮度分析样本为空（视频可能太短）' }

    $avgCode = $sumAvg / $samples
    if ($minCode -gt $maxCode) { $minCode = 0.0 }

    $maxSignal = Convert-YuvCodeToPqSignal -Code $maxCode
    $avgSignal = Convert-YuvCodeToPqSignal -Code $avgCode
    $minSignal = Convert-YuvCodeToPqSignal -Code $minCode

    $maxPq = [int][math]::Round($maxSignal * 4095.0)
    $avgPq = [int][math]::Round($avgSignal * 4095.0)
    $minPq = [int][math]::Round($minSignal * 4095.0)

    if ($avgPq -lt $minPq) { $avgPq = $minPq }
    if ($maxPq -lt $avgPq) { $maxPq = $avgPq }
    if ($maxPq -lt 1) { $maxPq = 1 }

    $maxNits = Convert-PqSignalToNits -Signal ($maxPq / 4095.0)
    $avgNits = Convert-PqSignalToNits -Signal ($avgPq / 4095.0)

    return @{
        Samples = $samples
        MaxPq = $maxPq
        AvgPq = $avgPq
        MinPq = $minPq
        MaxNits = $maxNits
        AvgNits = $avgNits
    }
}

# ---------------------------------------------------------------------------
# 帧数统计（以编码后的 HEVC 为准）
# ---------------------------------------------------------------------------
function Get-HevcFrameCount {
    param([string]$Path)
    # Our libx265 output contains one coded picture per access unit. Count
    # parsed packets first instead of decoding a whole 4K film just to count.
    # Keep the decoder-based fallback for formats without packet counts.
    $raw = & $script:Ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 $Path 2>$null
    $text = (($raw -join '') -replace '[^0-9]', '')
    $count = 0
    [int]::TryParse($text, [ref]$count) | Out-Null
    if ($count -le 0) {
        $raw = & $script:Ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 $Path 2>$null
        $text = (($raw -join '') -replace '[^0-9]', '')
        [int]::TryParse($text, [ref]$count) | Out-Null
    }
    if ($count -le 0) {
        $info = Get-MediaInfo -Path $Path
        $duration = 0.0
        if ($info.format -and $info.format.duration) { $duration = Get-NumericValue $info.format.duration }
        $v = @($info.streams | Where-Object { $_.codec_type -eq 'video' })[0]
        $fps = ConvertTo-FractionValue ([string]$v.r_frame_rate)
        if ($fps -le 0) { $fps = 25.0 }
        $count = [int][math]::Round($duration * $fps)
    }
    if ($count -le 0) { throw '无法确定帧数' }
    return $count
}

# ---------------------------------------------------------------------------
# 生成 DV 8.1 RPU
# ---------------------------------------------------------------------------
function New-DvRpu {
    param(
        [string]$DoviExe,
        [int]$FrameCount,
        [hashtable]$Stats,
        [string]$WorkDir
    )

    $maxCll = [int][math]::Max(1, [math]::Round($Stats.MaxNits))
    $maxFall = [int][math]::Max(1, [math]::Round($Stats.AvgNits))
    if ($maxFall -gt $maxCll) { $maxFall = $maxCll }

    $configPath = Join-Path $WorkDir 'generator.json'
    $rpuPath = Join-Path $WorkDir 'RPU.bin'
    $defaultBlocks = ''
    if ($HdrMode -eq 'TrueHDR') {
        # dovi_tool 2.3.4 Level9: index 2 = BT.2020, matching our reference
        # working/mastering colour space; its implicit default is P3 D65.
        $defaultBlocks = '"default_metadata_blocks": [{"Level9": {"length": 1, "source_primary_index": 2}}],'
    }

    $json = @"
{
    "cm_version": "V40",
    "profile": "$Profile",
    $defaultBlocks
    "long_play_mode": false,
    "level6": {
        "max_display_mastering_luminance": 1000,
        "min_display_mastering_luminance": 1,
        "max_content_light_level": $maxCll,
        "max_frame_average_light_level": $maxFall
    },
    "shots": [
        {
            "start": 0,
            "duration": $FrameCount,
            "metadata_blocks": [
                {
                    "Level1": {
                        "min_pq": $($Stats.MinPq),
                        "max_pq": $($Stats.MaxPq),
                        "avg_pq": $($Stats.AvgPq)
                    }
                }
            ]
        }
    ]
}
"@

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)

    Invoke-NativeQuiet -Exe $DoviExe -Arguments @('generate', '-j', $configPath, '-o', $rpuPath) -What 'dovi_tool generate'

    if (-not (Test-Path -LiteralPath $rpuPath)) { throw 'RPU 生成失败：找不到输出文件' }

    return @{ RpuPath = $rpuPath; MaxCll = $maxCll; MaxFall = $maxFall }
}

# ---------------------------------------------------------------------------
# MP4：音频 ES 提取 / 转码
# ---------------------------------------------------------------------------
function Get-Mp4AudioElementaryStream {
    param(
        [hashtable]$Info,
        [string]$WorkDir,
        [string]$AudioCodec,
        [int]$Bitrate
    )

    $results = @()
    $index = 0
    foreach ($stream in $Info.AudioStreams) {
        $codec = [string]$stream.codec_name
        $streamIndex = [int]$stream.index
        $outFile = $null
        $args = $null

        if ($codec -eq 'ac3') {
            $outFile = Join-Path $WorkDir ('audio{0}.ac3' -f $index)
            $args = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', $Info.Path, '-map', ('0:{0}' -f $streamIndex), '-c:a', 'copy', '-f', 'ac3', $outFile)
        }
        elseif ($codec -eq 'eac3') {
            $outFile = Join-Path $WorkDir ('audio{0}.ec3' -f $index)
            $args = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', $Info.Path, '-map', ('0:{0}' -f $streamIndex), '-c:a', 'copy', '-f', 'eac3', $outFile)
        }
        else {
            $ext = 'ec3'
            $format = 'eac3'
            if ($AudioCodec -eq 'ac3') { $ext = 'ac3'; $format = 'ac3' }
            $outFile = Join-Path $WorkDir ('audio{0}.{1}' -f $index, $ext)
            $args = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', $Info.Path, '-map', ('0:{0}' -f $streamIndex),
                '-c:a', $AudioCodec, '-b:a', ('{0}k' -f $Bitrate), '-ar', '48000', '-f', $format, $outFile)
        }

        Invoke-NativeQuiet -Exe $script:Ffmpeg -Arguments $args -What ('音轨 {0} 处理' -f ($index + 1))
        $converted = ($codec -ne 'ac3' -and $codec -ne 'eac3')
        $results += @{ Path = $outFile; Codec = $codec; Converted = $converted }
        $index++
    }
    return $results
}

function Get-Mp4AudioRemuxPlan {
    param([object[]]$AudioStreams, [string]$AudioCodec, [switch]$ForBilibili)

    $audioArguments = @()
    $encodings = @()
    for ($index = 0; $index -lt $AudioStreams.Count; $index++) {
        $stream = $AudioStreams[$index]
        $codec = [string]$stream.codec_name
        $useAlac = ($AudioCodec -eq 'alac' -or
            ($AudioCodec -eq 'auto' -and $ForBilibili -and $codec -like 'pcm_*'))
        if (-not $useAlac) { continue }

        # ALAC's encoder supports 16 or 24 bits. Refuse float/32-bit sources
        # instead of silently truncating, resampling or changing channel layout.
        $bits = [int]$stream.bits_per_raw_sample
        if ($bits -le 0) { $bits = [int]$stream.bits_per_sample }
        if (($codec -notmatch '^pcm_s(16|24)(le|be)$' -and $codec -notin @('alac', 'flac')) -or
            $bits -notin @(16, 24)) {
            throw ('音轨 {0}（{1} / {2}bit）不能无损编码为 ALAC；不会自动降位深。可显式 -Mp4AudioCodec copy 保持原封装。' -f ($index + 1), $codec, $bits)
        }
        $layout = [string]$stream.channel_layout
        if (-not $layout -and [int]$stream.channels -eq 1) { $layout = 'mono' }
        elseif (-not $layout -and [int]$stream.channels -eq 2) { $layout = 'stereo' }
        if ($layout -notin @('mono', 'stereo', '3.0', '4.0', '5.0', '5.1', '6.1(back)', '7.1(wide)')) {
            throw ('音轨 {0} 的声道布局“{1}”未受此 ALAC 路径支持；不会自动混音。可显式 -Mp4AudioCodec copy。' -f ($index + 1), $layout)
        }
        $sampleFormat = 's16p'
        if ($bits -eq 24) { $sampleFormat = 's32p' }
        $audioArguments += @(('-c:a:{0}' -f $index), 'alac',
            ('-sample_fmt:a:{0}' -f $index), $sampleFormat,
            ('-bits_per_raw_sample:a:{0}' -f $index), [string]$bits)
        $encodings += @{ Index = $index; Bits = $bits; Rate = [int]$stream.sample_rate;
            Channels = [int]$stream.channels; Layout = $layout }
    }
    return @{ Arguments = $audioArguments; Encodings = $encodings }
}

function Test-AlacAudioParameters {
    param([string]$Path, [hashtable]$Plan)
    if ($Plan.Encodings.Count -eq 0) { return }
    $media = Get-MediaInfo -Path $Path
    $audio = @($media.streams | Where-Object { $_.codec_type -eq 'audio' })
    foreach ($expected in $Plan.Encodings) {
        $actual = $audio[$expected.Index]
        if ($actual.codec_name -ne 'alac' -or [int]$actual.bits_per_raw_sample -ne $expected.Bits -or
            [int]$actual.sample_rate -ne $expected.Rate -or [int]$actual.channels -ne $expected.Channels -or
            $actual.channel_layout -ne $expected.Layout) {
            throw ('ALAC 音轨 {0} 的采样率、位深或声道布局校验失败。' -f ($expected.Index + 1))
        }
    }
}

function New-Mp4Output {
    param(
        [hashtable]$Info,
        [string]$HevcPath,
        [string]$OutputPath,
        [string]$WorkDir,
        [string]$AudioCodec,
        [int]$Bitrate,
        [switch]$ForRemux
    )

    $fpsArg = ConvertTo-FpsArgument $Info.FpsText
    $mp4Args = @('-i', $HevcPath, '--input-video-frame-rate', $fpsArg)

    $transcoded = 0
    $remuxAudio = ($AudioCodec -in @('auto', 'copy', 'alac') -and $Info.AudioStreams.Count -gt 0)
    if ($AudioCodec -in @('eac3', 'ac3')) {
        $audio = Get-Mp4AudioElementaryStream -Info $Info -WorkDir $WorkDir -AudioCodec $AudioCodec -Bitrate $Bitrate
        foreach ($item in $audio) {
            $mp4Args += @('-i', $item.Path)
            if ($item.Converted) { $transcoded++ }
        }
    }

    $muxOutput = $OutputPath
    if ($remuxAudio) { $muxOutput = Join-Path $WorkDir 'video_only.mp4' }

    $mp4Args += @(
        '-o', $muxOutput,
        '--dv-profile', '8',
        '--dv-bl-compatible-id', [string]$script:DvCompatibilityId,
        '--mpeg4-comp-brand', 'mp42,iso6,isom,msdh,dby1',
        '--overwrite'
    )

    if ($Profile -eq '8.4' -or $remuxAudio -or $ForRemux) { $mp4Args += @('--hvc1flag', '0') }
    Invoke-NativeQuiet -Exe $script:Mp4Muxer -Arguments $mp4Args -What 'mp4muxer 封装 MP4'
    if ($remuxAudio) {
        $audioPlan = Get-Mp4AudioRemuxPlan -AudioStreams $Info.AudioStreams -AudioCodec $AudioCodec -ForBilibili:$Bilibili
        if ($audioPlan.Encodings.Count -gt 0) {
            foreach ($encoding in $audioPlan.Encodings) {
                Write-Log ('音轨 {0} → ALAC 无损：{1} Hz / {2}bit，不改采样值和声道；其余音轨直接复制。' -f ($encoding.Index + 1), $encoding.Rate, $encoding.Bits)
            }
        }
        else { Write-Log ('音频原样复制：{0} 条，不改编码、采样率或位深' -f $Info.AudioStreams.Count) }
        $copyArgs = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', $muxOutput, '-i', $Info.Path, '-map', '0:v:0', '-map', '1:a?',
            '-map_metadata', '1', '-map_chapters', '1', '-c', 'copy',
            '-strict', 'unofficial', '-movflags', '+faststart')
        $copyArgs += @($audioPlan.Arguments) + @('-tag:v', 'hvc1')
        $copyArgs += $OutputPath
        try {
            Invoke-NativeQuiet -Exe $script:Ffmpeg -Arguments $copyArgs -What 'MP4 音轨封装'
            Test-AlacAudioParameters -Path $OutputPath -Plan $audioPlan
        }
        catch {
            throw ('MP4 音轨封装失败。可改用 -Format mkv 保留原音；不会自动改用有损编码。详情：{0}' -f $_.Exception.Message)
        }
    }
    return $transcoded
}

# ---------------------------------------------------------------------------
# MKV：ffmpeg 封装（音轨/字幕全保留）
# ---------------------------------------------------------------------------
function New-MkvOutput {
    param(
        [hashtable]$Info,
        [string]$HevcPath,
        [string]$OutputPath,
        [string]$WorkDir
    )

    $interimMp4 = Join-Path $WorkDir 'interim.mp4'
    $fpsArg = ConvertTo-FpsArgument $Info.FpsText

    if ($script:Mp4Muxer) {
        # Dolby's muxer reconstructs HEVC presentation order from POC. FFmpeg
        # inventing timestamps for raw HEVC can shift video relative to audio.
        $videoOnlyInfo = $Info.Clone()
        $videoOnlyInfo.AudioStreams = @()
        $null = New-Mp4Output -Info $videoOnlyInfo -HevcPath $HevcPath -OutputPath $interimMp4 `
            -WorkDir $WorkDir -AudioCodec 'copy' -Bitrate 640 -ForRemux
    }
    else {
        $interimArgs = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'hevc', '-r', $fpsArg, '-i', $HevcPath, '-c', 'copy', $interimMp4)
        Invoke-NativeQuiet -Exe $script:Ffmpeg -Arguments $interimArgs -What 'MKV 中转封装（视频）'
    }

    $remuxArgs = @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-i', $interimMp4, '-ss', '0', '-i', $Info.Path,
        '-map', '0:v:0', '-map', '1:a?', '-map', '1:s?',
        '-c', 'copy', '-avoid_negative_ts', 'disabled', '-map_chapters', '1', '-map_metadata', '1',
        $OutputPath)
    Invoke-NativeQuiet -Exe $script:Ffmpeg -Arguments $remuxArgs -What 'MKV 封装（音轨/字幕）'
    $mkvMedia = Get-MediaInfo -Path $OutputPath
    $mkvVideo = @($mkvMedia.streams | Where-Object { $_.codec_type -eq 'video' })[0]
    if ([math]::Abs((Get-NumericValue $mkvMedia.format.duration) - $Info.Duration) -ge 0.1 -or
        ($HdrMode -eq 'TrueHDR' -and [math]::Abs((Get-NumericValue $mkvVideo.start_time)) -ge 0.01)) {
        throw 'MKV 时长/视频起始 PTS 校验失败；请使用 Dolby mp4muxer 或选择 MP4，不会将音画偏移作为成功输出。'
    }
}

# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
function Test-DolbyVisionInMp4 {
    param([string]$Path)
    $media = Get-MediaInfo -Path $Path
    $video = @($media.streams | Where-Object { $_.codec_type -eq 'video' })[0]
    $dv = @($video.side_data_list | Where-Object { $_.side_data_type -eq 'DOVI configuration record' })
    return ($video.codec_name -eq 'hevc' -and $video.profile -eq 'Main 10' -and
        $video.pix_fmt -eq 'yuv420p10le' -and $video.color_primaries -eq 'bt2020' -and
        $video.color_space -eq 'bt2020nc' -and $dv.Count -eq 1 -and $dv[0].dv_profile -eq 8 -and
        $dv[0].dv_bl_signal_compatibility_id -eq $script:DvCompatibilityId -and
        $dv[0].rpu_present_flag -eq 1 -and $video.color_transfer -eq $script:OutputTransfer)
}

function Test-RpuInMkv {
    param([string]$Path, [string]$DoviExe, [int]$ExpectedFrames, [string]$WorkDir)
    $checkRpu = Join-Path $WorkDir 'check_rpu.bin'
    if (Test-Path -LiteralPath $checkRpu) { Remove-Item -LiteralPath $checkRpu -Force }
    # Reconstruct Annex B with global VPS/SPS/PPS before dovi_tool parses slice
    # order. Direct MKV parsing can fail on valid hvc1-derived CodecPrivate.
    $checkHevc = Join-Path $WorkDir 'check_stream.hevc'
    Invoke-NativeQuiet -Exe $script:Ffmpeg -Arguments @('-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-i', $Path, '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', $checkHevc) -What 'MKV 校验用 Annex B 提取'
    & $DoviExe extract-rpu $checkHevc -o $checkRpu 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $checkRpu)) { return $false }

    $raw = & $DoviExe info -i $checkRpu -s 2>$null
    $text = ($raw -join "`n")
    $frames = 0
    if ($text -match 'Frames:\s*(\d+)') { $frames = [int]$Matches[1] }
    return ($frames -eq $ExpectedFrames)
}

# ---------------------------------------------------------------------------
# 单文件处理
# ---------------------------------------------------------------------------
function Convert-SingleFile {
    param([string]$SourcePath)

    $fileStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $sourceItem = Get-Item -LiteralPath $SourcePath
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($sourceItem.Name)

    if (-not $OutputDir) {
        $targetDir = $sourceItem.DirectoryName
    }
    else {
        $targetDir = $OutputDir
        if (-not (Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
    }
    $targetDir = (Resolve-Path -LiteralPath $targetDir).Path

    $outputs = @()
    $suffix = '_DV' + $Profile.Replace('.', '')
    if ($HdrMode -eq 'TrueHDR') {
        if ($BrightnessMapping -eq 'Reference' -and $BrightnessStrength -gt 0) { $suffix = '_Ref' + $suffix }
        $suffix = '_TrueHDR' + $suffix
    }
    if ($Format -eq 'mp4' -or $Format -eq 'both') {
        $outputs += @{ Kind = 'mp4'; Path = (Join-Path $targetDir ($baseName + $suffix + '.mp4')) }
    }
    if ($Format -eq 'mkv' -or $Format -eq 'both') {
        $outputs += @{ Kind = 'mkv'; Path = (Join-Path $targetDir ($baseName + $suffix + '.mkv')) }
    }

    $pending = @()
    foreach ($o in $outputs) {
        if ((Test-Path -LiteralPath $o.Path) -and -not $Force) {
            Write-Log ('跳过（已存在）：{0}（用 -Force 覆盖）' -f $o.Path) 'WARN'
        }
        else {
            $pending += $o
        }
    }
    if ($pending.Count -eq 0) {
        Write-Log '所有输出已存在，跳过。' 'WARN'
        return
    }

    Write-Log ('处理：{0}' -f $sourceItem.Name) 'STEP'

    # 1. 探测
    Write-Log '探测输入参数…'
    $info = Get-IncomingVideoInfo -Path $sourceItem.FullName
    if ($Mp4AudioCodec -in @('auto', 'copy', 'alac') -and
        (@($pending | Where-Object { $_.Kind -eq 'mp4' }).Count -gt 0 -or $KeepHDR10)) {
        $null = Get-Mp4AudioRemuxPlan -AudioStreams $info.AudioStreams -AudioCodec $Mp4AudioCodec -ForBilibili:$Bilibili
    }
    Write-Log ('  {0} {1}x{2}  {3}fps  {4}  | 音轨 {5} 条，字幕 {6} 条' -f `
            $info.VideoCodec, $info.Width, $info.Height, [math]::Round($info.Fps, 3), $info.PixelFormat,
        $info.AudioStreams.Count, $info.SubtitleStreams.Count) 'DIM'
    if ($info.AudioStreams.Count -eq 0) {
        Write-Log '  输入不含音轨，输出将只有视频。' 'WARN'
    }
    if ([math]::Abs($info.AvgFps - $info.Fps) -gt 0.05) {
        Write-Log ('  检测到可变帧率（r={0:0.###} / avg={1:0.###}），按固定 {0:0.###}fps 处理，长片可能有轻微音画不同步' -f $info.Fps, $info.AvgFps) 'WARN'
    }

    # 2. 工作目录（必须 ASCII：Dolby mp4muxer 不支持非 ASCII 路径）
    $tempRoot = $script:WorkRoot
    if (-not (Test-Path -LiteralPath $tempRoot)) {
        New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    }
    $safeName = $baseName -replace '[^A-Za-z0-9\.\-]', '_'
    if (-not $safeName -or $safeName -match '^_*$') { $safeName = 'video' }
    $workDir = Join-Path $tempRoot ('{0}_{1}_{2}' -f $safeName, (Get-Date -Format 'HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8)))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null

    try {
        $encodeSource = $sourceItem.FullName
        # 3. L1 始终在 PQ 域测量；P8.4 的基础层另行编码为 HLG。
        $zscale = ('zscale=tin={0}:pin={1}:min={2}:rin={3}:p=bt2020:m=bt2020nc:t=smpte2084:npl={4}:r=limited,format=yuv420p10le' -f `
                $info.InTransfer, $info.InPrimaries, $info.InMatrix, $info.InRange, $WhitePoint)
        if ($PeakNits -gt 0) {
            Write-Log ('高光扩展至约 {0} nit；中低亮度仍按 {1} nit 白点基准（不恢复 SDR 中已丢失的细节）' -f $PeakNits, $WhitePoint)
            $lutPath = New-HighlightLut -WorkDir $workDir -DiffuseWhite ([int]$WhitePoint) -Peak $PeakNits
            $linear = ('zscale=tin={0}:pin={1}:min={2}:rin={3}:t=linear:p=bt2020:m=gbr:r=full,format=gbrpf32le' -f `
                $info.InTransfer, $info.InPrimaries, $info.InMatrix, $info.InRange)
            $zscale = ('{0},lut3d=file={1}:interp=tetrahedral,zscale=tin=linear:pin=bt2020:min=gbr:rin=full:t=smpte2084:p=bt2020:m=bt2020nc:npl={2}:r=limited,format=yuv420p10le' -f `
                $linear, [System.IO.Path]::GetFileName($lutPath), $PeakNits)
        }
        $encodeZscale = $zscale
        if ($Profile -eq '8.4') {
            $encodeZscale = $zscale.Replace(':t=smpte2084:', ':t=arib-std-b67:')
        }

        if ($HdrMode -eq 'TrueHDR') {
            $pqSource = Join-Path $workDir 'truehdr_pq.mkv'
            $backend = Join-Path $PSScriptRoot 'truehdr_backend.py'
            $hlgSource = Join-Path $workDir 'truehdr_hlg.mkv'
            $hdr10Video = ''
            $legacyPq = $false
            $applyCachedBrightness = $false
            $hdr10NeedsBrightness = $false
            $toneName = 'neutral'
            if ($BrightnessMapping -eq 'Reference' -and $BrightnessStrength -gt 0) { $toneName = 'reference' }
            $toneStrength = $BrightnessStrength.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
            $skinStrength = $SkinToneCorrection.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
            $toneInfo = $null
            $hdr10Suffix = '_TrueHDR_HDR10.mp4'
            if ($toneName -eq 'reference') { $hdr10Suffix = '_TrueHDR_Ref_HDR10.mp4' }
            if ($toneName -eq 'reference') {
                $mappingText = & $script:TrueHDRPython -B (Join-Path $PSScriptRoot 'brightness_mapping.py') --describe --strength $toneStrength --skin-strength $skinStrength
                if ($LASTEXITCODE -ne 0) { throw '无法读取参考亮度曲线配置。' }
                $toneInfo = ($mappingText -join "`n") | ConvertFrom-Json
            }
            if ($KeepHDR10) {
                $hdr10Target = Join-Path $targetDir ($baseName + $hdr10Suffix)
                if ((Test-Path -LiteralPath $hdr10Target) -and -not $Force) { throw "HDR10 输出已存在：$hdr10Target" }
            }
            $backendArgs = @('--input', $sourceItem.FullName, '--output', $hlgSource,
                '--ffmpeg', $script:Ffmpeg, '--ffprobe', $script:Ffprobe,
                '--report', (Join-Path $workDir 'truehdr-report.json'),
                '--luminance', [string]$TrueHDRLuminance, '--contrast', [string]$TrueHDRContrast,
                '--saturation', [string]$TrueHDRSaturation, '--middle-gray', [string]$TrueHDRMiddleGray,
                '--tone-mapping', $toneName, '--tone-strength', $toneStrength, '--skin-strength', $skinStrength)
            $backendArgs += @('--cache-encoder', $Encoder.ToLowerInvariant())
            if ($TrueHDRDebandingOff) { $backendArgs += '--debanding-off' }
            if ($TrueHDRCache) {
                $cacheArgs = @('-B', $backend, '--verify-cache', $TrueHDRCache, '--input', $sourceItem.FullName,
                    '--luminance', [string]$TrueHDRLuminance, '--contrast', [string]$TrueHDRContrast,
                    '--saturation', [string]$TrueHDRSaturation, '--middle-gray', [string]$TrueHDRMiddleGray)
                if ($TrueHDRDebandingOff) { $cacheArgs += '--debanding-off' }
                Write-Log '核验已完成的 TrueHDR 缓存（源 SHA256、媒体 SHA256、全部 SDK 参数）…'
                $cacheText = & $script:TrueHDRPython @cacheArgs 2>&1
                if ($LASTEXITCODE -ne 0) { throw ('TrueHDR 缓存校验失败：{0}' -f ($cacheText -join "`n")) }
                $cacheInfo = ($cacheText -join "`n") | ConvertFrom-Json
                Copy-Item -LiteralPath $cacheInfo.manifest -Destination (Join-Path $workDir 'truehdr-cache-manifest.json')
                if ($cacheInfo.kind -eq 'hlg-yuv420p10-lossless-v1') {
                    $hlgSource = [string]$cacheInfo.hlg
                    $hdr10Video = [string]$cacheInfo.hdr10
                    if ($KeepHDR10 -and -not $hdr10Video) { throw '该缓存未保留 HDR10。请去掉 -KeepHDR10 续跑，或不指定缓存重新生成 HDR10。' }
                    $cachedTone = $cacheInfo.brightness_mapping
                    $cachedName = 'neutral'
                    if ($cachedTone.name) { $cachedName = $cachedTone.name }
                    if ($cachedName -eq 'neutral') {
                        $applyCachedBrightness = ($toneName -eq 'reference')
                        $hdr10NeedsBrightness = $applyCachedBrightness
                    }
                    elseif ($cachedName -ne $toneName -or $cachedTone.strength -ne $BrightnessStrength -or
                        $cachedTone.skin_strength -ne $SkinToneCorrection -or
                        $cachedTone.profile_id -ne $toneInfo.profile_id -or $cachedTone.profile_sha256 -ne $toneInfo.profile_sha256) {
                        throw '缓存的亮度或肤色参数不同；不能再次叠加或撤销。请使用匹配参数，或重新生成缓存。'
                    }
                }
                else { $pqSource = [string]$cacheInfo.pq; $legacyPq = $true; $hdr10NeedsBrightness = ($toneName -eq 'reference') }
            }
            else {
                if ($KeepHDR10) {
                    $hdr10Video = Join-Path $workDir 'HDR10_video.mp4'
                    $backendArgs += @('--hdr10-output', $hdr10Video)
                }
                Write-Log ('NVIDIA TrueHDR → PQ→HLG 逐帧管道；使用 {0} 保存 Main10 无损缓存…' -f $Encoder)
                # Keep the SDK's working directory outside the per-run folder:
                # Windows can retain that directory handle after inference.
                Invoke-MediaWithProgress -Exe $script:TrueHDRPython -ArgumentPrefix @('-B', (Join-Path $PSScriptRoot 'truehdr_stream.py')) -Arguments $backendArgs -WorkDir $workDir `
                    -LogBaseName 'truehdr' -What 'NVIDIA TrueHDR 流式转换' -Label 'TrueHDR/HLG' -TotalSeconds $info.Duration -ProcessWorkingDirectory $tempRoot
            }
            if ($legacyPq) {
                $hlgArgs = @('--input', $pqSource, '--output', $hlgSource,
                    '--report', (Join-Path $workDir 'pq-to-hlg-report.json'), '--fps', $info.FpsText,
                    '--frames', [string]$cacheInfo.frames, '--ffmpeg', $script:Ffmpeg, '--ffprobe', $script:Ffprobe,
                    '--tone-mapping', $toneName, '--tone-strength', $toneStrength, '--skin-strength', $skinStrength,
                    '--cache-encoder', $Encoder.ToLowerInvariant())
                Invoke-MediaWithProgress -Exe $script:TrueHDRPython -ArgumentPrefix @('-B', (Join-Path $PSScriptRoot 'pq_to_hlg.py')) `
                    -Arguments $hlgArgs -WorkDir $workDir -LogBaseName 'pq_to_hlg' -What 'CUDA PQ→HLG' -Label 'PQ→HLG' -TotalSeconds $info.Duration -ProcessWorkingDirectory $tempRoot
            }
            $encodeSource = $hlgSource
            $encodeZscale = 'null'
            if ($applyCachedBrightness) {
                Write-Log '复用旧的中性 HLG 缓存；在编码时应用参考亮度曲线，不重跑 AI。'
                $encodeZscale = New-ReferenceBrightnessFilter -WorkDir $workDir -Transfer hlg
            }
            if ($toneName -eq 'reference') {
                Write-Log ('参考亮度曲线（强度 {0}）：保留黑位、扩展中高亮、1000 nit 高光柔和收拢；全片固定曲线。' -f $toneStrength)
                if ($SkinToneCorrection -gt 0) {
                    Write-Log ('暖色肤色去黄（强度 {0}）：轻微减绿、补蓝并保持亮度；按颜色范围选择。' -f $skinStrength)
                }
            }
            Write-Log 'PQ→HLG：1000 nit / gamma 1.2 / 理想黑；亮度相关 OOTF，超出 HLG 色体积时等比限制 RGB。'
            Write-Log 'TrueHDR 不叠加原模式高光曲线；RPU 仍是原有采样估计、全片单段静态元数据。' 'WARN'
            if ($KeepHDR10) {
                $hdr10Target = Join-Path $targetDir ($baseName + $hdr10Suffix)
                if ((Test-Path -LiteralPath $hdr10Target) -and -not $Force) { throw "HDR10 输出已存在：$hdr10Target" }
                $hdr10Work = Join-Path $workDir 'HDR10.mp4'
                $hdr10MasteringPeak = $TrueHDRLuminance
                if ($toneName -eq 'reference') { $hdr10MasteringPeak = 1000 }
                $hdr10Params = ('lossless=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L({0},1):log-level=error' -f (10000 * $hdr10MasteringPeak))
                $hdr10Args = @('-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
                    '-i', $pqSource, '-i', $sourceItem.FullName, '-map', '0:v:0', '-map', '1:a?',
                    '-map_metadata', '1', '-map_chapters', '1', '-vf',
                    'zscale=tin=smpte2084:pin=bt2020:min=gbr:rin=full:t=smpte2084:p=bt2020:m=bt2020nc:r=limited,format=yuv420p10le',
                    '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', $hdr10Params,
                    '-fps_mode', 'passthrough', '-tag:v', 'hvc1', '-c:a', 'copy', '-strict', 'unofficial',
                    '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc',
                    '-color_range', 'tv', '-movflags', '+faststart', $hdr10Work)
                if ($hdr10Video) {
                    $hdr10Args = @('-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
                        '-i', $hdr10Video, '-i', $sourceItem.FullName, '-map', '0:v:0', '-map', '1:a?',
                        '-map_metadata', '1', '-map_chapters', '1', '-c', 'copy', '-tag:v', 'hvc1',
                        '-strict', 'unofficial', '-movflags', '+faststart', $hdr10Work)
                }
                if ($hdr10NeedsBrightness) {
                    $matrix = 'gbr'
                    $hdr10Input = $pqSource
                    if ($hdr10Video) { $matrix = 'bt2020nc'; $hdr10Input = $hdr10Video }
                    $hdr10Filter = New-ReferenceBrightnessFilter -WorkDir $workDir -Transfer pq -InputMatrix $matrix
                    $hdr10Args = @('-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
                        '-i', $hdr10Input, '-i', $sourceItem.FullName, '-map', '0:v:0', '-map', '1:a?',
                        '-map_metadata', '1', '-map_chapters', '1', '-vf', $hdr10Filter,
                        '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', $hdr10Params,
                        '-fps_mode', 'passthrough', '-tag:v', 'hvc1', '-c:a', 'copy', '-strict', 'unofficial',
                        '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc',
                        '-color_range', 'tv', '-movflags', '+faststart', $hdr10Work)
                }
                $hdr10AudioPlan = Get-Mp4AudioRemuxPlan -AudioStreams $info.AudioStreams -AudioCodec $Mp4AudioCodec -ForBilibili:$Bilibili
                $hdr10Args = @($hdr10Args[0..($hdr10Args.Count - 2)]) + @($hdr10AudioPlan.Arguments) + @($hdr10Work)
                Write-Log '保存独立 HDR10/PQ 对照文件（Main10 无损压缩，原音复制）…'
                Invoke-MediaWithProgress -Exe $script:Ffmpeg -Arguments $hdr10Args -WorkDir $workDir `
                    -LogBaseName 'hdr10' -What 'HDR10 对照编码' -Label 'HDR10' -TotalSeconds $info.Duration
                $hdr10Media = Get-MediaInfo -Path $hdr10Work
                Test-AlacAudioParameters -Path $hdr10Work -Plan $hdr10AudioPlan
                $hdr10Video = @($hdr10Media.streams | Where-Object { $_.codec_type -eq 'video' })[0]
                if ($hdr10Video.codec_name -ne 'hevc' -or $hdr10Video.profile -ne 'Main 10' -or
                    $hdr10Video.color_transfer -ne 'smpte2084' -or $hdr10Video.color_primaries -ne 'bt2020' -or
                    $hdr10Video.color_space -ne 'bt2020nc') { throw 'HDR10 对照文件编码/色彩标记校验失败。' }
                Move-Item -LiteralPath $hdr10Work -Destination $hdr10Target -Force:$Force
            }
        }

        # 4. P8.1 预先分析以写 HDR10 SEI；P8.4 在编码后测量重建画面。
        $stats = $null
        if ($Profile -eq '8.1') {
            Write-Log ('分析画面亮度（L1 元数据依据，采样 {0} 帧/秒）…' -f $StatsFps)
            $stats = Measure-ContentBrightness -SourcePath $sourceItem.FullName -ZscaleChain $zscale -WorkDir $workDir -SampleFps $StatsFps -TotalSeconds $info.Duration
            Write-Log ('  样本 {0} 个 | 峰值 {1:0.0} nit | 平均 {2:0.0} nit' -f $stats.Samples, $stats.MaxNits, $stats.AvgNits) 'DIM'
        }

        # 5. 编码
        $blHevc = Join-Path $workDir 'BL.hevc'
        if ($Profile -eq '8.4') {
            $x265Params = 'hdr-opt=0:repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error'
        }
        else {
            $maxCll = [int][math]::Max(1, [math]::Round($stats.MaxNits))
            $maxFall = [int][math]::Max(1, [math]::Round($stats.AvgNits))
            if ($maxFall -gt $maxCll) { $maxFall = $maxCll }
            $x265Params = ('hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll={0},{1}:log-level=error' -f $maxCll, $maxFall)
        }
        if ($Lossless) { $x265Params = 'lossless=1:' + $x265Params }

        $encodeArgs = @(
            '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
            '-i', $encodeSource,
            '-map', '0:v:0', '-an', '-sn', '-dn',
            '-fps_mode', 'passthrough',
            '-vf', $encodeZscale
        )
        if ($Encoder -eq 'NVENC') {
            $encodeArgs += @(Get-NvencArguments -NvencPreset $NvencPreset -Bitrate $VideoBitrate `
                -MaxBitrate $MaxVideoBitrate -Quality $NvencCq -FrameRate $info.Fps -Lossless:$Lossless)
        }
        elseif ($VideoBitrate -gt 0) {
            $encodeArgs += @('-b:v', ('{0}k' -f $VideoBitrate), '-maxrate', ('{0}k' -f $MaxVideoBitrate),
                '-bufsize', ('{0}k' -f (2 * $MaxVideoBitrate)))
        }
        elseif (-not $Lossless) { $encodeArgs += @('-crf', [string]$Crf) }
        if ($Encoder -eq 'x265') { $encodeArgs += @('-c:v', 'libx265', '-preset', $Preset, '-x265-params', $x265Params) }
        $encodeArgs += @(
            '-color_primaries', 'bt2020',
            '-color_trc', $script:OutputTransfer,
            '-colorspace', 'bt2020nc',
            '-color_range', 'tv',
            '-f', 'hevc',
            $blHevc
        )

        $modeLabel = 'CRF ' + $Crf
        $presetLabel = $Preset
        if ($Encoder -eq 'NVENC') { $modeLabel = 'CQ ' + $NvencCq; $presetLabel = $NvencPreset }
        if ($VideoBitrate -gt 0) { $modeLabel = 'ABR {0}k / VBV {1}k' -f $VideoBitrate, $MaxVideoBitrate }
        if ($Lossless) { $modeLabel = '无损' }
        Write-Log ('编码 SDR → {0} / DV {1}（{2}，preset={3}，{4}）…' -f $script:OutputTransfer, $Profile, $Encoder, $presetLabel, $modeLabel)
        $encodeWatch = [System.Diagnostics.Stopwatch]::StartNew()
        if ($Encoder -eq 'NVENC' -and -not $Lossless) {
            Write-Log 'NVENC HQ：全分辨率逐帧多遍分析、lookahead 和 AQ；单次遍历视频。'
        }
        if ($VideoBitrate -gt 0 -and $Encoder -eq 'x265') {
            $paramIndex = [Array]::IndexOf($encodeArgs, '-x265-params') + 1
            $firstPassArgs = @($encodeArgs)
            $firstPassArgs[$paramIndex] = $x265Params + ':pass=1:slow-firstpass=0:stats=x265_2pass.log'
            $firstPassArgs[$firstPassArgs.Count - 1] = Join-Path $workDir 'pass1.hevc'
            Write-Log '目标码率使用两遍编码：第 1 遍分析码率分配…'
            Invoke-MediaWithProgress -Exe $script:Ffmpeg -Arguments $firstPassArgs -WorkDir $workDir `
                -LogBaseName 'encode_pass1' -What 'x265 第一遍' -Label '第一遍' -TotalSeconds $info.Duration
            $encodeArgs[$paramIndex] = $x265Params + ':pass=2:stats=x265_2pass.log'
            Write-Log '第 2 遍按分析结果编码…'
        }
        Invoke-MediaWithProgress -Exe $script:Ffmpeg -Arguments $encodeArgs -WorkDir $workDir `
            -LogBaseName 'encode' -What ($Encoder + ' 编码') -Label '编码' -TotalSeconds $info.Duration
        $encodeWatch.Stop()
        Write-Log ('  编码完成，用时 {0}，输出 {1}' -f (Format-Elapsed $encodeWatch.Elapsed), (Format-Size (Get-Item -LiteralPath $blHevc).Length)) 'OK'

        # 6. 帧数 + RPU
        $frameCount = Get-HevcFrameCount -Path $blHevc
        Write-Log ('编码帧数：{0}' -f $frameCount) 'DIM'
        if ($Profile -eq '8.4') {
            Write-Log ('从编码后的 HLG 基础层测量亮度（PQ 域，采样 {0} 帧/秒）…' -f $StatsFps)
            $decodedPq = 'zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=limited:t=smpte2084:p=bt2020:m=bt2020nc:npl=1000:r=limited,format=yuv420p10le'
            if ($HdrMode -eq 'TrueHDR') { $decodedPq = $decodedPq.Replace(':r=limited,', ':r=limited:agamma=0,') }
            $stats = Measure-ContentBrightness -SourcePath $blHevc -ZscaleChain $decodedPq -WorkDir $workDir -SampleFps $StatsFps -TotalSeconds $info.Duration
            Write-Log ('  样本 {0} 个 | 编码回读峰值 {1:0.0} nit | 平均 {2:0.0} nit' -f $stats.Samples, $stats.MaxNits, $stats.AvgNits) 'DIM'
        }

        Write-Log ('生成 Dolby Vision Profile {0} RPU（静态元数据，CM v4.0 会限制 L1 平均值下限）…' -f $Profile)
        $rpu = New-DvRpu -DoviExe $script:DoviTool -FrameCount $frameCount -Stats $stats -WorkDir $workDir
        Write-Log ('  L6: MaxCLL={0} nit / MaxFALL={1} nit' -f $rpu.MaxCll, $rpu.MaxFall) 'DIM'

        # 7. 注入
        $blDvHevc = Join-Path $workDir 'BL_DV.hevc'
        Write-Log '注入 RPU…'
        Invoke-NativeQuiet -Exe $script:DoviTool -Arguments @('inject-rpu', '-i', $blHevc, '-r', $rpu.RpuPath, '-o', $blDvHevc) -What 'dovi_tool inject-rpu'

        # 8. 封装 + 校验
        $results = @()
        foreach ($target in $pending) {
            if ($target.Kind -eq 'mp4') {
                if ($info.AudioStreams.Count -gt 0) {
                    Write-Log '封装 MP4（Dolby mp4muxer，写入 DOVI 配置记录）…'
                }
                else {
                    Write-Log '封装 MP4（Dolby mp4muxer，无音轨）…'
                }
                if ($info.SubtitleStreams.Count -gt 0) {
                    Write-Log '  MP4 不含字幕（要字幕请用 -Format mkv 或 both）' 'WARN'
                }

                # All containers stay in the owned ASCII work directory until
                # validated, so failed runs never publish incomplete outputs.
                $muxTarget = Join-Path $workDir 'final_mp4.mp4'

                $transcoded = New-Mp4Output -Info $info -HevcPath $blDvHevc -OutputPath $muxTarget -WorkDir $workDir -AudioCodec $Mp4AudioCodec -Bitrate $AudioBitrate
                if ($transcoded -gt 0) {
                    Write-Log ('  有 {0} 条音轨不是 AC3/EAC3，已转码为 {1} {2}k' -f $transcoded, $Mp4AudioCodec.ToUpperInvariant(), $AudioBitrate) 'WARN'
                }

                if (-not (Test-DolbyVisionInMp4 -Path $muxTarget)) {
                    throw ('MP4 校验失败：没有找到 DOVI 配置记录 - {0}' -f $target.Path)
                }
                Move-Item -LiteralPath $muxTarget -Destination $target.Path -Force:$Force
                $results += @{ Path = $target.Path; Size = (Get-Item -LiteralPath $target.Path).Length }
                Write-Log ('  校验通过：DV Profile {0}，兼容 ID={1}，transfer={2}' -f $Profile, $script:DvCompatibilityId, $script:OutputTransfer) 'OK'
            }
            else {
                Write-Log '封装 MKV（ffmpeg，保留全部音轨/字幕）…'
                $mkvTarget = Join-Path $workDir 'final_mkv.mkv'
                New-MkvOutput -Info $info -HevcPath $blDvHevc -OutputPath $mkvTarget -WorkDir $workDir
                if (-not (Test-RpuInMkv -Path $mkvTarget -DoviExe $script:DoviTool -ExpectedFrames $frameCount -WorkDir $workDir)) {
                    throw ('MKV 校验失败：RPU 缺失或帧数不匹配 - {0}' -f $target.Path)
                }
                Move-Item -LiteralPath $mkvTarget -Destination $target.Path -Force:$Force
                $results += @{ Path = $target.Path; Size = (Get-Item -LiteralPath $target.Path).Length }
                Write-Log '  校验通过：RPU 在流内，帧数匹配' 'OK'
            }
        }

        $fileStopwatch.Stop()
        Write-Log ('完成：{0}（用时 {1}）' -f $sourceItem.Name, (Format-Elapsed $fileStopwatch.Elapsed)) 'OK'
        foreach ($r in $results) {
            Write-Log ('  {0}  →  {1}' -f (Format-Size $r.Size), $r.Path) 'DIM'
        }
    }
    catch {
        if ($KeepTemp) {
            try {
                [IO.File]::WriteAllText((Join-Path $workDir 'failure.txt'),
                    ($_.Exception.Message + "`r`n" + $_.ScriptStackTrace), (New-Object Text.UTF8Encoding($false)))
            } catch { } # A full disk must not replace the original error.
        }
        throw
    }
    finally {
        # Resolve and bound every cleanup target to the directory created by
        # this invocation; never follow a user-provided path or cached PQ input.
        $resolvedWork = [IO.Path]::GetFullPath($workDir)
        $resolvedRoot = [IO.Path]::GetFullPath($tempRoot).TrimEnd('\')
        $safeCleanup = [string]::Equals([IO.Path]::GetDirectoryName($resolvedWork), $resolvedRoot,
            [StringComparison]::OrdinalIgnoreCase)
        if (Test-Path -LiteralPath $resolvedWork) {
            $safeCleanup = $safeCleanup -and -not ((Get-Item -LiteralPath $resolvedWork).Attributes -band [IO.FileAttributes]::ReparsePoint)
        }
        if ($KeepTemp) {
            Write-Log ('中间文件保留在：{0}' -f $resolvedWork) 'DIM'
        }
        elseif (-not $safeCleanup) {
            Write-Log ('清理路径安全检查未通过，未删除：{0}' -f $resolvedWork) 'WARN'
        }
        elseif (Test-Path -LiteralPath $resolvedWork) {
            # Windows may briefly retain directory handles after an external
            # process exits. Retry only this invocation's checked directory.
            for ($attempt = 1; $attempt -le 5; $attempt++) {
                try {
                    if (Test-Path -LiteralPath $resolvedWork) {
                        Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction Stop
                    }
                    Write-Log '已清理本次缓存、中间文件和日志。' 'DIM'
                    break
                }
                catch {
                    if ($attempt -eq 5) {
                        Write-Log ('清理未完成，请检查目录：{0}（{1}）' -f $resolvedWork, $_.Exception.Message) 'WARN'
                    }
                    else { Start-Sleep -Milliseconds 1000 }
                }
            }
        }
    }
}

# ===========================================================================
# 主流程
# ===========================================================================
Write-Host ''
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host ('  SDR → Dolby Vision 一键转换  v{0}' -f $script:ScriptVersion) -ForegroundColor White
Write-Host '  支持 PQ / Profile 8.1，以及 HLG / Profile 8.4（B站投稿）' -ForegroundColor DarkCyan
Write-Host '============================================================' -ForegroundColor DarkCyan
Write-Host ''

$exitCode = 0
try {
    if (-not $InputPath) {
        Write-Log '没有提供输入路径。' 'ERR'
        Write-Host ''
        Write-Host '  用法：' -ForegroundColor White
        Write-Host '    SDR2DV.ps1 -InputPath "D:\movie.mkv"                  # 默认输出 MP4' -ForegroundColor Gray
        Write-Host '    SDR2DV.ps1 -InputPath "D:\movie.mkv" -Format both    # 同时输出 MP4 + MKV' -ForegroundColor Gray
        Write-Host '    SDR2DV.ps1 -InputPath "D:\movie.mkv" -Bilibili       # P8.4 / HLG，目标 28 Mbps' -ForegroundColor Gray
        Write-Host '    SDR2DV.ps1 -InputPath "D:\movies"                    # 批量处理文件夹' -ForegroundColor Gray
        Write-Host '    也可以把视频文件拖到 “一键转DV.bat” 上。' -ForegroundColor Gray
        Write-Host ''
        exit 1
    }

    $InputPath = $InputPath.Trim().Trim('"').Trim("'")
    if (-not (Test-Path -LiteralPath $InputPath)) { throw "输入不存在：$InputPath" }

    if ($Bilibili) {
        if ($PSBoundParameters.ContainsKey('Profile') -and $Profile -ne '8.4') { throw '-Bilibili 必须使用 Profile 8.4。' }
        $Profile = '8.4'
        if (-not $PSBoundParameters.ContainsKey('PeakNits')) { $PeakNits = 1000 }
        if (-not $PSBoundParameters.ContainsKey('VideoBitrate')) { $VideoBitrate = 28000 }
        if (-not $PSBoundParameters.ContainsKey('MaxVideoBitrate')) { $MaxVideoBitrate = 30000 }
        if ($VideoBitrate -le 0 -or $VideoBitrate -gt 30000 -or $MaxVideoBitrate -le 0 -or $MaxVideoBitrate -gt 30000) {
            throw '-Bilibili 使用目标码率模式，视频目标与 VBV 上限须不超过 30000 kbps。'
        }
    }
    if ($HdrMode -eq 'TrueHDR') {
        if ($PSBoundParameters.ContainsKey('Profile') -and $Profile -ne '8.4') { throw 'TrueHDR 模式要求 -Profile 8.4。' }
        if ($PSBoundParameters.ContainsKey('PeakNits') -and $PeakNits -ne 0) { throw 'TrueHDR 不能叠加 -PeakNits 高光曲线；请用 -TrueHDRLuminance。' }
        if ($PSBoundParameters.ContainsKey('WhitePoint')) { throw 'TrueHDR 不使用 -WhitePoint；请用 -TrueHDRMiddleGray。' }
        if ($Mp4AudioCodec -notin @('auto', 'copy', 'alac')) { throw 'TrueHDR 模式保留原音采样值，请使用 -Mp4AudioCodec auto、copy 或 alac。' }
        $Profile = '8.4'
        $PeakNits = 0
        $script:TrueHDRPython = Resolve-ToolPath -Name '__truehdr_python__' -Candidates @($TrueHDRPython, (Join-Path $PSScriptRoot '.venv-truehdr\Scripts\python.exe')) -Hint '请先运行 Setup-TrueHDR.ps1，或用 -TrueHDRPython 指定独立环境。'
    }
    elseif ($KeepHDR10 -or $TrueHDRDebandingOff -or $PSBoundParameters.ContainsKey('BrightnessMapping') -or
        $PSBoundParameters.ContainsKey('SkinToneCorrection') -or
        $PSBoundParameters.ContainsKey('BrightnessStrength') -or @($PSBoundParameters.Keys | Where-Object { $_ -like 'TrueHDR*' }).Count -gt 0) {
        throw 'TrueHDR 参数需要 -HdrMode TrueHDR。'
    }
    if ($VideoBitrate -gt 0) {
        if ($Lossless -or $PSBoundParameters.ContainsKey('Crf')) { throw '-VideoBitrate 不能与 -Crf 或 -Lossless 同用。' }
        if ($MaxVideoBitrate -eq 0) { $MaxVideoBitrate = [int][math]::Min(200000, [math]::Ceiling(1.1 * $VideoBitrate)) }
        if ($MaxVideoBitrate -lt $VideoBitrate) { throw '-MaxVideoBitrate 不能小于 -VideoBitrate。' }
    }
    elseif ($MaxVideoBitrate -gt 0) { throw '-MaxVideoBitrate 需要同时指定正数 -VideoBitrate。' }
    $script:DvCompatibilityId = 1
    $script:OutputTransfer = 'smpte2084'
    if ($Profile -eq '8.4') {
        $script:DvCompatibilityId = 4
        $script:OutputTransfer = 'arib-std-b67'
    }
    if ($HdrMode -eq 'TrueHDR') {
        Write-Log ('目标 Profile 8.4 / HLG，NVIDIA TrueHDR 目标 {0} nit（HLG 参考显示 1000 nit）' -f $TrueHDRLuminance)
    }
    else { Write-Log ('目标 Profile {0} / {1}，SDR 白点映射至 {2} nit' -f $Profile, $script:OutputTransfer, $WhitePoint) }

    Write-Log '检查工具链…'
    $deployedTools = $null
    $deploymentConfig = Join-Path (Split-Path $PSScriptRoot -Parent) '.tools\tools.json'
    if (Test-Path -LiteralPath $deploymentConfig) {
        $deployment = Get-Content -LiteralPath $deploymentConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($deployment.schema -ne 1) { throw '不支持的部署配置，请重新运行 Deploy.ps1。' }
        $deployedTools = $deployment.tools
    }
    $script:Ffmpeg = Resolve-ToolPath -Name 'ffmpeg' -Candidates @($deployedTools.ffmpeg) -Hint '请先运行 deploy.bat，或安装 ffmpeg 并加入 PATH。'
    $script:Ffprobe = Resolve-ToolPath -Name 'ffprobe' -Candidates @($deployedTools.ffprobe) -Hint '请先运行 deploy.bat，ffprobe 应与 ffmpeg 一起安装。'
    $script:DoviTool = Resolve-ToolPath -Name 'dovi_tool' -Candidates @($DoviTool, $deployedTools.dovi_tool, (Join-Path $env:LOCALAPPDATA 'Programs\dovi_tool\dovi_tool.exe')) -Hint '请先运行 deploy.bat，或安装 dovi_tool（https://github.com/quietvoid/dovi_tool/releases）。'

    $requestedMp4Muxer = $Mp4Muxer
    $script:Mp4Muxer = ''
    if ($Format -eq 'mp4' -or $Format -eq 'both' -or $HdrMode -eq 'TrueHDR' -or $requestedMp4Muxer -or $deployedTools.mp4muxer -or
        (Test-Path -LiteralPath (Join-Path $env:USERPROFILE 'Desktop\1\dubi\mp4muxer.exe'))) {
        $script:Mp4Muxer = Resolve-ToolPath -Name 'mp4muxer' -Candidates @($requestedMp4Muxer, $deployedTools.mp4muxer, (Join-Path $env:USERPROFILE 'Desktop\1\dubi\mp4muxer.exe')) -Hint '请先运行 deploy.bat，或用 -Mp4Muxer 指定 Dolby mp4muxer 路径。'
    }

    Write-Log ('  ffmpeg   : {0}' -f $script:Ffmpeg) 'DIM'
    Write-Log ('  dovi_tool: {0}' -f $script:DoviTool) 'DIM'
    if ($script:Mp4Muxer) { Write-Log ('  mp4muxer : {0}' -f $script:Mp4Muxer) 'DIM' }

    $script:WorkRoot = Get-AsciiWorkRoot
    Write-Log ('  临时目录 : {0}' -f $script:WorkRoot) 'DIM'
    if ($Encoder -eq 'NVENC' -and $Profile -ne '8.4') { throw 'NVENC 当前仅支持 Profile 8.4 / HLG；8.1 / HDR10 基础层请使用 -Encoder x265。' }
    if ($Encoder -eq 'NVENC' -and $PSBoundParameters.ContainsKey('Crf')) { throw 'NVENC 质量模式请用 -NvencCq；需要 CRF 时使用 -Encoder x265。' }
    if ($Encoder -eq 'Auto' -and ($Profile -ne '8.4' -or $PSBoundParameters.ContainsKey('Crf'))) { $Encoder = 'x265' }
    if ($Encoder -ne 'x265') {
        $nvencProbe = Test-NvencSupport -Ffmpeg $script:Ffmpeg -IncludeLossless:($HdrMode -eq 'TrueHDR' -or $Lossless)
        if ($nvencProbe.Ready) { $Encoder = 'NVENC' }
        elseif ($Encoder -eq 'NVENC') { throw ('NVENC 10bit HEVC 初始化失败：' + $nvencProbe.Error) }
        else { Write-Log ('NVENC 不可用，Auto 使用 x265：' + $nvencProbe.Error) 'WARN'; $Encoder = 'x265' }
    }
    Write-Log ('  视频编码 : {0}' -f $Encoder) 'DIM'

    $inputItem = Get-Item -LiteralPath $InputPath
    $videoExtensions = @('.mkv', '.mp4', '.m4v', '.mov', '.ts', '.m2ts', '.mts', '.avi', '.wmv', '.flv', '.webm', '.vob', '.mxf', '.h265', '.hevc')
    $files = @()

    if ($inputItem.PSIsContainer) {
        $files = @(Get-ChildItem -LiteralPath $inputItem.FullName -File -ErrorAction SilentlyContinue |
            Where-Object { $videoExtensions -contains $_.Extension.ToLowerInvariant() } |
            Where-Object { $_.Name -notmatch '_DV(81|84)\.|_TrueHDR(_Ref)?_HDR10\.' } |
            Sort-Object Name)
        if ($files.Count -eq 0) { throw '文件夹里没有找到可处理的视频文件。' }
        Write-Log ('批量模式：找到 {0} 个文件' -f $files.Count) 'STEP'
    }
    else {
        $files = @($inputItem)
    }

    $totalWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $succeeded = @()
    $failed = @()

    foreach ($file in $files) {
        try {
            Convert-SingleFile -SourcePath $file.FullName
            $succeeded += $file.Name
        }
        catch {
            $failed += @{ Name = $file.Name; Error = $_.Exception.Message }
            Write-Log ('失败：{0} → {1}' -f $file.Name, $_.Exception.Message) 'ERR'
        }
    }

    $totalWatch.Stop()
    Write-Host ''
    Write-Log '================ 汇总 ================' 'STEP'
    Write-Log ('总用时：{0}' -f (Format-Elapsed $totalWatch.Elapsed))
    Write-Log ('成功：{0} 个' -f $succeeded.Count) 'OK'
    if ($failed.Count -gt 0) {
        Write-Log ('失败：{0} 个' -f $failed.Count) 'ERR'
        foreach ($f in $failed) { Write-Log ('  {0} → {1}' -f $f.Name, $f.Error) 'ERR' }
        $exitCode = 1
    }
    Write-Host ''
}
catch {
    Write-Host ''
    Write-Log ($_.Exception.Message) 'ERR'
    $exitCode = 1
}

exit $exitCode
