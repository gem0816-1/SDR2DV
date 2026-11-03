import unittest
import numpy as np
from compare_hdr_reference import brightness_metrics


class ReferenceComparisonTests(unittest.TestCase):
    def test_equal_pixels_zero_error_and_black_correlation_is_not_nan(self):
        black=np.zeros((8,8,3))
        result=brightness_metrics(black,black)
        self.assertEqual(result['pq_luminance_mae'],0)
        self.assertIsNone(result['log_luminance_correlation'])
        self.assertEqual(result['candidate']['max_rgb_nits'],0)

    def test_superwhite_is_measured_not_clipped_and_geometry_checked(self):
        reference=np.full((8,8,3),1600.)
        candidate=np.full((8,8,3),800.)
        result=brightness_metrics(reference,candidate)
        self.assertGreater(result['pq_luminance_mae'],0)
        self.assertAlmostEqual(result['reference']['max_y_nits'],1600)
        self.assertEqual(result['reference']['fraction_y_gt1000'],1)
        with self.assertRaises(ValueError):brightness_metrics(reference,candidate[:1])
        candidate[0,0,0]=np.nan
        with self.assertRaises(ValueError):brightness_metrics(reference,candidate)
