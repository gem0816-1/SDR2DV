import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from stream_diagnostics import DiagnosticStream


class BrokenDiagnosticParent:
    def write(self, value): raise OSError(22, 'Invalid argument')
    def flush(self): raise OSError(22, 'Invalid argument')


class DiagnosticTests(unittest.TestCase):
    def test_parent_stdio_failure_keeps_diagnostics_and_work_running(self):
        parent=BrokenDiagnosticParent()
        with self.assertRaises(OSError): print('pipeline status', file=parent, flush=True)
        saved=io.StringIO(); stream=DiagnosticStream(parent,saved)
        print('pipeline status', file=stream, flush=True)
        print('CUDA/encoder original error', file=stream, flush=True)
        self.assertIn('Invalid argument',stream.failure)
        self.assertIn('CUDA/encoder original error',saved.getvalue())

    def test_diagnostic_disk_errors_and_media_write_errors_are_not_suppressed(self):
        with self.assertRaises(OSError):DiagnosticStream(io.StringIO(),BrokenDiagnosticParent()).write('disk')
        from stream_diagnostics import diagnostic_streams
        with tempfile.TemporaryDirectory() as folder:
            with patch('sys.stdout',io.StringIO()),patch('sys.stderr',io.StringIO()):
                with self.assertRaisesRegex(OSError,'media'):
                    with diagnostic_streams(folder):raise OSError(22,'media pipe closed')

    def test_real_closed_stderr_pipe_does_not_abort_or_exit_120(self):
        with tempfile.TemporaryDirectory() as folder:
            code=('import sys;from stream_diagnostics import diagnostic_streams;'
                  'sys.stdin.read(1);\n'
                  'with diagnostic_streams(sys.argv[1]):\n'
                  ' print("before inference",file=sys.stderr,flush=True)\n'
                  ' print("after inference",file=sys.stderr,flush=True)\n'
                  ' print("conversion continues",flush=True)\n')
            p=subprocess.Popen([sys.executable,'-B','-c',code,folder],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=Path(__file__).resolve().parent)
            p.stderr.close();p.stderr=None
            out,_=p.communicate(b'x',timeout=10)
            self.assertEqual(p.returncode,0)
            self.assertIn(b'conversion continues',out)
            saved=(Path(folder)/'stream_runtime.log').read_text()
            self.assertIn('after inference',saved)
            self.assertIn('diagnostic parent',saved)

    def test_failure_diagnostics_survive_core_cleanup_without_copying_media(self):
        from tui_runner import Backend
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'owned-media';work.mkdir()
            media=work/'truehdr_hlg.mkv';media.write_bytes(b'not a log')
            (work/'stream_hlg_encode.log').write_text('encoder real error',encoding='utf-8')
            (work/'truehdr-report.json').write_text(json.dumps({'error':'original','traceback':'line 159','frames':0}))
            unrelated=root/'other';unrelated.mkdir();(unrelated/'keep').touch()
            events=root/'session/events.jsonl'
            script=root/'check.ps1'
            script.write_text('''param($Core,$Work,$Events)
$EventPath=$Events
function Write-Log { param($Message,$Level) }
. $Core
try { throw 'original failure' } catch { $saved=Save-CoreFailureDiagnostics $Work $_ }
# Simulate the core's already bounded cleanup on this test-owned directory.
Remove-Item -LiteralPath $Work -Recurse -Force
Write-Output $saved
''',encoding='utf-8-sig')
            command=[Backend().shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                str(Path(__file__).resolve().parent/'core.ps1'),str(work),str(events)]
            result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            saved=events.parent/('diagnostics_'+work.name)
            self.assertEqual((saved/'stream_hlg_encode.log').read_text(),'encoder real error')
            self.assertIn('line 159',(saved/'truehdr-report.json').read_text())
            self.assertFalse((saved/media.name).exists())
            self.assertTrue((unrelated/'keep').exists());self.assertFalse(work.exists())


if __name__=='__main__':unittest.main()
