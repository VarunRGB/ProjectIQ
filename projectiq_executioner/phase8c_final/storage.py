"""Observed disk budget and optional Windows fixed-volume bound.
Polling is a tripwire, NOT a hard quota. A mounted volume bounds files on it,
not a compromised host process or files written elsewhere.
"""
import os, stat, shutil, threading, time
from pathlib import Path

GIB=1024**3


def inspect_volume(path):
    path=Path(path).resolve(strict=True)
    usage=shutil.disk_usage(path)
    result={'total_bytes':usage.total,'free_bytes':usage.free,'separate_volume_observed':False,'volume_capacity_bounded':False,'global_disk_bound_verified':False}
    if os.name!='nt':return result
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    func=kernel.GetVolumePathNameW;func.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD];func.restype=wintypes.BOOL
    def mount(p):
        buf=ctypes.create_unicode_buffer(32768)
        if not func(str(p),buf,len(buf)):raise OSError('VOLUME_PATH_QUERY_FAILED')
        return buf.value
    # Compare volume GUIDs, not drive letters (mount aliases may share one disk).
    guidfn=kernel.GetVolumeNameForVolumeMountPointW;guidfn.argtypes=[wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD];guidfn.restype=wintypes.BOOL
    def guid(p):
        buf=ctypes.create_unicode_buffer(1024)
        if not guidfn(mount(p),buf,len(buf)):raise OSError('VOLUME_ID_QUERY_FAILED')
        return buf.value.casefold()
    sysroot=os.environ.get('SystemRoot')
    if not sysroot:return result
    result['separate_volume_observed']=guid(path)!=guid(sysroot)
    result['volume_capacity_bounded']=result['separate_volume_observed'] and usage.total<=32*GIB
    return result


def measure(root):
    """Bounded walk; don't follow links or accept unknown file kinds."""
    root=Path(root);st=root.lstat()
    if not stat.S_ISDIR(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('STORAGE_TREE_UNSAFE')
    total=0;count=0
    def fail(exc):raise exc
    for base,dirs,files in os.walk(root,followlinks=False,onerror=fail):
        for n in dirs+files:
            p=Path(base)/n;count+=1
            try:st=p.lstat()
            except FileNotFoundError:continue  # atomic journal replacement may remove an entry
            if count>10000 or stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('STORAGE_TREE_UNSAFE')
            if stat.S_ISREG(st.st_mode):total+=st.st_size
            elif not stat.S_ISDIR(st.st_mode):raise ValueError('STORAGE_TREE_UNSAFE')
    return total


class DiskWatchdog:
    def __init__(self,root,proc,*,max_bytes=14*GIB,min_free=6*GIB,interval=.25):
        self.root=Path(root);self.proc=proc;self.max_bytes=max_bytes;self.min_free=min_free;self.interval=interval;self.stop_event=threading.Event();self.tripped=threading.Event();self.peak_bytes=0;self.reason=None;self.thread=None;self.samples=0
    def sample(self):
        try:
            used=measure(self.root);self.peak_bytes=max(used,self.peak_bytes);self.samples+=1
            if used>=self.max_bytes or shutil.disk_usage(self.root).free<self.min_free:raise ValueError('STORAGE_BUDGET_EXCEEDED')
        except Exception as exc:
            self.reason='STORAGE_BUDGET_EXCEEDED' if type(exc) is ValueError and str(exc)=='STORAGE_BUDGET_EXCEEDED' else 'STORAGE_MEASUREMENT_FAILED';self.tripped.set()
            try:
                if self.proc.poll() is None:self.proc.kill()
            except Exception:self.reason='STORAGE_TERMINATION_UNVERIFIED'
            return False
        return True
    def run(self):
        while not self.stop_event.is_set():
            if not self.sample():return
            self.stop_event.wait(self.interval)
    def start(self):
        if not self.sample():raise ValueError('STORAGE_GUARD_TRIPPED')
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
        if self.thread and self.thread.is_alive():self.tripped.set();self.reason='STORAGE_MONITOR_STOP_UNVERIFIED'
    def report(self):
        return {'scope':'MANAGED_RUN_FILE_LENGTH_SAMPLING_NOT_HARD_QUOTA','samples':self.samples,'peak_logical_file_bytes':self.peak_bytes,'budget_bytes':self.max_bytes,'tripped':self.tripped.is_set(),'reason':self.reason,'global_disk_bound_verified':False}
