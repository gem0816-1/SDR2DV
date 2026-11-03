from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from tui_model import ConversionConfig,defaults,encoder_options,normalize_path
from tui_runner import Backend,ConversionTask

CAPS=dict(nvenc_ready=True,x265_ready=True,truehdr_ready=True,muxer_ready=True,
          mappings=['Neutral','Natural','Reference','realDolby'],bitrate_modes=['Source','Platform','Custom','Quality'])


class TUIModelTests(unittest.TestCase):
    def test_defaults_and_arguments_are_shared_with_core(self):
        config=ConversionConfig.initial('中文 视频.mov');d=defaults()
        self.assertEqual(config.mapping,d['brightness_mapping'])
        self.assertEqual(config.bitrate_mode,'Source')
        args=config.backend_args(CAPS)
        self.assertIn('1000',args);self.assertIn('Neutral',args)
        self.assertNotIn('-VideoBitrate',args)
        self.assertEqual(config.audio_codec,'auto')
        self.assertEqual(ConversionConfig.from_dict({'mapping':'Reference'}).skin_strength,d['reference_skin_tone_correction'])

    def test_drag_chinese_path_and_shell_metacharacters_are_one_argument(self):
        path=normalize_path(' & "中文 & $视频.mov" ')
        self.assertEqual(path,normalize_path('中文 & $视频.mov'))
        args=ConversionConfig.initial(path).backend_args(CAPS)
        self.assertEqual(args[1],path)
        with self.assertRaises(ValueError):normalize_path('a\nb')

    def test_invalid_hardware_and_combinations(self):
        config=ConversionConfig.initial('a.mov')
        cases=[dict(encoder='NVENC'),dict(lossless=True),dict(bitrate_mode='Custom'),dict(profile='8.1'),dict(mapping='Future'),dict(hdr_mode='Original',hdr10=True)]
        for changes in cases:
            current=ConversionConfig.from_dict({**asdict(config),**changes})
            with self.assertRaises(ValueError):current.validate({**CAPS,'nvenc_ready':False},require_path=False)
        config.bitrate_mode='Quality';config.lossless=True
        config.validate(CAPS,require_path=False)
        self.assertIn('-Lossless',config.backend_args(CAPS))
        self.assertNotIn('-Crf',config.backend_args(CAPS))

    def test_encoder_disabled_options_and_quality_mapping(self):
        opts=encoder_options({**CAPS,'nvenc_ready':False})
        self.assertIsNotNone(opts[1][2]);self.assertIsNone(opts[2][2])
        config=ConversionConfig.initial('a.mov');config.bitrate_mode='Quality'
        self.assertIn('-NvencCq',config.backend_args(CAPS))
        self.assertIn('-Crf',config.backend_args({**CAPS,'nvenc_ready':False}))
        with self.assertRaises(ValueError):ConversionConfig.from_dict({'magic':True})

    def test_mp4_audio_core_preflight_error_does_not_block_mkv_copy(self):
        config=ConversionConfig.initial('a.mov')
        description=dict(audio_plan_error='32bit PCM 不做隐式截断')
        with self.assertRaisesRegex(ValueError,'32bit'):config.validate(CAPS,require_path=False,description=description)
        config.container='mkv'
        config.validate(CAPS,require_path=False,description=description)

    def test_malformed_config_numbers_and_switches_fail_before_launch(self):
        for data in ([],{'video_bitrate':'8000'},{'max_video_bitrate':-1},{'hdr10':'false'},
                     {'brightness_strength':float('nan')}):
            with self.assertRaises(ValueError):ConversionConfig.from_dict(data)

    def test_nvenc_capabilities_follow_conversion_and_lossless_mode(self):
        caps={**CAPS,'nvenc_lossless_ready':False}
        self.assertIsNotNone(encoder_options(caps,'TrueHDR')[1][2])
        self.assertIsNone(encoder_options(caps,'Original')[1][2])
        self.assertIsNotNone(encoder_options(caps,'Original',True)[1][2])
        config=ConversionConfig.initial('a.mov');config.bitrate_mode='Quality'
        self.assertIn('-Crf',config.backend_args(caps))
        config.hdr_mode='Original'
        self.assertIn('-NvencCq',config.backend_args(caps))

    def test_questionary_arrow_menu_with_disabled_item(self):
        import questionary
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        with create_pipe_input() as keyboard:
            keyboard.send_text('\x1b[B\r')
            answer=questionary.select('编码器',choices=[questionary.Choice('自动',value='Auto'),
                questionary.Choice('NVENC',value='NVENC',disabled='不可用'),questionary.Choice('x265',value='x265')],
                input=keyboard,output=DummyOutput()).ask()
        self.assertEqual(answer,'x265')


class OwnedProcessTests(unittest.TestCase):
    def test_cancel_only_own_process_preserves_input_and_other_task(self):
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder);source=parent/'源.mov';source.write_bytes(b'original')
            other_cache=parent/'other-cache';other_cache.mkdir();(other_cache/'data').write_bytes(b'keep')
            options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
            other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],**options)
            task=ConversionTask(Backend(),[],parent/'sessions',command_override=[sys.executable,'-c','import time;time.sleep(60)']).start()
            try:
                task.cancel(.15)
                self.assertIsNotNone(task.process.poll());self.assertTrue(task.forced)
                self.assertIsNone(other.poll());self.assertEqual(source.read_bytes(),b'original')
                self.assertEqual((other_cache/'data').read_bytes(),b'keep')
            finally:task.close();other.terminate();other.wait()

    def test_cooperative_cancel_and_partial_unicode_event(self):
        with tempfile.TemporaryDirectory() as folder:
            task=ConversionTask(Backend(),[],folder)
            task.events.write_text('{"type":"log","data":{"message":"中文"}}',encoding='utf-8')
            self.assertEqual(task.read_events(),[])
            with task.events.open('a',encoding='utf-8') as file:file.write('\n')
            self.assertEqual(task.read_events()[0]['data']['message'],'中文')
            code='from pathlib import Path;import time,sys; p=Path(sys.argv[1]);\nwhile not p.exists():time.sleep(.02)'
            task.command=[sys.executable,'-c',code,str(task.cancel_file)]
            task.start()
            try:task.cancel(3);self.assertFalse(task.forced);self.assertEqual(task.process.returncode,0)
            finally:task.close()


if __name__=='__main__':unittest.main()
