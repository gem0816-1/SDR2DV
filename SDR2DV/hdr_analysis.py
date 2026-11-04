"""Full-resolution, every-frame HDR analysis and community shot-level L1.

CTA content light levels use display-linear MaxRGB, NOT EOTF(mean PQ),
and not BT.2020 CIE Y. CIE Y diagnostics are reported separately. This is
not Dolby's proprietary/certified analysis and creates no creative trims.
"""
import argparse
from contextlib import nullcontext
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
from pipe_io import CHUNK_BYTES
from media_probe import frame_rate,probe
from task_io import CREATE_FLAGS,check_cancel,progress,write_json


def fill_frame(pipe,view):
    """Reuse one frame buffer instead of allocating/copying 4K RGB bytes twice."""
    offset=0
    while offset<len(view):
        count=pipe.readinto(view[offset:offset+CHUNK_BYTES])
        if not count:
            if offset:raise RuntimeError('Truncated decoded HDR frame')
            return False
        offset+=count
    return True


def pq_code(xp,nits):
    value=(xp.maximum(nits,0)/10000)**(2610/16384)
    return ((3424/4096+(2413/128)*value)/(1+(2392/128)*value))**(2523/32)


def display_rgb(xp,code,transfer):
    code=xp.maximum(code,0)
    if transfer=='smpte2084':
        value=xp.minimum(code,1)**(1/78.84375)
        return 10000*(xp.maximum(value-.8359375,0)/xp.maximum(18.8515625-18.6875*value,1e-12))**(1/.1593017578125)
    scene=xp.where(code<=.5,code*code/3,(xp.exp((code-.55991073)/.17883277)+.28466892)/12)
    y=.2627*scene[0]+.6780*scene[1]+.0593*scene[2]
    return 1000*scene*xp.maximum(y,1e-30)[None,...]**.2


def frame_statistics(xp,rgb):
    maximum=xp.maximum(rgb.max(axis=0),0)
    y=.2627*rgb[0]+.6780*rgb[1]+.0593*rgb[2]
    pq=pq_code(xp,maximum)
    scalars=xp.stack((maximum.max(),maximum.mean(),y.max(),y.mean(),pq.min(),pq.max(),pq.mean(),
        xp.count_nonzero(maximum>1000),xp.count_nonzero(y>1000),xp.count_nonzero(maximum>1100),xp.count_nonzero(y>1100)))
    values=np.asarray(scalars) if xp is np else xp.asnumpy(scalars)
    if not np.isfinite(values).all():raise ValueError('Non-finite decoded HDR statistics')
    step_y=max(1,rgb.shape[1]//36);step_x=max(1,rgb.shape[2]//64)
    thumbnail=pq_code(xp,rgb[:,::step_y,::step_x])
    thumbnail=np.asarray(thumbnail) if xp is np else xp.asnumpy(thumbnail)
    return dict(max_rgb_nits=float(values[0]),mean_max_rgb_nits=float(values[1]),
        max_y_nits=float(values[2]),mean_y_nits=float(values[3]),
        min_pq=float(values[4]*4095),max_pq=float(values[5]*4095),avg_pq=float(values[6]*4095),
        pixel_count=int(maximum.size),pixels_maxrgb_gt1000=int(values[7]),pixels_y_gt1000=int(values[8]),
        pixels_maxrgb_gt1100=int(values[9]),pixels_y_gt1100=int(values[10])),thumbnail


def cut_distance(a,b):
    return float(np.abs(a-b).mean())


def find_cuts(thumbnails,min_frames=8,threshold=.12):
    detector=SceneDetector(min_frames,threshold)
    for thumbnail in thumbnails:detector.push(thumbnail)
    return detector.finish()


def is_cut(a,b,threshold):
    distance=cut_distance(a,b)
    # Absolute threshold alone misses cuts between dark shots. Require both a
    # minimum PQ difference and a large relative change, not per-frame exposure.
    relative=distance/(float(np.abs(a).mean()+np.abs(b).mean())*.5+.025)
    return distance>=threshold or (distance>=.025 and relative>=.30)


def flash(a,b,c):
    return cut_distance(a,c)<max(.0005,min(.012,cut_distance(a,b)*.35))


class SceneDetector:
    """One-frame lookahead; constant thumbnail memory even for long films."""
    def __init__(self,min_frames=8,threshold=.12):
        self.min_frames=min_frames;self.threshold=threshold;self.cuts=[0]
        self.previous=[];self.index=0

    def push(self,image):
        self.previous.append(image.astype(np.float16))
        if len(self.previous)>=3:
            a,b,c=self.previous[-3:]
            candidate=self.index-1
            if (candidate-self.cuts[-1]>=self.min_frames and
                is_cut(a,b,self.threshold) and
                not flash(a,b,c) and
                not getattr(self,'flash_return',False)):
                self.cuts.append(candidate)
            self.flash_return=(is_cut(a,b,self.threshold) and flash(a,b,c))
            self.previous=self.previous[-2:]
        self.index+=1

    def finish(self):
        if len(self.previous)==2 and self.index-1-self.cuts[-1]>=self.min_frames:
            if is_cut(*self.previous,self.threshold) and not getattr(self,'flash_return',False):
                self.cuts.append(self.index-1)
        return self.cuts


def make_shots(frames,cuts):
    if not frames or not cuts or cuts[0]!=0 or sorted(set(cuts))!=cuts or cuts[-1]>=len(frames):
        raise ValueError('Shot cuts must be unique sorted frame indices, begin with 0, and stay inside the video')
    shots=[]
    for start,end in zip(cuts,cuts[1:]+[len(frames)]):
        part=frames[start:end]
        raw=dict(min_pq=round(min(f['min_pq'] for f in part)),
                 max_pq=round(max(f['max_pq'] for f in part)),
                 avg_pq=round(sum(f['avg_pq'] for f in part)/len(part)))
        # dovi_tool 2.3.4 applies these CMv4 backward-compatibility bounds.
        # Keep raw measurements beside the encoded values; never pretend the
        # compatibility floor is the measured brightness or invent L3/L8 trims.
        encoded=dict(min_pq=min(max(raw['min_pq'],0),12),max_pq=min(max(raw['max_pq'],2081),4095))
        encoded['avg_pq']=min(max(raw['avg_pq'],1229),encoded['max_pq']-1)
        shots.append(dict(start=start,duration=end-start,measured_l1=raw,encoded_l1=encoded,
                          compatibility_clamped=raw!=encoded))
    return shots


class ShotSummary:
    """Same ordered sums and rounding as make_shots, without retaining frames."""
    def __init__(self,start):
        self.start=start;self.count=0;self.low=float('inf');self.high=0.;self.total=0.

    def add(self,stats):
        self.count+=1;self.low=min(self.low,stats['min_pq']);self.high=max(self.high,stats['max_pq'])
        self.total+=stats['avg_pq']

    def result(self):
        raw=dict(min_pq=round(self.low),max_pq=round(self.high),avg_pq=round(self.total/self.count))
        encoded=dict(min_pq=min(max(raw['min_pq'],0),12),max_pq=min(max(raw['max_pq'],2081),4095))
        encoded['avg_pq']=min(max(raw['avg_pq'],1229),encoded['max_pq']-1)
        return dict(start=self.start,duration=self.count,measured_l1=raw,encoded_l1=encoded,compatibility_clamped=raw!=encoded)


class StreamingAnalysis:
    """One pending statistic for detector lookahead; memory scales with shots.

    Global light levels and shot sums preserve the old frame iteration order.
    Thumbnails are still handled by the unchanged SceneDetector.
    """
    def __init__(self):
        self.count=0;self.pending=None;self.shot=ShotSummary(0);self.shots=[];self.total_y=0.
        self.maxima=dict.fromkeys(('max_cll_nits','max_fall_nits','max_cie_y_nits','max_frame_mean_y_nits',
            'max_frame_fraction_maxrgb_gt1000','max_frame_fraction_y_gt1000',
            'max_frame_fraction_maxrgb_gt1100','max_frame_fraction_y_gt1100'),0.)

    def _consume(self,cut):
        if self.pending is None:return
        index,stats=self.pending
        if cut==index and self.shot.count:
            self.shots.append(self.shot.result());self.shot=ShotSummary(index)
        self.shot.add(stats);self.pending=None

    def push(self,stats,cut=None):
        self._consume(cut)
        self.pending=(self.count,stats);self.count+=1;self.total_y+=stats['mean_y_nits']
        values=(stats['max_rgb_nits'],stats['mean_max_rgb_nits'],stats['max_y_nits'],stats['mean_y_nits'],
            stats['pixels_maxrgb_gt1000']/stats['pixel_count'],stats['pixels_y_gt1000']/stats['pixel_count'],
            stats['pixels_maxrgb_gt1100']/stats['pixel_count'],stats['pixels_y_gt1100']/stats['pixel_count'])
        for key,value in zip(self.maxima,values):self.maxima[key]=max(self.maxima[key],value)

    def finish(self,cut=None):
        if not self.count:raise ValueError('HDR analysis returned no frames')
        self._consume(cut)
        if self.shot.count:self.shots.append(self.shot.result());self.shot=ShotSummary(self.count)
        return {**self.maxima,'mean_y_nits':self.total_y/self.count,'shots':self.shots}


def generator_config(frames,shots,profile='8.4'):
    max_cll=max(f['max_rgb_nits'] for f in frames)
    max_fall=max(f['mean_max_rgb_nits'] for f in frames)
    return generator_from_levels(max_cll,max_fall,shots,profile)


def generator_from_levels(max_cll,max_fall,shots,profile='8.4'):
    return dict(cm_version='V40',profile=profile,long_play_mode=False,
        default_metadata_blocks=[{'Level9':dict(length=1,source_primary_index=2)}],
        level6=dict(max_display_mastering_luminance=1000,min_display_mastering_luminance=1,
            max_content_light_level=min(65535,round(max_cll)),max_frame_average_light_level=min(65535,round(max_fall))),
        shots=[dict(start=s['start'],duration=s['duration'],metadata_blocks=[{'Level1':s['encoded_l1']}]) for s in shots])


def analyze(path,output,generator=None,ffmpeg='ffmpeg',ffprobe='ffprobe',fps=None,
            device='auto',cuts_file=None,progress_path=None,active_area=(0,0,0,0),frame_stats_path=None):
    media=probe(path,ffprobe)
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    transfer=video.get('color_transfer')
    if transfer not in ('smpte2084','arib-std-b67'):raise ValueError('Final HDR analysis requires explicit PQ or HLG')
    if video.get('color_primaries')!='bt2020':raise ValueError('HDR analysis requires BT.2020')
    rate=float(Fraction(fps)) if fps else frame_rate(video)
    if not np.isfinite(rate) or not 0<rate<=1000:raise ValueError('Invalid frame rate')
    width,height=video['width'],video['height']
    if not (0<width<=16384 and 0<height<=16384) or width%2 or height%2:raise ValueError('Invalid HDR analysis dimensions')
    left,right,top,bottom=active_area
    if min(active_area)<0 or left+right>=width or top+bottom>=height:raise ValueError('Invalid active area')
    xp=np
    if device!='cpu':
        try:
            import cupy
            cupy.cuda.Device(0).use()
            xp=cupy
        except Exception:
            if device=='cuda':raise
    ran='full' if video.get('color_range')=='pc' else 'limited'
    native_gpu=(xp is not np and video.get('pix_fmt')=='yuv420p10le' and video.get('color_space')=='bt2020nc')
    siting=video.get('chroma_location','left')
    if native_gpu:
        from hdr_cuda import yuv420_rgb,SITING
        if siting not in SITING:native_gpu=False
    chain=(f'zscale=tin={transfer}:pin=bt2020:min={video["color_space"]}:rin={ran}:'
           f't={transfer}:p=bt2020:m=gbr:r=full:agamma=0:filter=bilinear,format=gbrpf32le')
    command=[ffmpeg,'-v','error','-xerror','-nostdin','-threads',str(min(8,os.cpu_count() or 1)),
             '-err_detect','explode','-i',str(path),'-map','0:v:0']
    command+=['-pix_fmt','yuv420p10le'] if native_gpu else ['-vf',chain]
    command+=[
             '-fps_mode','passthrough','-an','-sn','-dn','-f','rawvideo',
             '-blocksize',str(CHUNK_BYTES),'pipe:1']
    aggregate=StreamingAnalysis();detector=SceneDetector(max(8,round(rate*.3)));start=time.monotonic()
    manual=json.loads(Path(cuts_file).read_text(encoding='utf-8-sig')) if cuts_file else None
    if manual is not None and (not isinstance(manual,list) or not manual or manual[0]!=0 or
        any(type(i) is not int or i<0 for i in manual) or sorted(set(manual))!=manual):raise ValueError('Invalid manual shot cuts')
    manual_set=set(manual) if manual is not None else None
    count=width*height*3//2 if native_gpu else width*height*3
    dtype='<u2' if native_gpu else '<f4';nbytes=count*np.dtype(dtype).itemsize
    host=np.empty(count,dtype=dtype) if xp is np else np.frombuffer(
        xp.cuda.alloc_pinned_memory(nbytes),dtype=dtype,count=count)
    view=memoryview(host).cast('B')
    with tempfile.TemporaryFile() as errors, (open(frame_stats_path,'x',encoding='utf-8') if frame_stats_path else nullcontext()) as frame_log:
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,creationflags=CREATE_FLAGS)
        try:
            while True:
                check_cancel()
                if not fill_frame(child.stdout,view):break
                if native_gpu:rgb=yuv420_rgb(xp,xp.asarray(host),width,height,siting,ran=='full')
                else:rgb=xp.asarray(host.reshape(3,height,width))[[2,0,1]]
                rgb=rgb[:,top:height-bottom or None,left:width-right or None]
                stats,thumbnail=frame_statistics(xp,display_rgb(xp,rgb,transfer))
                detector.push(thumbnail)
                cut=aggregate.count-1 if manual_set is not None and aggregate.count-1 in manual_set else detector.cuts[-1] if manual_set is None else None
                aggregate.push(stats,cut)
                if frame_log:frame_log.write(json.dumps(stats,separators=(',',':'))+'\n')
                if aggregate.count%15==0:progress(progress_path,aggregate.count,aggregate.count/rate)
            if child.wait():
                errors.seek(0);raise RuntimeError(errors.read().decode('utf-8',errors='replace'))
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close()
    if manual is not None and manual[-1]>=aggregate.count:raise ValueError('Manual cuts outside decoded frame range')
    last_cut=aggregate.count-1 if manual_set is not None and aggregate.count-1 in manual_set else detector.finish()[-1] if manual_set is None else None
    summary=aggregate.finish(last_cut);shots=summary['shots']
    config=generator_from_levels(summary['max_cll_nits'],summary['max_fall_nits'],shots,'8.4' if transfer=='arib-std-b67' else '8.1')
    if any(active_area):
        config['default_metadata_blocks'].append({'Level5':dict(active_area_left_offset=left,
            active_area_right_offset=right,active_area_top_offset=top,active_area_bottom_offset=bottom)})
    result=dict(path=str(Path(path).resolve()),frames=aggregate.count,fps=rate,width=width,height=height,
        model='BT.2100 HLG 1000nit/gamma1.2/ideal black; ST2084 PQ absolute',
        light_level_method='CTA-861 linear MaxRGB, every pixel of every decoded frame in analysis area',
        active_area=dict(left=left,right=right,top=top,bottom=bottom),
        **summary,scene_detection='manual' if cuts_file else 'conservative RGB-PQ thumbnail cuts with single-frame flash suppression',
        l1_analysis_method='community PQ-encoded MaxRGB measurements; not Dolby proprietary LMS/CM backward compatibility analysis',
        scene_detection_certified=False,dolby_analysis_certified=False,creative_trims_created=False,
        compatibility_clamped_shots=sum(s['compatibility_clamped'] for s in shots),
        analysis_device='cuda' if xp is not np else 'cpu',elapsed_seconds=time.monotonic()-start,
        rgb_reconstruction='BT.2020 NCL / bilinear / '+siting,analysis_native_yuv_gpu=native_gpu,
        frame_statistics_file=str(frame_stats_path) if frame_stats_path else None,
        statistics_storage='streaming aggregates, one-frame lookahead; O(shots) metadata')
    write_json(output,result)
    if generator:write_json(generator,config)
    print(f'Full HDR analysis: {aggregate.count} frames, {len(shots)} shots; MaxCLL={result["max_cll_nits"]:.2f}, MaxFALL={result["max_fall_nits"]:.2f} nit')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('--output',required=True);parser.add_argument('--generator')
    parser.add_argument('--ffmpeg',default='ffmpeg');parser.add_argument('--ffprobe',default='ffprobe')
    parser.add_argument('--fps');parser.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    parser.add_argument('--cuts-file');parser.add_argument('--active-area',default='0,0,0,0')
    parser.add_argument('--frame-statistics',help='Optional streaming JSONL diagnostics; never read by PowerShell')
    parser.add_argument('-progress',dest='progress');parser.add_argument('-nostats',action='store_true')
    args=parser.parse_args()
    analyze(args.input,args.output,args.generator,args.ffmpeg,args.ffprobe,args.fps,args.device,args.cuts_file,
            args.progress,tuple(map(int,args.active_area.split(','))),args.frame_statistics)
