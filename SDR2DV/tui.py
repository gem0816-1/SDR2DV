"""Interactive presentation only; SDR2DV.ps1 owns all media operations."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

import questionary
from questionary import Choice
from rich.console import Console,Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress,SpinnerColumn,BarColumn,TextColumn,TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from tui_model import ConversionConfig,defaults,encoder_options,normalize_path,nvenc_available
from tui_runner import Backend,ConversionTask

console=Console(highlight=False)
STYLE=questionary.Style([('qmark','fg:#69c9b5'),('pointer','fg:#69c9b5 bold'),
    ('highlighted','fg:#69c9b5 bold'),('selected','fg:#69c9b5'),('answer','fg:#c9dbec')])


def choose(title,choices,default=None):
    answer=questionary.select(title,choices=choices,default=default,style=STYLE,use_shortcuts=False).ask()
    if answer is None:raise KeyboardInterrupt
    return answer


def text(title,default=''):
    answer=questionary.text(title,default=str(default),style=STYLE).ask()
    if answer is None:raise KeyboardInterrupt
    return answer


def yes(title,default=False):
    answer=questionary.confirm(title,default=default,style=STYLE).ask()
    if answer is None:raise KeyboardInterrupt
    return answer


def media_view(description):
    media=description['media'];rate=description['video_bitrate']
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    table=Table.grid(padding=(0,2));table.add_column(style='dim');table.add_column()
    for label,value in [('视频',f'{video["codec_name"]}  {video["width"]}×{video["height"]}  {video.get("avg_frame_rate")} fps  {video.get("pix_fmt")}'),
                        ('视频流码率',f'{rate["bps"]/1e6:.3f} Mbps  ·  {rate["method"]}'),
                        ('色彩',f'{video.get("color_primaries","未标记")} / {video.get("color_transfer","未标记")} / {video.get("color_space","未标记")}'),
                        ('时长',f'{float(media.get("format",{}).get("duration",0)):.3f} 秒')]:table.add_row(label,value)
    for index,audio in enumerate(s for s in media['streams'] if s['codec_type']=='audio'):
        bits=audio.get('bits_per_raw_sample') or audio.get('bits_per_sample') or '未知'
        table.add_row(f'音轨 {index+1}',f'{audio["codec_name"]}  {audio["sample_rate"]} Hz / {bits}bit / {audio["channels"]}声道 {audio.get("channel_layout","")}')
    console.print(Panel(table,title='输入媒体',border_style='grey37'))
    for index,spec in enumerate(description.get('audio_precheck',[])):
        console.print(Text(f'音轨 {index+1} Hi-Res 预检：{spec["note"]}',style='dim'))
    if description.get('audio_plan_error'):console.print(Text('MP4 音频预检：'+description['audio_plan_error'],style='yellow'))
    if description.get('truehdr_input_error'):console.print(Text('TrueHDR 输入预检：'+description['truehdr_input_error'],style='yellow'))


def summary(config,caps,description):
    chosen='NVENC' if config.encoder=='Auto' and nvenc_available(caps,config.hdr_mode,config.lossless) else ('x265' if config.encoder=='Auto' else config.encoder)
    rate=description['video_bitrate']['bps']/1e6
    strategies={'Source':f'匹配源视频流 {rate:.3f} Mbps','Platform':f'平台兼容，目标 ≤28 Mbps（源 {rate:.3f}）',
                'Custom':f'自定义 {config.video_bitrate/1000:.3f} Mbps','Quality':'无损编码' if config.lossless else f'质量优先 · CQ {config.nvenc_cq}' if chosen=='NVENC' else f'质量优先 · CRF {config.crf}'}
    table=Table.grid(padding=(0,2));table.add_column(style='dim');table.add_column()
    values=[('输入',config.input_path),('处理',config.hdr_mode),('编码',f'{config.encoder} → {chosen} · '+(config.nvenc_preset if chosen=='NVENC' else config.preset)),
        ('HDR','Dolby Vision 8.4 · BT.2020 / HLG · Main10 · 1000 nit'),
        ('亮度映射',f'{config.mapping} · 强度 {config.brightness_strength} · 肤色修正 {config.skin_strength}' if config.hdr_mode=='TrueHDR' else '标准 SDR 上变换'),
        ('码率',strategies[config.bitrate_mode]),('音频','MKV：全部音轨原样复制' if config.container=='mkv' else {'auto':'兼容性优先：可复制则复制，PCM → 经采样校验的 ALAC','copy':'强制复制（PCM MP4 兼容性待验证）','alac':'显式 ALAC 无损转换'}[config.audio_codec]),
        ('输出',config.container.upper()+(' + HDR10/PQ 对照' if config.hdr10 else '')),('输出目录',config.output_dir or '与输入相同'),
        ('中间文件','保留本次临时文件' if config.keep_temp else '正常结束/取消后清理本次媒体缓存'),('覆盖已有成片','是' if config.force else '否，跳过')]
    for label,value in values:table.add_row(label,Text(str(value)))
    console.print(Panel(table,title='转换配置',border_style='cyan'))
    if config.mapping=='Natural':console.print('[yellow]Natural 数值与转换测试已实现；HDR 设备观感仍待确认。默认保留 Neutral。[/yellow]')
    console.print('[dim]SDR 高光缺失无法真实恢复；同码率不等于无损。B站 DV / Hi-Res 仍需投稿实测。[/dim]')


def configure(config,caps,description):
    while True:
        action=choose('配置 · ↑↓ 选择，Enter 确认',[
            Choice('查看摘要并开始',value='start'),Choice('编码器',value='encoder'),Choice('HDR 亮度映射',value='mapping'),
            Choice('输出容器',value='container'),Choice('码率策略',value='rate'),Choice('音频策略',value='audio'),
            Choice('高级质量与输出设置',value='advanced'),Choice('处理模式',value='mode'),Choice('退出',value='exit')])
        if action=='exit':raise KeyboardInterrupt
        if action=='start':
            try:config.validate(caps,description['media'],description=description)
            except ValueError as error:console.print(Text(str(error),style='red'));continue
            summary(config,caps,description)
            if yes('确认开始转换？',True):return config
        elif action=='encoder':
            options=encoder_options(caps,config.hdr_mode,config.lossless)
            default=config.encoder if any(name==config.encoder and reason is None for name,_,reason in options) else 'Auto'
            config.encoder=choose('视频编码器',[Choice(f'{name} · {label}',value=name,disabled=reason) for name,label,reason in options],default)
        elif action=='mapping':
            config.mapping=choose('亮度分配（峰值目标固定 1000 nit）',[
                Choice('Neutral · 保留 TrueHDR 输出，当前默认',value='Neutral',disabled=None if 'Neutral' in caps['mappings'] else '后端未实现'),
                Choice('Natural · 保护中低亮度，平滑高光（实验，待观感验收）',value='Natural',disabled=None if 'Natural' in caps['mappings'] else '后端未实现'),
                Choice('Reference · 旧参考曲线，可对照',value='Reference',disabled=None if 'Reference' in caps['mappings'] else '后端未实现')],config.mapping)
            config.skin_strength=defaults()['reference_skin_tone_correction'] if config.mapping=='Reference' else 0
        elif action=='container':config.container=choose('输出容器',[Choice('MP4 · 投稿 / 播放',value='mp4'),Choice('MKV · 原音与字幕',value='mkv'),Choice('MP4 + MKV',value='both')],config.container)
        elif action=='rate':
            mode=choose('视频码率',[Choice('匹配源视频流平均码率（默认）',value='Source'),Choice('平台兼容（显式限制 ≤28 / VBV30 Mbps）',value='Platform'),Choice('自定义平均码率',value='Custom'),Choice('质量优先 / 无损',value='Quality')],config.bitrate_mode)
            try:
                custom=int(text('目标视频码率（kbps）',config.video_bitrate or round(description['video_bitrate']['bps']/1000))) if mode=='Custom' else 0
                if mode=='Custom' and custom<=0:raise ValueError
                config.bitrate_mode=mode;config.video_bitrate=custom;config.max_video_bitrate=0
                if mode!='Quality':config.lossless=False
            except ValueError:console.print('[red]码率必须是正整数。[/red]')
        elif action=='audio':
            error=description.get('audio_plan_error')
            config.audio_codec=choose('音频处理（不重采样、不混音、不升位深）',[
                Choice('兼容性优先 · 原样复制 / PCM 无损 ALAC',value='auto',disabled=error if config.container!='mkv' else None),
                Choice('强制复制 · PCM MP4 需自行验证',value='copy'),Choice('显式 ALAC 无损',value='alac',disabled='MKV 原音复制' if config.container=='mkv' else description.get('alac_plan_error'))],config.audio_codec)
        elif action=='mode':config.hdr_mode=choose('SDR → HDR',[
            Choice('NVIDIA TrueHDR',value='TrueHDR',disabled=description.get('truehdr_input_error') if caps['truehdr_ready'] else 'SDK / GPU 未就绪，先运行部署'),
            Choice('标准 SDR 上变换（不执行 AI）',value='Original')],config.hdr_mode if caps['truehdr_ready'] and not description.get('truehdr_input_error') else 'Original')
        elif action=='advanced':
            try:
                config.nvenc_preset=choose('NVENC 预设',list('p'+str(i) for i in range(1,8)),config.nvenc_preset)
                config.preset=choose('x265 预设',['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'],config.preset)
                config.nvenc_cq=int(text('NVENC CQ（仅质量模式，0..51）',config.nvenc_cq))
                config.crf=int(text('x265 CRF（仅质量模式，0..51）',config.crf))
                config.brightness_strength=float(text('亮度曲线强度（Natural / Reference，0..1）',config.brightness_strength))
                config.skin_strength=float(text('可选暖色肤色修正（默认 0 保持原色，0..1）',config.skin_strength))
                config.lossless=yes('使用视频无损编码？需要巨量空间，码率策略将切到 Quality',config.lossless)
                if config.lossless:config.bitrate_mode='Quality';config.max_video_bitrate=0
                config.hdr10=yes('同时保存 HDR10/PQ 对照？额外占用空间',config.hdr10)
                config.output_dir=text('输出目录（留空与输入相同）',config.output_dir)
                config.keep_temp=yes('保留本次中间媒体与诊断？',config.keep_temp)
                config.force=yes('覆盖已有同名成片？',config.force)
            except ValueError:console.print('[red]请输入有效数字；开始前会再次校验全部设置。[/red]')


def render_conversion(task):
    progress=Progress(SpinnerColumn(),TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>5.1f}%'),TimeElapsedColumn(),console=console)
    item=progress.add_task('启动转换核心',total=100)
    logs=[];expanded=False;speed='';stage='启动转换核心'
    def screen():
        tail=logs[-20:] if expanded else logs[-4:]
        return Group(Panel(Group(progress,Text(f'{speed}  ·  Ctrl+C 安全取消  ·  L 展开/收起日志',style='dim')),title='SDR2DV',border_style='cyan'),
            Panel(Text('\n'.join(tail) or '正在检查输入与工具链…'),title='日志',border_style='grey37',
                height=max(4,min(20,console.height-8)) if expanded else 6))
    try:
        with Live(screen(),console=console,refresh_per_second=5) as live:
            while task.process.poll() is None:
                for event in task.read_events():
                    kind=event['type'];data=event['data']
                    if kind=='stage':
                        stage=data.get('label',data.get('stage','处理中'));speed='';progress.update(item,description=stage,completed=0)
                    elif kind=='progress':
                        total=data.get('total_seconds',0)
                        pct=100*data.get('seconds',0)/total if total else None
                        if pct is not None:progress.update(item,completed=max(0,min(100,float(pct))))
                        speed=f'{data.get("fps",0):.2f} fps  {data.get("speed","")}'
                    elif kind in ('log','error'):
                        logs.append(str(data.get('message',data.get('text',data))))
                if os.name=='nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        key=msvcrt.getwch()
                        if key.lower()=='l':expanded=not expanded
                        elif key=='\x03':raise KeyboardInterrupt
                live.update(screen());time.sleep(.15)
            progress.update(item,completed=100 if task.process.returncode==0 else 0)
            live.update(screen())
    except KeyboardInterrupt:
        console.print('[yellow]正在请求本次任务停止并清理，请稍候…[/yellow]')
        task.cancel()
        if task.forced:console.print('[yellow]核心未及时响应，已终止本次进程树；本次残留缓存可能需按日志位置检查。[/yellow]')
        return 130
    finally:task.close()
    if task.process.returncode:
        output=(task.directory/'console.log').read_text(encoding='utf-8-sig',errors='replace')
        console.print(Panel(Text(output[-16000:]),title='转换失败 · 完整日志见下方路径',border_style='red'))
    elif task.report.exists():
        result=json.loads(task.report.read_text(encoding='utf-8-sig'))
        for file in result.get('results',[]):
            for out in file.get('outputs',[]):
                deviation=out.get('deviation_percent')
                target=f'{file["target_video_bps"]/1e6:.3f}' if file.get('target_video_bps') else '不设'
                console.print(Text(f'成片：{out["path"]}\n视频：源 {file["source_video_bps"]/1e6:.3f} / 目标 {target} / 实际 {out["actual_video_bps"]/1e6:.3f} Mbps'+(f' · 偏差 {deviation:+.2f}%' if deviation is not None else ' · 质量模式')))
    console.print(Text(f'本次界面日志与校验报告：{task.directory}',style='dim'))
    return task.process.returncode


def main(argv=None):
    parser=argparse.ArgumentParser(description='SDR2DV 终端界面，转换核心为 SDR2DV.ps1')
    parser.add_argument('input',nargs='?');parser.add_argument('--config');parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--show-defaults',action='store_true');parser.add_argument('--shell')
    args=parser.parse_args(argv)
    if args.show_defaults:print(json.dumps(defaults(),ensure_ascii=False,indent=2));return 0
    try:
        backend=Backend(shell=args.shell)
        config=ConversionConfig.from_dict(json.loads(Path(args.config).read_text(encoding='utf-8-sig'))) if args.config else ConversionConfig.initial()
        if args.input:config.input_path=normalize_path(args.input)
        if not config.input_path:
            if not sys.stdin.isatty():raise ValueError('非交互运行请提供输入路径，并使用 PowerShell CLI；--dry-run 仅校验参数')
            config.input_path=normalize_path(text('拖入 SDR 视频或输入完整路径'))
        console.print(Text('SDR2DV  /  SDR → Dolby Vision',style='bold cyan'))
        with console.status('检查真实 GPU、编码器与 SDK 能力…'):caps=backend.json_request(['-GetCapabilities'])
        # Folder batches are handled by the existing CLI; probe a representative
        # input only after the user explicitly selected a file in the TUI.
        if Path(config.input_path).is_dir():raise ValueError('TUI 当前请选择单个视频；文件夹批量处理保留 PowerShell -InputPath 方式')
        with console.status('读取视频流和音频封装策略…'):
            description=backend.json_request(['-DescribeInput','-InputPath',config.input_path,'-Bilibili','-HdrMode',config.hdr_mode])
        media_view(description)
        if args.dry_run:
            config.validate(caps,description['media'],description=description);summary(config,caps,description)
            print(json.dumps({'backend_argv':backend.command(config.backend_args(caps))},ensure_ascii=False));return 0
        if not sys.stdin.isatty():raise ValueError('交互界面需要终端；自动化请直接调用 SDR2DV.ps1')
        configure(config,caps,description)
        return render_conversion(ConversionTask(backend,config.backend_args(caps)).start())
    except KeyboardInterrupt:console.print('已取消。');return 130
    except (ValueError,RuntimeError,OSError) as error:console.print(Panel(Text(str(error)),title='无法开始',border_style='red'));return 1


if __name__=='__main__':raise SystemExit(main())
