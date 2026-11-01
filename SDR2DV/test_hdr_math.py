"""Numerical and FFmpeg integration tests; no SDK mock or claimed AI output."""
import json
import subprocess
import unittest
import numpy as np
from hdr_math import pq_eotf, pq_encode, hlg_eotf, display_to_hlg, pq_to_hlg_filter


class TransferTests(unittest.TestCase):
    def test_absolute_pq(self):
        self.assertAlmostEqual(float(pq_eotf(.751827096247041)), 1000, places=7)
        values = np.array([0, .01, 1, 18, 100, 203, 650, 1000])
        np.testing.assert_allclose(pq_eotf(pq_encode(values)), values, atol=1e-8)

    def test_hlg_reference_and_colour_volume(self):
        grey = np.array([[0, 0, 0], [18]*3, [203]*3, [1000]*3], dtype=float)
        hlg, limited = display_to_hlg(grey)
        self.assertFalse(limited.any())
        self.assertAlmostEqual(float(hlg[2, 0]), .7498773651, places=8)
        np.testing.assert_allclose(hlg_eotf(hlg), grey, atol=.0001)
        colour = np.array([[1000, 0, 0], [0, 0, 1000], [300, 50, 20]])
        hlg, limited = display_to_hlg(colour)
        self.assertTrue(limited[0] and limited[1])
        restored = hlg_eotf(hlg)
        np.testing.assert_allclose(restored / restored.max(axis=1)[:, None],
                                   colour / colour.max(axis=1)[:, None], atol=1e-6)

    def test_actual_ffmpeg_pq_to_hlg(self):
        rgb = np.array([[0, 0, 0], [18]*3, [100]*3, [203]*3, [1000]*3,
                        [1000, 0, 0], [0, 0, 1000], [50, 300, 100], [20, 20, 800]], dtype=float)
        # Repeat each patch over an even-width raster to exercise geq RGB order.
        frame = np.repeat(rgb[None, :, :], 2, axis=0)
        frame = np.repeat(frame, 2, axis=1)
        planar = pq_encode(frame)[..., [1, 2, 0]].transpose(2, 0, 1).astype('<f4')
        chain = pq_to_hlg_filter().replace('m=bt2020nc:r=limited', 'm=gbr:r=full').replace(
            'format=yuv420p10le', 'format=gbrpf32le')
        cmd = ['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'gbrpf32le',
               '-video_size', '18x2', '-i', 'pipe:0', '-vf', chain,
               '-frames:v', '1', '-f', 'rawvideo', 'pipe:1']
        result = subprocess.run(cmd, input=planar.tobytes(), capture_output=True, check=True)
        actual = np.frombuffer(result.stdout, '<f4').reshape(3, 2, 18).transpose(1, 2, 0)[..., [2, 0, 1]]
        expected, _ = display_to_hlg(frame)
        np.testing.assert_allclose(actual, expected, atol=3e-5)


if __name__ == '__main__':
    unittest.main()
