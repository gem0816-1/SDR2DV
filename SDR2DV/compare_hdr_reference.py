"""Compare aligned HLG base layers under one BT.2100 reference display model.

This measures brightness matching, not Dolby device rendering or visual quality.
Only decoded video pixels are measured; RPU/MaxCLL are never used as pixel data.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np

from hdr_math import hlg_eotf, luminance, pq_encode
from media_probe import probe
from pipe_io import CHUNK_BYTES
from task_io import CREATE_FLAGS


def tool_path(name, supplied=None):
    if supplied:return supplied
    config=Path(__file__).resolve().parent.parent/'.tools/tools.json'
    if config.exists():
        value=json.loads(config.read_text(encoding='utf-8-sig')).get('tools',{}).get(name)
        if value and Path(value).is_file():return value
    value=shutil.which(name)
    if not value:raise FileNotFoundError(f'{name} unavailable; deploy the project or supply --{name}')
    return value


def video_info(path, ffprobe):
    media=probe(str(path),ffprobe)
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    if (video.get('color_transfer')!='arib-std-b67' or video.get('color_primaries')!='bt2020' or
            video.get('color_space')!='bt2020nc' or video.get('color_range') not in ('tv','pc')):
        raise ValueError('Comparison requires explicitly tagged BT.2020 NCL / HLG video')
    duration=float(video.get('duration') or media.get('format',{}).get('duration') or 0)
    rate=Fraction(video['r_frame_rate'])
    if duration<=0 or rate<=0:raise ValueError('Unknown video duration/frame rate')
    return video,duration,rate


def decoded_frame(path, seconds, video, width=640, ffmpeg='ffmpeg'):
    w=video['width'] if width==0 else min(width,video['width'])
    h=round(video['height']*w/video['width'])
    ran='full' if video['color_range']=='pc' else 'limited'
    chain=(f'zscale=w={w}:h={h}:filter=spline36:tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin={ran}:'
           't=arib-std-b67:p=bt2020:m=gbr:r=full:agamma=0,format=gbrpf32le')
    command=[ffmpeg,'-v','error','-nostdin','-noautorotate','-threads','4','-ss',f'{seconds:.9f}',
             '-i',str(path),'-map','0:v:0','-an','-sn','-dn','-vf',chain,'-frames:v','1',
             '-f','rawvideo','-pix_fmt','gbrpf32le','-blocksize',str(CHUNK_BYTES),'pipe:1']
    raw=subprocess.check_output(command,creationflags=CREATE_FLAGS)
    if len(raw)!=w*h*12:raise ValueError(f'Incomplete frame at {seconds}s in {path}')
    rgb=np.frombuffer(raw,'<f4').reshape(3,h,w)[[2,0,1]].transpose(1,2,0)
    return hlg_eotf(rgb)


def brightness_metrics(reference,candidate):
    if reference.shape!=candidate.shape:raise ValueError('Aligned frame shapes differ')
    if not np.isfinite(reference).all() or not np.isfinite(candidate).all():raise ValueError('Nonfinite decoded pixels')
    ref=luminance(reference);value=luminance(candidate)
    difference=np.abs(pq_encode(value)-pq_encode(ref))
    a,b=np.log1p(ref).ravel(),np.log1p(value).ravel()
    correlation=float(np.corrcoef(a,b)[0,1]) if a.std()>1e-10 and b.std()>1e-10 else None
    result=dict(pq_luminance_mae=float(difference.mean()),log_luminance_correlation=correlation)
    for label,light,rgb in [('reference',ref,reference),('candidate',value,candidate)]:
        result[label]=dict(mean_y_nits=float(light.mean()),max_y_nits=float(light.max()),
            max_rgb_nits=float(rgb.max()),y_percentiles=dict(zip(['p50','p90','p99','p999'],
                map(float,np.percentile(light,[50,90,99,99.9])))),fraction_y_gt1000=float((light>1000).mean()))
    return result


def compare(reference,candidate,baseline=None,frames=24,width=640,start=0,end=None,ffmpeg=None,ffprobe=None):
    ffmpeg,ffprobe=tool_path('ffmpeg',ffmpeg),tool_path('ffprobe',ffprobe)
    if not 2<=frames<=240 or (width!=0 and width<32):raise ValueError('Use 2..240 frames and width0 or >=32')
    paths={'reference':Path(reference),'candidate':Path(candidate)}
    if baseline:paths['baseline']=Path(baseline)
    info={k:video_info(v,ffprobe) for k,v in paths.items()}
    ref,duration,rate=info['reference']
    for video,seconds,fps in info.values():
        if ((video['width'],video['height'])!=(ref['width'],ref['height']) or fps!=rate or
                abs(seconds-duration)>.1):raise ValueError('Videos must have aligned geometry/frame rate/duration; confirm matching pictures separately')
    end=duration if end is None else end
    if not np.isfinite([start,end]).all() or not 0<=start<end<=duration:raise ValueError('Invalid comparison interval')
    # Integer frame centres keep all seeks on the same CFR frame index.
    times=np.floor((start+(np.arange(frames)+.5)*(end-start)/frames)*float(rate))/float(rate)
    records=[]
    for seconds in times:
        images={k:decoded_frame(path,float(seconds),info[k][0],width,ffmpeg) for k,path in paths.items()}
        row=dict(seconds=float(seconds),candidate=brightness_metrics(images['reference'],images['candidate']))
        if baseline:row['baseline']=brightness_metrics(images['reference'],images['baseline'])
        records.append(row)
    result=dict(schema=1,method='BT.2100 HLG base-layer pixels; 1000cd/m2 ideal black gamma1.2; super-whites retained',
        spatial_width=width or ref['width'],frames=records,paths={k:str(v.resolve()) for k,v in paths.items()},
        dolby_display_render_tested=False,visual_quality_score=False,
        candidate_pq_luminance_mae=float(np.mean([r['candidate']['pq_luminance_mae'] for r in records])))
    if baseline:
        before=float(np.mean([r['baseline']['pq_luminance_mae'] for r in records]))
        result.update(baseline_pq_luminance_mae=before,
            brightness_error_reduction_percent=(1-result['candidate_pq_luminance_mae']/before)*100 if before>0 else None)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',required=True)
    parser.add_argument('--candidate',required=True)
    parser.add_argument('--baseline')
    parser.add_argument('--frames',type=int,default=24)
    parser.add_argument('--width',type=int,default=640,help='0 for full resolution; default640 is explicitly spatially sampled')
    parser.add_argument('--start',type=float,default=0)
    parser.add_argument('--end',type=float)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    parser.add_argument('--output',required=True,help='New JSON path; will not overwrite an existing report')
    args=vars(parser.parse_args());output=Path(args.pop('output'))
    if output.exists():parser.error('Output report already exists')
    result=compare(**args)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:v for k,v in result.items() if k.endswith('mae') or k.endswith('percent')},allow_nan=False))
