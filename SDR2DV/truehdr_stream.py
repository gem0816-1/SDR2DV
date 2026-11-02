"""Bounded SDR -> NVIDIA TrueHDR -> HLG -> lossless Main10 cache pipeline.

Raw frames exist only in RAM/pipes. The cache stores the exact 10-bit 4:2:0
pixels needed by the final encoder, not full-film PQ RGB or 16-bit RGB FFV1.
NVENC or x265 reuses this lossless cache without repeating AI or adding an
intermediate quantizer. Optional HDR10 retains x265 mastering SEI, never via PQ
FFV1. Audio is handled separately by the existing muxing stage.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import numpy as np
from truehdr_backend import CREATE_FLAGS, consume, file_hash, read_frame, runtime, source_plan
from pq_to_hlg import rgb_pq_to_nits, rgb_nits_to_hlg, cuda_reference_check
from brightness_mapping import luminance_lut, apply_mapping, mapping_info
from stream_diagnostics import diagnostic_streams, log_tail
from pipe_io import CHUNK_BYTES, FrameWriteError, write_frame

HLG_TO_YUV = ('zscale=tin=arib-std-b67:pin=bt2020:min=gbr:rin=full:'
              't=arib-std-b67:p=bt2020:m=bt2020nc:r=limited:agamma=0:'
              'dither=error_diffusion,format=yuv420p10le')
PQ_TO_YUV = ('zscale=tin=smpte2084:pin=bt2020:min=gbr:rin=full:'
             't=smpte2084:p=bt2020:m=bt2020nc:r=limited:agamma=0:dither=error_diffusion,format=yuv420p10le')


def required_remaining_bytes(cache_bytes, hdr10_bytes, completed, total):
    """Rolling estimate, not a worst-case compression guarantee.

    Reserve an additional copy of optional HDR10 for its audio remux, plus
    4 GiB for final encoding/mux/verification. A 30% margin covers sampling and
    encoder buffering; continuous low-space checks remain mandatory.
    """
    if completed <= 0:
        return 4 * 1024**3
    growth = (cache_bytes + hdr10_bytes) * max(total - completed, 0) / completed
    hdr10_remux = hdr10_bytes * total / completed
    return int(1.3 * (growth + hdr10_remux) + 4 * 1024**3)


def encoder_command(args, width, height, fps, output, pq=False):
    transfer = 'smpte2084' if pq else 'arib-std-b67'
    params = (f'lossless=1:repeat-headers=1:colorprim=bt2020:transfer={transfer}:'
              'colormatrix=bt2020nc:log-level=error')
    if pq:
        mastering_peak = 1000 if mapping_info(args.tone_mapping,args.tone_strength,args.skin_strength)['name'] == 'reference' else args.luminance
        params += (':master-display=G(8500,39850)B(6550,2300)R(35400,14600)'
                   f'WP(15635,16450)L({mastering_peak * 10000},1)')
    encoding = cache_encoding_args(getattr(args,'cache_encoder','x265'),transfer)
    if pq:
        # Keep the optional PQ comparison's existing mastering-display SEI.
        encoding = ['-c:v','libx265','-preset','ultrafast','-x265-params',params]
    command = [args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
               '-f', 'rawvideo', '-pixel_format', 'gbrp16le',
               '-video_size', f'{width}x{height}', '-framerate', str(fps),
               '-blocksize', str(CHUNK_BYTES), '-i', 'pipe:0',
               '-an', '-vf', PQ_TO_YUV if pq else HLG_TO_YUV, '-fps_mode', 'passthrough'] + encoding + [
               '-color_primaries', 'bt2020', '-color_trc', transfer, '-colorspace', 'bt2020nc',
               '-color_range', 'tv', '-flush_packets', '1']
    if pq:
        command += ['-tag:v', 'hvc1']
    return command + [str(output)]


def cache_encoding_args(encoder,transfer='arib-std-b67'):
    if encoder == 'nvenc':
        return ['-c:v','hevc_nvenc','-gpu','0','-profile:v','main10','-pix_fmt','p010le',
                '-preset','p6','-tune','lossless','-rc','constqp','-qp','0','-bf','0']
    if encoder != 'x265':
        raise ValueError('Cache encoder must be nvenc or x265')
    return ['-c:v','libx265','-preset','ultrafast','-x265-params',
            f'lossless=1:repeat-headers=1:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc:log-level=error']


def convert(args):
    # Mark a running/failed pipeline explicitly; only completed, hashed caches
    # can be reused or preserved by PowerShell's failure cleanup.
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    hdr10 = Path(args.hdr10_output).resolve() if args.hdr10_output else None
    if hdr10 and hdr10.exists():
        raise FileExistsError(hdr10)
    report_path = Path(args.report).resolve()
    report = dict(cache_kind='hlg-yuv420p10-lossless-v1', cache_ready=False, success=False,
                  input=str(Path(args.input).resolve()), output=str(output),
                  parameters={name: getattr(args, name) for name in
                              ('luminance', 'contrast', 'saturation', 'middle_gray', 'debanding_off')},
                  reference_peak_nits=1000, hlg_system_gamma=1.2,
                  intermediate_pixel_format='yuv420p10le', intermediate_lossless=True,
                  raw_video_files_created=False)
    report['cache_encoder'] = getattr(args,'cache_encoder','x265')
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    decoder = hlg_encoder = pq_encoder = None
    handles = []
    count = limited_count = 0
    start = time.monotonic()
    tone = mapping_info(args.tone_mapping,args.tone_strength,args.skin_strength)
    report['brightness_mapping'] = tone
    phase = 'source_timing'
    try:
        print('Checking decoded PTS and SDR colour interpretation...', file=sys.stderr, flush=True)
        video, fps, expected, input_filter = source_plan(args)
        phase = 'runtime_initialization'
        cp, nvvfx = runtime()
        tone_lut = cp.asarray(luminance_lut()) if tone['name'] == 'reference' else None
        reference_error = cuda_reference_check(cp)
        width, height = video['width'], video['height']
        report.update(binding=nvvfx.__version__, sdk=nvvfx.get_sdk_version(),
                      fps=str(fps), input_frames=expected, input_filter=input_filter,
                      input_sha256=file_hash(args.input),
                      cuda_reference_max_signal_error=reference_error)
        decode = [args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-noautorotate',
                  '-i', args.input, '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', input_filter,
                  '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                  '-blocksize', str(CHUNK_BYTES), 'pipe:1']
        encode = encoder_command(args, width, height, fps, output)
        report.update(decode_command=decode, hlg_encode_command=encode)

        def logfile(name):
            handle = open(output.parent / name, 'wb')
            handles.append(handle)
            return handle

        decoder = subprocess.Popen(decode, stdout=subprocess.PIPE,
                                   stderr=logfile('stream_decode.log'), creationflags=CREATE_FLAGS)
        hlg_encoder = subprocess.Popen(encode, stdin=subprocess.PIPE,
                                       stderr=logfile('stream_hlg_encode.log'), creationflags=CREATE_FLAGS)
        if hdr10:
            pq_command = encoder_command(args, width, height, fps, hdr10, pq=True)
            report['hdr10_encode_command'] = pq_command
            pq_encoder = subprocess.Popen(pq_command, stdin=subprocess.PIPE,
                                          stderr=logfile('stream_hdr10_encode.log'), creationflags=CREATE_FLAGS)
        print('NVIDIA TrueHDR -> CUDA PQ/HLG -> lossless Main10 cache (pipes only).',
              file=sys.stderr, flush=True)
        phase = 'truehdr'
        from task_io import progress
        progress(args.progress, 0, 0, phase=phase)
        last_storage = {}
        with nvvfx.TrueHDR(luminance=args.luminance, contrast=args.contrast,
                          saturation=args.saturation, middle_gray=args.middle_gray,
                          debanding_off=int(args.debanding_off)) as effect:
            effect.load()
            while True:
                from task_io import check_cancel
                check_cancel()
                frame = read_frame(decoder.stdout, width * height * 3)
                if not frame:
                    break
                host = np.frombuffer(frame, np.uint8).reshape(height, width, 3)
                gpu = cp.ascontiguousarray(cp.asarray(host).transpose(2, 0, 1), dtype=cp.float32)
                gpu *= cp.float32(1 / 255)
                cp.cuda.Device().synchronize()
                packed = consume(cp, effect.run(gpu).image)
                if packed.shape != (height, width) or packed.dtype != cp.uint32:
                    raise RuntimeError('Invalid TrueHDR RGB10A2 buffer')
                rgb_pq = cp.stack([((packed >> shift) & 1023).astype(cp.float32)/1023 for shift in (0,10,20)])
                display = apply_mapping(cp,rgb_pq_to_nits(cp,rgb_pq),tone,tone_lut)
                hlg, limited = rgb_nits_to_hlg(cp,display)
                planes = cp.rint(cp.clip(hlg[[1,2,0]],0,1)*65535).astype(cp.uint16)
                limited_count += int(limited.sum().get())
                write_frame(hlg_encoder.stdin,cp.asnumpy(planes).astype('<u2', copy=False),
                            label='HLG cache',frame_number=count+1)
                if pq_encoder:
                    pq_display = display/cp.maximum(cp.max(display,axis=0)/1000,1)[None,...]
                    normalized = cp.maximum(pq_display,0)/10000
                    p = normalized ** cp.float32(2610/16384)
                    code = ((3424/4096+(2413/128)*p)/(1+(2392/128)*p)) ** cp.float32(2523/32)
                    codes = cp.rint(cp.clip(code[[1,2,0]],0,1)*65535).astype(cp.uint16)
                    write_frame(pq_encoder.stdin,cp.asnumpy(codes).astype('<u2',copy=False),
                                label='HDR10 comparison',frame_number=count+1)
                count += 1
                if count % 15 == 0:
                    cache_bytes = output.stat().st_size if output.exists() else 0
                    pq_bytes = hdr10.stat().st_size if hdr10 and hdr10.exists() else 0
                    free = shutil.disk_usage(output.parent).free
                    estimate = required_remaining_bytes(cache_bytes, pq_bytes, count, expected)
                    last_storage = dict(frame=count, free_bytes=free, cache_bytes=cache_bytes,
                                        hdr10_bytes=pq_bytes, estimated_remaining_bytes=estimate)
                    if count % 60 == 0:
                        report.update(frames=count, storage=last_storage,
                                      elapsed_seconds=time.monotonic()-start)
                        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    progress(args.progress, count, count / float(fps), phase=phase)
                    if free < 1024**3:
                        raise RuntimeError('Temporary disk has less than 1 GiB free')
                    if count >= max(90, int(float(fps) * 5)) and free < estimate:
                        raise RuntimeError(f'Early storage check: estimated {estimate / 1024**3:.1f} GiB '
                                           f'remaining including outputs, only {free / 1024**3:.1f} GiB free')
        phase = 'encoder_finalize'
        hlg_encoder.stdin.close()
        if pq_encoder:
            pq_encoder.stdin.close()
        decode_code = decoder.wait()
        hlg_code = hlg_encoder.wait()
        if not decode_code and not hlg_code and count == expected:
            report.update(cache_ready=True, frames=count, hlg_file_sha256=file_hash(output),
                          cache_bytes=output.stat().st_size)
        if decode_code or hlg_code or count != expected:
            raise RuntimeError(f'Stream failure: decoder={decode_code}, HLG={hlg_code}, '
                               f'frames={count}/{expected}; see stream logs')
        if pq_encoder:
            if pq_encoder.wait():
                raise RuntimeError('HDR10 encoder failed; completed HLG cache can be reused')
            report.update(hdr10_file_sha256=file_hash(hdr10), hdr10_bytes=hdr10.stat().st_size)
        report.update(success=True, colour_volume_limited_fraction=limited_count/(count*width*height),
                      storage=last_storage, elapsed_seconds=time.monotonic()-start)
        progress(args.progress,count,count/float(fps),phase='truehdr',ended=True)
    except BaseException as error:
        if isinstance(error, (OSError,FrameWriteError)):
            # A pipe error can race with FFmpeg's exit/stderr flush. Preserve
            # that diagnostic instead of killing it immediately and losing it.
            for child in (hlg_encoder,pq_encoder):
                if child is not None and child.poll() is None:
                    try: child.wait(timeout=2)
                    except subprocess.TimeoutExpired: pass
        report.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc(),
                      frames=count, failed_phase=phase)
        raise
    finally:
        report['child_exit_codes_before_cleanup'] = {name: child.poll() for name, child in
            (('decoder', decoder), ('hlg_encoder', hlg_encoder), ('pq_encoder', pq_encoder)) if child is not None}
        for child in (decoder, hlg_encoder, pq_encoder):
            if child is not None and child.poll() is None:
                child.kill()
                child.wait()
        for handle in handles:
            handle.close()
        failures = {name: stream.failure for name, stream in (('stdout',sys.stdout),('stderr',sys.stderr))
                    if getattr(stream, 'failure', None)}
        if failures: report['diagnostic_stream_fallbacks'] = failures
        if report.get('error'):
            report['child_stderr'] = {name: log_tail(output.parent/name) for name in
                ('stream_decode.log','stream_hlg_encode.log','stream_hdr10_encode.log')
                if (output.parent/name).exists()}
            print(f'Failed phase={phase}, completed_frames={count}/{report.get("input_frames", "unknown")}',
                  file=sys.stderr)
            for name, tail in report['child_stderr'].items():
                if tail: print(f'[{name}]\n{tail}',file=sys.stderr)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('frames','cache_bytes','elapsed_seconds','cache_ready')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--hdr10-output')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    parser.add_argument('--luminance', type=int, default=1000)
    parser.add_argument('--contrast', type=int, default=100)
    parser.add_argument('--saturation', type=int, default=100)
    parser.add_argument('--middle-gray', type=int, default=50)
    parser.add_argument('--debanding-off', action='store_true')
    parser.add_argument('--tone-mapping',choices=['reference','neutral','natural'],default='neutral')
    parser.add_argument('--tone-strength',type=float,default=1.)
    parser.add_argument('--skin-strength',type=float)
    parser.add_argument('--cache-encoder',choices=['nvenc','x265'],default='nvenc')
    parser.add_argument('-progress', dest='progress')
    parser.add_argument('-nostats', action='store_true')
    args = parser.parse_args()
    for name, low, high in [('luminance',400,2000),('contrast',0,200),
                            ('saturation',0,200),('middle_gray',10,100)]:
        if not low <= getattr(args,name) <= high:
            parser.error(f'{name} must be {low}..{high}')
    with diagnostic_streams(Path(args.report).resolve().parent):
        try:
            convert(args)
        except Exception as error:
            print(f'TrueHDR streaming failed: {type(error).__name__}: {error}', file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
