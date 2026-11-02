"""Small shared I/O utilities for core helpers, not a second conversion engine."""
import json
import os
from pathlib import Path
import subprocess

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
        except PermissionError:
            return False
        return True


def json_command(command):
    check_cancel()
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=CREATE_FLAGS)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    check_cancel()
    return json.loads(result.stdout)


def stream_lines(command):
    # Read ffprobe incrementally; packet tables need not occupy RAM for a film.
    import tempfile
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=errors,text=True,
                                 encoding='utf-8',creationflags=CREATE_FLAGS)
        try:
            for line in process.stdout:
                check_cancel()
                yield line.rstrip('\r\n')
            if process.wait():
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8',errors='replace'))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def compact_fields(line):
    return dict(part.split('=',1) for part in line.split('|') if '=' in part)
