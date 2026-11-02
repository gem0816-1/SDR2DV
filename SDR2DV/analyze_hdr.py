"""Decode actual RGB pixels and report BT.2100 reference-display luminance.

No use of MaxCLL/Dolby metadata as a proxy for pixel light. Statistics sample
complete frames at --fps, without spatial resizing. Percentiles use 0.1-nit
histogram bins. Save sparse colour samples for independently aligned comparisons.
"""
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
from hdr_math import pq_eotf, hlg_eotf, luminance
from truehdr_backend import read_frame, CREATE_FLAGS
from pipe_io import CHUNK_BYTES


def analyze(path, fps, prefix):
    media = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                               '-show_format', '-of', 'json', path]))
    v = next(s for s in media['streams'] if s['codec_type'] == 'video')
    transfer = v.get('color_transfer')
    if transfer not in ('smpte2084', 'arib-std-b67'):
        raise ValueError(f'Expected HDR input; got {transfer}')
    matrix = v['color_space']
    ran = 'full' if v.get('color_range') == 'pc' else 'limited'
    chain = (f'fps={fps},zscale=tin={transfer}:pin=bt2020:min={matrix}:rin={ran}:'
             f't={transfer}:p=bt2020:m=gbr:r=full:agamma=0,format=gbrpf32le')
    cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-noautorotate', '-i', path,
           '-map', '0:v:0', '-vf', chain, '-an', '-sn', '-dn', '-f', 'rawvideo',
           '-blocksize', str(CHUNK_BYTES), 'pipe:1']
    hist = np.zeros(100001, np.int64)
    peak = rgb_peak = total = 0.
    pixels = high203 = high400 = high800 = boundary = negative = 0
    frames, sparse = [], []
    w, h = v['width'], v['height']
    log = Path(str(prefix) + '.decode.log')
    with open(log, 'wb') as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=CREATE_FLAGS)
        try:
            while True:
                raw = read_frame(p.stdout, w * h * 12)
                if not raw:
                    break
                code = np.frombuffer(raw, '<f4').reshape(3, h, w).transpose(1, 2, 0)[..., [2, 0, 1]]
                boundary += int(np.any(code >= 1 - 1 / 1023, axis=-1).sum())
                negative += int(np.any(code < 0, axis=-1).sum())
                display = pq_eotf(np.clip(code, 0, 1)) if transfer == 'smpte2084' else hlg_eotf(np.maximum(code, 0))
                y = luminance(display)
                hist += np.bincount(np.rint(np.clip(y, 0, 10000) * 10).astype(np.int32).ravel(), minlength=len(hist))
                peak = max(peak, float(y.max()))
                rgb_peak = max(rgb_peak, float(display.max()))
                total += float(y.sum())
                pixels += y.size
                high203 += int((y > 203).sum())
                high400 += int((y > 400).sum())
                high800 += int((y > 800).sum())
                frames.append(dict(index=len(frames), mean_nits=float(y.mean()), peak_nits=float(y.max())))
                sparse.append(display[::32, ::32].astype(np.float32))
            if p.wait():
                raise RuntimeError(f'Decode failed; see {log}')
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    cumulative = np.cumsum(hist)
    result = dict(path=str(Path(path).resolve()), stream=v, duration=media['format']['duration'],
                  model='BT.2100 HLG: 1000 cd/m2, black=0, gamma=1.2; PQ: ST2084 absolute RGB EOTF',
                  sample_fps=fps, sampled_frames=len(frames), sampled_pixels=pixels,
                  mean_nits=total / pixels, peak_luminance_nits=peak, peak_rgb_nits=rgb_peak,
                  percentiles_nits={str(q): float(np.searchsorted(cumulative, pixels * q / 100) / 10)
                                    for q in [1, 10, 50, 90, 95, 99, 99.9]},
                  above203_fraction=high203 / pixels, above400_fraction=high400 / pixels,
                  above800_fraction=high800 / pixels,
                  signal_boundary_fraction=boundary / pixels, negative_signal_fraction=negative / pixels,
                  boundary_note='Signal boundary is a clipping indicator, not proof of lost highlight detail',
                  max_sampled_frame_average_nits=max(x['mean_nits'] for x in frames),
                  frames=frames, histogram_nit_step=.1)
    Path(str(prefix) + '.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    np.savez_compressed(str(prefix) + '.npz', histogram=hist, sparse_rgb=np.asarray(sparse))
    print(json.dumps({k: result[k] for k in ['path', 'sampled_frames', 'mean_nits', 'peak_luminance_nits',
                                           'peak_rgb_nits', 'percentiles_nits', 'above400_fraction',
                                           'signal_boundary_fraction']}, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--fps', type=float, default=4)
    parser.add_argument('--output-prefix', required=True)
    opts = parser.parse_args()
    if opts.fps <= 0:
        parser.error('--fps must be positive')
    analyze(opts.input, opts.fps, opts.output_prefix)
