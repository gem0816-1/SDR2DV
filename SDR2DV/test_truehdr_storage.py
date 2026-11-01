"""Storage planning and resume integrity checks; no mocked TrueHDR inference."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest

from truehdr_stream import required_remaining_bytes
from truehdr_backend import verify_cache, file_hash


class StorageTests(unittest.TestCase):
    def test_remaining_budget_includes_hdr10_remux(self):
        gib=1024**3
        base=required_remaining_bytes(gib,0,100,1000)
        self.assertEqual(base,int(1.3*9*gib+4*gib))
        with_hdr10=required_remaining_bytes(gib,gib,100,1000)
        self.assertEqual(with_hdr10,int(1.3*28*gib+4*gib))
        self.assertGreater(with_hdr10,base)

    def test_completed_hlg_cache_reused_after_later_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'input.mov';source.write_bytes(b'source identity')
            cache=root/'truehdr_hlg.mkv';cache.write_bytes(b'cache identity')
            args=argparse.Namespace(verify_cache=tmp,input=str(source),luminance=1000,
                                    contrast=100,saturation=100,middle_gray=50,debanding_off=False)
            params={k:getattr(args,k) for k in ('luminance','contrast','saturation','middle_gray','debanding_off')}
            manifest=dict(success=False,cache_ready=True,cache_kind='hlg-yuv420p10-lossless-v1',
                          parameters=params,input_sha256=file_hash(source),hlg_file_sha256=file_hash(cache),
                          frames=30,sdk='test-manifest')
            (root/'truehdr-report.json').write_text(json.dumps(manifest))
            result=verify_cache(args)
            self.assertTrue(result['verified'])
            self.assertEqual(result['hlg'],str(cache))
            cache.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):verify_cache(args)


if __name__=='__main__':unittest.main()
