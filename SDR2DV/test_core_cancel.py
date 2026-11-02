"""Opt-in real PowerShell/TrueHDR cancellation, using an explicitly chosen fixture."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from tui_runner import Backend,ConversionTask


@unittest.skipUnless(os.environ.get('SDR2DV_TEST_CANCEL_INPUT'),'Set SDR2DV_TEST_CANCEL_INPUT to a real SDR fixture')
class CoreCancellationTests(unittest.TestCase):
    def test_core_cooperative_cancel_cleans_only_its_own_directory(self):
        source=Path(os.environ['SDR2DV_TEST_CANCEL_INPUT']);before=source.stat()
        with tempfile.TemporaryDirectory(prefix='SDR2DV_cancel_test_') as folder:
            root=Path(folder);sentinel=root/'other-cache';sentinel.mkdir();(sentinel/'data').write_bytes(b'keep')
            task=ConversionTask(Backend(),['-InputPath',str(source),'-HdrMode','TrueHDR','-Bilibili',
                '-OutputDir',str(root/'output')],root/'sessions').start()
            workspace=None;ready=False
            try:
                until=time.monotonic()+45
                while time.monotonic()<until and task.process.poll() is None:
                    for event in task.read_events():
                        if event['type']=='workspace':workspace=Path(event['data']['path'])
                        if event['type']=='stage' and event['data'].get('stage')=='TrueHDR/HLG':ready=True
                    if ready:break
                    time.sleep(.05)
                self.assertTrue(ready,'Core never reached the TrueHDR stage')
                task.cancel(12)
                self.assertFalse(task.forced,'Core did not cancel cooperatively')
                self.assertEqual(task.process.returncode,130)
                self.assertIsNotNone(workspace);self.assertFalse(workspace.exists())
                self.assertEqual((sentinel/'data').read_bytes(),b'keep')
                self.assertEqual(source.stat().st_size,before.st_size)
                self.assertEqual(source.stat().st_mtime_ns,before.st_mtime_ns)
                self.assertEqual(json.loads(task.report.read_text())['exit_code'],130)
            finally:task.close()


if __name__=='__main__':unittest.main()
