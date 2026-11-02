"""BT.2020 nonconstant-luminance YUV reconstruction for GPU analysis only.

Full resolution, every pixel; bilinear reconstruction with explicit chroma
siting. This does not encode/alter the video. CPU reference is FFmpeg/zscale.
"""
_kernel=None
SITING={'left':(0.,.5),'center':(.5,.5),'topleft':(0.,0.),'top':(.5,0.),
        'bottomleft':(0.,1.),'bottom':(.5,1.)}


def yuv420_rgb(cp,planar,width,height,siting='left',full_range=False):
    global _kernel
    if siting not in SITING:raise ValueError('Unsupported chroma siting: '+siting)
    if _kernel is None:
        _kernel=cp.ElementwiseKernel('raw uint16 src, int32 w, int32 h, float32 sx, float32 sy, float32 yo, float32 yd, float32 cd',
            'float32 rgb',r'''
            int x=i%w, y=(i/w)%h, channel=i/(w*h), cw=w/2, ch=h/2;
            float cx=fminf(fmaxf((x-sx)*0.5f,0.0f),cw-1.0f);
            float cy=fminf(fmaxf((y-sy)*0.5f,0.0f),ch-1.0f);
            int x0=(int)floorf(cx),y0=(int)floorf(cy),x1=min(x0+1,cw-1),y1=min(y0+1,ch-1);
            float fx=cx-x0,fy=cy-y0,uv[2];
            for(int c=0;c<2;c++){
                int base=w*h+c*cw*ch;
                float upper=(1-fx)*src[base+y0*cw+x0]+fx*src[base+y0*cw+x1];
                float lower=(1-fx)*src[base+y1*cw+x0]+fx*src[base+y1*cw+x1];
                uv[c]=((1-fy)*upper+fy*lower-512.0f)/cd;
            }
            float yy=(src[y*w+x]-yo)/yd;
            rgb=channel==0?yy+1.4746f*uv[1]:(channel==1?yy-0.1645531268f*uv[0]-0.5713531268f*uv[1]:yy+1.8814f*uv[0]);
            ''','sdr2dv_bt2020_bilinear')
    sx,sy=SITING[siting]
    out=cp.empty(3*width*height,dtype=cp.float32)
    _kernel(planar,width,height,sx,sy,0. if full_range else 64.,1023. if full_range else 876.,
            1023. if full_range else 896.,out)
    return out.reshape(3,height,width)
