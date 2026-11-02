"""Own diagnostic files so a failed Windows stdio pipe cannot abort media work."""
from contextlib import contextmanager
import io
from pathlib import Path
import sys


class DiagnosticStream:
    def __init__(self, parent, logfile):
        self.parent = parent
        self.logfile = logfile
        self.failure = None

    @property
    def encoding(self): return 'utf-8'

    def isatty(self): return False

    def _forward(self, operation, *args):
        if self.failure is not None: return
        try:
            if self.parent is not None: getattr(self.parent, operation)(*args)
        except (OSError, ValueError) as error:
            # This is diagnostic text only. Media pipes and disk I/O still fail
            # normally; they are never redirected, retried or suppressed here.
            self.failure = f'{type(error).__name__}: {error}'
            self.logfile.write(f'\n[diagnostic parent {operation} failed; using owned log: {self.failure}]\n')
            self.logfile.flush()

    def write(self, text):
        self.logfile.write(text)
        self.logfile.flush()
        self._forward('write', text)
        return len(text)

    def flush(self):
        self.logfile.flush()
        self._forward('flush')


@contextmanager
def diagnostic_streams(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    previous = sys.stdout, sys.stderr
    with (root/'stream_runtime.stdout.log').open('w', encoding='utf-8', buffering=1) as out, \
         (root/'stream_runtime.log').open('w', encoding='utf-8', buffering=1) as err:
        sys.stdout = DiagnosticStream(previous[0], out)
        sys.stderr = DiagnosticStream(previous[1], err)
        try:
            yield
        finally:
            try:
                sys.stdout.flush(); sys.stderr.flush()
            finally:
                # A failed buffered parent may fail again during interpreter
                # shutdown (exit code 120). Do not restore a known broken pipe.
                sys.stdout, sys.stderr = tuple(io.StringIO() if current.failure else parent
                    for current, parent in zip((sys.stdout,sys.stderr),previous))


def log_tail(path, limit=8192):
    try:
        with Path(path).open('rb') as source:
            source.seek(0, 2)
            size = source.tell()
            source.seek(max(0, size-limit))
            return source.read().decode('utf-8', errors='replace')
    except OSError as error:
        return f'Cannot read diagnostic log: {error}'
