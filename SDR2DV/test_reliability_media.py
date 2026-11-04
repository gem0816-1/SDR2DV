"""Opt-in actual FFmpeg/dovi_tool and optional NVIDIA pipeline regressions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from media_probe import probe
from test_reliability import HERE,ROOT,SHELL
from tui_runner import Backend,ConversionTask,subprocess_environment


@unittest.skipUnless(os.environ.get('SDR2DV_TEST_RELIABILITY_MEDIA')=='1','Enable actual conversion fixtures explicitly')
class ActualReliabilityMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='SDR2DV_media_reliability_')
        self.root=Path(self.temp.name)/'中文 [目录] & QA';self.root.mkdir()
        self.scratch=Path(self.temp.name)/'scratch'
        self.evidence=ROOT/'validation/reliability_20261104'/('ps51' if Path(SHELL).name.lower()=='powershell.exe' else 'ps7')/self._testMethodName
        self.evidence.mkdir(parents=True,exist_ok=True)
        self.addCleanup(self.temp.cleanup)
        self.scratch.mkdir()
        self.srt=self.root/'中文 [字幕].srt'
        self.srt.write_text('1\n00:00:00,000 --> 00:00:00,800\n字幕 text\n',encoding='utf-8')

    def ffmpeg(self,*args):
        subprocess.run(['ffmpeg','-v','error','-nostdin','-n',*map(str,args)],capture_output=True,check=True,timeout=30)

    def fixture(self,extension='mov',subtitles=False,attachments=False,sar='1/1'):
        target=self.root/('中文 & [1].'+extension)
        arguments=['-f','lavfi','-i','testsrc2=size=320x180:rate=25:duration=1',
            '-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=1']
        if subtitles:arguments+=['-i',self.srt,'-i',self.srt]
        arguments+=['-map','0:v','-map','1:a']
        if subtitles:arguments+=['-map','2:s','-map','3:s']
        arguments+=['-vf','setsar='+sar+',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
            '-c:v','libx264','-preset','ultrafast','-crf','18','-pix_fmt','yuv420p',
            '-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709',
            '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv','-c:a','pcm_s24le']
        if subtitles:
            arguments+=['-c:s','ass' if attachments else 'mov_text','-metadata:s:s:0','language=chi',
                '-metadata:s:s:0','title=中文标题','-metadata:s:s:1','language=eng','-disposition:s:0','0','-disposition:s:1','default+forced']
        if attachments:
            font=Path(os.environ['SystemRoot'])/'Fonts/arial.ttf'
            if not font.is_file():self.skipTest('No Arial font fixture available')
            arguments+=['-attach',font,'-metadata:s:t','mimetype=application/x-truetype-font','-metadata:s:t','filename=arial.ttf']
        self.ffmpeg(*arguments,target)
        return target

    def convert(self,source,name,extra=(),expected=0):
        output=self.root/name;output.mkdir(exist_ok=True)
        report=self.evidence/(name+'-result.json');events=self.evidence/(name+'-'+str(time.time_ns())+'-events.jsonl')
        self.last_event_path=events
        command=[SHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'SDR2DV.ps1'),
            '-InputPath',str(source),'-OutputDir',str(output),'-ReportPath',str(report),'-EventPath',str(events),*extra]
        start=time.monotonic()
        result=subprocess.run(command,capture_output=True,timeout=180,
            env={**subprocess_environment(command),'TEMP':str(self.scratch)})
        (self.evidence/(name+'-console.log')).write_bytes(result.stdout+result.stderr)
        (self.evidence/(name+'-command.json')).write_text(json.dumps(dict(command=command,exit_code=result.returncode,
            elapsed_seconds=time.monotonic()-start),ensure_ascii=False,indent=2),encoding='utf-8')
        self.assertEqual(result.returncode,expected,(result.stdout+result.stderr).decode('utf-8',errors='replace')[-16000:])
        return output,json.loads(report.read_text(encoding='utf-8-sig'))

    def test_pq_x265_source_two_pass_mov_text_to_mkv_and_force_skip(self):
        source=self.fixture(subtitles=True,sar='4/3');original=hashlib.sha256(source.read_bytes()).hexdigest()
        output,result=self.convert(source,'pq',['-Format','both','-Encoder','x265','-Preset','ultrafast'])
        self.assertEqual(result['results'][0]['status'],'converted')
        targets=[Path(x['path']) for x in result['results'][0]['outputs']]
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in targets}
        mkv=next(p for p in targets if p.suffix=='.mkv')
        media=probe(mkv);subs=[s for s in media['streams'] if s['codec_type']=='subtitle']
        self.assertEqual([s['codec_name'] for s in subs],['subrip','subrip'])
        self.assertEqual([s['tags']['language'] for s in subs],['chi','eng'])
        self.assertEqual([s['disposition']['default'] for s in subs],[0,1])
        source_subs=[s for s in probe(source)['streams'] if s['codec_type']=='subtitle']
        self.assertEqual(subs[1]['disposition']['forced'],source_subs[1]['disposition']['forced'])
        _,skipped=self.convert(source,'pq',['-Format','both','-Encoder','x265','-Preset','ultrafast'])
        self.assertEqual(skipped['results'][0]['status'],'skipped')
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in targets})
        _,overwritten=self.convert(source,'pq',['-Format','both','-Encoder','x265','-Preset','ultrafast','-Force'])
        self.assertEqual(overwritten['results'][0]['status'],'converted')
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)

    def test_hlg_x265_ass_tracks_and_font_attachment(self):
        source=self.fixture(extension='mkv',subtitles=True,attachments=True)
        _,result=self.convert(source,'hlg',['-Profile','8.4','-Format','both','-Encoder','x265','-Preset','ultrafast'])
        output=next(Path(x['path']) for x in result['results'][0]['outputs'] if x['path'].endswith('.mkv'))
        media=probe(output)
        self.assertEqual([s['codec_name'] for s in media['streams'] if s['codec_type']=='subtitle'],['ass','ass'])
        self.assertEqual(len([s for s in media['streams'] if s['codec_type']=='attachment']),1)
        self.assertEqual(media['streams'][0]['color_transfer'],'arib-std-b67')

    def test_invalid_unknown_colour_nonzero_pts_vfr_and_zero_input_fail_without_overwrite(self):
        source=self.fixture()
        unknown=self.root/'unknown.mov'
        self.ffmpeg('-i',source,'-map','0:v:0','-an','-vf',
            'setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown',
            '-c:v','libx264','-preset','ultrafast',unknown)
        nonzero=self.root/'nonzero.mov'
        self.ffmpeg('-i',source,'-map','0:v:0','-an','-vf','setpts=PTS+1/TB','-c:v','libx264',
            '-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709',nonzero)
        vfr=self.root/'vfr.mov'
        self.ffmpeg('-i',source,'-map','0:v:0','-an','-vf','select=not(eq(mod(n\\,3)\\,1))',
            '-fps_mode','vfr','-c:v','libx264','-x264-params','colorprim=bt709:transfer=bt709:colormatrix=bt709',vfr)
        empty=self.root/'empty.mov';empty.touch()
        broken=self.root/'broken.mov';broken.write_bytes(b'corrupt movie data')
        for index,invalid in enumerate((unknown,nonzero,vfr,empty,broken)):
            directory=self.root/f'invalid{index}';directory.mkdir()
            old=directory/(invalid.stem+'_DV81.mp4');old.write_bytes(b'OLD validated finished file')
            before=hashlib.sha256(old.read_bytes()).hexdigest()
            _,result=self.convert(invalid,directory.name,['-Encoder','x265','-Preset','ultrafast','-Force'],expected=1)
            self.assertEqual(result['results'][0]['status'],'failed')
            self.assertFalse(result['results'][0]['outputs'])
            self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),before)

    @unittest.skipUnless(os.environ.get('SDR2DV_TEST_NVENC')=='1','NVIDIA TrueHDR/NVENC run explicitly enabled')
    def test_actual_truehdr_nvenc_hdr10_cache_binding_and_reuse(self):
        source=self.fixture()
        output,result=self.convert(source,'truehdr',['-HdrMode','TrueHDR','-Bilibili','-Format','both',
            '-Encoder','NVENC','-KeepHDR10','-KeepTemp'])
        events=[json.loads(line) for line in self.last_event_path.read_text().splitlines()]
        work=Path(next(e['data']['path'] for e in events if e['type']=='workspace'))
        manifest=json.loads((work/'truehdr-report.json').read_text())
        self.assertEqual(manifest['frames'],25);self.assertTrue(manifest['cache_binding'])
        shutil.copyfile(work/'truehdr-report.json',self.evidence/'cache-manifest.json')
        shutil.copyfile(work/'hdr-analysis.json',self.evidence/'hdr-analysis.json')
        self.assertNotIn('frame_statistics',json.loads((work/'hdr-analysis.json').read_text()))
        _,reused=self.convert(source,'reuse',['-HdrMode','TrueHDR','-Bilibili','-Encoder','NVENC',
            '-TrueHDRCache',str(work),'-KeepHDR10'])
        self.assertEqual(reused['results'][0]['delivery'],'committed')
        self.assertTrue((work/'truehdr_hlg.mkv').exists())
        # Test-owned kept cache only. Its exact manifest names this generated
        # fixture. Never inspect/delete any pre-existing user cache directory.
        self.assertEqual(Path(manifest['input']),source)
        self.assertEqual(work.parent.resolve(),self.scratch/'SDR2DV')
        # All cache files belong to this test's TemporaryDirectory, removed by
        # its context cleanup; no shared production Temp path is cleaned.

    @unittest.skipUnless(os.environ.get('SDR2DV_TEST_NVENC')=='1','NVIDIA conversion through actual TUI bridge explicitly enabled')
    def test_tui_bridge_conversion_with_nested_owned_windows_jobs(self):
        source=self.fixture();output=self.root/'tui-output';output.mkdir()
        with patch.dict(os.environ,{'TEMP':str(self.scratch)}):
            task=ConversionTask(Backend(shell=SHELL),['-InputPath',str(source),'-HdrMode','TrueHDR',
                '-Bilibili','-Encoder','NVENC','-OutputDir',str(output)],self.root/'sessions').start()
        try:
            task.process.wait(timeout=120)
            shutil.copyfile(task.directory/'console.log',self.evidence/'tui-console.log')
            self.assertEqual(task.process.returncode,0,(task.directory/'console.log').read_text(encoding='utf-8',errors='replace'))
            result=json.loads(task.report.read_text(encoding='utf-8-sig'))
            self.assertEqual(result['results'][0]['delivery'],'committed')
            self.assertEqual(result['results'][0]['outputs'][0]['frames'],25)
            shutil.copyfile(task.report,self.evidence/'tui-result.json')
        finally:task.close()


if __name__=='__main__':unittest.main()
