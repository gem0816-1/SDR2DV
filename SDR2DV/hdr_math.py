"""BT.2100 reference math. RGB order is R,G,B; display light is cd/m2.

HLG reference: ideal black, 1000 cd/m2 white, system gamma 1.2.
This is a numerical reference, not a measurement of a phone's display.
"""
import numpy as np

KR, KG, KB = 0.2627, 0.6780, 0.0593
A, B, C = 0.17883277, 0.28466892, 0.55991073


def pq_eotf(code):
    p = np.maximum(np.asarray(code, dtype=np.float64), 0) ** (1 / 78.84375)
    return 10000 * (np.maximum(p - 0.8359375, 0) /
                    np.maximum(18.8515625 - 18.6875 * p, 1e-12)) ** (1 / 0.1593017578125)


def pq_encode(nits):
    p = (np.maximum(nits, 0) / 10000) ** 0.1593017578125
    return ((0.8359375 + 18.8515625 * p) / (1 + 18.6875 * p)) ** 78.84375


def luminance(rgb):
    return np.sum(rgb * np.array([KR, KG, KB]), axis=-1)


def hlg_eotf(code):
    code = np.maximum(code, 0)
    scene = np.where(code <= .5, code ** 2 / 3,
                     (np.exp((code - C) / A) + B) / 12)
    return 1000 * scene * np.maximum(luminance(scene), 1e-30)[..., None] ** .2


def display_to_hlg(rgb, preserve_headroom=False):
    d = np.maximum(rgb, 0) / 1000
    scene = d * np.maximum(luminance(d), 1e-30)[..., None] ** (-1 / 6)
    # Uniform scaling in RGB retains chromaticity at the HLG colour-volume edge.
    m = np.ones(scene.shape[:-1]) if preserve_headroom else np.maximum(np.max(scene, axis=-1), 1)
    limited = scene / m[..., None]
    encoded = np.where(limited <= 1 / 12, np.sqrt(3 * limited),
                       A * np.log(np.maximum(12 * limited - B, 1e-30)) + C)
    return encoded, m > 1 + 1e-9


def pq_to_hlg_filter():
    # zscale's default agamma=1 is a per-channel approximation. Force the
    # luminance-dependent BT.2100 OOTF in BOTH directions with agamma=0.
    # First enter display-linear RGB, normalized to 1000 nits. Bound HLG colour
    # volume by a single RGB multiplier, rather than clipping channels apart.
    prefix = ('zscale=tin=smpte2084:pin=bt2020:min=gbr:rin=full:'
              't=linear:p=bt2020:m=gbr:r=full:npl=1000:agamma=0,format=gbrpf32le')
    shared = ('st(0,max(r(X,Y),max(g(X,Y),b(X,Y))));'
              'st(1,max(0.2627*r(X,Y)+0.6780*g(X,Y)+0.0593*b(X,Y),1e-12));'
              'st(2,ld(0)*pow(ld(1),-1/6));')
    expr = ':'.join(f"{ch}='{shared}{ch}(X,Y)*pow(max(1,ld(2)),-1.2)'" for ch in 'rgb')
    return (prefix + ',geq=' + expr +
            ',zscale=tin=linear:pin=bt2020:min=gbr:rin=full:'
            't=arib-std-b67:p=bt2020:m=bt2020nc:r=limited:npl=1000:agamma=0:'
            'dither=error_diffusion,format=yuv420p10le')
