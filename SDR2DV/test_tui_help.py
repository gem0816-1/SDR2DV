from dataclasses import asdict
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from rich.console import Console

from tui_controls import BACK, Option, choice_for, menu_question, number_validator, wrap_cells
from tui_menus import (advanced_options, configure, configure_advanced, input_file_valid,
                       main_options, mapping_label, output_directory_valid, preset_options)
from tui_model import ConversionConfig, planned_outputs
from test_tui import CAPS


class ExplainedMenuTests(unittest.TestCase):
    def test_descriptions_are_gray_and_wrap_chinese_by_cells(self):
        config=ConversionConfig.initial('中文.mov')
        options=main_options(config)+advanced_options(config,CAPS)+preset_options('NVENC')+preset_options('x265')
        for option in options:
            self.assertTrue(option.detail)
            choice=choice_for(option,50)
            self.assertTrue(all(style=='class:help' for style,_ in choice.title[1:]))
            self.assertEqual(choice.value,option.value)
            for _,line in choice.title[1:]:self.assertLessEqual(get_cwidth(line.lstrip('\n')),48)
        self.assertEqual(''.join(wrap_cells('中文 test 中文',7)),'中文 test 中文')

    def test_real_arrows_skip_disabled_default_and_keep_boolean_values(self):
        with create_pipe_input() as keyboard:
            keyboard.send_text('\x1b[B\r')
            answer=menu_question('菜单',[Option(True,'开启','说明'),Option('bad','不可选','原因','不可用'),
                Option(False,'关闭','说明')],True,input=keyboard,output=DummyOutput()).unsafe_ask()
            self.assertIs(answer,False)
        with create_pipe_input() as keyboard:
            keyboard.send_text('\r')
            answer=menu_question('菜单',[Option('bad','不可选','原因','不可用'),Option('ok','可选','说明')],
                'bad',input=keyboard,output=DummyOutput()).unsafe_ask()
            self.assertEqual(answer,'ok')

    def test_real_escape_returns_from_submenu(self):
        with create_pipe_input() as keyboard:
            keyboard.send_text('\x1b')
            question=menu_question('菜单',[Option('a','选项','说明')],input=keyboard,output=DummyOutput())
            question.application.ttimeoutlen=.01
            self.assertEqual(question.unsafe_ask(),BACK)

    def test_ineffective_quality_and_grading_options_are_disabled(self):
        config=ConversionConfig.initial('a.mov')
        options={o.value:o for o in advanced_options(config,CAPS)}
        for name in ('quality','strength','skin'):self.assertTrue(options[name].disabled)
        config.mapping='Natural';config.bitrate_mode='Quality'
        options={o.value:o for o in advanced_options(config,CAPS)}
        for name in ('quality','strength','skin'):self.assertIsNone(options[name].disabled)
        config.brightness_strength=0
        self.assertIn('旁路',mapping_label(config))
        self.assertTrue(next(o for o in advanced_options(config,CAPS) if o.value=='skin').disabled)
        config.hdr_mode='Original'
        self.assertTrue(next(o for o in advanced_options(config,CAPS) if o.value=='hdr10').disabled)

    def test_numeric_validation_rejects_invalid_before_edit(self):
        validate=number_validator(0,1)
        for bad in ('-1','2','nan','inf','hello'):self.assertIsInstance(validate(bad),str)
        self.assertIs(validate('0.5'),True)
        self.assertIsInstance(number_validator(0,51,True)('18.5'),str)
        config=ConversionConfig.initial('a.mov');config.mapping='Natural'
        before=asdict(config)
        with patch('tui_menus.choose',side_effect=['strength',BACK]),patch('tui_menus.number',return_value=BACK):
            configure_advanced(config,CAPS)
        self.assertEqual(asdict(config),before)

    def test_platform_downgrade_cancel_preserves_source_strategy(self):
        config=ConversionConfig.initial('a.mov');before=asdict(config)
        console=Console(file=StringIO())
        with patch('tui_menus.choose',side_effect=['rate','Platform','exit']),patch('tui_menus.toggle',return_value=False) as confirm:
            with self.assertRaises(KeyboardInterrupt):configure(config,CAPS,{'video_bitrate':{'bps':106305301}},None,console)
        self.assertFalse(confirm.call_args.args[1])
        self.assertEqual(asdict(config),before)

    def test_switching_modes_clears_only_incompatible_options(self):
        config=ConversionConfig.initial('a.mov');config.hdr10=True;config.audio_codec='alac'
        with patch('tui_menus.choose',side_effect=['container','mkv','mode','Original','exit']):
            with self.assertRaises(KeyboardInterrupt):configure(config,CAPS,{},None,Console(file=StringIO()))
        self.assertEqual(config.audio_codec,'auto');self.assertFalse(config.hdr10)
        self.assertEqual(config.bitrate_mode,'Source')

    def test_mkv_hdr10_still_checks_mp4_audio_compatibility(self):
        config=ConversionConfig.initial('a.mov');config.container='mkv';config.hdr10=True
        with self.assertRaisesRegex(ValueError,'32bit'):
            config.validate(CAPS,require_path=False,description={'audio_plan_error':'32bit PCM'})

    def test_paths_quoted_unicode_existing_file_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'中文 视频.mov';source.touch()
            self.assertIs(input_file_valid('"'+str(source)+'"'),True)
            self.assertIsInstance(input_file_valid('\0'),str)
            self.assertIsInstance(input_file_valid(folder),str)
            self.assertIsInstance(output_directory_valid(str(source/'bad')),str)
            self.assertIs(output_directory_valid(str(Path(folder)/'新输出')),True)
            config=ConversionConfig.initial(str(source));config.mapping='Natural';config.container='both';config.hdr10=True
            self.assertEqual([p.name for p in planned_outputs(config)],['中文 视频_TrueHDR_Natural_DV84.mp4',
                '中文 视频_TrueHDR_Natural_DV84.mkv','中文 视频_TrueHDR_Natural_HDR10.mp4'])
            config.brightness_strength=0
            self.assertEqual(planned_outputs(config)[0].name,'中文 视频_TrueHDR_DV84.mp4')

    def test_summary_narrow_terminal_keeps_complete_output_filename(self):
        from tui import summary
        config=ConversionConfig.initial('C:/long/folder/name/another-long-folder/中文测试电影.mov')
        buffer=StringIO()
        with patch('tui.console',Console(file=buffer,width=60)):
            summary(config,CAPS,{'video_bitrate':{'bps':106305301},'media':{'format':{'duration':'202.15'}}})
        rendered=buffer.getvalue()
        self.assertNotIn('…',rendered)
        compact=''.join(rendered.split()).replace('│','')
        self.assertIn('中文测试电影_TrueHDR_DV84.mp4',compact)


if __name__=='__main__':unittest.main()
