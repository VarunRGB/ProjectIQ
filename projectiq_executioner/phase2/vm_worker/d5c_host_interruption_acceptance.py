"""D5c Gate 1: Windows host-controller hard-interruption acceptance.

Deliberately kills a separate *trusted* Python VM controller after a clean
non-student QEMU guest boots. Verifies that QEMU's exact Windows process handle
becomes signalled because of the controller's kill-on-close Job Object.
This is an opt-in development acceptance probe, not untrusted-code approval.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .d5c_windows_job import ensure_controller_job, verify_child_in_job
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

STATUS_PASS = 'D5C_WINDOWS_CONTROLLER_INTERRUPT_VERIFIED_NOT_ATTESTED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CHECKS = (
    'pinned_toolchain_and_template',
    'controller_job_guard_initialized',
    'vm_process_identity_verified',
    'fresh_disposable_vm_booted',
    'controller_hard_termination_observed',
    'qemu_exited_after_controller_death',
    'temporary_vm_storage_removed',
)
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
BOOT_TIMEOUT = 160
EXIT_TIMEOUT = 35


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2, 'stage': 'd5c_gate1_host_controller_interruption',
        'status': 'NOT_EXECUTED', 'cleanup': 'NOT_STARTED',
        'execution': 'NOT_PERFORMED', 'security_attestation': ATTESTATION,
        'student_code_executed': False,
        'checks': {check: 'NOT_VERIFIED' for check in CHECKS},
        'remaining_security_gates': [
            'D5C_STARTUP_CANCELLATION_AND_ORPHAN_STORAGE_SWEEP_PENDING',
            'D5C_PARTIAL_CLEANUP_AND_RACE_INJECTION_PENDING',
            'D5C_LINUX_PARENT_DEATH_AND_HOST_POWER_LOSS_NOT_COVERED',
            'D5D_ISOLATION_AND_DESCENDANT_ACCOUNTING_PENDING',
            'D5E_SECURITY_GO_NO_GO_PENDING',
        ],
    }


def _atomic_record(path: Path, record: dict) -> None:
    temp = path.with_name(path.name + '.writing')
    temp.write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
    os.replace(temp, path)


def _read_record(path: Path, *, maximum: int = 600) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            return None
        value = json.loads(path.read_text('utf-8'))
        return value if type(value) is dict else None
    except (OSError, ValueError, UnicodeError):
        return None


def _child_vm(config_file: Path, workspace: Path) -> int:
    """Separate process: normal finally blocks must NOT execute on hard kill."""
    try:
        config = load_config(config_file)
        if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            raise RuntimeError('HOST_TOOLCHAIN_INVALID')
        ensure_controller_job()   # before *any* QEMU process launch
        _atomic_record(workspace / 'job_ready.json', {'schema_version': '0.1', 'armed': True})
        work = workspace / 'vm'
        work.mkdir(mode=0o700)
        key, pub = make_ssh_key(config, work)
        seed = make_nocloud_iso(work, pub, 'projectiq-d5c-' + uuid.uuid4().hex,
                                workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True)
        port = _free_loopback_port()
        worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB}, work,
                            port, ram_mib=VM_RAM_MIB,
                            cpus=effective_vm_vcpus(config, 2))
        expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
        if worker.network_spec() != expected:
            raise RuntimeError('NETWORK_POLICY_MISMATCH')
        worker.start(seed)
        if worker.proc is None or worker.proc.poll() is not None or not verify_child_in_job(worker.proc):
            raise RuntimeError('VM_NOT_GUARDED')
        _atomic_record(workspace / 'launched.json', {
            'schema_version': '0.1', 'vm_pid': worker.proc.pid,
            'guarded': True,
        })
        ssh = SSHControl(config, work, port, key)
        ssh.wait_ssh(worker, seconds=110)
        if worker.proc.poll() is not None:
            raise RuntimeError('VM_EXITED_BEFORE_BOOT')
        _atomic_record(workspace / 'booted.json', {'schema_version': '0.1', 'booted': True})
        # Deliberately never stop QEMU: parent forcibly kills this controller.
        while worker.proc.poll() is None:
            time.sleep(0.2)
        raise RuntimeError('VM_EXITED_BEFORE_CONTROLLER_TERMINATION')
    except Exception as exc:
        try:
            _atomic_record(workspace / 'child_error.json', {
                'schema_version': '0.1', 'reason': type(exc).__name__,
            })
        except OSError:
            pass
        return 2


class _WindowsProcessHandle:
    """Immutable kernel handle to one verified QEMU process (not a PID alone)."""
    def __init__(self, pid: int, expected_image: str):
        if os.name != 'nt' or type(pid) is not int or not 0 < pid < 2**31:
            raise ValueError('INVALID_QEMU_PID')
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        handle = k.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            raise ValueError('QEMU_HANDLE_NOT_OPENABLE')
        self._kernel = k
        self._handle = handle
        self.pid = pid
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                raise ValueError('QEMU_PROCESS_IMAGE_UNREADABLE')
            observed = os.path.normcase(os.path.realpath(buf.value))
            expected = os.path.normcase(os.path.realpath(expected_image))
            if observed != expected:
                raise ValueError('QEMU_PROCESS_IMAGE_MISMATCH')
            if k.WaitForSingleObject(handle, 0) != 258:   # WAIT_TIMEOUT: alive
                raise ValueError('QEMU_NOT_LIVE_AT_INSPECTION')
        except Exception:
            k.CloseHandle(handle)
            self._handle = None
            raise

    def wait_exited(self, seconds: int = EXIT_TIMEOUT) -> bool:
        return self._kernel.WaitForSingleObject(self._handle, seconds * 1000) == 0

    def is_live(self) -> bool:
        return self._handle is not None and self._kernel.WaitForSingleObject(self._handle, 0) == 258

    def emergency_terminate(self) -> bool:
        """Only the already-verified OS handle, never an untrusted/reused PID."""
        if self._handle is None or not self.is_live():
            return True
        from ctypes import wintypes
        self._kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel.TerminateProcess.restype = wintypes.BOOL
        return (bool(self._kernel.TerminateProcess(self._handle, 1))
                and self.wait_exited(12))

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _expected_qemu(config: dict) -> str:
    binary = config.get('qemu_binary') or shutil.which('qemu-system-x86_64')
    if not binary:
        raise ValueError('QEMU_BINARY_NOT_CONFIGURED')
    result = shutil.which(str(binary)) or str(binary)
    return str(Path(result).resolve())


def run(config_path: Path, *, execute: bool = False, acknowledge: bool = False,
        work_root: Path | None = None) -> dict:
    result = _base_report()
    if os.name != 'nt':
        result['status'] = 'WINDOWS_JOB_ACCEPTANCE_REQUIRES_WINDOWS'
        return result
    try:
        config = load_config(config_path)
        toolchain = verify_host_toolchain(config)
        if toolchain['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            result['status'] = 'HOST_GATES_BLOCKED'
            return result
        result['checks']['pinned_toolchain_and_template'] = 'PASS'
        expected_image = _expected_qemu(config)
    except (OSError, ValueError, KeyError) as exc:
        result['status'] = 'HOST_GATES_BLOCKED'
        result['failure_stage'] = 'HOST_PREFLIGHT_' + type(exc).__name__.upper()
        return result
    if not execute:
        result['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return result
    if not acknowledge:
        result['status'] = 'HOST_TERMINATION_ACKNOWLEDGMENT_REQUIRED'
        return result

    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    workspace = None
    controller = None
    qemu = None
    complete = False
    failure = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5c_ctrl_', dir=parent) as td:
            workspace = Path(td)
            try:
                cmd = [sys.executable, '-m',
                       'projectiq_executioner.phase2.vm_worker.d5c_host_interruption_acceptance',
                       '--_trusted-child', '--vm-config', str(config_path.resolve()),
                       '--_child-workspace', str(workspace)]
                # The parent does NOT join its own job: only the child controller
                # is terminated, and only its own QEMU descendants are affected.
                with (workspace / 'controller.log').open('wb') as sink:
                    controller = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                                  stdout=sink, stderr=subprocess.STDOUT,
                                                  cwd=str(Path(__file__).resolve().parents[3]))
                result['execution'] = 'CHILD_CONTROLLER_STARTED'
                deadline = time.monotonic() + BOOT_TIMEOUT
                launched = None
                while time.monotonic() < deadline:
                    if controller.poll() is not None:
                        failure = 'CONTROLLER_EXITED_BEFORE_READY'
                        break
                    guard = _read_record(workspace / 'job_ready.json')
                    if guard == {'schema_version': '0.1', 'armed': True}:
                        result['checks']['controller_job_guard_initialized'] = 'PASS'
                    if launched is None:
                        candidate = _read_record(workspace / 'launched.json')
                        if candidate is not None and set(candidate) == {'schema_version', 'vm_pid', 'guarded'} and (
                                candidate['schema_version'] == '0.1' and candidate['guarded'] is True and
                                type(candidate['vm_pid']) is int):
                            launched = candidate
                            try:
                                qemu = _WindowsProcessHandle(launched['vm_pid'], expected_image)
                                result['checks']['vm_process_identity_verified'] = 'PASS'
                            except (ValueError, OSError):
                                failure = 'QEMU_IDENTITY_NOT_VERIFIED'
                                break
                    booted = _read_record(workspace / 'booted.json')
                    if booted == {'schema_version': '0.1', 'booted': True} and qemu is not None:
                        result['checks']['fresh_disposable_vm_booted'] = 'PASS'
                        break
                    if _read_record(workspace / 'child_error.json') is not None:
                        failure = 'CHILD_PROBE_REPORTED_FAILURE'
                        break
                    time.sleep(0.2)
                else:
                    failure = 'CLEAN_VM_BOOT_TIMEOUT'
                # The target must be alive *immediately before* controller kill.
                # A VM crash that happens to precede the kill is not evidence.
                vm_alive_at_kill = bool(qemu is not None and qemu.is_live())
                if not vm_alive_at_kill:
                    failure = failure or 'QEMU_NOT_ALIVE_BEFORE_HARD_TERMINATION'
                # Always terminate *only* the controller process we started.
                if controller.poll() is None:
                    controller.kill()  # Windows TerminateProcess, bypasses Python finally
                    controller.wait(timeout=12)
                if (controller.poll() is not None and vm_alive_at_kill and
                        result['checks']['fresh_disposable_vm_booted'] == 'PASS'):
                    result['checks']['controller_hard_termination_observed'] = 'PASS'
                if qemu is not None and qemu.wait_exited():
                    if result['checks']['controller_hard_termination_observed'] == 'PASS':
                        result['checks']['qemu_exited_after_controller_death'] = 'PASS'
                elif qemu is not None:
                    failure = failure or 'ORPHAN_QEMU_PROCESS_SURVIVED'
                    result['emergency_verified_handle_kill'] = qemu.emergency_terminate()
            finally:
                if controller is not None and controller.poll() is None:
                    controller.kill()
                    controller.wait(timeout=12)
                if qemu is not None:
                    qemu.close()
            # Do NOT remove the temp directory until the QEMU process ended.
            if result['checks']['qemu_exited_after_controller_death'] != 'PASS':
                failure = failure or 'QEMU_LIFETIME_UNVERIFIED'
                # TemporaryDirectory may fail to remove a still-running VM's
                # locked files. That is a CLEANUP_FAILED result, never success.
        complete = workspace is not None and not workspace.exists()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        failure = failure or 'HOST_PROBE_' + type(exc).__name__.upper()
    if complete and result['checks']['qemu_exited_after_controller_death'] == 'PASS':
        result['checks']['temporary_vm_storage_removed'] = 'PASS'
        result['cleanup'] = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
    else:
        result['cleanup'] = 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
    if failure is not None:
        result['failure_code'] = failure
    if all(v == 'PASS' for v in result['checks'].values()) and failure is None:
        result['status'] = STATUS_PASS
        result['execution'] = 'HARD_TERMINATED_CONTROLLER_QEMU_EXIT_VERIFIED'
    elif result['cleanup'] != 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED':
        result['status'] = 'CLEANUP_FAILED'
    else:
        result['status'] = 'D5C_HOST_INTERRUPTION_NOT_VERIFIED'
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5c Gate 1: Windows kill-on-close job recovery')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--out', type=Path)
    p.add_argument('--run-clean-vm', action='store_true')
    p.add_argument('--acknowledge-controller-hard-kill', action='store_true')
    p.add_argument('--work-root', type=Path)
    p.add_argument('--_trusted-child', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--_child-workspace', type=Path, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args._trusted_child:
        if not args._child_workspace or not args._child_workspace.is_dir():
            return 2
        return _child_vm(args.vm_config, args._child_workspace)
    if args.out is None:
        p.error('--out is required')
    try:
        report = run(args.vm_config, execute=args.run_clean_vm,
                     acknowledge=args.acknowledge_controller_hard_kill,
                     work_root=args.work_root)
    except Exception as exc:
        report = _base_report()
        report['status'] = 'HOST_GATES_BLOCKED'
        report['failure_code'] = 'TOP_LEVEL_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5c Gate 1 - ' + report['status'])
    print('  Cleanup: ' + report['cleanup'])
    print('  Report: ' + str(args.out))
    return 0 if report['status'] in (STATUS_PASS, 'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
