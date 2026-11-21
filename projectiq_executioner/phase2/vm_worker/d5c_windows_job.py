"""D5c Windows process-lifetime guard for the QEMU VM controller.

The *controller itself* joins a kill-on-last-handle-close Windows Job Object
before it spawns QEMU. All regular child processes inherit the job atomically
at process creation. If Python is TerminateProcess'ed, Windows closes its job
handle and terminates the job members, including QEMU.

This is not filesystem cleanup, host-power-loss protection, or hypervisor
security attestation. On Linux a different parent-death mechanism is required.
"""
from __future__ import annotations

import os

_JOB_HANDLE: int | None = None


class JobGuardError(RuntimeError):
    """The Windows lifetime guard could not be established."""


def _winapi():
    if os.name != 'nt':
        raise JobGuardError('Windows Job Objects require Windows')
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_int64),
            ('PerJobUserTimeLimit', ctypes.c_int64),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
        ]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', BasicLimit),
            ('IoInfo', IOCounters),
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    return ctypes, wintypes, kernel, ExtendedLimit


def ensure_controller_job() -> None:
    """Attach this controller before any VM child launch; never close the job.

    Attaching the parent, instead of assigning after Popen, closes the
    spawn-before-assignment orphan race. Nested Job Object restrictions or
    missing Windows support are fatal *before* a VM can launch.
    """
    global _JOB_HANDLE
    if os.name != 'nt':
        return
    if _JOB_HANDLE is not None:
        return
    ctypes, _, kernel, ExtendedLimit = _winapi()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise JobGuardError('JOB_CREATE_FAILED')
    attached = False
    try:
        limit = ExtendedLimit()
        limit.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
            raise JobGuardError('JOB_KILL_ON_CLOSE_POLICY_FAILED')
        if not kernel.SetHandleInformation(job, 0x1, 0):  # HANDLE_FLAG_INHERIT
            raise JobGuardError('JOB_HANDLE_INHERIT_POLICY_FAILED')
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise JobGuardError('CONTROLLER_JOB_ASSIGNMENT_FAILED')
        attached = True
        # Hold the *raw OS handle* for the entire controller process lifetime.
        # Never CloseHandle on this attached job while the controller is alive;
        # doing so would terminate the controller and all its children.
        _JOB_HANDLE = int(job)
    finally:
        if not attached:
            kernel.CloseHandle(job)


def verify_child_in_job(proc) -> bool:
    """Confirm kernel membership of the exact process handle (not a reused PID)."""
    if os.name != 'nt':
        return True
    if _JOB_HANDLE is None or proc is None or not getattr(proc, '_handle', None):
        return False
    ctypes, wintypes, kernel, _ = _winapi()
    value = wintypes.BOOL(False)
    if not kernel.IsProcessInJob(proc._handle, _JOB_HANDLE, ctypes.byref(value)):
        return False
    return bool(value.value)
