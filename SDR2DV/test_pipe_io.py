import io
import unittest
from unittest.mock import patch

from pipe_io import CHUNK_BYTES, FrameWriteError, write_frame


class BoundedPipe(io.BytesIO):
    def write(self, data):
        if len(data)>CHUNK_BYTES: raise OSError(22,'Invalid argument')
        # Real binary streams can accept fewer bytes than requested.
        return super().write(data[:65521])


class PipeWriteTests(unittest.TestCase):
    def test_large_frame_reads_are_bounded_and_truncation_still_fails(self):
        from truehdr_backend import read_frame
        payload=bytes(range(256))*(3840*1770*3//256)
        class BoundedRead(io.BytesIO):
            def read(self,n):
                if n>CHUNK_BYTES:raise OSError(22,'Invalid argument')
                return super().read(min(n,65521))
        self.assertEqual(read_frame(BoundedRead(payload),len(payload)),payload)
        with self.assertRaisesRegex(RuntimeError,'truncated'):
            read_frame(BoundedRead(payload[:-1]),len(payload))

    def test_hdr_analysis_pinned_frame_readinto_uses_same_bound(self):
        from hdr_analysis import fill_frame
        payload=b'abc'*(CHUNK_BYTES+7)
        class BoundedRead(io.BytesIO):
            def readinto(self,view):
                if len(view)>CHUNK_BYTES:raise OSError(22,'Invalid argument')
                return super().readinto(view)
        target=bytearray(len(payload))
        self.assertTrue(fill_frame(BoundedRead(payload),memoryview(target)))
        self.assertEqual(target,payload)
        with self.assertRaisesRegex(RuntimeError,'Truncated'):
            fill_frame(BoundedRead(payload[:-1]),memoryview(target))

    def test_4k_frame_chunking_and_partial_writes_preserve_every_byte(self):
        payload=bytes(range(256))*(3840*1770*6//256)
        pipe=BoundedPipe()
        with self.assertRaises(OSError):pipe.write(payload)
        write_frame(pipe,payload,label='HLG',frame_number=1)
        self.assertEqual(pipe.getvalue(),payload)

    def test_failed_write_is_not_retried_or_treated_as_a_completed_frame(self):
        class BrokenPipe:
            calls=0
            def write(self,data):
                self.calls+=1
                if self.calls==2:raise OSError(22,'Invalid argument')
                return len(data)
        pipe=BrokenPipe()
        with self.assertRaisesRegex(FrameWriteError,'1048576/2097152 confirmed bytes') as caught:
            write_frame(pipe,b'x'*(CHUNK_BYTES*2),frame_number=4)
        self.assertEqual(pipe.calls,2)
        self.assertEqual(caught.exception.__cause__.errno,22)

    def test_no_progress_is_an_error_and_cancellation_is_not_hidden(self):
        class ZeroPipe:
            def write(self,data):return 0
        with self.assertRaises(FrameWriteError):write_frame(ZeroPipe(),b'frame')
        pipe=io.BytesIO()
        with patch('task_io.check_cancel',side_effect=InterruptedError('cancelled')):
            with self.assertRaises(InterruptedError):write_frame(pipe,b'frame')
        self.assertEqual(pipe.getvalue(),b'')


if __name__=='__main__':unittest.main()
