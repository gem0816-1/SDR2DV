"""Small shared I/O utilities for core helpers, not a second conversion engine."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def check_cancel():
    path=os.environ.get('SDR2DV_CANCEL_FILE')
    if path and Path(path).is_file():
        raise InterruptedError('Cancelled by this task')


def write_json(path,data):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def progress(path,frames,seconds=0,phase=None,ended=False):
    if path:
        phase_line=f'phase={phase}\n' if phase else ''
        end_line='progress=end\n' if ended else ''
        target=Path(path)
        staging=target.with_name(target.name+f'.{os.getpid()}.writing')
        # Readers see a complete update. A transient Windows sharing violation
        # affects this optional UI update, never the video/audio conversion.
        try:
            staging.write_text(f'frame={frames}\nout_time_us={int(seconds*1e6)}\n'+phase_line+end_line,encoding='ascii')
            os.replace(staging,target)
        except OSError:
            return False
        return True


def json_command(command):
    check_cancel()
    # Metadata has a deliberately bounded size. Large packet/frame tables use
    # stream_lines instead. Neither stderr nor stdout accumulate in Python RAM.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child=subprocess.Popen(command,stdout=output,stderr=errors,creationflags=CREATE_FLAGS)
        try:
            while True:
                check_cancel()
                if os.fstat(output.fileno()).st_size>4*1024**2 or os.fstat(errors.fileno()).st_size>16*1024**2:
                    raise RuntimeError('Probe metadata/log limit exceeded')
                try:child.wait(timeout=.1);break
                except subprocess.TimeoutExpired:pass
            if os.fstat(output.fileno()).st_size>4*1024**2:raise RuntimeError('Probe metadata limit exceeded')
            if child.returncode or os.fstat(errors.fileno()).st_size:
                raise RuntimeError('Probe failed: '+error_tail(errors))
            output.seek(0);check_cancel()
            return json.load(output)
        finally:
            if child.poll() is None:child.kill();child.wait()


def error_tail(stream,limit=65536):
    stream.seek(0,2);stream.seek(max(0,stream.tell()-limit))
    return stream.read(limit).decode('utf-8',errors='replace')


def stream_lines(command):
    # Read ffprobe incrementally; packet tables need not occupy RAM for a film.
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,text=True,
                                 encoding='utf-8',creationflags=CREATE_FLAGS)
        try:
            while True:
                line=process.stdout.readline(65537)
                if not line:break
                check_cancel()
                if len(line)>65536:raise RuntimeError('Probe record exceeds line limit')
                if os.fstat(errors.fileno()).st_size>16*1024**2:raise RuntimeError('Probe error log limit exceeded')
                yield line.rstrip('\r\n')
            if process.wait() or os.fstat(errors.fileno()).st_size:
                raise RuntimeError('Probe failed: '+error_tail(errors))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def compact_fields(line):
    return dict(part.split('=',1) for part in line.split('|') if '=' in part)
