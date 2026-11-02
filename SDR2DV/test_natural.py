import unittest
import numpy as np
from brightness_mapping import natural_luminance,apply_mapping,mapping_info
from hdr_math import display_to_hlg,hlg_eotf,pq_encode,pq_eotf


class NaturalTests(unittest.TestCase):
    def test_black_shadows_diffuse_white_and_midtones_are_identity(self):
        values=np.array([0,.0001,.01,.1,1,10,50,100,203,400,600],dtype=np.float32)
        np.testing.assert_array_equal(natural_luminance(np,values),values)
        rgb=np.repeat(values[None,:],3,axis=0)
        np.testing.assert_array_equal(apply_mapping(np,rgb,mapping_info('natural')),rgb)

    def test_shoulder_monotonic_bounded_and_continuous(self):
        x=np.r_[0,np.geomspace(.0001,10000,10000)]
        y=natural_luminance(np,x)
        self.assertTrue(np.all(np.diff(y)>=0))
        self.assertLessEqual(y.max(),1000)
        self.assertAlmostEqual(float(natural_luminance(np,np.array(1000.))),980)
        for point,slope in [(600,1),(1000,.05)]:
            left=(natural_luminance(np,np.array(point))-natural_luminance(np,np.array(point-.001)))/.001
            right=(natural_luminance(np,np.array(point+.001))-natural_luminance(np,np.array(point)))/.001
            self.assertAlmostEqual(left,right,places=4)
            self.assertAlmostEqual(left,slope,places=4)

    def test_preserves_chromaticity_without_skin_or_saturation_manipulation(self):
        rgb=np.array([[800,1800,0],[700,1200,0],[600,150,0]],dtype=np.float32)
        result=apply_mapping(np,rgb,mapping_info('natural'))
        self.assertEqual(result.dtype,np.dtype('float32'))
        np.testing.assert_allclose(result[:,:2]/result[:,:2].sum(axis=0),
                                   rgb[:,:2]/rgb[:,:2].sum(axis=0),atol=1e-7)
        self.assertEqual(mapping_info('natural')['skin_strength'],0)

    def test_bt2100_reference_points_and_roundtrip(self):
        pq=np.array([0,1,100,203,1000],dtype=float)
        np.testing.assert_allclose(pq_eotf(pq_encode(pq)),pq,atol=1e-8)
        encoded,_=display_to_hlg(np.array([[203.,203,203],[1000.,1000,1000]]))
        np.testing.assert_allclose(encoded[:,0],[.749877,.999999996],atol=5e-5)
        np.testing.assert_allclose(hlg_eotf(encoded),[[203]*3,[1000]*3],atol=1e-4)


if __name__=='__main__':unittest.main()
