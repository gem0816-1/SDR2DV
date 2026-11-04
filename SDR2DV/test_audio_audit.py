import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from audio_audit import compare,hires_precheck
from media_probe import probe
from mp4_audio import audio_entries,validate_entries


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg/ffprobe required')
class AudioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='SDR2DV_audio_test_')
        cls.directory=Path(cls.temporary.name)
        t=np.arange(48000)
        # Distinct channels and nonzero low bits catch truncation/channel swaps.
        values=np.stack(((np.sin(t*.071)*.6*(2**23-1)).astype(np.int32),
                         (np.cos(t*.043)*.3*(2**23-1)).astype(np.int32)),axis=1)
        raw=(values.astype('<i4').view('u1').reshape(-1,4)[:,:3]).tobytes()
        cls.raw=cls.directory/'24bit.pcm';cls.raw.write_bytes(raw)
        cls.source=cls.directory/'原始24bit.wav'
        cls.run_ffmpeg('-f','s24le','-ar','48000','-ac','2','-i',str(cls.raw),'-c:a','pcm_s24le',str(cls.source))

    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    @staticmethod
    def run_ffmpeg(*args):
        result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y',*args],capture_output=True)
        if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))

    def test_pcm24_to_alac_sample_exact_and_descriptor(self):
        out=self.directory/'ALAC24.mp4'
        self.run_ffmpeg('-i',str(self.source),'-c:a','alac','-sample_fmt','s32p','-bits_per_raw_sample','24',str(out))
        result=compare(self.source,out)
        self.assertTrue(result['tracks'][0]['decoded_samples_identical'])
        self.assertEqual(result['tracks'][0]['target_pcm']['samples_per_channel'],48000)
        self.assertEqual(result['mp4_sample_entries'][0]['alac'],dict(bit_depth=24,channels=2,sample_rate=48000))
        self.assertFalse(result['platform_audio_verified'])

    def test_16bit_stays_16bit_not_fake_hires(self):
        src=self.directory/'16bit.wav';out=self.directory/'16bit.mp4'
        self.run_ffmpeg('-i',str(self.source),'-c:a','pcm_s16le',str(src))
        self.run_ffmpeg('-i',str(src),'-c:a','alac','-sample_fmt','s16p','-bits_per_raw_sample','16',str(out))
        result=compare(src,out)
        self.assertEqual(result['tracks'][0]['effective_bits'],16)
        self.assertFalse(result['tracks'][0]['hires']['source_spec_candidate'])

    def test_pcm_big_endian_to_alac(self):
        src=self.directory/'big-endian.mov';out=self.directory/'big-endian.mp4'
        self.run_ffmpeg('-i',str(self.source),'-c:a','pcm_s24be',str(src))
        self.run_ffmpeg('-i',str(src),'-c:a','alac','-bits_per_raw_sample','24',str(out))
        self.assertTrue(compare(src,out)['tracks'][0]['decoded_samples_identical'])

    def test_ipcm24_descriptor_valid_but_platform_unverified(self):
        out=self.directory/'PCM24.mp4'
        self.run_ffmpeg('-i',str(self.source),'-c:a','copy','-strict','unofficial',str(out))
        result=compare(self.source,out)
        entry=result['mp4_sample_entries'][0]
        self.assertEqual(entry['tag'],'ipcm');self.assertEqual(entry['pcmC']['sample_size'],24)
        self.assertTrue(entry['pcmC']['little_endian'])
        self.assertIn('platform_compatibility_risk',entry)
        altered=self.directory/'bad-descriptor.mp4'
        raw=bytearray(out.read_bytes());index=raw.index(b'pcmC');raw[index+9]=16;altered.write_bytes(raw)
        streams=[s for s in probe(out)['streams'] if s['codec_type']=='audio']
        with self.assertRaises(ValueError):validate_entries(altered,streams)

    def test_all_tracks_and_compressed_packets_preserved(self):
        src=self.directory/'two-aac.m4a';out=self.directory/'two-aac-copy.mp4'
        self.run_ffmpeg('-i',str(self.source),'-map','0:a','-map','0:a','-c:a','aac','-b:a','192k',str(src))
        self.run_ffmpeg('-i',str(src),'-map','0:a','-c:a','copy',str(out))
        result=compare(src,out)
        self.assertEqual(len(result['tracks']),2)
        self.assertTrue(all(t['compressed_packets_identical'] for t in result['tracks']))

    def test_changed_signal_or_missing_samples_fails(self):
        out=self.directory/'changed.mp4'
        self.run_ffmpeg('-i',str(self.source),'-af','volume=0.5','-c:a','alac','-bits_per_raw_sample','24',str(out))
        with self.assertRaisesRegex(ValueError,'samples differ'):compare(self.source,out)
        with self.assertRaisesRegex(ValueError,'samples differ'):compare(self.source,out,allow_lossy=True)
        copied=self.directory/'changed-copy.wav'
        self.run_ffmpeg('-i',str(self.source),'-af','volume=0.5','-c:a','pcm_s24le',str(copied))
        with self.assertRaisesRegex(ValueError,'samples differ'):compare(self.source,copied,allow_lossy=True)
        self.run_ffmpeg('-i',str(self.source),'-t','0.9','-c:a','alac','-bits_per_raw_sample','24',str(out))
        with self.assertRaisesRegex(ValueError,'samples differ'):compare(self.source,out)

    def test_audio_video_offset_preserved_and_shift_rejected(self):
        src=self.directory/'offset.mov';out=self.directory/'offset.mp4'
        self.run_ffmpeg('-f','lavfi','-i','color=black:size=64x64:rate=30:duration=1.25',
            '-itsoffset','0.125','-i',str(self.source),'-c:v','libx264','-c:a','copy',str(src))
        self.run_ffmpeg('-copyts','-i',str(src),'-c:v','copy','-c:a','alac','-bits_per_raw_sample','24','-avoid_negative_ts','disabled',str(out))
        self.assertTrue(compare(src,out)['tracks'][0]['decoded_samples_identical'])
        self.run_ffmpeg('-i',str(src),'-c:v','copy','-af','asetpts=PTS-STARTPTS','-c:a','alac','-bits_per_raw_sample','24',str(out))
        with self.assertRaisesRegex(ValueError,'offset changed'):compare(src,out)


if __name__=='__main__':unittest.main()
