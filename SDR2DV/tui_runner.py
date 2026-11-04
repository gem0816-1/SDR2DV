"""Run only SDR2DV.ps1; cooperative cancellation then this process tree only."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid
from windows_job import OwnedJob

HERE=Path(__file__).resolve().parent


def subprocess_environment(command):
    environment={**os.environ,'PYTHONUTF8':'1'}
    if os.name=='nt' and Path(command[0]).name.lower() in ('powershell','powershell.exe'):
        # Windows PowerShell cannot safely import inherited PowerShell 7 module
        # paths. Let 5.1 build its own standard paths, within this child only.
        environment={key:value for key,value in environment.items() if key.casefold()!='psmodulepath'}
    return environment


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
        environment=subprocess_environment(self.command(args))
        options=dict(stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
        else:options['start_new_session']=True
        process=subprocess.Popen(self.command(args),**options)
        job=None
        try:
            job=OwnedJob(process)
            out,err=process.communicate()
        except BaseException:
            stop_owned_process(process)
            raise
        finally:
            if job:job.close()
        text=out.decode('utf-8-sig',errors='replace').strip()
        if process.returncode:
            try:message=json.loads(text).get('error') or text
            except (ValueError,AttributeError):message=text
            raise RuntimeError(message+'\n'+err.decode('utf-8',errors='replace'))
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
        self.process=None;self._job=None;self._offset=0;self._partial='';self.last_event_error=None
        self.command=command_override or backend.command([*args,'-EventPath',str(self.events),
            '-CancelFile',str(self.cancel_file),'-ReportPath',str(self.report)])

    def start(self):
        self._log=(self.directory/'console.log').open('wb')
        options=dict(stdout=self._log,stderr=subprocess.STDOUT,env=subprocess_environment(self.command))
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
        else:options['start_new_session']=True
        try:
            self.process=subprocess.Popen(self.command,**options)
            self._job=OwnedJob(self.process)
        except BaseException:
            if self.process:stop_owned_process(self.process)
            self._log.close();raise
        self.started=time.monotonic()
        return self

    def read_events(self):
        if not self.events.exists():return []
        try:
            with self.events.open('r',encoding='utf-8-sig') as source:
                source.seek(self._offset)
                try:text=source.read(262144)
                except UnicodeDecodeError:return [] # retry an incomplete UTF-8 write
                self._offset=source.tell()
        except OSError as error:
            self.last_event_error=str(error);return []
        text=self._partial+text
        lines=text.split('\n');self._partial=lines.pop()
        events=[]
        for line in lines:
            if not line.strip():continue
            try:
                value=json.loads(line)
                if not isinstance(value,dict) or 'type' not in value or not isinstance(value.get('data'),dict):raise ValueError('Invalid event shape')
                events.append(value)
            except ValueError as error:self.last_event_error=str(error)
        if len(self._partial)>1048576:
            self.last_event_error='Event record exceeds 1 MiB';self._partial=''
        return events

    def cancel(self,grace_seconds=10):
        self.cancelled=True
        try:self.cancel_file.touch(exist_ok=True)
        except OSError as error:
            self.last_event_error='Cannot write cancellation request: '+str(error)
            if self.process and self.process.poll() is None:
                self.forced=True;stop_owned_process(self.process)
            return
        end=time.monotonic()+grace_seconds
        while self.process and self.process.poll() is None and time.monotonic()<end:time.sleep(.1)
        if self.process and self.process.poll() is None:
            self.forced=True
            stop_owned_process(self.process)

    def close(self):
        try:
            if self.process and self.process.poll() is None:self.cancel()
        finally:
            if self._job:self._job.close()
            if hasattr(self,'_log'):self._log.close()
