"""Own a Windows child tree even if the launching TUI is terminated.

Uses the same kill-on-close Job Object contract as the PowerShell core. The
short Popen-to-assignment window is not claimed to be crash-atomic.
"""
import ctypes
from ctypes import wintypes
import os


class OwnedJob:
    def __init__(self,process):
        self.handle=None
        if os.name!='nt':return
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]

        class Limits(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
                ('flags',wintypes.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),
                ('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_=[('basic',Limits),('io',ctypes.c_ulonglong*6),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]

        self.handle=kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        native_process=None
        try:
            info=Extended();info.basic.flags=0x2000
            if not kernel.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            native_process=kernel.OpenProcess(0x0101,False,process.pid) # SET_QUOTA | TERMINATE
            assigned=native_process and kernel.AssignProcessToJobObject(self.handle,native_process)
            if not assigned and process.poll() is None:raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:self.close();raise
        finally:
            if native_process:kernel.CloseHandle(native_process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle);self.handle=None
