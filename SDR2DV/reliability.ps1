#Requires -Version 5.1
# Output delivery and resource protection. This module never encodes media.
if (-not ('Sdr2dv.Native' -as [type])) { Add-Type -Path (Join-Path $PSScriptRoot 'process_io.cs') }

function Assert-NoReparsePath {
    param([string]$Path)
    $part=[IO.Path]::GetFullPath($Path)
    while ($part) {
        if (Test-Path -LiteralPath $part) {
            if ((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw ('路径包含重解析点；为避免锁路径别名或错误覆盖，请使用物理路径：'+$part)
            }
        }
        $parent=[IO.Path]::GetDirectoryName($part)
        if ($parent -eq $part) { break };$part=$parent
    }
}

function Initialize-TaskSidecars {
    # Runs before the first Write-Log: optional diagnostic paths must never
    # append to or truncate the source, scene list, cache manifest or tools.
    $protected=@()
    if ($InputPath) {
        $inputFull=[IO.Path]::GetFullPath($InputPath.Trim().Trim('"').Trim("'"))
        $protected+=@($inputFull)
        if ([IO.Directory]::Exists($inputFull)) {
            $protected+=@(Get-ChildItem -LiteralPath $inputFull -File | Where-Object {$_.Extension.ToLowerInvariant() -in $script:VideoExtensions} | ForEach-Object {$_.FullName})
        }
    }
    if ($SceneCutsFile) {$protected+=@([IO.Path]::GetFullPath($SceneCutsFile))}
    if ($TrueHDRCache) {$protected+=@(Join-Path ([IO.Path]::GetFullPath($TrueHDRCache)) 'truehdr-report.json')}
    $seen=@{}
    foreach ($entry in @(@{kind='EventPath';path=$EventPath},@{kind='ReportPath';path=$ReportPath},@{kind='CancelFile';path=$CancelFile})) {
        if (-not $entry.path) {continue}
        $full=[IO.Path]::GetFullPath($entry.path)
        if ($full -in $protected -or $seen.ContainsKey($full)) {
            $script:EventPath='';$script:ReportPath=''
            throw ('诊断/取消路径与输入或其他任务文件冲突，未写入：'+$full)
        }
        $seen[$full]=$true
    }
    foreach ($kind in @('EventPath','ReportPath')) {
        $path=Get-Variable -Name $kind -ValueOnly
        if (-not $path) {continue}
        try {
            $full=[IO.Path]::GetFullPath($path);Assert-NoReparsePath $full
            if ([IO.Path]::GetFileName($full).IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {throw '诊断文件名无效或包含 ADS'}
            if ([IO.Directory]::Exists($full)) {throw '路径是目录'}
            if ([IO.File]::Exists($full)) {
                $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                try {
                    if ($kind -eq 'ReportPath') {
                        if ($stream.Length -gt 16MB) {throw '已有报告过大，请选用新的报告路径'}
                        $reader=New-Object IO.StreamReader($stream,[Text.Encoding]::UTF8,$true)
                        try {$data=$reader.ReadToEnd() | ConvertFrom-Json} finally {$reader.Dispose()}
                        if ($data.schema -ne 1 -or -not ($data.PSObject.Properties.Name -contains 'results')) {throw '不是 SDR2DV 结果报告'}
                    } elseif ($stream.Length) {
                        $bytes=New-Object byte[] ([int][math]::Min(65536,$stream.Length))
                        $count=$stream.Read($bytes,0,$bytes.Length)
                        $first=([Text.Encoding]::UTF8.GetString($bytes,0,$count).TrimStart([char]0xfeff) -split "`n")[0]
                        $data=$first | ConvertFrom-Json
                        if (-not $data.type -or -not $data.timestamp -or -not $data.data) {throw '不是 SDR2DV 事件日志'}
                    }
                } finally {$stream.Dispose()}
            }
            if ($kind -eq 'ReportPath') {$script:ReportOriginalHash=Get-DeliveryHash $full}
            Set-Variable -Name $kind -Scope Script -Value $full
        } catch {
            Set-Variable -Name $kind -Scope Script -Value ''
            Write-Warning ('已禁用可选 '+$kind+'，保留已有文件：'+$path+'；'+$_.Exception.Message)
        }
    }
}

function Save-TaskReport {
    param([string]$Path,[object]$Data)
    $full=[IO.Path]::GetFullPath($Path);Assert-NoReparsePath $full
    if ((Get-DeliveryHash $full) -ne $script:ReportOriginalHash) {throw '报告路径被外部改动，未覆盖'}
    $writing=$full+'.'+[guid]::NewGuid().ToString('N')+'.partial'
    try {
        [IO.File]::WriteAllText($writing,($Data | ConvertTo-Json -Depth 24),(New-Object Text.UTF8Encoding($false)))
        if ($script:ReportOriginalHash) {[Sdr2dv.Native]::ReplaceJournal($writing,$full)} else {[IO.File]::Move($writing,$full)}
    } finally {if ([IO.File]::Exists($writing)) {[IO.File]::Delete($writing)}}
}

function Get-DeliveryHash {
    param([string]$Path)
    if (-not [IO.File]::Exists($Path)) { return $null }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try {return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','')}
    finally {$algorithm.Dispose();$stream.Dispose()}
}

function Enter-OutputLocks {
    param([string[]]$Paths)
    $handles=New-Object 'Collections.Generic.List[IDisposable]'
    try {
        foreach ($path in @($Paths | Sort-Object -Unique)) {
            $full=[IO.Path]::GetFullPath($path)
            Assert-NoReparsePath $full
            $parent=[IO.Path]::GetDirectoryName($full)
            if (-not [IO.Directory]::Exists($parent)) { throw ('输出目录不存在：'+$parent) }
            $algorithm=[Security.Cryptography.SHA256]::Create()
            try { $key=[BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFileName($full).ToUpperInvariant()))).Replace('-','') }
            finally { $algorithm.Dispose() }
            # Persistent tiny lock files must not be unlinked: deleting a lock
            # after closing it introduces a lock/unlink race with the next owner.
            $lockPath=Join-Path $parent ('.sdr2dv-lock-'+$key)
            Assert-NoReparsePath $lockPath
            try { $handle=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
            catch { throw ('输出正在被其他任务使用，或目录不允许创建锁：'+$full+'；'+$_.Exception.Message) }
            $handles.Add($handle)
        }
        return ,$handles
    } catch { foreach ($handle in $handles) { $handle.Dispose() };throw }
}

function Save-DeliveryJournal {
    param([hashtable]$Transaction)
    $json=$Transaction.Journal | ConvertTo-Json -Depth 8
    $path=Join-Path $Transaction.Directory 'delivery.json'
    $writing=Join-Path $Transaction.Directory 'delivery.writing'
    $stream=[IO.File]::Open($writing,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {
        $bytes=[Text.Encoding]::UTF8.GetBytes($json)
        $stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)
    } finally { $stream.Dispose() }
    if ([IO.File]::Exists($path)) { [Sdr2dv.Native]::ReplaceJournal($writing,$path) } else { [IO.File]::Move($writing,$path) }
}

function New-OutputTransaction {
    param([object[]]$Outputs,[switch]$Overwrite)
    $paths=@($Outputs | ForEach-Object {[IO.Path]::GetFullPath($_.Path)})
    foreach ($path in $paths) {
        $name=[IO.Path]::GetFileName($path)
        if ($name.Length -gt 255 -or $name.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {throw ('输出文件名过长或无效，未启动转换：'+$path)}
    }
    if (@($paths | Sort-Object -Unique).Count -ne $paths.Count) { throw '任务内输出路径发生碰撞。' }
    $tx=@{Locks=(Enter-OutputLocks $paths);Directory='';Committed=$false;RecoveryRequired=$false;Pending=@();Journal=@{schema=1;state='preparing';entries=@()}}
    try {
        foreach ($output in $Outputs) {
            $output.Path=[IO.Path]::GetFullPath($output.Path)
            if ([IO.Directory]::Exists($output.Path)) { throw ('输出位置是目录：'+$output.Path) }
            if ([IO.File]::Exists($output.Path) -and -not $Overwrite) { continue }
            if ([IO.File]::Exists($output.Path) -and ((Get-Item -LiteralPath $output.Path -Force).Attributes -band [IO.FileAttributes]::ReadOnly)) { throw ('已有输出只读，未启动转换：'+$output.Path) }
            $tx.Pending+=@($output)
        }
        if (-not $tx.Pending.Count) { return $tx }
        $parent=[IO.Path]::GetDirectoryName($tx.Pending[0].Path)
        foreach ($output in $tx.Pending) { if ([IO.Path]::GetDirectoryName($output.Path) -ne $parent) { throw '单次交付必须位于同一输出目录。' } }
        # An interrupted transaction is never silently overwritten by a rerun.
        foreach ($directory in @(Get-ChildItem -LiteralPath $parent -Directory -Force -Filter '.sdr2dv-txn-*')) {
            Assert-NoReparsePath $directory.FullName
            $journal=Join-Path $directory.FullName 'delivery.json'
            if (-not [IO.File]::Exists($journal)) { continue }
            $old=Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($old.state -in @('committed','rolled_back')) { continue }
            if (@($old.entries | Where-Object {$_.path -in $paths}).Count) {
                throw ('发现未完成交付，请先运行 Repair-Delivery.ps1 -Directory "'+$directory.FullName+'"：'+$old.state)
            }
        }
        $tx.Directory=Join-Path $parent ('.sdr2dv-txn-'+[guid]::NewGuid().ToString('N'))
        $null=New-Item -ItemType Directory -Path $tx.Directory
        foreach ($output in $tx.Pending) {
            $index=$tx.Journal.entries.Count
            $tx.Journal.entries+=@{path=$output.Path;original_sha256=(Get-DeliveryHash $output.Path);new_sha256=$null;
                staged=('{0}.staged' -f $index);backup=('{0}.backup' -f $index)}
        }
        Save-DeliveryJournal $tx
        return $tx
    } catch { Close-OutputTransaction $tx;throw }
}

function Assert-TaskDiskSpace {
    param([string[]]$Paths=$script:DiskWatchPaths,[long]$RequiredBytes=1GB)
    foreach ($path in @($Paths | Where-Object {$_} | Sort-Object -Unique)) {
        if ([Sdr2dv.Native]::AvailableBytes($path) -lt $RequiredBytes) { throw ('磁盘空间不足：'+$path+'；需保留 '+$RequiredBytes+' 字节。') }
    }
}

function Copy-DeliveryFile {
    param([string]$Source,[string]$Destination)
    $sourceStream=[IO.File]::Open($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $output=$null
    try {
        Assert-TaskDiskSpace @([IO.Path]::GetDirectoryName($Destination)) ($sourceStream.Length+1GB)
        $output=[IO.File]::Open($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $buffer=New-Object byte[] 1048576
        while (($count=$sourceStream.Read($buffer,0,$buffer.Length)) -gt 0) {
            Assert-TaskNotCancelled;Assert-TaskDiskSpace
            $output.Write($buffer,0,$count)
        }
        $output.Flush($true)
    } finally { if ($output) {$output.Dispose()};$sourceStream.Dispose() }
    if ((Get-DeliveryHash $Source) -ne (Get-DeliveryHash $Destination)) { throw '输出卷暂存复制 SHA256 不一致。' }
}

function Restore-DeliveryEntries {
    param([hashtable]$Transaction)
    $entries=@($Transaction.Journal.entries)
    [array]::Reverse($entries)
    foreach ($entry in $entries) {
        $backup=Join-Path $Transaction.Directory $entry.backup
        Assert-NoReparsePath $backup;Assert-NoReparsePath $entry.path
        $current=Get-DeliveryHash $entry.path
        if ($current -eq $entry.original_sha256) { continue }
        if ($null -ne $current -and $current -ne $entry.new_sha256) { throw ('回滚冲突，保留备份，不覆盖外部修改：'+$entry.path) }
        if ($entry.original_sha256) {
            if ((Get-DeliveryHash $backup) -ne $entry.original_sha256) { throw ('回滚备份缺失或损坏：'+$backup) }
            if ($current) { [IO.File]::Replace($backup,$entry.path,(Join-Path $Transaction.Directory ($entry.staged+'.undo'))) }
            else { [IO.File]::Move($backup,$entry.path) }
        } elseif ($current) {
            [IO.File]::Move($entry.path,(Join-Path $Transaction.Directory ($entry.staged+'.undo')))
        }
    }
    $Transaction.Journal.state='rolled_back';Save-DeliveryJournal $Transaction
    $Transaction.RecoveryRequired=$false
}

function Complete-OutputTransaction {
    param([hashtable]$Transaction,[object[]]$Deliveries)
    foreach ($entry in $Transaction.Journal.entries) {
        $delivery=@($Deliveries | Where-Object {$_.Path -eq $entry.path})
        if ($delivery.Count -ne 1) { throw ('缺少已校验的产物：'+$entry.path) }
        $staged=Join-Path $Transaction.Directory $entry.staged
        Copy-DeliveryFile $delivery[0].Source $staged
        $entry.new_sha256=Get-DeliveryHash $staged
    }
    $Transaction.Journal.state='prepared';Save-DeliveryJournal $Transaction
    try {
        Assert-TaskNotCancelled
        # Verify the complete old set before altering even the first file.
        foreach ($entry in $Transaction.Journal.entries) {
            Assert-NoReparsePath $entry.path
            if ((Get-DeliveryHash $entry.path) -ne $entry.original_sha256) { throw ('提交前输出被其他程序改动：'+$entry.path) }
        }
        $Transaction.RecoveryRequired=$true
        $Transaction.Journal.state='committing';Save-DeliveryJournal $Transaction
        foreach ($entry in $Transaction.Journal.entries) {
            Assert-TaskNotCancelled;Assert-TaskDiskSpace
            $staged=Join-Path $Transaction.Directory $entry.staged
            if ($entry.original_sha256) { [IO.File]::Replace($staged,$entry.path,(Join-Path $Transaction.Directory $entry.backup)) }
            else { [IO.File]::Move($staged,$entry.path) }
            if ((Get-DeliveryHash $entry.path) -ne $entry.new_sha256) { throw ('提交后 SHA256 校验失败：'+$entry.path) }
        }
        $Transaction.Journal.state='committed';Save-DeliveryJournal $Transaction
        $Transaction.Committed=$true;$Transaction.RecoveryRequired=$false
    } catch {
        $failure=$_
        if ($Transaction.RecoveryRequired) {
            try { Restore-DeliveryEntries $Transaction }
            catch { throw ($failure.Exception.Message+'；回滚未完成，备份与恢复日志保留在 '+$Transaction.Directory+'；'+$_.Exception.Message) }
        }
        throw $failure
    }
}

function Close-OutputTransaction {
    param([hashtable]$Transaction)
    try {
        if ($Transaction.Directory -and -not $Transaction.RecoveryRequired) {
            # Flat, invocation-owned staging only. Never recursively follow links.
            Assert-NoReparsePath $Transaction.Directory
            foreach ($item in @(Get-ChildItem -LiteralPath $Transaction.Directory -Force)) {
                if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    $item.Name -notmatch '^(delivery\.(json|writing)|\d+\.(staged|backup)(\.undo)?)$') { throw '交付暂存目录含非预期文件，保留以供检查。' }
            }
            foreach ($item in @(Get-ChildItem -LiteralPath $Transaction.Directory -Force -File)) { [IO.File]::Delete($item.FullName) }
            [IO.Directory]::Delete($Transaction.Directory,$false)
        }
    } catch { Write-Warning ('交付暂存保留：'+$Transaction.Directory+'；'+$_.Exception.Message) }
    finally { foreach ($handle in $Transaction.Locks) { $handle.Dispose() } }
}

function Enter-GpuTaskLock {
    # Same physical GPU across checkouts and Windows sessions. Fail rather than
    # probing a busy device and silently choosing a different encoding path.
    $mutex=New-Object Threading.Mutex($false,'Global\SDR2DV_GPU_0')
    $owned=$false
    try {
        try {$owned=$mutex.WaitOne(0)} catch [Threading.AbandonedMutexException] {$owned=$true}
        if (-not $owned) { throw 'GPU 0 正由另一 SDR2DV 任务使用，请等待该任务结束。' }
        return $mutex
    } catch {$mutex.Dispose();throw}
}

function Test-GeneratedVideoName {
    param([string]$Name)
    return ($Name -match '(?i)(_DV(81|84)|_TrueHDR(_Ref|_Natural|_realDolby)?_HDR10)\.(mp4|mkv)$')
}

function Get-InputBaseName {
    param([IO.FileInfo]$Source)
    $peers=@(Get-ChildItem -LiteralPath $Source.DirectoryName -File | Where-Object {
        $_.BaseName -eq $Source.BaseName -and $_.Extension.ToLowerInvariant() -in $script:VideoExtensions -and
        -not (Test-GeneratedVideoName $_.Name) })
    if ($peers.Count -gt 1) {
        $name=$Source.BaseName+'__src_'+$Source.Extension.TrimStart('.').ToLowerInvariant()
        $conflict=@(Get-ChildItem -LiteralPath $Source.DirectoryName -File | Where-Object {$_.BaseName -eq $name -and $_.Extension.ToLowerInvariant() -in $script:VideoExtensions})
        if ($conflict.Count) {
            $algorithm=[Security.Cryptography.SHA256]::Create()
            try { $name+='_'+[BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Source.Name.ToUpperInvariant()))).Replace('-','').Substring(0,16) }
            finally {$algorithm.Dispose()}
        }
        return $name
    }
    return $Source.BaseName
}

function Get-MkvSubtitlePlan {
    param([hashtable]$Info)
    $arguments=@();$index=0
    foreach ($stream in $Info.SubtitleStreams) {
        $codec=[string]$stream.codec_name
        if ($codec -in @('mov_text','text')) {
            $arguments+=@(('-c:s:{0}' -f $index),'srt')
            Write-Log ('字幕 {0}：{1} → SubRip；保留文本/时间/语言，容器样式无法原样表示。' -f ($index+1),$codec) 'WARN'
        } elseif ($codec -notin @('subrip','ass','ssa','webvtt','hdmv_pgs_subtitle','dvd_subtitle','dvb_subtitle')) {
            throw ('MKV 不支持此字幕复制路径：'+$codec+'；请先明确转换字幕，不会在编码后丢弃字幕。')
        }
        $flags=@($stream.disposition.PSObject.Properties | Where-Object {$_.Value -eq 1} | ForEach-Object {$_.Name})
        # Disable FFmpeg's automatic first-subtitle default assignment when the
        # source has none. Keep forced/hearing-impaired/etc. on each track.
        $disposition='0';if ($flags.Count) {$disposition=$flags -join '+'}
        $arguments+=@(('-disposition:s:{0}' -f $index),$disposition)
        $index++
    }
    foreach ($stream in $Info.AttachmentStreams) {
        if (-not $stream.tags.filename -or -not $stream.tags.mimetype) { throw 'MKV 附件缺少文件名或 MIME 类型，无法保证字体完整封装。' }
    }
    return ,$arguments
}

function Test-OwnedTreeWithoutLinks {
    param([string]$Path)
    $stack=New-Object 'Collections.Generic.Stack[string]'
    $stack.Push([IO.Path]::GetFullPath($Path))
    while ($stack.Count) {
        $directory=$stack.Pop()
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {
            $attributes=[IO.File]::GetAttributes($entry)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) {return $false}
            if ($attributes -band [IO.FileAttributes]::Directory) {$stack.Push($entry)}
        }
    }
    return $true
}

function Test-MkvSubtitles {
    param([hashtable]$Info,[object]$Media)
    $subtitles=@($Media.streams | Where-Object {$_.codec_type -eq 'subtitle'})
    $attachments=@($Media.streams | Where-Object {$_.codec_type -eq 'attachment'})
    if ($subtitles.Count -ne $Info.SubtitleStreams.Count -or $attachments.Count -ne $Info.AttachmentStreams.Count) { throw 'MKV 字幕/附件数量不一致。' }
    for ($i=0;$i -lt $subtitles.Count;$i++) {
        $source=$Info.SubtitleStreams[$i];$actual=$subtitles[$i]
        $expected=$source.codec_name;if ($expected -in @('mov_text','text')) {$expected='subrip'}
        if ($actual.codec_name -ne $expected -or
            ($source.tags.language -and $source.tags.language -ne $actual.tags.language) -or
            ($source.tags.title -and $source.tags.title -ne $actual.tags.title)) { throw 'MKV 字幕编码、语言或标题不一致。' }
        foreach ($flag in $source.disposition.PSObject.Properties) {
            if ([int]$flag.Value -ne [int]$actual.disposition.($flag.Name)) { throw ('MKV 字幕标记不一致：'+$flag.Name) }
        }
    }
    for ($i=0;$i -lt $attachments.Count;$i++) {
        $source=$Info.AttachmentStreams[$i];$actual=$attachments[$i]
        if ($source.tags.filename -ne $actual.tags.filename -or $source.tags.mimetype -ne $actual.tags.mimetype -or
            -not $source.extradata_hash -or $source.extradata_hash -ne $actual.extradata_hash) { throw 'MKV 字体/附件的名称、类型或内容 SHA256 不一致。' }
    }
}
