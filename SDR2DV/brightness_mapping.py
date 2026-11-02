"""Fixed display-light grading calibrated against a 1000-nit HLG reference.

This is a creative brightness preset, not reconstruction of an unavailable SDR
master. A single monotone curve is used throughout the video: no frame-wise
histogram matching, exposure pumping, gamma-tag changes or raised black floor.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from hdr_math import display_to_hlg, hlg_eotf, pq_encode, pq_eotf

PROFILE_ID = 'reference1000-v2'
# Display-linear luminance, cd/m2. Calibrated using 20 temporally distributed
# full-resolution frames; near-black scene occupancy is not histogram-matched.
# The upper knots deliberately retain a shoulder instead of copying clipped
# reference pixels or an out-of-range HLG signal above the 1000-nit model.
ANCHORS = np.array([
    [0, 0], [.1, .1], [.52, .17], [.89, .39], [2.27, 2.51],
    [7.23, 40.97], [25.41, 227.11], [59.53, 487.33],
    [164.65, 950], [403.15, 990], [1000, 1000], [10000, 1000],
], dtype=np.float64)
PROFILE_SHA256 = hashlib.sha256(PROFILE_ID.encode()+b':warm-skin-v1:'+ANCHORS.astype('<f8').tobytes()).hexdigest()
NATURAL_ID = 'natural1000-v1'
NATURAL_SHA256 = hashlib.sha256(b'natural1000-v1:identity<=600;hermite600-980;exp-tail400;no-auto-skin').hexdigest()


def mapping_info(name='reference', strength=1.0, skin_strength=None):
    name = name.lower()
    strength = float(strength)
    skin_strength = float((.5 if name=='reference' else 0) if skin_strength is None else skin_strength)
    if (name not in ('reference', 'neutral', 'natural') or not np.isfinite(strength) or not 0 <= strength <= 1 or
            not np.isfinite(skin_strength) or not 0 <= skin_strength <= 1):
        raise ValueError('Brightness mapping must be reference/neutral with strength 0..1')
    if name == 'neutral' or strength == 0:
        return dict(name='neutral', strength=0., skin_strength=0., profile_id=None, profile_sha256=None)
    if name == 'natural':
        return dict(name=name,strength=strength,skin_strength=skin_strength,
                    profile_id=NATURAL_ID,profile_sha256=NATURAL_SHA256)
    return dict(name=name, strength=strength, skin_strength=skin_strength, profile_id=PROFILE_ID, profile_sha256=PROFILE_SHA256)


def _monotone_cubic(x, y, values):
    """Fritsch-Carlson/PCHIP slopes; do not introduce luminance overshoots."""
    h, delta = np.diff(x), np.diff(y) / np.diff(x)
    slope = np.zeros_like(x)
    for i in range(1, len(x)-1):
        if delta[i-1] > 0 and delta[i] > 0:
            w1, w2 = 2*h[i]+h[i-1], h[i]+2*h[i-1]
            slope[i] = (w1+w2)/(w1/delta[i-1]+w2/delta[i])
    for end, first, second, hf, hs in ((0,delta[0],delta[1],h[0],h[1]),
                                      (-1,delta[-1],delta[-2],h[-1],h[-2])):
        value = ((2*hf+hs)*first-hf*second)/(hf+hs)
        slope[end] = max(0., min(value,3*first))
    i = np.clip(np.searchsorted(x,values,side='right')-1,0,len(x)-2)
    t = np.clip((values-x[i])/h[i],0,1)
    result = ((2*t**3-3*t**2+1)*y[i]+(t**3-2*t**2+t)*h[i]*slope[i] +
              (-2*t**3+3*t**2)*y[i+1]+(t**3-t**2)*h[i]*slope[i+1])
    return np.where(values <= x[0],y[0],np.where(values >= x[-1],y[-1],result))


def natural_luminance(xp, nits):
    """Creative shoulder, not a BT.2100 transfer function or AI reconstruction.

    Identity through 600 nit (including black, shadows and 203-nit diffuse
    white). A C1 Hermite shoulder reaches 980 nit at an input of 1000 nit;
    an exponential tail approaches 1000 without forcing a shot to that peak.
    No per-frame exposure, shadow crush, colour or saturation adjustment.
    """
    t = xp.clip((nits-600)/400,0,1)
    shoulder = 600+400*t+320*t*t-340*t*t*t
    tail = 980+20*(-xp.expm1(-xp.maximum(nits-1000,0)/400))
    return xp.where(nits<=600,nits,xp.where(nits<=1000,shoulder,tail))


def luminance_lut(size=4096, name='reference'):
    if name == 'natural':
        result = natural_luminance(np,pq_eotf(np.linspace(0,1,size)))
        result[0] = 0
        return result.astype(np.float32)
    x, y = pq_encode(ANCHORS[:,0]), pq_encode(ANCHORS[:,1])
    x[0] = y[0] = 0.
    result = pq_eotf(_monotone_cubic(x,y,np.linspace(0,1,size)))
    result[0] = 0.
    return np.clip(result,0,1000).astype(np.float32)


def map_display_rgb(xp, rgb, table, strength=1.0):
    """RGB is channel-first absolute display light. Scale channels together."""
    if table is None or strength == 0:
        return rgb
    y = xp.maximum(.2627*rgb[0]+.6780*rgb[1]+.0593*rgb[2],0)
    p = (y/10000) ** (2610/16384)
    code = ((3424/4096+(2413/128)*p)/(1+(2392/128)*p)) ** (2523/32)
    position = xp.clip(code*(len(table)-1),0,len(table)-1)
    low = position.astype(xp.int32)
    high = xp.minimum(low+1,len(table)-1)
    # An int32 subtraction promotes float32 to float64 in NumPy/CuPy.
    # Keep the incoming precision for the remaining full-resolution GPU grade.
    fraction = position-low.astype(position.dtype)
    mapped = table[low]*(1-fraction)+table[high]*fraction
    target = y+(mapped-y)*strength
    scale = xp.where(y > 1e-12,target/xp.maximum(y,1e-12),1)
    return rgb*scale[None,...]


def apply_mapping(xp,rgb,tone,table=None):
    if tone['name']=='natural':
        y=xp.maximum(.2627*rgb[0]+.6780*rgb[1]+.0593*rgb[2],0)
        target=y+(natural_luminance(xp,y)-y)*tone['strength']
        scale=xp.where(y>1e-12,target/xp.maximum(y,1e-12),1)
        mapped=rgb*scale[None,...]
    else:
        mapped=map_display_rgb(xp,rgb,table,tone['strength'])
    return correct_warm_skin(xp,mapped,tone['skin_strength'])


def correct_warm_skin(xp, rgb, strength=.5):
    """Smooth hue/saturation selection, not face detection; preserve luminance.

    A Rec.709-like encoded space is used only to select warm skin-like hues.
    The actual correction is in display-linear BT.2020. Neutral greys and
    saturated stage-light primaries are excluded by the soft mask.
    """
    if strength == 0:
        return rgb
    r,g,b = rgb
    r709 = xp.maximum(1.660491*r-.587641*g-.072850*b,0) ** (1/2.2)
    g709 = xp.maximum(-.124550*r+1.132900*g-.008349*b,0) ** (1/2.2)
    b709 = xp.maximum(-.018151*r-.100579*g+1.118730*b,0) ** (1/2.2)
    maximum = xp.maximum(r709,xp.maximum(g709,b709))
    minimum = xp.minimum(r709,xp.minimum(g709,b709))
    delta = xp.maximum(maximum-minimum,1e-12)
    hue = xp.where(maximum==r709,((g709-b709)/delta)%6,
          xp.where(maximum==g709,(b709-r709)/delta+2,(r709-g709)/delta+4))*60
    saturation = (maximum-minimum)/xp.maximum(maximum,1e-12)
    y = .2627*r+.6780*g+.0593*b
    def smooth(low,high,value):
        t=xp.clip((value-low)/(high-low),0,1)
        return t*t*(3-2*t)
    mask = (smooth(18,32,hue)*(1-smooth(52,72,hue))*smooth(.04,.15,saturation)*
            (1-smooth(.55,.85,saturation))*smooth(.5,3,y)*(1-smooth(500,900,y)))
    weight = strength*mask
    green,blue = g*(1-.12*weight),b*(1+.45*weight)
    corrected_y = .2627*r+.6780*green+.0593*blue
    scale = xp.where(y>1e-12,y/xp.maximum(corrected_y,1e-12),1)
    return xp.stack((r*scale,green*scale,blue*scale))


def map_encoded_rgb(rgb, transfer='hlg', strength=1.0, skin_strength=None, mapping='reference'):
    """CPU reference, channel-last RGB; used for LUT export and verification."""
    tone = mapping_info(mapping,strength,skin_strength)
    display = hlg_eotf(np.clip(rgb,0,1)) if transfer == 'hlg' else pq_eotf(np.clip(rgb,0,1))
    table = luminance_lut(name=tone['name']) if tone['name']!='neutral' else None
    mapped = np.moveaxis(apply_mapping(np,np.moveaxis(display,-1,0),tone,table),0,-1)
    if transfer == 'hlg':
        return display_to_hlg(mapped)[0]
    mapped = mapped/np.maximum(np.max(mapped,axis=-1)/1000,1)[...,None]
    return np.clip(pq_encode(mapped),0,1)


def write_cube(path, transfer='hlg', strength=1., size=129, skin_strength=None, mapping='reference'):
    if size < 2 or size > 256:
        raise ValueError('Cube size must be 2..256')
    tone=mapping_info(mapping,strength,skin_strength)
    axis = np.linspace(0,1,size)
    red, green = np.meshgrid(axis,axis)
    with Path(path).open('x',encoding='ascii',newline='\n') as output:
        output.write(f'TITLE "{tone["profile_id"]}; {transfer}; strength={strength}; skin={tone["skin_strength"]}"\nLUT_3D_SIZE {size}\n')
        output.write('DOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for blue in axis:
            rgb = np.stack((red.ravel(),green.ravel(),np.full(size*size,blue)),axis=-1)
            np.savetxt(output,map_encoded_rgb(rgb,transfer,strength,skin_strength,mapping),fmt='%.9f')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--describe',action='store_true')
    parser.add_argument('--write-cube')
    parser.add_argument('--transfer',choices=['hlg','pq'],default='hlg')
    parser.add_argument('--strength',type=float,default=1.)
    parser.add_argument('--size',type=int,default=129)
    parser.add_argument('--skin-strength',type=float)
    parser.add_argument('--mode',choices=['natural','neutral','reference'],default='reference')
    args = parser.parse_args()
    info = mapping_info(args.mode,args.strength,args.skin_strength)
    if args.describe:
        print(json.dumps(info))
    if args.write_cube:
        write_cube(args.write_cube,args.transfer,args.strength,args.size,args.skin_strength,args.mode)
    if not args.describe and not args.write_cube:
        parser.error('--describe or --write-cube is required')
