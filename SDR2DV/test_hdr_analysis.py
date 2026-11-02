import unittest
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import numpy as np
from hdr_analysis import SceneDetector,display_rgb,frame_statistics,make_shots,pq_code,generator_config
from rpu_audit import verify


class HDRAnalysisTests(unittest.TestCase):
    def test_cta_is_linear_maxrgb_not_eotf_of_mean_or_y(self):
        rgb=np.array([[[0.,1000.]],[[0.,0.]],[[0.,0.]]])
        stats,_=frame_statistics(np,rgb)
        self.assertAlmostEqual(stats['max_rgb_nits'],1000)
        self.assertAlmostEqual(stats['mean_max_rgb_nits'],500)
        self.assertAlmostEqual(stats['mean_y_nits'],131.35)
        self.assertAlmostEqual(stats['avg_pq'],float(pq_code(np,np.array([0,1000])).mean()*4095))

    def test_hlg_75_percent_and_pq_reference(self):
        white=np.full((3,1,1),.75)
        self.assertAlmostEqual(display_rgb(np,white,'arib-std-b67')[0,0,0],203.15,delta=.1)
        encoded=pq_code(np,np.array([0,100,203,1000]))
        np.testing.assert_allclose(display_rgb(np,encoded,'smpte2084'),[0,100,203,1000],atol=.0001)

    def test_cut_detector_hard_cuts_and_single_flash_bounded_memory(self):
        a=np.zeros((3,8,8));b=np.ones_like(a)*.5
        detector=SceneDetector(8)
        for frame in [a]*10+[b]+[a]*10+[b]*20:detector.push(frame)
        self.assertEqual(detector.finish(),[0,21])
        self.assertLessEqual(len(detector.previous),2)

    def test_final_frame_cut_and_manual_shot_bounds(self):
        a=np.zeros((3,1,1));detector=SceneDetector(2)
        for frame in [a]*10+[a+1]:detector.push(frame)
        self.assertEqual(detector.finish(),[0,10])
        stats=dict(min_pq=50,max_pq=1800,avg_pq=500,max_rgb_nits=100,mean_max_rgb_nits=20)
        shots=make_shots([stats]*10,[0,5])
        self.assertEqual(shots[0]['measured_l1']['avg_pq'],500)
        self.assertEqual(shots[0]['encoded_l1'],dict(min_pq=12,max_pq=2081,avg_pq=1229))
        self.assertEqual([s['duration'] for s in shots],[5,5])
        config=generator_config([stats]*10,shots)
        self.assertEqual(config['level6']['max_frame_average_light_level'],20)
        self.assertEqual(list(config['shots'][0]['metadata_blocks'][0]),['Level1'])
        with self.assertRaises(ValueError):make_shots([stats]*10,[0,10])

    def test_dark_cut_detected_without_splitting_gradual_change(self):
        a=np.full((3,8,8),.08);b=a+.04
        detector=SceneDetector(8)
        for frame in [a]*10+[b]*10:detector.push(frame)
        self.assertEqual(detector.finish(),[0,10])
        detector=SceneDetector(8)
        for delta in np.linspace(0,.1,100):detector.push(a+delta)
        self.assertEqual(detector.finish(),[0])

    @unittest.skipUnless(shutil.which('dovi_tool'),'dovi_tool required')
    def test_actual_generated_rpu_l1_and_scene_refresh_flags(self):
        stats=[dict(min_pq=0,max_pq=3000,avg_pq=1500,max_rgb_nits=1000,mean_max_rgb_nits=70)]*10
        stats+= [dict(min_pq=30,max_pq=1800,avg_pq=400,max_rgb_nits=100,mean_max_rgb_nits=10)]*10
        config=generator_config(stats,make_shots(stats,[0,10]))
        with tempfile.TemporaryDirectory(prefix='SDR2DV_rpu_test_') as directory:
            root=Path(directory);cfg=root/'config.json';rpu=root/'RPU.bin';levels=root/'l1.json';scenes=root/'scenes.txt'
            cfg.write_text(json.dumps(config),encoding='utf-8')
            subprocess.run(['dovi_tool','generate','-j',str(cfg),'-o',str(rpu)],check=True,capture_output=True)
            subprocess.run(['dovi_tool','export','-i',str(rpu),'-l','level1='+str(levels),'-d','scenes='+str(scenes),'-f','json'],check=True,capture_output=True)
            actual=json.loads(levels.read_text());cuts=[int(s) for s in scenes.read_text().splitlines()]
            self.assertTrue(verify(config,actual,cuts)['encoded_l1_verified'])
            actual[12]['avg_pq']+=1
            with self.assertRaises(ValueError):verify(config,actual,cuts)


if __name__=='__main__':unittest.main()
