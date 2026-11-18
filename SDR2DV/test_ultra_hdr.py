"""Numerical and compatibility checks for the source-aware UltraHDR preset."""
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from brightness_mapping import apply_mapping, mapping_info, write_cube


class UltraHdrTests(unittest.TestCase):
    def test_protected_tones_and_logarithmic_highlight_blend(self):
        # At 75% linear SDR, the highlight weight is 1/2. A 4x suggested
        # gain applies 2x (sqrt(4)), rather than a linear average of 1x and 4x.
        source_linear = np.array([0, .18, .5, .75, 1, 1], dtype=np.float32)
        sdr = np.tile(source_linear ** (1 / 2.2), (3, 1))
        hdr = np.tile(np.array([25, 200, 300, 609, 650, 2000], dtype=np.float32), (3, 1))
        result = apply_mapping(np, hdr, mapping_info('UltraHDR'), sdr_rgb=sdr)
        expected = np.tile([0, 36.54, 101.5, 304.5, 650, 1000], (3, 1))
        self.assertEqual(result.dtype, np.float32)
        np.testing.assert_allclose(result, expected, rtol=2e-6, atol=1e-5)

    def test_coloured_highlight_uses_one_gain_and_retains_sdr_chromaticity(self):
        # Red and blue in Rec.709, their known linear BT.2020 coordinates.
        sdr = np.array([[1, 0], [0, 0], [0, 1]], dtype=np.float64)
        hdr = np.full_like(sdr, 800)
        result = apply_mapping(np, hdr, mapping_info('UltraHDR'), sdr_rgb=sdr)
        np.testing.assert_allclose(result, [[627.403896, 43.313066],
            [69.097289, 11.362316], [16.391439, 895.595253]], atol=.001)

    def test_strength_bypass_and_matching_frame_requirement(self):
        sdr = np.full((3, 2), .4, dtype=np.float32)
        hdr = np.full_like(sdr, 100)
        tone = mapping_info('UltraHDR')
        self.assertEqual(tone['profile_id'], 'UltraHDR-v1')
        self.assertEqual(tone['skin_strength'], 0)
        np.testing.assert_array_equal(apply_mapping(np, hdr, mapping_info('ultrahdr', 0)), hdr)
        full = apply_mapping(np, hdr, tone, sdr_rgb=sdr)
        mixed = apply_mapping(np, hdr, mapping_info('ultrahdr', .5), sdr_rgb=sdr)
        np.testing.assert_allclose(mixed, (hdr + full) / 2, rtol=1e-6)
        for source in (None, sdr[:, :1]):
            with self.assertRaisesRegex(ValueError, 'matching SDR'):
                apply_mapping(np, hdr, tone, sdr_rgb=source)
        with tempfile.TemporaryDirectory() as directory:
            cube = Path(directory) / 'ultrahdr.cube'
            with self.assertRaisesRegex(ValueError, 'matching SDR'):
                write_cube(cube, mapping='UltraHDR')
            self.assertFalse(cube.exists())

    @unittest.skipUnless(os.environ.get('SDR2DV_TEST_HDR_CUDA') == '1', 'Enable real CuPy/GPU check')
    def test_cuda_matches_cpu_for_same_pairs(self):
        import cupy as cp
        rng = np.random.default_rng(128)
        sdr = rng.random((3, 32, 48), dtype=np.float32)
        hdr = rng.random(sdr.shape, dtype=np.float32) * 1400
        tone = mapping_info('UltraHDR')
        expected = apply_mapping(np, hdr.astype(np.float64), tone, sdr_rgb=sdr.astype(np.float64))
        actual = apply_mapping(cp, cp.asarray(hdr), tone, sdr_rgb=cp.asarray(sdr))
        self.assertEqual(actual.dtype, cp.float32)
        np.testing.assert_allclose(cp.asnumpy(actual), expected, rtol=2e-6, atol=.0003)


if __name__ == '__main__':
    unittest.main()
