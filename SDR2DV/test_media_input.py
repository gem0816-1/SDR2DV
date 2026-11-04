import unittest
from truehdr_backend import source_video_filter


class TrueHDRInputTests(unittest.TestCase):
    def test_valid_sdr_preflight_builds_no_resize_or_tone_filter(self):
        chain=source_video_filter(self.video())
        self.assertIn('t=bt470m',chain)
        self.assertNotIn('scale=',chain.replace('zscale=',''))
        self.assertNotIn('tonemap',chain)

    @staticmethod
    def video():return dict(color_transfer='bt709',color_primaries='bt709',color_space='bt709',
        color_range='tv',width=1920,height=1080,pix_fmt='yuv420p',bits_per_raw_sample='8',sample_aspect_ratio='1:1')

    def test_hdr_ambiguous_colour_bit_depth_geometry_and_rotation_rejected(self):
        for changes in (dict(color_transfer='arib-std-b67'),dict(color_transfer=None),
            dict(pix_fmt='yuv420p10le',bits_per_raw_sample='10'),dict(width=1919),
            dict(pix_fmt='yuv420p14le',bits_per_raw_sample='0'),dict(pix_fmt='p210le',bits_per_raw_sample='0'),dict(color_primaries='bt2020'),
            dict(sample_aspect_ratio='4:3'),dict(side_data_list=[dict(rotation=90)])):
            with self.assertRaises(ValueError):source_video_filter({**self.video(),**changes})

    def test_invalid_frame_geometry_and_interlacing_are_rejected(self):
        for changes in (dict(width=0),dict(width=-2),dict(height=0),dict(width=20000),
                        dict(field_order='tt'),dict(disposition={'attached_pic':1})):
            with self.assertRaises(ValueError):source_video_filter({**self.video(),**changes})


if __name__=='__main__':unittest.main()
