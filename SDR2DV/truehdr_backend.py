"""Official nvvfx.TrueHDR adapter; no alternate backend or synthetic fallback.

Decode with FFmpeg/zscale to full-range Rec709/D65 gamma 2.2 RGB24. Transfer
one frame to CUDA, expand to CHW float32, then call the official Python API.
SDK-owned DLPack output is copied and synchronized before the next run.
R10G10B10A2 -> x2bgr10le -> FFV1 gbrp10le preserves all RGB PQ code values.
Audio is handled only by the existing PowerShell muxer.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from fractions import Fraction

from hdr_math import pq_to_hlg_filter
from pipe_io import CHUNK_BYTES, write_frame

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_cache(args):
    manifest = Path(args.verify_cache).resolve() / 'truehdr-report.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    params = {k: getattr(args, k) for k in ('luminance', 'contrast', 'saturation', 'middle_gray', 'debanding_off')}
    if not (report.get('success') or report.get('cache_ready')) or report.get('parameters') != params:
        raise ValueError('TrueHDR cache did not finish successfully or its SDK parameters differ')
    if report.get('input_sha256') != file_hash(args.input):
        raise ValueError('TrueHDR cache source SHA256 does not match this input')
    if type(report.get('frames')) is not int or not 0<report['frames']<=2147483647:
        raise ValueError('Invalid cache frame count')
    if report.get('cache_kind') == 'hlg-yuv420p10-lossless-v1':
        hlg = manifest.parent / 'truehdr_hlg.mkv'
        if report.get('hlg_file_sha256') != file_hash(hlg):
            raise ValueError('TrueHDR HLG cache SHA256 mismatch')
        from cache_binding import verify_binding
        binding_status=verify_binding(report,hlg,getattr(args,'ffprobe','ffprobe'))
        hdr10 = manifest.parent / 'HDR10_video.mp4'
        hdr10_path = ''
        if report.get('hdr10_file_sha256'):
            if report['hdr10_file_sha256'] != file_hash(hdr10):
                raise ValueError('TrueHDR HDR10 cache SHA256 mismatch')
            verify_binding(report,hdr10,getattr(args,'ffprobe','ffprobe'))
            hdr10_path = str(hdr10)
        return dict(verified=True, kind=report['cache_kind'], hlg=str(hlg), hdr10=hdr10_path,cache_binding_status=binding_status,
                    brightness_mapping=report.get('brightness_mapping',dict(name='neutral',strength=0.)),
                    manifest=str(manifest), frames=report['frames'], sdk=report['sdk'])
    if report.get('cache_kind') not in (None,'pq-rgb-ffv1-v1'):raise ValueError('Unsupported cache kind')
    pq = manifest.parent / 'truehdr_pq.mkv'
    if report.get('pq_file_sha256') != file_hash(pq):
        raise ValueError('TrueHDR cache PQ SHA256 mismatch')
    return dict(verified=True, kind='pq-rgb-ffv1-v1', pq=str(pq), manifest=str(manifest), frames=report['frames'], sdk=report['sdk'])


def run_json(args):
    from task_io import json_command
    return json_command(args)


def runtime():
    import nvvfx
    import cupy as cp
    cp.cuda.Device(0).use()
    return cp, nvvfx


def consume(cp, capsule):
    # Raw PyCapsule, not __dlpack__ object: CuPy's legacy importer accepts it.
    cp.cuda.Device().synchronize()
    value = cp.fromDlpack(capsule).copy()
    cp.cuda.Device().synchronize()
    return value


def check(args):
    cp, nvvfx = runtime()
    sample = cp.full((3, 180, 320), .9, dtype=cp.float32)
    cp.cuda.Device().synchronize()
    with nvvfx.TrueHDR(luminance=args.luminance, contrast=args.contrast,
                      saturation=args.saturation, middle_gray=args.middle_gray,
                      debanding_off=int(args.debanding_off)) as effect:
        effect.load()
        output = consume(cp, effect.run(sample).image)
        result = cp.asnumpy(output)
        if result.shape != (180, 320) or str(result.dtype) != 'uint32':
            raise RuntimeError(f'Unexpected SDK output {result.shape}/{result.dtype}')
        rgb = [(result >> shift) & 1023 for shift in (0, 10, 20)]
        if max(int(x.max()) for x in rgb) == 0:
            raise RuntimeError('SDK returned all-black output for nonblack input')
    props = cp.cuda.runtime.getDeviceProperties(0)
    return dict(binding=nvvfx.__version__, sdk=nvvfx.get_sdk_version(),
                cupy=cp.__version__, gpu=props['name'].decode(),
                compute_capability=[props['major'], props['minor']],
                driver_api=cp.cuda.runtime.driverGetVersion(),
                cuda_runtime=cp.cuda.runtime.runtimeGetVersion(),
                inference_test='passed', output_rgb_codes=[int(x[0, 0]) for x in rgb])


def timing(args, video):
    from task_io import check_cancel, progress, stream_lines
    fps = Fraction(video['r_frame_rate'])
    if fps <= 0:
        raise ValueError('TrueHDR requires a known CFR frame rate')
    # Check decoded presentation times, not merely r_frame_rate==avg_frame_rate.
    cmd = [args.ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
           '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0', args.input]
    last = None
    count = 0
    progress(getattr(args,'progress',None),0,0,phase='source_timing')
    if not 0<float(fps)<=1000:raise ValueError('Invalid/unsupported frame rate')
    lines=stream_lines(cmd)
    try:
        for line in lines:
            check_cancel()
            field = line.strip().split(',')[0]
            if not field:continue
            t = float(field)
            import math
            if not math.isfinite(t):raise ValueError('Non-finite source PTS')
            if last is None and abs(t) > .001:
                raise ValueError('TrueHDR currently requires video start PTS=0; no implicit timestamp reset')
            if last is not None and abs(t - last - float(1 / fps)) > .0011:
                raise ValueError('TrueHDR currently rejects VFR/discontinuous PTS to protect audio sync')
            last = t
            count += 1
            if count>2147483647:raise ValueError('Frame count exceeds the supported RPU/PowerShell range')
            if count % 120 == 0:
                progress(getattr(args,'progress',None),count,count/float(fps),phase='source_timing')
        if count == 0:raise RuntimeError('PTS scan returned no frames')
    finally:
        lines.close()
    return fps, count


def read_frame(pipe, n):
    parts = bytearray()
    while len(parts) < n:
        data = pipe.read(min(CHUNK_BYTES, n - len(parts)))
        if not data:
            break
        parts.extend(data)
    if parts and len(parts) != n:
        raise RuntimeError('Decoder returned a truncated frame')
    return parts


def source_video_filter(video):
    """Metadata-only preflight, shared with the core's TUI probe."""
    if video.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
        raise ValueError('TrueHDR accepts SDR only')
    if video.get('field_order') in ('tt','bb','tb','bt') or video.get('disposition',{}).get('attached_pic'):
        raise ValueError('TrueHDR requires progressive video, not interlaced fields or cover art')
    for field in ('color_transfer', 'color_space', 'color_primaries', 'color_range'):
        if video.get(field) in (None, 'unknown', 'unspecified'):
            raise ValueError(f'TrueHDR requires explicit input {field}; source interpretation is ambiguous')
    if video.get('color_space') not in ('bt709', 'smpte170m', 'bt470bg', 'bt2020nc'):
        raise ValueError('Unsupported source matrix for TrueHDR input conversion')
    if video.get('color_transfer') not in ('bt709', 'gamma22', 'gamma28', 'smpte170m', 'iec61966-2-1'):
        raise ValueError('Unsupported SDR transfer for TrueHDR input conversion')
    if video.get('color_primaries') in ('bt2020','smpte431','smpte432'):
        raise ValueError('TrueHDR SDK input is Rec.709; refusing implicit wide-gamut SDR clipping. Use Original mode.')
    if video.get('sample_aspect_ratio', '1:1') not in ('1:1', '0:1', 'N/A'):
        raise ValueError('TrueHDR currently requires square pixels; no implicit aspect-ratio change')
    if any(abs(float(item.get('rotation', 0))) > .001 for item in video.get('side_data_list', [])):
        raise ValueError('TrueHDR currently requires unrotated input; no implicit rotation/resize')
    bits=int(video.get('bits_per_raw_sample') or 0)
    verified_8bit={'yuv420p','yuv422p','yuv444p','yuv440p','yuv411p','yuv410p',
        'yuvj420p','yuvj422p','yuvj444p','yuvj440p','nv12','nv21','yuyv422','uyvy422',
        'rgb24','bgr24','rgba','bgra','argb','abgr','rgb0','bgr0','0rgb','0bgr','gbrp','gray','gray8'}
    if bits>8 or video.get('pix_fmt') not in verified_8bit:
        raise ValueError('NVIDIA TrueHDR SDK accepts 8bit SDR input; rejecting implicit 10/12/16bit -> 8bit conversion. Use Original mode.')
    w, h = video['width'], video['height']
    if not (0<w<=16384 and 0<h<=16384) or w % 2 or h % 2:
        raise ValueError('Main10 yuv420 output requires even input dimensions')
    # No resize, fps filter, tone mapper, or HDR lift in this input conversion.
    transfer = {'gamma22': 'bt470m', 'gamma28': 'bt470bg',
                'iec61966-2-1': 'iec61966_2_1'}.get(video['color_transfer'], video['color_transfer'])
    in_range = 'full' if video['color_range'] == 'pc' else 'limited'
    chain = (f"zscale=tin={transfer}:pin={video['color_primaries']}:"
             f"min={video['color_space']}:rin={in_range}:t=bt470m:p=bt709:m=gbr:r=full:agamma=0,"
             'format=gbrp,format=rgb24')
    return chain


def source_plan(args):
    media=run_json([args.ffprobe,'-v','error','-show_streams','-of','json',args.input])
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    chain=source_video_filter(video)
    fps,expected=timing(args,video)
    return video,fps,expected,chain


def convert(args):
    import numpy as np
    video, fps, expected, chain = source_plan(args)
    cp, nvvfx = runtime()
    w, h = video['width'], video['height']
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    dec_cmd = [args.ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-noautorotate',
               '-i', args.input, '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', chain,
               '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-blocksize', str(CHUNK_BYTES), 'pipe:1']
    enc_cmd = [args.ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-n',
               '-f', 'rawvideo', '-pixel_format', 'x2bgr10le', '-video_size', f'{w}x{h}',
               '-framerate', str(fps), '-blocksize', str(CHUNK_BYTES), '-i', 'pipe:0', '-an', '-vf', 'format=gbrp10le',
               '-c:v', 'ffv1', '-level', '3', '-coder', '1', '-context', '1', '-g', '1',
               '-slicecrc', '1', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
               '-colorspace', 'rgb', '-color_range', 'pc', str(path)]
    report = dict(input=args.input, output=str(path), input_filter=chain,
                  decode_command=dec_cmd, encode_command=enc_cmd, fps=str(fps),
                  binding=nvvfx.__version__, sdk=nvvfx.get_sdk_version(),
                  parameters={k: getattr(args, k) for k in ('luminance', 'contrast', 'saturation',
                                                           'middle_gray', 'debanding_off')},
                  pq_to_hlg_filter=pq_to_hlg_filter(), reference_peak_nits=1000,
                  hlg_system_gamma=1.2, input_frames=expected, input_sha256=file_hash(args.input))
    decoder = encoder = None
    start = time.monotonic()
    count = 0
    digest = hashlib.sha256()
    try:
        with open(path.with_suffix('.decode.log'), 'wb') as de, open(path.with_suffix('.encode.log'), 'wb') as ee:
            decoder = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=de, creationflags=CREATE_FLAGS)
            encoder = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=ee, creationflags=CREATE_FLAGS)
            with nvvfx.TrueHDR(luminance=args.luminance, contrast=args.contrast,
                              saturation=args.saturation, middle_gray=args.middle_gray,
                              debanding_off=int(args.debanding_off)) as effect:
                effect.load()
                while True:
                    frame = read_frame(decoder.stdout, w * h * 3)
                    if not frame:
                        break
                    host = np.frombuffer(frame, np.uint8).reshape(h, w, 3)
                    gpu = cp.ascontiguousarray(cp.asarray(host).transpose(2, 0, 1), dtype=cp.float32)
                    gpu *= cp.float32(1 / 255)
                    cp.cuda.Device().synchronize()
                    out = consume(cp, effect.run(gpu).image)
                    if out.shape != (h, w) or out.dtype != cp.uint32:
                        raise RuntimeError('Invalid TrueHDR RGB10A2 buffer')
                    raw = cp.asnumpy(out).astype('<u4', copy=False)
                    # Ignore alpha in verification; preserve the 30 RGB bits.
                    digest.update((raw & np.uint32(0x3fffffff)).tobytes())
                    write_frame(encoder.stdin,raw,label='PQ cache',frame_number=count+1)
                    count += 1
                    if count % 15 == 0 and args.progress:
                        from task_io import progress
                        progress(args.progress,count,count/float(fps))
            encoder.stdin.close()
            if decoder.wait() or encoder.wait():
                raise RuntimeError('FFmpeg failed; see PQ decode/encode logs')
        if count != expected:
            raise RuntimeError(f'Frame count mismatch: {count} != {expected}')
        report.update(frames=count, elapsed_seconds=time.monotonic() - start,
                      sdk_rgb_payload_sha256=digest.hexdigest(), pq_file_sha256=file_hash(path), success=True)
    except BaseException as exc:
        report.update(success=False, frames=count, error=f'{type(exc).__name__}: {exc}')
        import traceback
        report['traceback'] = traceback.format_exc()
        for suffix in ('.decode.log', '.encode.log'):
            log = path.with_suffix(suffix)
            if log.exists():
                print(log.read_text(errors='replace')[-4000:], file=sys.stderr)
        raise
    finally:
        for child in (decoder, encoder):
            if child is not None and child.poll() is None:
                child.kill()
                child.wait()
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true')
    p.add_argument('--print-hlg-filter', action='store_true')
    p.add_argument('--verify-cache')
    p.add_argument('--input')
    p.add_argument('--output')
    p.add_argument('--report', default='truehdr-report.json')
    p.add_argument('--ffmpeg', default='ffmpeg')
    p.add_argument('--ffprobe', default='ffprobe')
    p.add_argument('--luminance', type=int, default=1000)
    p.add_argument('--contrast', type=int, default=100)
    p.add_argument('--saturation', type=int, default=100)
    p.add_argument('--middle-gray', type=int, default=50)
    p.add_argument('--debanding-off', action='store_true')
    p.add_argument('-progress', dest='progress')
    p.add_argument('-nostats', action='store_true')
    args = p.parse_args()
    for name, lo, hi in [('luminance', 400, 2000), ('contrast', 0, 200),
                         ('saturation', 0, 200), ('middle_gray', 10, 100)]:
        if not lo <= getattr(args, name) <= hi:
            p.error(f'{name} must be {lo}..{hi}')
    if args.print_hlg_filter:
        print(pq_to_hlg_filter())
        return
    if args.verify_cache:
        if not args.input:
            p.error('--verify-cache requires --input')
        print(json.dumps(verify_cache(args), indent=2))
    elif args.check:
        print(json.dumps(check(args), indent=2))
    else:
        if not args.input or not args.output:
            p.error('--input and --output are required')
        result = convert(args)
        print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'TrueHDR failed: {type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
