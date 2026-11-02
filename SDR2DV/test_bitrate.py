import unittest
from unittest.mock import patch
from media_probe import choose_rate,packet_average,video_bitrate


class BitrateTests(unittest.TestCase):
    def test_metadata_uses_video_stream_not_container(self):
        media={'streams':[{'codec_type':'video','bit_rate':'8000000','duration':'10'}],
               'format':{'bit_rate':'50000000'}}
        self.assertEqual(video_bitrate('unused',media=media)['bps'],8000000)

    def test_packets_missing_metadata_reordered_pts_and_discard(self):
        packets=[dict(size='100',pts_time='.04',duration_time='.04'),
                 dict(size='100',pts_time='0',duration_time='.04'),
                 dict(size='100',pts_time='.08',duration_time='.04'),
                 dict(size='999999',pts_time='-.04',duration_time='.04',flags='KD')]
        result=packet_average(packets,25)
        self.assertEqual(result['bps'],20000)
        self.assertEqual(result['packets'],3)

    def test_packets_without_timestamps_require_fps_not_container_duration(self):
        self.assertEqual(packet_average([{'size':'100'}]*30,30)['bps'],24000)
        with self.assertRaises(ValueError):packet_average([{'size':'100'}])

    def test_source_strategy_preserves_8_15_25_50_mbps(self):
        for rate in (8,15,25,50):
            result=choose_rate(rate*1000000)
            self.assertEqual(result['target_kbps'],rate*1000)
            self.assertGreater(result['max_kbps'],result['target_kbps'])
        self.assertEqual(choose_rate(8000000,'Platform')['target_kbps'],8000)
        self.assertEqual(choose_rate(50000000,'Platform')['target_kbps'],28000)
        with self.assertRaises(ValueError):choose_rate(50000000,max_kbps=30000)


if __name__=='__main__':unittest.main()
