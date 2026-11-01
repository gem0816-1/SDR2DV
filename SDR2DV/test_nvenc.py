"""Opt-in real NVENC bit-depth/lossless round-trip test; no fake GPU backend."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np
from truehdr_stream import cache_encoding_args


@unittest.skipUnless(os.environ.get('SDR2DV_TEST_NVENC') == '1', 'Set SDR2DV_TEST_NVENC=1 on a compatible NVIDIA GPU')
class NvencTests(unittest.TestCase):
    def test_main10_cache_decodes_to_exact_source_samples(self):
        ffmpeg = os.environ.get('SDR2DV_FFMPEG') or shutil.which('ffmpeg')
        self.assertIsNotNone(ffmpeg)
        rng = np.random.default_rng(7)
        # Every low bit is exercised; truncating this to 8bit must fail.
        source = b''.join(rng.integers(64,941,256*144*3//2,dtype='<u2').tobytes() for _ in range(3))
        with tempfile.TemporaryDirectory(prefix='SDR2DV_nvenc_test_') as temporary:
            target = Path(temporary) / 'lossless.mkv'
            encode = [ffmpeg,'-v','error','-nostdin','-n','-f','rawvideo','-pix_fmt','yuv420p10le',
                '-s','256x144','-r','30','-color_primaries','bt2020','-color_trc','arib-std-b67',
                '-colorspace','bt2020nc','-color_range','tv','-i','pipe:0','-frames:v','3','-an']
            subprocess.run(encode+cache_encoding_args('nvenc')+['-color_primaries','bt2020',
                '-color_trc','arib-std-b67','-colorspace','bt2020nc','-color_range','tv',str(target)],
                input=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
            decoded = subprocess.check_output([ffmpeg,'-v','error','-nostdin','-i',str(target),
                '-map','0:v:0','-pix_fmt','yuv420p10le','-f','rawvideo','pipe:1'])
            self.assertEqual(decoded,source)


if __name__ == '__main__':
    unittest.main()
