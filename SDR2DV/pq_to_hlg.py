"""Exact BT.2100 display-reference conversion on CUDA, bounded one-frame buffers.

1000-nit ideal-black HLG display, gamma 1.2. Uniform RGB scaling at the HLG
colour-volume boundary, with an optional reference brightness/colour preset.
16-bit HLG RGB is piped
through the single 4:2:0 conversion into a lossless Main10 cache.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import numpy as np
from hdr_math import display_to_hlg
from truehdr_backend import CREATE_FLAGS, read_frame
from brightness_mapping import luminance_lut, map_display_rgb, mapping_info, correct_warm_skin, apply_mapping


def rgb_nits_to_hlg(cp, d, preserve_headroom=False):
    d = cp.maximum(d, 0) / cp.float32(1000)
    y = .2627*d[0] + .6780*d[1] + .0593*d[2]
    scene = d * cp.maximum(y, cp.float32(1e-30)) ** cp.float32(-1/6)
    maximum = cp.ones_like(y) if preserve_headroom else cp.maximum(scene.max(axis=0), 1)
    scene /= maximum
    code = cp.where(scene <= 1/12, cp.sqrt(3*scene),
                    .17883277*cp.log(cp.maximum(12*scene-.28466892, cp.float32(1e-30)))+.55991073)
    return code, maximum > 1 + 1e-6


def hlg_transport_format(tone):
    return 'gbrpf32le' if tone['name']=='realdolby' else 'gbrp16le'


def hlg_transport_planes(xp, code, tone):
    if tone['name']=='realdolby':
        # UNORM16 would destroy HLG codes above1 before the YUV conversion.
        return xp.ascontiguousarray(code[[1,2,0]], dtype=xp.float32)
    return xp.rint(xp.clip(code[[1,2,0]],0,1)*65535).astype(xp.uint16)


def pq_display_rgb(xp, display, tone):
    peak = 10000 if tone['name']=='realdolby' else 1000
    return display/xp.maximum(xp.max(display,axis=0)/peak,1)[None,...]


def packed_to_hlg(cp, packed, mapping_lut=None, strength=1.):
    rgb = cp.stack([((packed >> shift) & 1023).astype(cp.float32) / 1023 for shift in (0,10,20)])
    return rgb_pq_to_hlg(cp, rgb, mapping_lut, strength)


def rgb_pq_to_nits(cp, rgb):
    p = rgb ** cp.float32(1/78.84375)
    return cp.float32(10000) * (cp.maximum(p-.8359375,0) /
                              cp.maximum(18.8515625-18.6875*p,cp.float32(1e-12))) ** cp.float32(1/.1593017578125)


def rgb_pq_to_hlg(cp, rgb, mapping_lut=None, strength=1., skin_strength=0.):
    nits = map_display_rgb(cp,rgb_pq_to_nits(cp,rgb),mapping_lut,strength)
    nits = correct_warm_skin(cp,nits,skin_strength)
    code, limited = rgb_nits_to_hlg(cp, nits)
    # Raw planar GBR ordering, full-range 16-bit UNORM, no colour tag substitution.
    planes = cp.rint(cp.clip(code[[1,2,0]], 0, 1)*65535).astype(cp.uint16)
    return planes, limited


def cuda_reference_check(cp):
    rgb = np.array([[0,0,0],[18]*3,[203]*3,[1000]*3,[1000,0,0],[0,0,1000],
                    [50,300,100],[20,20,800]], dtype=np.float32)
    actual, _ = rgb_nits_to_hlg(cp, cp.asarray(rgb.T[:,None,:]))
    actual = cp.asnumpy(actual)[:,0,:].T
    expected, _ = display_to_hlg(rgb.astype(float))
    delta = float(np.max(np.abs(actual-expected)))
    if delta > 1e-6:
        raise RuntimeError(f'CUDA HLG math disagrees with CPU reference: {delta}')
    return delta


def convert(args):
    import cupy as cp
    error = cuda_reference_check(cp)
    tone = mapping_info(args.tone_mapping,args.tone_strength,args.skin_strength)
    tone_lut = cp.asarray(luminance_lut(name=tone['name'])) if tone['name'] in ('reference','realdolby') else None
    media = json.loads(subprocess.check_output([args.ffprobe, '-v','error','-show_streams','-of','json',args.input]))
    v = next(s for s in media['streams'] if s['codec_type']=='video')
    hlg_input = getattr(args,'input_transfer','pq') == 'hlg'
    if hlg_input and (v.get('pix_fmt')!='yuv420p10le' or v.get('color_range')!='tv' or v.get('color_transfer')!='arib-std-b67' or
                      v.get('color_space')!='bt2020nc' or v.get('color_primaries')!='bt2020'):
        raise ValueError('HLG adapter requires the verified BT.2020 Main10 HLG cache')
    if not hlg_input and (v['pix_fmt'] != 'gbrp10le' or v['codec_name'] != 'ffv1'):
        raise ValueError('PQ adapter requires the verified TrueHDR FFV1 RGB10 cache')
    w,h=v['width'],v['height']
    out=Path(args.output)
    if out.exists():
        raise FileExistsError(out)
    from pipe_io import CHUNK_BYTES, write_frame
    dec=[args.ffmpeg,'-hide_banner','-v','error','-nostdin','-i',args.input,'-map','0:v:0','-an']
    if hlg_input:
        dec += ['-vf','zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=limited:t=arib-std-b67:p=bt2020:m=gbr:r=full:agamma=0,format=gbrpf32le']
    dec += ['-pix_fmt','gbrpf32le' if hlg_input else 'gbrp10le','-fps_mode','passthrough','-f','rawvideo',
         '-blocksize',str(CHUNK_BYTES),'pipe:1']
    from truehdr_stream import HLG_TO_YUV, cache_encoding_args
    enc=[args.ffmpeg,'-hide_banner','-v','error','-nostdin','-n','-f','rawvideo',
         '-pixel_format',hlg_transport_format(tone),'-video_size',f'{w}x{h}','-framerate',args.fps,
         '-blocksize',str(CHUNK_BYTES),'-i','pipe:0',
         '-an','-vf',HLG_TO_YUV,'-fps_mode','passthrough'] + cache_encoding_args(args.cache_encoder) + [
         '-color_primaries','bt2020','-color_trc','arib-std-b67','-colorspace','bt2020nc','-color_range','tv',str(out)]
    p=q=None
    count=limited_count=0
    start=time.monotonic()
    report=dict(reference_peak_nits=1000,black_nits=0,system_gamma=1.2,
                cuda_vs_float64_reference_max_signal_error=error,
                algorithm='BT.2100 EOTF; display-light grade; inverse HLG OOTF/OETF',
                input_transfer='hlg' if hlg_input else 'pq',
                output_peak_policy=tone.get('peak_policy','1000nit_nominal_colour_volume'),
                hlg_rgb_transport=hlg_transport_format(tone),
                input=args.input,output=args.output,decode_command=dec,encode_command=enc,
                brightness_mapping=tone,cache_encoder=args.cache_encoder)
    try:
        with open(out.with_suffix('.decode.log'),'wb') as de,open(out.with_suffix('.encode.log'),'wb') as ee:
            p=subprocess.Popen(dec,stdout=subprocess.PIPE,stderr=de,creationflags=CREATE_FLAGS)
            q=subprocess.Popen(enc,stdin=subprocess.PIPE,stderr=ee,creationflags=CREATE_FLAGS)
            while True:
                from task_io import check_cancel
                check_cancel()
                raw=read_frame(p.stdout,w*h*(12 if hlg_input else 6))
                if not raw: break
                # FFmpeg's gbrp10le -> x2bgr10le swscale path changes RGB codes
                # on this build. Read native planar FFV1 directly, with no pixel
                # format conversion; reorder only G,B,R -> R,G,B on CUDA.
                planar=cp.asarray(np.frombuffer(raw,'<f4' if hlg_input else '<u2').reshape(3,h,w))
                if hlg_input:
                    from hdr_analysis import display_rgb
                    display=display_rgb(cp,planar[[2,0,1]],'arib-std-b67')
                else:
                    display=rgb_pq_to_nits(cp,planar[[2,0,1]].astype(cp.float32)/1023)
                nits=apply_mapping(cp,display,tone,tone_lut)
                code,limited=rgb_nits_to_hlg(cp,nits,preserve_headroom=tone['name']=='realdolby')
                planes=hlg_transport_planes(cp,code,tone)
                limited_count+=int(limited.sum().get())
                write_frame(q.stdin,cp.asnumpy(planes).astype('<f4' if tone['name']=='realdolby' else '<u2',copy=False),
                            label='HLG cache',frame_number=count+1)
                count+=1
                if count%15==0:
                    import shutil
                    if shutil.disk_usage(out.parent).free<1024**3:
                        raise RuntimeError('Temporary disk has less than 1 GiB free')
                if args.progress and count%15==0:
                    from fractions import Fraction
                    from task_io import progress
                    progress(args.progress,count,count/float(Fraction(args.fps)))
            q.stdin.close()
            if p.wait() or q.wait(): raise RuntimeError('PQ/HLG FFmpeg pipe failed; see logs')
        if count!=args.frames: raise RuntimeError(f'HLG frames {count} != PQ frames {args.frames}')
        report.update(success=True,frames=count,colour_volume_limited_fraction=limited_count/(count*w*h),
                      elapsed_seconds=time.monotonic()-start)
    except BaseException as exc:
        report.update(success=False,error=str(exc))
        for suffix in ('.decode.log','.encode.log'):
            log=out.with_suffix(suffix)
            if log.exists(): print(log.read_text(errors='replace')[-4000:])
        raise
    finally:
        for child in (p,q):
            if child is not None and child.poll() is None: child.kill();child.wait()
        Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--fps',required=True)
    parser.add_argument('--frames',required=True,type=int)
    parser.add_argument('--ffmpeg',default='ffmpeg')
    parser.add_argument('--ffprobe',default='ffprobe')
    parser.add_argument('--input-transfer',choices=['pq','hlg'],default='pq')
    parser.add_argument('--tone-mapping',type=str.lower,choices=['reference','neutral','natural','realdolby'],default='neutral')
    parser.add_argument('--tone-strength',type=float,default=1.)
    parser.add_argument('--skin-strength',type=float)
    parser.add_argument('--cache-encoder',choices=['nvenc','x265'],default='nvenc')
    parser.add_argument('-progress',dest='progress')
    parser.add_argument('-nostats',action='store_true')
    convert(parser.parse_args())
