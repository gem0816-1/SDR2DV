"""Windows file/process fault injection; never substitutes for GPU/media tests."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

import numpy as np
from hdr_analysis import StreamingAnalysis,SceneDetector,make_shots,generator_config,generator_from_levels,frame_statistics
from cache_binding import new_binding,verify_binding
from task_io import stream_lines,json_command
from tui_runner import Backend,ConversionTask,subprocess_environment

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
SHELL=os.environ.get('SDR2DV_TEST_SHELL') or shutil.which('pwsh') or shutil.which('powershell')


def quoted(value):return "'"+str(value).replace("'","''")+"'"


@unittest.skipUnless(os.name=='nt' and SHELL,'Real Windows PowerShell required')
class WindowsReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='SDR2DV_reliability_')
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def script(self,code):
        path=self.root/(str(time.time_ns())+'.ps1')
        prefix="$ErrorActionPreference='Stop'\n. "+quoted(ROOT/'tests/Load-Core.ps1')+' '+quoted(HERE)+'\n'
        path.write_text(prefix+code,encoding='utf-8-sig')
        return path

    def run_ps(self,code,timeout=25):
        result=subprocess.run([SHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.script(code))],
            capture_output=True,timeout=timeout,env=subprocess_environment([SHELL]))
        self.assertEqual(result.returncode,0,result.stdout.decode(errors='replace')+'\n'+result.stderr.decode(errors='replace'))
        return result.stdout.decode('utf-8-sig',errors='replace')

    def transaction_code(self):
        return '''
$directory='''+quoted(self.root)+'''
$a=Join-Path $directory '中文 & [1].mp4';$b=Join-Path $directory 'second.mkv'
[IO.File]::WriteAllText($a,'OLD-A');[IO.File]::WriteAllText($b,'OLD-B')
$sa=Join-Path $directory 'new-a';$sb=Join-Path $directory 'new-b'
[IO.File]::WriteAllText($sa,'NEW-A');[IO.File]::WriteAllText($sb,'NEW-B')
$beforeA=Get-DeliveryHash $a;$beforeB=Get-DeliveryHash $b
$tx=New-OutputTransaction @(@{Path=$a},@{Path=$b}) -Overwrite
$deliveries=@(@{Path=$a;Source=$sa},@{Path=$b;Source=$sb})
'''

    def test_force_commit_second_file_locked_restores_old_sha256(self):
        self.run_ps(self.transaction_code()+'''
$locked=[IO.File]::Open($b,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
  $failed=$false
  try {Complete-OutputTransaction $tx $deliveries} catch {$failed=$true}
  if (-not $failed -or (Get-DeliveryHash $a) -ne $beforeA -or (Get-DeliveryHash $b) -ne $beforeB) {throw 'Old finished file damaged'}
  if ($tx.Journal.state -ne 'rolled_back') {throw 'Rollback not recorded'}
} finally {$locked.Dispose();Close-OutputTransaction $tx}
''')

    def test_force_copy_failure_and_full_disk_preserve_old_files(self):
        self.run_ps(self.transaction_code()+'''
function Assert-TaskDiskSpace {param($Paths,$RequiredBytes) throw 'INJECTED disk full'}
try {
  $failed=$false;try {Complete-OutputTransaction $tx $deliveries} catch {$failed=$_.Exception.Message -match 'disk full'}
  if (-not $failed -or (Get-DeliveryHash $a) -ne $beforeA -or (Get-DeliveryHash $b) -ne $beforeB) {throw 'Disk-full safety failed'}
} finally {Close-OutputTransaction $tx}
''')

    def test_cancellation_between_replacements_rolls_back_set(self):
        self.run_ps(self.transaction_code()+'''
$script:commitChecks=0
function Assert-TaskNotCancelled {
  if ($tx.Journal.state -eq 'committing') {
    $script:commitChecks++
    if ($script:commitChecks -eq 2) {throw (New-Object OperationCanceledException('injected cancel'))}
  }
}
try {
  $failed=$false;try {Complete-OutputTransaction $tx $deliveries} catch {$failed=$true}
  if (-not $failed -or $script:commitChecks -ne 2 -or (Get-DeliveryHash $a) -ne $beforeA -or (Get-DeliveryHash $b) -ne $beforeB) {throw 'Cancel rollback failed'}
} finally {Close-OutputTransaction $tx}
''')

    def test_rollback_blocked_keeps_backup_and_explicit_repair_restores_old_set(self):
        self.run_ps(self.transaction_code()+'''
$script:commitChecks=0;$script:blocker=$null
function Assert-TaskDiskSpace {
  param($Paths,$RequiredBytes)
  if ($tx.Journal.state -eq 'committing') {
    $script:commitChecks++
    if ($script:commitChecks -eq 2) {
      $script:blocker=[IO.File]::Open($a,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
      throw 'INJECTED late failure'
    }
  }
}
$directory=$tx.Directory
try {
  $failed=$false;try {Complete-OutputTransaction $tx $deliveries} catch {$failed=$_.Exception.Message -match '回滚未完成'}
  if (-not $failed -or -not $tx.RecoveryRequired -or (Get-DeliveryHash (Join-Path $directory '0.backup')) -ne $beforeA) {throw 'Rollback backup not retained'}
} finally {if ($script:blocker) {$script:blocker.Dispose()};Close-OutputTransaction $tx}
& '''+quoted(HERE/'Repair-Delivery.ps1')+''' -Directory $directory
if ((Get-DeliveryHash $a) -ne $beforeA -or (Get-DeliveryHash $b) -ne $beforeB) {throw 'Explicit repair failed'}
''')

    def test_successful_transaction_and_conflict_before_commit(self):
        self.run_ps(self.transaction_code()+'''
[IO.File]::WriteAllText($a,'EXTERNAL modification')
try {
  $failed=$false;try {Complete-OutputTransaction $tx $deliveries} catch {$failed=$_.Exception.Message -match '提交前'}
  if (-not $failed -or [IO.File]::ReadAllText($a) -ne 'EXTERNAL modification' -or (Get-DeliveryHash $b) -ne $beforeB) {throw 'External conflict lost'}
} finally {Close-OutputTransaction $tx}
$tx=New-OutputTransaction @(@{Path=$a},@{Path=$b}) -Overwrite
try {
  Complete-OutputTransaction $tx $deliveries
  if ([IO.File]::ReadAllText($a) -ne 'NEW-A' -or [IO.File]::ReadAllText($b) -ne 'NEW-B' -or -not $tx.Committed) {throw 'Delivery failed'}
} finally {Close-OutputTransaction $tx}
''')

    def test_two_real_processes_compete_for_same_output_even_with_force(self):
        target=self.root/'same.mp4';ready=self.root/'ready';stop=self.root/'stop'
        holder=self.script('$tx=New-OutputTransaction @(@{Path='+quoted(target)+'}) -Overwrite\n'
            'try {[IO.File]::WriteAllText('+quoted(ready)+",'ready');while (-not [IO.File]::Exists("+quoted(stop)+')) {Start-Sleep -Milliseconds 50}} finally {Close-OutputTransaction $tx}')
        child=subprocess.Popen([SHELL,'-NoProfile','-File',str(holder)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=subprocess_environment([SHELL]))
        try:
            self.wait_file(ready,child)
            self.run_ps("$failed=$false;try {$tx=New-OutputTransaction @(@{Path="+quoted(target)+"}) -Overwrite;Close-OutputTransaction $tx} catch {$failed=$_.Exception.Message -match '其他任务'};if (-not $failed) {throw 'Concurrent writer accepted'}")
        finally:stop.touch();child.wait(timeout=10)
        self.assertFalse(target.exists())
        self.run_ps('$tx=New-OutputTransaction @(@{Path='+quoted(target)+'}) -Overwrite;Close-OutputTransaction $tx')

    def wait_file(self,path,child):
        end=time.monotonic()+15
        while not path.exists() and child.poll() is None and time.monotonic()<end:time.sleep(.04)
        self.assertTrue(path.exists(),f'Worker did not reach checkpoint (exit={child.poll()})')

    def test_hard_kill_mid_commit_journal_blocks_rerun_then_explicit_recovery(self):
        ready=self.root/'ready';journal=self.root/'journal-path'
        code=self.transaction_code()+'''
$script:commitChecks=0
function Assert-TaskNotCancelled {}
function Assert-TaskDiskSpace {
  param($Paths,$RequiredBytes)
  if ($tx.Journal.state -eq 'committing') {
    $script:commitChecks++
    if ($script:commitChecks -eq 2) {
      [IO.File]::WriteAllText('''+quoted(journal)+''',$tx.Directory)
      [IO.File]::WriteAllText('''+quoted(ready)+''','ready')
      Start-Sleep -Seconds 120
    }
  }
}
Complete-OutputTransaction $tx $deliveries
'''
        child=subprocess.Popen([SHELL,'-NoProfile','-File',str(self.script(code))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=subprocess_environment([SHELL]))
        try:self.wait_file(ready,child)
        finally:child.kill();child.wait(timeout=10)
        stage=Path(journal.read_text(encoding='utf-8-sig'))
        self.assertEqual((self.root/'中文 & [1].mp4').read_text(),'NEW-A')
        self.run_ps("$blocked=$false;try {$tx=New-OutputTransaction @(@{Path="+quoted(self.root/'中文 & [1].mp4')+"}) -Overwrite;Close-OutputTransaction $tx} catch {$blocked=$_.Exception.Message -match 'Repair-Delivery'};if (-not $blocked) {throw 'Incomplete transaction ignored'}")
        self.run_ps('& '+quoted(HERE/'Repair-Delivery.ps1')+' -Directory '+quoted(stage))
        self.assertEqual((self.root/'中文 & [1].mp4').read_text(),'OLD-A')
        self.assertEqual((self.root/'second.mkv').read_text(),'OLD-B')
        self.assertFalse(stage.exists())

    def test_mkv_failure_and_hdr10_then_rpu_failure_publish_nothing(self):
        for index,(failure,expected,existing) in enumerate([('mkv','INJECTED MKV',False),('rpu','INJECTED RPU',False),('rpu','INJECTED RPU',True)]):
            folder=self.root/str(index);folder.mkdir()
            output=self.run_ps('& '+quoted(ROOT/'tests/Reproduce-Delivery.ps1')+' -Root '+quoted(HERE)+' -Directory '+quoted(folder)+' -Failure '+failure+(' -ExistingHDR10' if existing else ''))
            data=json.loads(output.strip().splitlines()[-1])
            self.assertIn(expected,data['error'])
            self.assertTrue(data['old_mp4_unchanged']);self.assertFalse(data['hdr10_published'])
            self.assertEqual(data['source'],'untouched source')

    def test_skip_is_not_converted_and_batch_names_are_stable(self):
        self.run_ps('''
$script:VideoExtensions=@('.mp4','.mkv','.mov')
$directory='''+quoted(self.root)+'''
foreach ($name in @('video.mp4','video.mkv','video_TrueHDR_realDolby_HDR10.mp4')) {[IO.File]::WriteAllText((Join-Path $directory $name),'test')}
$one=Get-InputBaseName (Get-Item -LiteralPath (Join-Path $directory 'video.mp4'))
$two=Get-InputBaseName (Get-Item -LiteralPath (Join-Path $directory 'video.mkv'))
if ($one -eq $two -or $one -ne 'video__src_mp4' -or $two -ne 'video__src_mkv') {throw 'Input name collision'}
if (-not (Test-GeneratedVideoName 'video_TrueHDR_realDolby_HDR10.mp4') -or (Test-GeneratedVideoName 'original.mp4')) {throw 'Batch filter failed'}
foreach ($invalid in @((('x'*256)+'.mp4'),'file.mp4:stream')) {
  $rejected=$false;try {$tx=New-OutputTransaction @(@{Path=(Join-Path $directory $invalid)}) -Overwrite;Close-OutputTransaction $tx} catch {$rejected=$true}
  if (-not $rejected) {throw 'Invalid/too-long output reached encoding'}
}
$OutputDir=$directory;$Format='mp4';$Profile='8.4';$HdrMode='Original';$script:Results=@()
$target=Join-Path $directory ($one+'_DV84.mp4');[IO.File]::WriteAllText($target,'OLD')
function Write-Log {param($Message,$Level)}
$status=Convert-SingleFile (Join-Path $directory 'video.mp4')
if ($status -ne 'Skipped' -or $script:Results[0].status -ne 'skipped') {throw 'Skip counted as conversion'}
''')

    def test_optional_event_log_denied_and_malformed_tui_records_are_nonfatal(self):
        self.run_ps('$EventPath='+quoted(self.root)+";Write-TaskEvent 'log' @{message='test'};Write-TaskEvent 'completed' @{outputs=@()};Write-Output 'continued'")
        task=ConversionTask(Backend(shell=SHELL),[],self.root/'events')
        task.events.write_text('bad json\n[]\n{"type":"log","data":{"message":"ok"}}\n',encoding='utf-8')
        self.assertEqual(task.read_events()[0]['data']['message'],'ok');self.assertIsNotNone(task.last_event_error)
        with patch.object(Path,'open',side_effect=PermissionError('injected')):self.assertEqual(task.read_events(),[])

    def test_sdr_metadata_aliases_produce_valid_actual_zscale_arguments(self):
        if not shutil.which('ffmpeg'):self.skipTest('Actual FFmpeg needed')
        self.run_ps('''
$script:fakeVideo=[pscustomobject]@{codec_type='video';codec_name='h264';width=16;height=16;pix_fmt='yuv420p';
    color_transfer='gamma22';color_primaries='bt709';color_space='bt709';color_range='tv';r_frame_rate='25/1';avg_frame_rate='25/1'}
function Get-MediaInfo {param($Path) return [pscustomobject]@{streams=@($script:fakeVideo);format=[pscustomobject]@{duration='1'}}}
foreach ($alias in @('gamma22','gamma28')) {
  $script:fakeVideo.color_transfer=$alias;$info=Get-IncomingVideoInfo 'test'
  $expected='bt470m';if ($alias -eq 'gamma28') {$expected='bt470bg'}
  if ($info.InTransfer -ne $expected) {throw 'Incorrect source transfer alias'}
  $filter='zscale=tin='+$info.InTransfer+':pin=bt709:min=bt709:rin=limited:p=bt2020:m=bt2020nc:t=smpte2084:npl=203:r=limited,format=yuv420p10le'
  Invoke-NativeQuiet -Exe '''+quoted(shutil.which('ffmpeg'))+''' -Arguments @('-v','error','-nostdin','-f','lavfi','-i','color=black:size=16x16:rate=1','-vf',$filter,'-frames:v','1','-f','null','-') -What 'actual SDR alias filter'
}
''')

    def test_stale_other_task_cancel_environment_does_not_cancel_cli_probe(self):
        if not shutil.which('ffmpeg'):self.skipTest('Actual FFmpeg needed')
        source=self.root/'source.mov';flag=self.root/'other-task-cancel';flag.touch()
        subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','color=black:size=32x32:rate=25:duration=0.12',
            '-c:v','libx264','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709',str(source)],
            capture_output=True,check=True,timeout=15)
        with patch.dict(os.environ,{'SDR2DV_CANCEL_FILE':str(flag)}):
            description=Backend(shell=SHELL).json_request(['-DescribeInput','-InputPath',str(source)])
            self.assertGreater(description['video_bitrate']['bps'],0)
        self.assertTrue(flag.exists())

    def test_stdout_stderr_flood_bounded_logs_and_failure_exit(self):
        code="import os,sys; data=b'x'*65536; [(os.write(1,data),os.write(2,data)) for _ in range(400)]; sys.exit(7)"
        output=self.run_ps('$script:CurrentWorkDir='+quoted(self.root)+'\n'+'''
$failed=$false
try {Invoke-NativeQuiet -Exe '''+quoted(sys.executable)+' -Arguments '+"@('-c',"+quoted(code)+''') -What 'flood'} catch {
  $failed=$_.Exception.Message -match '7'
  if ($_.Exception.Message.Length -gt 132000) {throw 'Unbounded error text'}
}
if (-not $failed) {throw 'Nonzero exit was lost'}
''',timeout=35)
        for log in self.root.glob('*.log'):self.assertLessEqual(log.stat().st_size,16*1024**2)

    def test_hard_kill_parent_reaps_owned_python_and_grandchild_only(self):
        ready=self.root/'pids.json'
        child_code=("import sys,time,subprocess,json;from pathlib import Path;time.sleep(.5);"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
            "Path(sys.argv[1]).write_text(json.dumps([__import__('os').getpid(),p.pid]));time.sleep(120)")
        wrapper=self.script('Invoke-NativeQuiet -Exe '+quoted(sys.executable)+" -Arguments @('-c',"+quoted(child_code)+','+quoted(ready)+") -What 'job test'")
        parent=subprocess.Popen([SHELL,'-NoProfile','-File',str(wrapper)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=subprocess_environment([SHELL]))
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        handles=[]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        try:
            self.wait_file(ready,parent)
            handles=[kernel.OpenProcess(0x100000,False,pid) for pid in json.loads(ready.read_text())]
            self.assertTrue(all(handles))
            parent.kill();parent.wait(timeout=10)
            for handle in handles:self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'Owned descendant survived')
            self.assertIsNone(other.poll())
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            other.terminate();other.wait()
            for handle in handles:
                if handle:kernel.CloseHandle(handle)

    def test_hard_kill_tui_parent_reaps_conversion_child(self):
        ready=self.root/'tui-child-pid'
        code=('import sys,time;from pathlib import Path;from tui_runner import Backend,ConversionTask;'
            'task=ConversionTask(Backend(),[],sys.argv[1],command_override=[sys.executable,"-c","import time;time.sleep(120)"]).start();'
            'Path(sys.argv[2]).write_text(str(task.process.pid));time.sleep(120)')
        parent=subprocess.Popen([sys.executable,'-c',code,str(self.root/'sessions'),str(ready)],
            env={**os.environ,'PYTHONPATH':str(HERE)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=None
        try:
            self.wait_file(ready,parent)
            handle=kernel.OpenProcess(0x100000,False,int(ready.read_text()))
            self.assertTrue(handle)
            parent.kill();parent.wait(timeout=10)
            self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'TUI orphaned its conversion child')
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            if handle:kernel.CloseHandle(handle)

    def test_gpu_resource_lock_rejects_second_process(self):
        ready=self.root/'gpu-ready';stop=self.root/'gpu-stop'
        script=self.script('$lock=Enter-GpuTaskLock;try {[IO.File]::WriteAllText('+quoted(ready)+",'ready');while (-not [IO.File]::Exists("+quoted(stop)+')) {Start-Sleep -Milliseconds 50}} finally {$lock.ReleaseMutex();$lock.Dispose()}')
        child=subprocess.Popen([SHELL,'-NoProfile','-File',str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=subprocess_environment([SHELL]))
        try:
            self.wait_file(ready,child)
            self.run_ps("$failed=$false;try {$lock=Enter-GpuTaskLock;$lock.ReleaseMutex();$lock.Dispose()} catch {$failed=$_.Exception.Message -match 'GPU 0'};if (-not $failed) {throw 'GPU contention accepted'}")
        finally:stop.touch();child.wait(timeout=10)

    def test_readonly_output_rejected_before_encoding_and_cleanup_refuses_junctions(self):
        self.run_ps('''
$path=Join-Path '''+quoted(self.root)+''' 'readonly.mp4'
[IO.File]::WriteAllText($path,'OLD');[IO.File]::SetAttributes($path,[IO.FileAttributes]::ReadOnly)
try {
  $failed=$false;try {$tx=New-OutputTransaction @(@{Path=$path}) -Overwrite;Close-OutputTransaction $tx} catch {$failed=$true}
  if (-not $failed -or [IO.File]::ReadAllText($path) -ne 'OLD') {throw 'Readonly target not protected'}
} finally {[IO.File]::SetAttributes($path,[IO.FileAttributes]::Normal)}
$owned=Join-Path '''+quoted(self.root)+''' 'owned';$other=Join-Path '''+quoted(self.root)+''' 'unrelated'
$null=New-Item -ItemType Directory -Path $owned,$other
[IO.File]::WriteAllText((Join-Path $other 'sentinel'),'keep')
$null=New-Item -ItemType Junction -Path (Join-Path $owned 'link') -Target $other
try {if (Test-OwnedTreeWithoutLinks $owned) {throw 'Unsafe recursive cleanup accepted'}}
finally {[IO.Directory]::Delete((Join-Path $owned 'link'))}
if ([IO.File]::ReadAllText((Join-Path $other 'sentinel')) -ne 'keep') {throw 'Unrelated cache damaged'}
''')

    def test_real_windows_acl_write_denial_preserves_existing_finished_file(self):
        self.run_ps('''
$directory=Join-Path '''+quoted(self.root)+''' 'denied'
$null=New-Item -ItemType Directory -Path $directory
$path=Join-Path $directory 'finished.mp4';[IO.File]::WriteAllText($path,'OLD')
$acl=Get-Acl -LiteralPath $directory
$original=$acl.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,[Security.AccessControl.FileSystemRights]::WriteData,[Security.AccessControl.AccessControlType]::Deny)
try {
  $null=$acl.AddAccessRule($rule);Set-Acl -LiteralPath $directory -AclObject $acl
  $failed=$false;try {$tx=New-OutputTransaction @(@{Path=$path}) -Overwrite;Close-OutputTransaction $tx} catch {$failed=$true}
  if (-not $failed -or [IO.File]::ReadAllText($path) -ne 'OLD') {throw 'Access denial damaged output or was ignored'}
} finally {
  $restore=Get-Acl -LiteralPath $directory;$restore.SetSecurityDescriptorSddlForm($original)
  Set-Acl -LiteralPath $directory -AclObject $restore
}
''')

    def test_diagnostic_paths_cannot_modify_source_or_unrelated_existing_files(self):
        self.run_ps('''
$InputPath=Join-Path '''+quoted(self.root)+''' 'source.mov'
[IO.File]::WriteAllText($InputPath,'SOURCE BYTES');$before=Get-DeliveryHash $InputPath
foreach ($kind in @('EventPath','ReportPath')) {
  $EventPath='';$ReportPath='';Set-Variable $kind $InputPath
  $failed=$false;try {Initialize-TaskSidecars} catch {$failed=$true}
  if (-not $failed -or $EventPath -or $ReportPath -or (Get-DeliveryHash $InputPath) -ne $before) {throw 'Source sidecar collision not blocked'}
}
$other=Join-Path '''+quoted(self.root)+''' 'unrelated.json'
[IO.File]::WriteAllText($other,'{"important":"user data"}')
$ReportPath=$other;$EventPath='';Initialize-TaskSidecars
if ($ReportPath -or [IO.File]::ReadAllText($other) -ne '{"important":"user data"}') {throw 'Unrelated JSON not preserved'}
$ReportPath=Join-Path '''+quoted(self.root)+''' 'result.json';Initialize-TaskSidecars
Save-TaskReport $ReportPath @{schema=1;results=@();exit_code=0}
Initialize-TaskSidecars
Save-TaskReport $ReportPath @{schema=1;results=@();exit_code=1}
if ((Get-Content -LiteralPath $ReportPath -Raw|ConvertFrom-Json).exit_code -ne 1) {throw 'Atomic report update failed'}
''')
        source=self.root/'real-source.mov';source.write_bytes(b'original source data')
        backend=Backend(shell=SHELL)
        for flag in ('-EventPath','-ReportPath'):
            with self.assertRaisesRegex(RuntimeError,'冲突'):
                backend.json_request(['-DescribeInput','-InputPath',str(source),flag,str(source)])
            self.assertEqual(source.read_bytes(),b'original source data')

    def test_deploy_zip_windows_aliases_and_links_rejected_before_extraction(self):
        cases=[['file:stream'],['CON.txt'],['COM¹.txt'],['LPT³'],['bad?.exe'],['folder./file'],['A.txt','a.txt'],['../escape'],['a','a/b'],['/absolute'],['link']]
        for i,names in enumerate(cases):
            archive=self.root/f'{i}.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for name in names:
                    entry=zipfile.ZipInfo(name)
                    if name=='link':entry.external_attr=0o120777<<16
                    z.writestr(entry,b'test')
        self.run_ps('. '+quoted(ROOT/'scripts/deploy-common.ps1')+'\n'+'''
foreach ($archive in Get-ChildItem -LiteralPath '''+quoted(self.root)+''' -Filter '*.zip') {
  $target=$archive.FullName+'.out';$failed=$false
  try {Expand-DeployZip $archive.FullName $target} catch {$failed=$true}
  if (-not $failed -or (Test-Path -LiteralPath $target)) {throw ('Unsafe ZIP extracted: '+$archive.Name)}
}
''')


class StreamingReferenceTests(unittest.TestCase):
    def test_streamed_shots_l1_and_light_levels_equal_old_cpu_reference(self):
        rng=np.random.default_rng(21);frames=[];detector=SceneDetector(8);aggregate=StreamingAnalysis()
        for i in range(120):
            rgb=rng.random((3,4,8))*(10 if i<40 else 900 if i<90 else 1800)
            stats,thumb=frame_statistics(np,rgb);frames.append(stats);detector.push(thumb)
            aggregate.push(stats,detector.cuts[-1])
        cuts=detector.finish();actual=aggregate.finish(cuts[-1]);shots=make_shots(frames,cuts)
        self.assertEqual(actual['shots'],shots)
        self.assertEqual(actual['max_cll_nits'],max(f['max_rgb_nits'] for f in frames))
        self.assertEqual(actual['max_fall_nits'],max(f['mean_max_rgb_nits'] for f in frames))
        self.assertEqual(actual['max_cie_y_nits'],max(f['max_y_nits'] for f in frames))
        self.assertEqual(actual['max_frame_mean_y_nits'],max(f['mean_y_nits'] for f in frames))
        self.assertEqual(actual['mean_y_nits'],sum(f['mean_y_nits'] for f in frames)/len(frames))
        for key,field in [('maxrgb','pixels_maxrgb'),('y','pixels_y')]:
            for threshold in (1000,1100):
                self.assertEqual(actual[f'max_frame_fraction_{key}_gt{threshold}'],max(f[f'{field}_gt{threshold}']/f['pixel_count'] for f in frames))
        self.assertEqual(generator_from_levels(actual['max_cll_nits'],actual['max_fall_nits'],shots),generator_config(frames,shots))

    def test_manual_cuts_final_frame_and_flash_match_old_reference(self):
        frames=[frame_statistics(np,np.ones((3,2,2))*v)[0] for v in [0]*10+[1000]+[0]*10+[50]*10]
        for cuts in ([0],[0,10,11,21,30]):
            aggregate=StreamingAnalysis()
            for i,stats in enumerate(frames):aggregate.push(stats,i-1 if i-1 in cuts else None)
            self.assertEqual(aggregate.finish(len(frames)-1 if len(frames)-1 in cuts else None)['shots'],make_shots(frames,list(cuts)))

    def test_long_single_shot_does_not_retain_frame_records(self):
        import tracemalloc
        stats,_=frame_statistics(np,np.full((3,2,2),203.))
        tracemalloc.start()
        try:
            aggregate=StreamingAnalysis()
            for _ in range(100000):aggregate.push(dict(stats))
            _,peak=tracemalloc.get_traced_memory()
            self.assertLess(peak,512*1024)
            summary=aggregate.finish();self.assertEqual(summary['shots'][0]['duration'],100000)
        finally:tracemalloc.stop()

    def test_cache_media_and_manifest_interpretation_are_bound(self):
        report=dict(parameters={'luminance':1000},brightness_mapping={'name':'neutral'},frames=30,input_frames=30)
        binding=report['cache_binding']=new_binding(report)
        tags={'sdr2dv_cache_id':binding['id'],'sdr2dv_config_sha256':binding['config_sha256']}
        with patch('cache_binding.media_tags',return_value=tags):
            self.assertEqual(verify_binding(report,'unused','unused'),'media_and_configuration_sha256')
            report['frames']=29
            with self.assertRaisesRegex(ValueError,'binding mismatch'):verify_binding(report,'unused','unused')
            report['frames']=30
            report['brightness_mapping']['name']='realdolby'
            with self.assertRaisesRegex(ValueError,'binding mismatch'):verify_binding(report,'unused','unused')
            report.pop('cache_binding')
            with self.assertRaisesRegex(ValueError,'removed'):verify_binding(report,'unused','unused')

    def test_real_child_stderr_flood_does_not_deadlock_stream_probe(self):
        code="import os,sys;os.write(2,b'e'*2**20);print('frame=1');sys.exit(9)"
        with self.assertRaisesRegex(RuntimeError,'Probe failed'):
            list(stream_lines([sys.executable,'-c',code]))
        with self.assertRaisesRegex(RuntimeError,'metadata.*limit'):
            json_command([sys.executable,'-c',"print(' '*5000000)"])

    def test_rpu_json_streaming_preserves_verification_and_rejects_bad_json(self):
        from rpu_audit import iter_level_records,verify
        stats=dict(min_pq=0,max_pq=3000,avg_pq=1500,max_rgb_nits=1000,mean_max_rgb_nits=70)
        config=generator_config([stats]*3,make_shots([stats]*3,[0]))
        records=[dict(frame=i,min_pq=0,max_pq=3000,avg_pq=1500) for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'levels.json';path.write_text(json.dumps(records),encoding='utf-8')
            self.assertEqual(verify(config,iter_level_records(path),[0]),verify(config,records,[0]))
            for value in ('[{},]','[{}','[] junk','[null]','{"frame":0}'):
                path.write_text(value,encoding='utf-8')
                with self.assertRaises(ValueError):list(iter_level_records(path))

    def test_long_rpu_export_verification_is_bounded_in_memory(self):
        import tracemalloc
        from rpu_audit import iter_level_records,verify
        count=100000;l1=dict(min_pq=0,max_pq=3000,avg_pq=1500)
        config={'shots':[dict(start=0,duration=count,metadata_blocks=[{'Level1':l1}])]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'levels.json'
            with path.open('w') as out:
                out.write('[')
                for frame in range(count):out.write((',' if frame else '')+json.dumps(dict(frame=frame,**l1)))
                out.write(']')
            tracemalloc.start()
            try:
                result=verify(config,iter_level_records(path),[0]);_,peak=tracemalloc.get_traced_memory()
                self.assertEqual(result['frames'],count);self.assertLess(peak,2*1024**2)
            finally:tracemalloc.stop()


if __name__=='__main__':unittest.main()
