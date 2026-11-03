"""Reference curve, extended HLG transport and UI/CLI integration regressions."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from brightness_mapping import (REAL_DOLBY_ANCHORS, REAL_DOLBY_SHA256, apply_mapping,
    luminance_lut, map_display_rgb, map_encoded_rgb, mapping_info, write_cube)
from hdr_math import display_to_hlg, hlg_eotf, pq_encode
from pq_to_hlg import (hlg_transport_format, hlg_transport_planes, pq_display_rgb,
                       rgb_nits_to_hlg)
from truehdr_stream import HLG_TO_YUV, encoder_command


class RealDolbyTests(unittest.TestCase):
    def test_black_monotonicity_reference_anchors_and_no_1000_cap(self):
        table=luminance_lut(name='realDolby')
        values=np.r_[0,np.geomspace(1e-8,10000,20000)]
        rgb=np.repeat(values[None,:],3,axis=0)
        output=map_display_rgb(np,rgb,table)[0]
        self.assertTrue(np.isfinite(output).all())
        self.assertTrue(np.all(np.diff(output)>=-1e-6))
        self.assertEqual(output[0],0)
        anchors=map_display_rgb(np,np.repeat(REAL_DOLBY_ANCHORS[:,0][None,:],3,axis=0),table)[0]
        np.testing.assert_allclose(anchors,REAL_DOLBY_ANCHORS[:,1],rtol=.003,atol=.15)
        self.assertGreater(anchors[-5],1000)
        self.assertGreater(anchors[-1],2000)

    def test_chromaticity_float32_and_zero_strength_bypass(self):
        rgb=np.array([[12,400,950,0],[8,300,990,0],[3,100,980,0]],dtype=np.float32)
        tone=mapping_info('realDolby')
        self.assertEqual(tone['skin_strength'],0)
        output=apply_mapping(np,rgb,tone,luminance_lut(name='realDolby'))
        self.assertEqual(output.dtype,np.dtype('float32'))
        np.testing.assert_allclose(output[:,:3]/output[:,:3].sum(axis=0),
            rgb[:,:3]/rgb[:,:3].sum(axis=0),atol=2e-7)
        np.testing.assert_array_equal(output[:,-1],rgb[:,-1])
        self.assertEqual(mapping_info('realDolby',0),mapping_info('neutral'))
        self.assertEqual(len(REAL_DOLBY_SHA256),64)
        self.assertEqual(tone['peak_policy'],'reference_no_1000nit_cap')
        with self.assertRaisesRegex(ValueError,'calibrated'):
            apply_mapping(np,rgb,tone)

    def test_extended_hlg_roundtrip_and_unchanged_nominal_modes(self):
        rgb=np.array([[0,0,0],[203,203,203],[1600,1600,1600],[400,1500,4000]],dtype=float)
        code,limited=display_to_hlg(rgb,preserve_headroom=True)
        self.assertGreater(code.max(),1)
        self.assertFalse(limited.any())
        np.testing.assert_allclose(hlg_eotf(code),rgb,rtol=3e-7,atol=1e-6)
        actual,_=rgb_nits_to_hlg(np,rgb.T,preserve_headroom=True)
        np.testing.assert_allclose(actual.T,code,atol=3e-7,rtol=0)
        nominal,_=display_to_hlg(rgb)
        self.assertLessEqual(nominal.max(),1.0000001)
        mapped=map_encoded_rgb(np.array([[1.08]*3]),mapping='realDolby')
        self.assertGreater(mapped.min(),1)

    def test_float_transport_and_hdr10_do_not_clip_reference_headroom(self):
        tone=mapping_info('realDolby')
        code=np.array([[[.2,1.12]],[[.3,1.08]],[[.1,1.2]]],dtype=np.float32)
        planes=hlg_transport_planes(np,code,tone)
        self.assertEqual(planes.dtype,np.dtype('float32'))
        np.testing.assert_array_equal(planes,code[[1,2,0]])
        self.assertEqual(hlg_transport_format(tone),'gbrpf32le')
        self.assertEqual(hlg_transport_format(mapping_info('neutral')),'gbrp16le')
        rgb=np.array([[2000.],[1500.],[3000.]])
        np.testing.assert_array_equal(pq_display_rgb(np,rgb,tone),rgb)
        self.assertEqual(float(pq_display_rgb(np,rgb,mapping_info('neutral')).max()),1000)
        with self.assertRaisesRegex(ValueError,'super-whites'):
            write_cube('unused.cube',mapping='realDolby')
        with tempfile.TemporaryDirectory() as folder:
            cube=Path(folder)/'pq.cube'
            write_cube(cube,transfer='pq',size=5,mapping='realDolby')
            entries=np.loadtxt(cube,skiprows=4)
            self.assertGreater(entries.max(),float(pq_encode(1000)))
            self.assertLessEqual(entries.max(),1)
        args=argparse.Namespace(tone_mapping='realDolby',tone_strength=1,skin_strength=0,
            luminance=1000,ffmpeg='ffmpeg',cache_encoder='nvenc')
        hlg=encoder_command(args,3840,2160,60,Path('cache.mkv'))
        self.assertEqual(hlg[hlg.index('-pixel_format')+1],'gbrpf32le')
        pq=encoder_command(args,3840,2160,60,Path('hdr10.mp4'),pq=True)
        self.assertEqual(pq[pq.index('-pixel_format')+1],'gbrp16le')

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg needed for extended-range transport check')
    def test_ffmpeg_preserves_extended_hlg_in_main10_yuv_range(self):
        values=np.repeat([.75,1.,1.08],2).astype(np.float32)
        rgb=np.tile(values,(3,2,1))
        planes=hlg_transport_planes(np,rgb,mapping_info('realDolby'))
        result=subprocess.run(['ffmpeg','-v','error','-nostdin','-f','rawvideo','-pixel_format','gbrpf32le',
            '-video_size','6x2','-i','pipe:0','-vf',HLG_TO_YUV,'-frames:v','1',
            '-pix_fmt','yuv420p10le','-f','rawvideo','pipe:1'],input=planes.tobytes(),capture_output=True,check=True)
        y=np.frombuffer(result.stdout,'<u2')[:12].reshape(2,6)
        np.testing.assert_allclose(y[0],64+876*values,atol=1)
        self.assertGreater(y.max(),940)
        self.assertLessEqual(y.max(),1023)

    def test_cli_case_and_profile_identification(self):
        output=subprocess.check_output([sys.executable,str(Path(__file__).with_name('brightness_mapping.py')),
            '--describe','--mode','realDolby'])
        self.assertEqual(json.loads(output)['profile_id'],'realDolby-v1')

    @unittest.skipUnless(os.environ.get('SDR2DV_TEST_HDR_CUDA')=='1','Enable real CuPy/GPU numeric check explicitly')
    def test_cuda_extended_reference_curve_matches_cpu_without_float64_grade(self):
        import cupy as cp
        rgb=np.array([[0,5,80,400,995],[0,2,40,600,998],[0,1,10,800,990]],dtype=np.float32)
        tone=mapping_info('realDolby');table=luminance_lut(name='realDolby')
        cpu=apply_mapping(np,rgb.astype(float),tone,table)
        gpu=apply_mapping(cp,cp.asarray(rgb),tone,cp.asarray(table))
        self.assertEqual(gpu.dtype,cp.dtype('float32'))
        actual,_=rgb_nits_to_hlg(cp,gpu,preserve_headroom=True)
        expected,_=display_to_hlg(cpu.T,preserve_headroom=True)
        np.testing.assert_allclose(cp.asnumpy(actual).T,expected,atol=2e-5,rtol=0)
        self.assertGreater(float(actual.max()),1)


if __name__=='__main__':unittest.main()
