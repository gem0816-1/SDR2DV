#Requires -Version 5.1
# Core services invoked by SDR2DV.ps1. The TUI only calls that entrypoint.

function Get-ConfiguredTool {
    param([string]$Name)
    $path = Join-Path (Split-Path $PSScriptRoot -Parent) '.tools\tools.json'
    if (Test-Path -LiteralPath $path) {
        $configuration = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($configuration.schema -eq 1) { return [string]$configuration.tools.$Name }
    }
    return ''
}

function Get-CorePython {
    $candidates = @($CorePython)
    if ($HdrMode -eq 'TrueHDR') { $candidates += @($script:TrueHDRPython, $TrueHDRPython, (Get-ConfiguredTool 'truehdr_python')) }
    $candidates += @((Join-Path $PSScriptRoot '.venv-tui\Scripts\python.exe'),
        (Get-ConfiguredTool 'core_python'), (Join-Path $PSScriptRoot '.venv-truehdr\Scripts\python.exe'))
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw '缺少项目内 Python 运行环境，请先运行 deploy.bat；也可用 -CorePython 指定已有环境。'
}

function Write-TaskEvent {
    param([string]$Type, [hashtable]$Data)
    if (-not $EventPath) { return }
    $record = @{ type=$Type; timestamp=[DateTime]::UtcNow.ToString('o'); data=$Data }
    [IO.File]::AppendAllText([IO.Path]::GetFullPath($EventPath),
        (($record | ConvertTo-Json -Depth 12 -Compress) + "`n"), (New-Object Text.UTF8Encoding($false)))
}

function Save-CoreFailureDiagnostics {
    param([string]$WorkDir, [object]$Failure)
    try {
        $parent = ''
        if ($EventPath) { $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($EventPath)) }
        elseif ($ReportPath) { $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ReportPath)) }
        else { $parent = Join-Path (Split-Path $PSScriptRoot -Parent) '.tools\failures' }
        $destination = Join-Path $parent ('diagnostics_' + [IO.Path]::GetFileName($WorkDir))
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        $utf8 = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText((Join-Path $destination 'failure.txt'),
            ($Failure.Exception.Message + "`r`n" + $Failure.ScriptStackTrace), $utf8)
        # Only small text diagnostics from this task. No media or external cache.
        foreach ($file in (Get-ChildItem -LiteralPath $WorkDir -File)) {
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                ($file.Extension -ne '.log' -and $file.Name -notin @('truehdr-report.json','hdr-analysis.json'))) { continue }
            $stream = [IO.File]::OpenRead($file.FullName)
            try {
                $length = [int][math]::Min(1MB, $stream.Length)
                $null = $stream.Seek(-$length, [IO.SeekOrigin]::End)
                $bytes = New-Object byte[] $length
                $offset=0
                while ($offset -lt $length) {
                    $read=$stream.Read($bytes,$offset,$length-$offset)
                    if ($read -eq 0) { break }
                    $offset+=$read
                }
                [IO.File]::WriteAllBytes((Join-Path $destination $file.Name),$bytes)
            }
            finally { $stream.Dispose() }
        }
        Write-TaskEvent -Type 'diagnostics' -Data @{path=$destination}
        Write-Log ('本次错误诊断已保存：{0}' -f $destination) 'WARN'
        return $destination
    }
    catch { Write-Log ('错误诊断未能完整保存：{0}' -f $_.Exception.Message) 'WARN'; return '' }
}

function Assert-TaskNotCancelled {
    if ($CancelFile -and (Test-Path -LiteralPath $CancelFile -PathType Leaf)) {
        throw (New-Object OperationCanceledException('已取消本次任务。'))
    }
}

function Invoke-CoreHelper {
    param([string]$Module, [string[]]$Arguments, [string]$WorkDir, [string]$Label, [double]$Duration=0)
    Invoke-MediaWithProgress -Exe $script:CorePython -ArgumentPrefix @('-B',(Join-Path $PSScriptRoot $Module)) `
        -Arguments $Arguments -WorkDir $WorkDir -LogBaseName ([IO.Path]::GetFileNameWithoutExtension($Module)) `
        -What $Label -Label $Label -TotalSeconds $Duration
}

function Get-CoreVideoRate {
    param([string]$Path, [string]$WorkDir, [switch]$Packets, [switch]$Plan)
    $resultPath = Join-Path $WorkDir ('rate_' + [guid]::NewGuid().ToString('N') + '.json')
    $arguments = @($Path,'--ffprobe',$script:Ffprobe,'--output',$resultPath)
    if ($Packets) { $arguments += '--packets' }
    if ($Plan -and $HdrMode -eq 'Original') { $arguments += '--check-timing' }
    if ($Plan) {
        $maximum=0; if ($script:BoundOptions.ContainsKey('MaxVideoBitrate')) { $maximum=$MaxVideoBitrate }
        $arguments+=@('--strategy',$BitrateMode,'--custom-kbps',[string]$VideoBitrate,'--max-kbps',[string]$maximum,
            '--platform-target',[string]$script:Defaults.platform_target_kbps,'--platform-max',[string]$script:Defaults.platform_max_kbps)
    }
    Invoke-CoreHelper -Module 'media_probe.py' -Arguments $arguments -WorkDir $WorkDir -Label '视频流码率检测'
    return (Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Test-CoreVideoOutput {
    param([hashtable]$Info,[object]$Record,[int]$ExpectedFrames)
    $video=@($Record.media.streams | Where-Object {$_.codec_type -eq 'video'})[0]
    $sar=[string]$Info.SourceStream.sample_aspect_ratio
    $outSar=[string]$video.sample_aspect_ratio
    if ($sar -in @('','N/A','0:1')) { $sar='1:1' }
    if ($outSar -in @('','N/A','0:1')) { $outSar='1:1' }
    $fps=ConvertTo-FractionValue ([string]$video.avg_frame_rate)
    if ($video.width -ne $Info.Width -or $video.height -ne $Info.Height -or $sar -ne $outSar -or
        [math]::Abs($fps-$Info.Fps) -gt .001 -or $Record.video_bitrate.packets -ne $ExpectedFrames) {
        throw '最终视频的尺寸、画面比例、帧率或完整帧数与输入不一致。'
    }
    $duration=$ExpectedFrames/$Info.Fps
    if ([math]::Abs($Record.video_bitrate.duration_seconds-$duration) -ge .1) { throw '最终视频流时长偏差达到 0.1 秒。' }
}

function Test-CoreAudio {
    param([hashtable]$Info,[string]$Path,[string]$WorkDir)
    $resultPath = Join-Path $WorkDir ('audio_' + [guid]::NewGuid().ToString('N') + '.json')
    $arguments = @($Info.Path,$Path,'--ffmpeg',$script:Ffmpeg,'--ffprobe',$script:Ffprobe,'--output',$resultPath)
    if ($Mp4AudioCodec -in @('eac3','ac3')) { $arguments += '--allow-lossy' }
    Invoke-CoreHelper -Module 'audio_audit.py' -Arguments $arguments -WorkDir $WorkDir -Label '音频完整性校验' -Duration (2*$Info.Duration)
    $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.verification -eq 'passed') { Write-Log ('音频验证通过：{0} 条；采样值、采样数与音画偏移已检查；平台播放未验证。' -f @($result.tracks).Count) 'OK' }
    else { Write-Log '显式有损音频：仅参数、时间与时长通过，未宣称采样一致；平台播放未验证。' 'WARN' }
    return $result
}

function Get-FinalHdrAnalysis {
    param([string]$Path,[hashtable]$Info,[string]$WorkDir)
    $resultPath=Join-Path $WorkDir 'hdr-analysis.json'
    $generatorPath=Join-Path $WorkDir 'generator.json'
    $arguments=@($Path,'--output',$resultPath,'--generator',$generatorPath,'--ffmpeg',$script:Ffmpeg,
        '--ffprobe',$script:Ffprobe,'--fps',$Info.FpsText,'--active-area',$ActiveArea)
    if ($SceneCutsFile) { $arguments += @('--cuts-file',$SceneCutsFile) }
    Invoke-CoreHelper -Module 'hdr_analysis.py' -Arguments $arguments -WorkDir $WorkDir -Label '逐帧 HDR / 镜头分析' -Duration $Info.Duration
    $analysis=Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Log ('逐帧分析 {0} 帧 / {1} 镜头；MaxCLL={2:0.0} / MaxFALL={3:0.0} nit（线性 MaxRGB）' -f
        $analysis.frames,@($analysis.shots).Count,$analysis.max_cll_nits,$analysis.max_fall_nits)
    if ($analysis.compatibility_clamped_shots -gt 0) {
        Write-Log ('{0} 个镜头的编码 L1 受 dovi_tool CMv4 兼容范围约束；原始测量与编码值分别记录。未生成创作性 Trim。' -f $analysis.compatibility_clamped_shots) 'WARN'
    }
    if ($HdrMode -eq 'TrueHDR' -and $BrightnessMapping -eq 'realDolby' -and $BrightnessStrength -gt 0) {
        Write-Log ('realDolby 允许参考超白；最终像素 MaxRGB {0:0.0} nit / CIE Y {1:0.0} nit（1000 nit HLG 测量参考），不把参考值当成输出峰值。' -f $analysis.max_cll_nits, $analysis.max_cie_y_nits)
    }
    elseif ($analysis.max_cll_nits -gt 1010) { Write-Log ('最终重建像素 MaxRGB 峰值 {0:0.0} nit 超过 1000 nit 参考目标；如实记录超调，不将目标元数据冒充像素测量。' -f $analysis.max_cll_nits) 'WARN' }
    return $analysis
}

function Get-CoreCapabilities {
    $ffmpeg = Get-ConfiguredTool 'ffmpeg'
    if (-not $ffmpeg) { $command=Get-Command ffmpeg -ErrorAction SilentlyContinue; if ($command) { $ffmpeg=$command.Source } }
    $ffprobe = Get-ConfiguredTool 'ffprobe'
    if (-not $ffprobe) { $command=Get-Command ffprobe -ErrorAction SilentlyContinue; if ($command) { $ffprobe=$command.Source } }
    $muxer = Get-ConfiguredTool 'mp4muxer'
    if (-not $muxer) { $muxer=Join-Path $env:USERPROFILE 'Desktop\1\dubi\mp4muxer.exe' }
    $sdkPython=Get-ConfiguredTool 'truehdr_python'
    if ($TrueHDRPython) { $sdkPython=$TrueHDRPython }
    if (-not $sdkPython) { $sdkPython=Join-Path $PSScriptRoot '.venv-truehdr\Scripts\python.exe' }
    $ready=$false; $sdkError='TrueHDR 环境未部署'
    if (Test-Path -LiteralPath $sdkPython -PathType Leaf) {
        $preference=$ErrorActionPreference
        try {
            $ErrorActionPreference='Continue'
            $sdkOutput = & $sdkPython -B (Join-Path $PSScriptRoot 'truehdr_backend.py') --check 2>&1
            $ready=($LASTEXITCODE -eq 0)
            if ($ready) { $sdkError='' } else { $sdkError=$sdkOutput -join "`n" }
        } finally { $ErrorActionPreference=$preference }
    }
    $nvenc=@{Ready=$false;Error='FFmpeg 未就绪'}
    $nvencLossless=$nvenc
    $x265=$false
    if ($ffmpeg) {
        $nvenc=Test-NvencSupport -Ffmpeg $ffmpeg
        if ($nvenc.Ready) { $nvencLossless=Test-NvencSupport -Ffmpeg $ffmpeg -IncludeLossless }
        $encoderText=& $ffmpeg -hide_banner -encoders 2>&1
        $x265=(($encoderText -join "`n") -match '\blibx265\b')
    }
    return @{ schema=1; defaults=$script:Defaults; ffmpeg=$ffmpeg; ffprobe=$ffprobe;
        mp4muxer=$muxer; muxer_ready=(Test-Path -LiteralPath $muxer -PathType Leaf);
        truehdr_ready=$ready; truehdr_error=$sdkError; truehdr_python=$sdkPython;
        nvenc_ready=$nvenc.Ready; nvenc_error=$nvenc.Error; x265_ready=$x265;
        nvenc_lossless_ready=$nvencLossless.Ready; nvenc_lossless_error=$nvencLossless.Error;
        mappings=@('Neutral','Natural','Reference','realDolby'); natural_status=$script:Defaults.natural_status;
        realdolby_status='paired_reference_user_visual_accepted';
        bitrate_modes=@('Source','Platform','Custom','Quality'); profiles=@('8.4'); peak_nits=1000 }
}
