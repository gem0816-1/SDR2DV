"""Run only SDR2DV.ps1; cooperative cancellation then this process tree only."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid

HERE=Path(__file__).resolve().parent


def stop_owned_process(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),'/PID',str(process.pid),'/T','/F'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(process.pid,signal.SIGTERM)
    process.wait(timeout=10)


class Backend:
    def __init__(self,root=HERE,shell=None):
        self.root=Path(root)
        self.shell=shell or shutil.which('pwsh') or shutil.which('powershell')
        if not self.shell:raise RuntimeError('缺少 PowerShell 5.1/7')

    def command(self,args):
        return [self.shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'SDR2DV.ps1'),*map(str,args)]

    def json_request(self,args):
        environment={**os.environ,'PYTHONUTF8':'1'}
        options=dict(stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
        else:options['start_new_session']=True
        process=subprocess.Popen(self.command(args),**options)
        try:
            out,err=process.communicate()
        except BaseException:
            stop_owned_process(process)
            raise
        text=out.decode('utf-8-sig',errors='replace').strip()
        if process.returncode:raise RuntimeError(text+'\n'+err.decode('utf-8',errors='replace'))
        return json.loads(text)


class ConversionTask:
    def __init__(self,backend,args,session_parent=None,command_override=None):
        parent=Path(session_parent or HERE.parent/'.tools/tui-runs')
        self.directory=parent/uuid.uuid4().hex
        self.directory.mkdir(parents=True,exist_ok=False)
        self.events=self.directory/'events.jsonl'
        self.cancel_file=self.directory/'cancel'
        self.report=self.directory/'result.json'
        self.cancelled=False;self.forced=False;self.started=None
        self.process=None;self._offset=0;self._partial=''
        self.command=command_override or backend.command([*args,'-EventPath',str(self.events),
            '-CancelFile',str(self.cancel_file),'-ReportPath',str(self.report)])

    def start(self):
        self._log=(self.directory/'console.log').open('wb')
        options=dict(stdout=self._log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONUTF8':'1'})
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
        else:options['start_new_session']=True
        try:self.process=subprocess.Popen(self.command,**options)
        except BaseException:self._log.close();raise
        self.started=time.monotonic()
        return self

    def read_events(self):
        if not self.events.exists():return []
        with self.events.open('r',encoding='utf-8-sig') as source:
            source.seek(self._offset)
            try:text=source.read()
            except UnicodeDecodeError:return [] # retry an incomplete UTF-8 write
            self._offset=source.tell()
        text=self._partial+text
        lines=text.split('\n');self._partial=lines.pop()
        return [json.loads(line) for line in lines if line.strip()]

    def cancel(self,grace_seconds=10):
        self.cancelled=True
        self.cancel_file.touch(exist_ok=True)
        end=time.monotonic()+grace_seconds
        while self.process and self.process.poll() is None and time.monotonic()<end:time.sleep(.1)
        if self.process and self.process.poll() is None:
            self.forced=True
            stop_owned_process(self.process)

    def close(self):
        if self.process and self.process.poll() is None:self.cancel()
        if hasattr(self,'_log'):self._log.close()
