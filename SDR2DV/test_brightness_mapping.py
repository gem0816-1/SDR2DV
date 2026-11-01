import unittest
import numpy as np

from brightness_mapping import ANCHORS, correct_warm_skin, luminance_lut, map_display_rgb, map_encoded_rgb, mapping_info
from hdr_math import pq_encode, hlg_eotf, display_to_hlg, luminance


class BrightnessMappingTests(unittest.TestCase):
    def test_curve_is_monotone_and_bounded(self):
        light = np.r_[0.,np.geomspace(1e-7,10000,10000)]
        rgb = np.repeat(light[None,:],3,axis=0)
        mapped = map_display_rgb(np,rgb,luminance_lut())
        self.assertTrue(np.isfinite(mapped).all())
        self.assertTrue(np.all(np.diff(mapped[0])>=-1e-7))
        self.assertEqual(mapped[0,0],0)
        self.assertLessEqual(mapped.max(),1000.001)

    def test_measured_anchor_targets_and_soft_shoulder(self):
        rgb = np.repeat(ANCHORS[:,0][None,:],3,axis=0)
        mapped = map_display_rgb(np,rgb,luminance_lut())[0]
        np.testing.assert_allclose(mapped,ANCHORS[:,1],atol=.08,rtol=.002)
        grey = np.repeat(np.array([200.,400.,600.,800.])[None,:],3,axis=0)
        values = map_display_rgb(np,grey,luminance_lut())[0]
        self.assertTrue(np.all(np.diff(values)>0))
        self.assertTrue(np.all(values<1000))

    def test_chromaticity_and_neutral_bypass(self):
        rgb = np.array([[12.,80.,2.,0.],[7.,60.,30.,0.],[3.,20.,9.,0.]])
        mapped = map_display_rgb(np,rgb,luminance_lut())
        np.testing.assert_allclose(mapped[:,:3]/mapped[:,:3].sum(axis=0),
            rgb[:,:3]/rgb[:,:3].sum(axis=0),atol=1e-10)
        np.testing.assert_array_equal(map_display_rgb(np,rgb,None),rgb)
        np.testing.assert_array_equal(map_display_rgb(np,rgb,luminance_lut(),0),rgb)
        self.assertEqual(mapping_info('reference',0),mapping_info('neutral'))

    def test_hlg_reference_colour_volume(self):
        rgb = np.array([[0.,0.,0.],[7.,7.,7.],[25.,20.,15.],[600.,40.,5.]])
        encoded,_ = display_to_hlg(rgb)
        mapped = map_encoded_rgb(encoded)
        display = hlg_eotf(mapped)
        self.assertTrue(np.all((mapped>=0)&(mapped<=1.000001)))
        self.assertTrue(np.all(luminance(display)<=1000.001))
        self.assertGreater(luminance(display)[1],luminance(rgb)[1])

    def test_warm_correction_reduces_yellow_and_preserves_luminance(self):
        rgb = np.array([[80.,40.],[64.,30.],[25.,18.]])
        corrected = correct_warm_skin(np,rgb,.5)
        self.assertTrue(np.all(corrected[2]/corrected[1] > rgb[2]/rgb[1]))
        self.assertTrue(np.all(corrected[1]/corrected[0] < rgb[1]/rgb[0]))
        np.testing.assert_allclose(luminance(corrected.T),luminance(rgb.T),rtol=1e-12)
        np.testing.assert_array_equal(correct_warm_skin(np,rgb,0),rgb)

    def test_warm_correction_excludes_grey_black_and_saturated_primaries(self):
        rgb = np.array([[0.,1.,30.,100.,0.,0.],
                        [0.,1.,30.,0.,100.,0.],
                        [0.,1.,30.,0.,0.,100.]])
        corrected = correct_warm_skin(np,rgb,1)
        self.assertTrue(np.isfinite(corrected).all())
        np.testing.assert_allclose(corrected,rgb,atol=1e-12)


if __name__=='__main__':
    unittest.main()
