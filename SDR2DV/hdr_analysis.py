"""Full-resolution, every-frame HDR analysis and community shot-level L1.

CTA content light levels use display-linear MaxRGB, NOT EOTF(mean PQ),
and not BT.2020 CIE Y. CIE Y diagnostics are reported separately. This is
not Dolby's proprietary/certified analysis and creates no creative trims.
"""
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np
from media_probe import frame_rate,probe
from task_io import CREATE_FLAGS,check_cancel,progress,write_json


def fill_frame(pipe,view):
    """Reuse one frame buffer instead of allocating/copying 4K RGB bytes twice."""
    offset=0
    while offset<len(view):
        count=pipe.readinto(view[offset:])
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


def generator_config(frames,shots,profile='8.4'):
    max_cll=max(f['max_rgb_nits'] for f in frames)
    max_fall=max(f['mean_max_rgb_nits'] for f in frames)
    return dict(cm_version='V40',profile=profile,long_play_mode=False,
        default_metadata_blocks=[{'Level9':dict(length=1,source_primary_index=2)}],
        level6=dict(max_display_mastering_luminance=1000,min_display_mastering_luminance=1,
            max_content_light_level=min(65535,round(max_cll)),max_frame_average_light_level=min(65535,round(max_fall))),
        shots=[dict(start=s['start'],duration=s['duration'],metadata_blocks=[{'Level1':s['encoded_l1']}]) for s in shots])


def analyze(path,output,generator=None,ffmpeg='ffmpeg',ffprobe='ffprobe',fps=None,
            device='auto',cuts_file=None,progress_path=None,active_area=(0,0,0,0)):
    media=probe(path,ffprobe)
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    transfer=video.get('color_transfer')
    if transfer not in ('smpte2084','arib-std-b67'):raise ValueError('Final HDR analysis requires explicit PQ or HLG')
    if video.get('color_primaries')!='bt2020':raise ValueError('HDR analysis requires BT.2020')
    rate=float(Fraction(fps)) if fps else frame_rate(video)
    if rate<=0:raise ValueError('Unknown frame rate')
    width,height=video['width'],video['height']
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
    command=[ffmpeg,'-v','error','-nostdin','-threads',str(min(8,os.cpu_count() or 1)),'-i',str(path),'-map','0:v:0']
    command+=['-pix_fmt','yuv420p10le'] if native_gpu else ['-vf',chain]
    command+=[
             '-fps_mode','passthrough','-an','-sn','-dn','-f','rawvideo','pipe:1']
    frames=[];detector=SceneDetector(max(8,round(rate*.3)));start=time.monotonic()
    count=width*height*3//2 if native_gpu else width*height*3
    dtype='<u2' if native_gpu else '<f4';nbytes=count*np.dtype(dtype).itemsize
    host=np.empty(count,dtype=dtype) if xp is np else np.frombuffer(
        xp.cuda.alloc_pinned_memory(nbytes),dtype=dtype,count=count)
    view=memoryview(host).cast('B')
    with tempfile.TemporaryFile() as errors:
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,creationflags=CREATE_FLAGS)
        try:
            while True:
                check_cancel()
                if not fill_frame(child.stdout,view):break
                if native_gpu:rgb=yuv420_rgb(xp,xp.asarray(host),width,height,siting,ran=='full')
                else:rgb=xp.asarray(host.reshape(3,height,width))[[2,0,1]]
                rgb=rgb[:,top:height-bottom or None,left:width-right or None]
                stats,thumbnail=frame_statistics(xp,display_rgb(xp,rgb,transfer))
                frames.append(stats);detector.push(thumbnail)
                if len(frames)%15==0:progress(progress_path,len(frames),len(frames)/rate)
            if child.wait():
                errors.seek(0);raise RuntimeError(errors.read().decode('utf-8',errors='replace'))
        finally:
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close()
    if not frames:raise ValueError('HDR analysis returned no frames')
    cuts=json.loads(Path(cuts_file).read_text(encoding='utf-8-sig')) if cuts_file else detector.finish()
    shots=make_shots(frames,cuts)
    config=generator_config(frames,shots,'8.4' if transfer=='arib-std-b67' else '8.1')
    if any(active_area):
        config['default_metadata_blocks'].append({'Level5':dict(active_area_left_offset=left,
            active_area_right_offset=right,active_area_top_offset=top,active_area_bottom_offset=bottom)})
    result=dict(path=str(Path(path).resolve()),frames=len(frames),fps=rate,width=width,height=height,
        model='BT.2100 HLG 1000nit/gamma1.2/ideal black; ST2084 PQ absolute',
        light_level_method='CTA-861 linear MaxRGB, every pixel of every decoded frame in analysis area',
        active_area=dict(left=left,right=right,top=top,bottom=bottom),
        max_cll_nits=max(f['max_rgb_nits'] for f in frames),
        max_fall_nits=max(f['mean_max_rgb_nits'] for f in frames),
        max_cie_y_nits=max(f['max_y_nits'] for f in frames),
        max_frame_mean_y_nits=max(f['mean_y_nits'] for f in frames),
        mean_y_nits=sum(f['mean_y_nits'] for f in frames)/len(frames),
        max_frame_fraction_maxrgb_gt1000=max(f['pixels_maxrgb_gt1000']/f['pixel_count'] for f in frames),
        max_frame_fraction_y_gt1000=max(f['pixels_y_gt1000']/f['pixel_count'] for f in frames),
        max_frame_fraction_maxrgb_gt1100=max(f['pixels_maxrgb_gt1100']/f['pixel_count'] for f in frames),
        max_frame_fraction_y_gt1100=max(f['pixels_y_gt1100']/f['pixel_count'] for f in frames),
        shots=shots,scene_detection='manual' if cuts_file else 'conservative RGB-PQ thumbnail cuts with single-frame flash suppression',
        l1_analysis_method='community PQ-encoded MaxRGB measurements; not Dolby proprietary LMS/CM backward compatibility analysis',
        scene_detection_certified=False,dolby_analysis_certified=False,creative_trims_created=False,
        compatibility_clamped_shots=sum(s['compatibility_clamped'] for s in shots),
        analysis_device='cuda' if xp is not np else 'cpu',elapsed_seconds=time.monotonic()-start,
        rgb_reconstruction='BT.2020 NCL / bilinear / '+siting,analysis_native_yuv_gpu=native_gpu,
        frame_statistics=frames)
    write_json(output,result)
    if generator:write_json(generator,config)
    print(f'Full HDR analysis: {len(frames)} frames, {len(shots)} shots; MaxCLL={result["max_cll_nits"]:.2f}, MaxFALL={result["max_fall_nits"]:.2f} nit')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('--output',required=True);parser.add_argument('--generator')
    parser.add_argument('--ffmpeg',default='ffmpeg');parser.add_argument('--ffprobe',default='ffprobe')
    parser.add_argument('--fps');parser.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    parser.add_argument('--cuts-file');parser.add_argument('--active-area',default='0,0,0,0')
    parser.add_argument('-progress',dest='progress');parser.add_argument('-nostats',action='store_true')
    args=parser.parse_args()
    analyze(args.input,args.output,args.generator,args.ffmpeg,args.ffprobe,args.fps,args.device,args.cuts_file,
            args.progress,tuple(map(int,args.active_area.split(','))))
