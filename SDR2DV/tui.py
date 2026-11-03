"""Interactive presentation only; SDR2DV.ps1 owns all media operations."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from rich.console import Console,Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress,SpinnerColumn,BarColumn,TextColumn,TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from tui_model import ConversionConfig,defaults,normalize_path,planned_outputs
from tui_runner import Backend,ConversionTask
from tui_controls import BACK, text
from tui_menus import configure as configure_menu, active_encoder, mapping_label, input_file_valid

console=Console(highlight=False)


def media_view(description):
    media=description['media'];rate=description['video_bitrate']
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    table=Table.grid(padding=(0,2));table.add_column(style='dim',no_wrap=True);table.add_column(overflow='fold')
    from fractions import Fraction
    try:fps=f'{float(Fraction(video.get("avg_frame_rate","0/1"))):g}'
    except (ValueError,ZeroDivisionError):fps='未知'
    for label,value in [('视频',f'{video["codec_name"]}  {video["width"]}×{video["height"]}  {fps} fps  {video.get("pix_fmt")}'),
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
    chosen=active_encoder(config,caps)
    rate=description['video_bitrate']['bps']/1e6
    strategies={'Source':f'匹配源视频流 {rate:.3f} Mbps','Platform':f'平台兼容，目标 {min(rate,defaults()["platform_target_kbps"]/1000):.3f} Mbps（源 {rate:.3f}）',
                'Custom':f'自定义 {config.video_bitrate/1000:.3f} Mbps','Quality':'无损编码' if config.lossless else f'质量优先 · CQ {config.nvenc_cq}' if chosen=='NVENC' else f'质量优先 · CRF {config.crf}'}
    table=Table.grid(padding=(0,2));table.add_column(style='dim',no_wrap=True);table.add_column(overflow='fold')
    values=[('输入',config.input_path),('处理',config.hdr_mode),('编码',f'{config.encoder} → {chosen} · '+(config.nvenc_preset if chosen=='NVENC' else config.preset)),
        ('HDR','Dolby Vision 8.4 · BT.2020 / HLG · Main10 · '+
            ('跟随苹果参考，保留超白，不限 1000 nit' if config.mapping=='realDolby' and config.brightness_strength>0 and config.hdr_mode=='TrueHDR' else '1000 nit 目标')),
        ('亮度映射',mapping_label(config)),
        ('码率',strategies[config.bitrate_mode]),('音频','MKV：全部音轨原样复制' if config.container=='mkv' else {'auto':'兼容性优先：可复制则复制，PCM → 经采样校验的 ALAC','copy':'强制复制（PCM MP4 兼容性待验证）','alac':'显式 ALAC 无损转换'}[config.audio_codec]),
        ('输出',config.container.upper()+(' + HDR10/PQ 对照' if config.hdr10 else '')),('输出目录',config.output_dir or '与输入相同'),
        ('中间文件','保留本次临时文件' if config.keep_temp else '正常结束/取消后清理本次媒体缓存'),('覆盖已有成片','是' if config.force else '否，跳过')]
    for label,value in values:table.add_row(label,Text(str(value),overflow='fold'))
    for path in planned_outputs(config):
        status='（已存在，将覆盖）' if config.force else ('（已存在：需换目录或允许覆盖）' if path.name.endswith('_HDR10.mp4') else '（已存在，将跳过，旧文件不会改变）')
        table.add_row('成片路径',Text(str(path)+(status if path.exists() else ''),overflow='fold'))
    target=rate
    if config.bitrate_mode=='Platform':target=min(rate,defaults()['platform_target_kbps']/1000)
    elif config.bitrate_mode=='Custom':target=config.video_bitrate/1000
    if config.bitrate_mode!='Quality':
        duration=float(description['media'].get('format',{}).get('duration',0))
        copies=2 if config.container=='both' else 1
        size=target*1e6*duration/8*copies
        size_label=f'{size/1024**3:.2f} GiB' if size>=1024**3 else f'{size/1024**2:.1f} MiB'
        table.add_row('视频体积估算',size_label+'（不含音频、HDR10 对照和缓存；实际随编码变化）')
    console.print(Panel(table,title='转换配置',border_style='cyan'))
    if config.bitrate_mode!='Quality' and target<rate*.9:
        console.print(Text(f'当前将视频目标从 {rate:.3f} 降到 {target:.3f} Mbps，可能损失细节。',style='yellow'))
    if config.bitrate_mode!='Quality' and chosen=='NVENC':
        console.print('[dim]NVENC 按平均码率目标分配；简单画面/短片可能用不满预算，完成后显示实际偏差。[/dim]')
    if config.hdr_mode=='TrueHDR' and config.mapping=='Natural' and config.brightness_strength>0:
        console.print('[yellow]Natural 数值与转换测试已实现；HDR 设备观感仍待确认。默认保留 Neutral。[/yellow]')
    if config.hdr_mode=='TrueHDR' and config.mapping=='realDolby' and config.brightness_strength>0:
        console.print('[dim]realDolby 按配对参考校准并保留扩展 HLG；1000 nit 仅为测量参考。配对样片观感已获用户确认；其他素材建议先试片，创作 Trim 与丢失细节不能直接复制。[/dim]')
    console.print('[dim]SDR 高光缺失无法真实恢复；同码率不等于无损。B站 DV / Hi-Res 仍需投稿实测。[/dim]')


def configure(config,caps,description):
    return configure_menu(config,caps,description,summary,console)


def render_conversion(task):
    progress=Progress(SpinnerColumn(),TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>5.1f}%'),TimeElapsedColumn(),console=console)
    item=progress.add_task('启动转换核心',total=100)
    logs=[];expanded=False;speed='';stage='启动转换核心'
    def screen():
        tail=logs[-20:] if expanded else logs[-4:]
        elapsed=max(0,int(time.monotonic()-task.started)) if task.started else 0
        return Group(Panel(Group(progress,Text(f'总用时 {elapsed//60:02d}:{elapsed%60:02d}  {speed}  ·  Ctrl+C 安全取消  ·  L 展开/收起日志',style='dim')),title='SDR2DV · 当前阶段进度',border_style='cyan'),
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
            for event in task.read_events():
                if event['type'] in ('log','error'):
                    logs.append(str(event['data'].get('message',event['data'])))
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
        if not any(file.get('outputs') for file in result.get('results',[])):
            console.print('[yellow]本次未生成新成片；检查上方“已存在/跳过”提示，旧文件的码率不会改变。[/yellow]')
        for file in result.get('results',[]):
            for out in file.get('outputs',[]):
                deviation=out.get('deviation_percent')
                target=f'{file["target_video_bps"]/1e6:.3f}' if file.get('target_video_bps') else '不设'
                console.print(Text(f'成片：{out["path"]}\n视频：源 {file["source_video_bps"]/1e6:.3f} / 目标 {target} / 实际 {out["actual_video_bps"]/1e6:.3f} Mbps'+(f' · 偏差 {deviation:+.2f}%' if deviation is not None else ' · 质量模式')))
                if deviation is not None and abs(deviation)>10:
                    console.print(Text('实际视频码率与目标偏差超过 10%；请检查报告和画面，不能据目标值宣称已匹配。',style='yellow'))
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
        console.print(Text('SDR2DV  /  SDR → Dolby Vision',style='bold cyan'))
        console.print(Text(f'程序位置：{backend.root}',style='dim'))
        console.print('[dim]默认 1000 nit；realDolby 跟随参考 · 匹配源视频流 · Esc 返回，Ctrl+C 退出[/dim]')
        if args.input:config.input_path=normalize_path(args.input)
        if not config.input_path:
            if not sys.stdin.isatty():raise ValueError('非交互运行请提供输入路径，并使用 PowerShell CLI；--dry-run 仅校验参数')
            value=text('拖入 SDR 视频或输入完整路径',detail='支持带空格的中文路径和外层引号；仅接收单个视频。',validate=input_file_valid)
            if value==BACK:raise KeyboardInterrupt
            config.input_path=normalize_path(value)
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
