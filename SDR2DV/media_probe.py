"""Video-stream-only bitrate measurements, shared by the PowerShell core/probe."""
import argparse
from fractions import Fraction
import math
from pathlib import Path
from task_io import compact_fields,json_command,progress,stream_lines,write_json


def number(value,default=None):
    try:
        result=float(value)
        return result if math.isfinite(result) else default
    except (TypeError,ValueError):return default


def frame_rate(stream):
    for key in ('avg_frame_rate','r_frame_rate'):
        try:
            value=float(Fraction(stream.get(key,'0/0')))
            if math.isfinite(value) and 0<value<=1000:return value
        except (ValueError,ZeroDivisionError,OverflowError,TypeError):pass
    return 0


def effective_bits(stream):
    return int(number(stream.get('bits_per_raw_sample'),0) or number(stream.get('bits_per_sample'),0) or 0)


def hires_precheck(stream):
    bits=effective_bits(stream);rate=int(number(stream.get('sample_rate'),0))
    lossless=stream.get('codec_name','').startswith('pcm_') or stream.get('codec_name') in ('alac','flac','wavpack','truehd')
    eligible=bool(lossless and bits>=24 and rate>=48000)
    return dict(source_spec_candidate=eligible,effective_bits=bits,sample_rate=rate,platform_verified=False,
        note='规格候选，平台接收/转码尚未验证' if eligible else '源规格不满足本工具的保守 Hi-Res 预检；不会升位深')


def packet_average(packets,fps=0,progress_path=None):
    total=count=0
    first=last=None
    for packet in packets:
        if 'D' in packet.get('flags',''):continue
        size=number(packet.get('size'))
        if size is None or size<0:raise ValueError('Video packet size missing/invalid')
        total+=int(size)
        count+=1
        timestamp=number(packet.get('pts_time'),number(packet.get('dts_time')))
        duration=number(packet.get('duration_time'),1/fps if fps>0 else 0)
        if timestamp is not None:
            first=timestamp if first is None else min(first,timestamp)
            end=timestamp+(duration if duration and duration>0 else (1/fps if fps>0 else 0))
            last=end if last is None else max(last,end)
        if count%240==0:progress(progress_path,count,count/fps if fps>0 else 0)
    duration=last-first if first is not None and last is not None else (count/fps if fps>0 else 0)
    if not total or not count or duration<=0:raise ValueError('Cannot measure video packet bytes/time span')
    return dict(bps=round(total*8/duration),packet_bytes=total,packets=count,duration_seconds=duration,
                first_pts=first,last_end_pts=last,method='video_packets')


def probe(path,ffprobe='ffprobe'):
    return json_command([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(path)])


def check_timing(path,stream,ffprobe='ffprobe'):
    fps=frame_rate(stream)
    if fps<=0:raise ValueError('Cannot preserve an unknown input frame rate')
    command=[ffprobe,'-v','error','-select_streams','v:0','-show_frames','-show_entries',
        'frame=best_effort_timestamp_time','-of','compact=p=0:nk=0',str(path)]
    count=0;first=None
    for line in stream_lines(command):
        value=number(compact_fields(line).get('best_effort_timestamp_time'))
        if value is None:
            if 'best_effort_timestamp_time' in compact_fields(line):raise ValueError('Missing/invalid frame PTS')
            continue
        if first is None:first=value
        if abs(first)>.001:raise ValueError('Nonzero video start PTS requires explicit timeline preparation; no implicit timestamp rewrite')
        if abs(value-count/fps)>max(.0005,.02/fps):raise ValueError('Variable/discontinuous frame timestamps cannot be preserved by raw HEVC muxing; no implicit frame duplication/drop')
        count+=1
        if count>2147483647:raise ValueError('Frame count exceeds supported RPU/PowerShell range')
    if not count:raise ValueError('No decoded source frames')
    return dict(frames=count,duration_seconds=count/fps,fps=fps)


def video_bitrate(path,ffprobe='ffprobe',force_packets=False,media=None,progress_path=None):
    media=media or probe(path,ffprobe)
    stream=next(s for s in media['streams'] if s['codec_type']=='video')
    value=number(stream.get('bit_rate'))
    duration=number(stream.get('duration'))
    if duration is None and stream.get('duration_ts') is not None:
        try:duration=float(Fraction(stream['time_base'])*int(stream['duration_ts']))
        except (ValueError,KeyError,ZeroDivisionError):pass
    # Never use format.bit_rate (which includes audio and mux overhead) or BPS tags.
    if not force_packets and value and 0<value<1e12 and duration and duration>0:
        return dict(bps=round(value),duration_seconds=duration,method='stream.bit_rate')
    command=[ffprobe,'-v','error','-select_streams','v:0','-show_packets','-show_entries',
             'packet=pts_time,dts_time,duration_time,size,flags','-of','compact=p=0:nk=0',str(path)]
    return packet_average((compact_fields(line) for line in stream_lines(command)),frame_rate(stream),progress_path)


def choose_rate(source_bps,mode='Source',custom_kbps=0,max_kbps=0,platform_target=28000,platform_max=30000):
    mode=mode.lower()
    if mode=='quality':return dict(mode='Quality',source_bps=source_bps,target_kbps=0,max_kbps=0)
    if mode=='custom':
        if custom_kbps<=0:raise ValueError('Custom bitrate must be positive')
        target=custom_kbps
    elif mode in ('source','platform'):
        if source_bps<=0:raise ValueError('Source video bitrate unavailable')
        target=max(1,round(source_bps/1000))
        if mode=='platform':target=min(target,platform_target)
    else:raise ValueError('Unknown bitrate strategy')
    maximum=max_kbps or (min(platform_max,max(target,round(target*1.5))) if mode=='platform' else round(target*1.5))
    if maximum<target:raise ValueError('Maximum bitrate cannot be below the target; use Platform mode explicitly')
    return dict(mode=mode.capitalize(),source_bps=source_bps,target_kbps=target,max_kbps=maximum)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--ffprobe',default='ffprobe')
    parser.add_argument('--packets',action='store_true')
    parser.add_argument('--output',required=True)
    parser.add_argument('--strategy',choices=['Source','Platform','Custom','Quality'])
    parser.add_argument('--check-timing',action='store_true')
    parser.add_argument('--custom-kbps',type=int,default=0)
    parser.add_argument('--max-kbps',type=int,default=0)
    parser.add_argument('--platform-target',type=int,default=28000)
    parser.add_argument('--platform-max',type=int,default=30000)
    parser.add_argument('-progress',dest='progress')
    parser.add_argument('-nostats',action='store_true')
    args=parser.parse_args()
    media=probe(args.input,args.ffprobe)
    rate=video_bitrate(args.input,args.ffprobe,args.packets,media,args.progress)
    result=dict(path=str(Path(args.input).resolve()),media=media,video_bitrate=rate)
    result['audio_precheck']=[hires_precheck(s) for s in media['streams'] if s['codec_type']=='audio']
    from truehdr_backend import source_video_filter
    try:
        source_video_filter(next(s for s in media['streams'] if s['codec_type']=='video'))
        result['truehdr_input_error']=None
    except ValueError as error:result['truehdr_input_error']=str(error)
    if args.check_timing:result['timing']=check_timing(args.input,next(s for s in media['streams'] if s['codec_type']=='video'),args.ffprobe)
    if args.strategy:
        result['rate_plan']=choose_rate(rate['bps'],args.strategy,args.custom_kbps,args.max_kbps,args.platform_target,args.platform_max)
    write_json(args.output,result)
    print(f'Video stream bitrate: {rate["bps"]/1e6:.3f} Mbps ({rate["method"]})')


if __name__=='__main__':main()
