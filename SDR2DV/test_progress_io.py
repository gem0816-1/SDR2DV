import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_io import progress


class ProgressTests(unittest.TestCase):
    def test_update_is_complete_and_preserves_stage_and_end_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'task.progress'
            self.assertTrue(progress(path,120,2,phase='source_timing'))
            self.assertIn('phase=source_timing',path.read_text())
            self.assertTrue(progress(path,240,4,phase='truehdr',ended=True))
            self.assertEqual(path.read_text(),'frame=240\nout_time_us=4000000\nphase=truehdr\nprogress=end\n')
            self.assertEqual(list(Path(folder).glob('*.writing')),[])

    def test_optional_progress_sharing_conflict_does_not_abort_media(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'task.progress';progress(path,1)
            with patch('task_io.os.replace',side_effect=PermissionError(13,'reader sharing violation')):
                self.assertFalse(progress(path,2))
            self.assertIn('frame=1',path.read_text())
            self.assertTrue(progress(path,3))
            self.assertIn('frame=3',path.read_text())

    @unittest.skipUnless(os.name=='nt','Windows sharing modes')
    def test_real_windows_read_lock_and_read_write_delete_sharing(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
            wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'task.progress';progress(path,1)
            for share in (1,1|2|4):
                handle=kernel.CreateFileW(str(path),0x80000000,share,None,3,0,None)
                self.assertNotEqual(handle,wintypes.HANDLE(-1).value)
                try:
                    if share==1:
                        with self.assertRaises(PermissionError):path.write_text('old writer')
                        self.assertFalse(progress(path,2))
                    else:
                        path.write_text('shared reader permits a writer')
                        # MoveFileEx may still reject replacing an open target.
                        # Dropping that optional update is allowed; aborting is not.
                        self.assertIsInstance(progress(path,3),bool)
                finally:kernel.CloseHandle(handle)
            self.assertTrue(progress(path,4))
            self.assertIn('frame=4',path.read_text())


if __name__=='__main__':unittest.main()
