import os
import subprocess
import unittest
import numpy as np


@unittest.skipUnless(os.environ.get('SDR2DV_TEST_HDR_CUDA')=='1','Set SDR2DV_TEST_HDR_CUDA=1 with CuPy/GPU')
class HDRGPUReferenceTests(unittest.TestCase):
    def test_saturated_full_and_limited_range_and_pq_reference(self):
        import cupy as cp
        from hdr_cuda import yuv420_rgb
        from hdr_analysis import display_rgb
        width,height=128,64;rng=np.random.default_rng(9)
        for full in (False,True):
            y=rng.integers(0 if full else 64,1024 if full else 941,width*height,dtype='<u2')
            uv=rng.integers(0 if full else 64,1024 if full else 961,width*height//2,dtype='<u2')
            data=np.concatenate((y,uv));ran='full' if full else 'limited'
            chain=f'zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:rin={ran}:chromalin=left:t=smpte2084:p=bt2020:m=gbr:r=full:agamma=0:filter=bilinear,format=gbrpf32le'
            out=subprocess.run(['ffmpeg','-v','error','-nostdin','-f','rawvideo','-pix_fmt','yuv420p10le','-s',f'{width}x{height}','-i','pipe:0','-vf',chain,'-frames:v','1','-f','rawvideo','pipe:1'],
                input=data.tobytes(),capture_output=True,check=True).stdout
            reference=np.frombuffer(out,'<f4').reshape(3,height,width)[[2,0,1]]
            actual=cp.asnumpy(yuv420_rgb(cp,cp.asarray(data),width,height,'left',full))
            np.testing.assert_allclose(actual,reference,atol=3e-6,rtol=0)
            np.testing.assert_allclose(cp.asnumpy(display_rgb(cp,cp.asarray(actual),'smpte2084')),
                display_rgb(np,reference,'smpte2084'),rtol=.0001,atol=.04)

    def test_native_yuv_reconstruction_matches_ffmpeg_bilinear_reference(self):
        import cupy as cp
        from hdr_cuda import yuv420_rgb
        from hdr_analysis import display_rgb,frame_statistics
        width,height=256,144
        rng=np.random.default_rng(8)
        # Smooth chroma exercises siting/upsampling without clipping HDR RGB.
        y=rng.integers(500,700,width*height,dtype='<u2')
        u=rng.integers(480,545,width*height//4,dtype='<u2');v=rng.integers(480,545,width*height//4,dtype='<u2')
        data=np.concatenate((y,u,v))
        for siting in ('left','center','topleft'):
            chain=f'zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:rin=limited:chromalin={siting}:t=arib-std-b67:p=bt2020:m=gbr:r=full:agamma=0:filter=bilinear,format=gbrpf32le'
            command=['ffmpeg','-v','error','-nostdin','-f','rawvideo','-pix_fmt','yuv420p10le','-s',f'{width}x{height}','-i','pipe:0','-vf',chain,'-frames:v','1','-f','rawvideo','pipe:1']
            out=subprocess.run(command,input=data.tobytes(),capture_output=True,check=True).stdout
            reference=np.frombuffer(out,'<f4').reshape(3,height,width)[[2,0,1]]
            actual=cp.asnumpy(yuv420_rgb(cp,cp.asarray(data),width,height,siting))
            np.testing.assert_allclose(actual,reference,atol=3e-6,rtol=0)
            cpu,_=frame_statistics(np,display_rgb(np,reference,'arib-std-b67'))
            gpu,_=frame_statistics(cp,display_rgb(cp,cp.asarray(actual),'arib-std-b67'))
            for name in ('max_rgb_nits','mean_max_rgb_nits','max_y_nits','mean_y_nits'):
                self.assertAlmostEqual(cpu[name],gpu[name],delta=.03)


if __name__=='__main__':unittest.main()
