"""D5/B3b clean-VM systemd cgroup-v2 *capability and enforcement* pilot.

Does NOT enable cgroup enforcement for actual project adapters yet. If the
probe passes, it proves ONLY the bounded clean-VM probe slice. The next gate
must place every application launch into an aggregate, root-defined slice.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_cgroup_guest_probe import CHECKS
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
PROBE_MIB = 96
REMAINING = (
    'NORMAL_APPLICATION_LAUNCHES_NOT_YET_ATTACHED_TO_CGROUP_SLICE',
    'CGROUP_MEMORY_EXHAUSTION_NOT_YET_MEASURED',
    'CGROUP_IO_THROUGHPUT_NOT_YET_MEASURED',
    'ROOT_QCOW2_AND_OTHER_GUEST_WRITES_NOT_FULLY_CONFINED',
    'PER_RUN_SSH_HOST_KEY_IDENTITY_NOT_VERIFIED',
    'QEMU_HYPERVISOR_ESCAPE_RESISTANCE_UNTESTED',
    'CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_NOT_VERIFIED',
)


def _base_report() -> dict:
    return {'schema_version': '0.1', 'phase': 2,
            'stage': 'd5b3b_cgroup_pilot', 'status': 'NOT_EXECUTED',
            'execution': 'NOT_PERFORMED', 'student_code_executed': False,
            'security_attestation': ATTESTATION, 'cleanup': 'NOT_STARTED',
            'checks': {name: 'NOT_VERIFIED' for name in CHECKS},
            'remaining_security_gates': list(REMAINING)}


def _parse(stdout: bytes) -> tuple[dict, dict] | None:
    if not isinstance(stdout, bytes) or len(stdout) > 5000:
        return None
    try:
        d = json.loads(stdout.decode('utf-8'))
        if not isinstance(d, dict) or d.get('schema_version') != '0.1':
            return None
        checks = d.get('checks')
        if not isinstance(checks, dict) or set(checks) != set(CHECKS):
            return None
        if any(type(v) is not str or v not in ('PASS','FAIL','NOT_VERIFIED')
               for v in checks.values()):
            return None
        raw = d.get('diagnostics')
        if not isinstance(raw, dict):
            return None
        allowed = {
            'manager_error': str,
            'slice_start_error': str,
            'bootstrap_state': str,
            'system_user_service': str,
            'system_user_slice': str,
            'system_uid_slice': str,
            'user_probe_slice': str,
            'user_probe_io_policy': str,
            'manager_control_owner': str,
            'manager_direct_procs': int,
            'scope_exit_code': int,
            'scope_error': str,
            'app_cgroup_tail': str,
            'pids_events_delta': int,
            'threads_started': int,
            'pids_current_before': int,
            'pids_current_held': int,
            'scope_pids_current_held': int,
            'pids_events_held_delta': int,
            'scope_pids_events_delta': int,
            'thread_denied': bool,
            'thread_error': str,
            'scope_pids_max': str,
            'nproc_soft_hard': str,
            'child_error': str,
            'child_error_detail': str,
            'probe_error': str,
        }
        diagnostics = {}
        for key, ty in allowed.items():
            if type(raw.get(key)) is ty:
                diagnostics[key] = raw[key][-350:] if ty is str else raw[key]
        return checks, diagnostics
    except (TypeError, ValueError, KeyError, UnicodeError):
        return None


def run(config: dict, *, run_clean_vm: bool = False, work_root: Path | None = None) -> dict:
    result = _base_report()
    tools = verify_host_toolchain(config)
    result['host_tools_and_image'] = ('PASS' if tools['status'] ==
        'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' else 'FAIL')
    if result['host_tools_and_image'] != 'PASS':
        result['status'] = 'HOST_GATES_BLOCKED'
        return result
    if not run_clean_vm:
        result['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return result
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    worker = None
    proc = None
    stopped = False
    expired = threading.Event()
    watchdog = None
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b3b_', dir=parent) as temp:
            temp_path = Path(temp)
            try:
                key, pub = make_ssh_key(config, temp_path)
                seed = make_nocloud_iso(temp_path, pub, 'projectiq-d5b3b-'+uuid.uuid4().hex,
                                        workspace_disk_mib=PROBE_MIB, cgroup_probe=True)
                port = _free_loopback_port()
                worker = QemuWorker({**config,'workspace_disk_mib':PROBE_MIB}, temp_path, port,
                                    ram_mib=1536, cpus=effective_vm_vcpus(config, 2))
                expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
                if worker.network_spec() != expected:
                    raise WorkerFailure('Unexpected QEMU networking for clean VM')
                worker.start(seed)
                proc = worker.proc
                if proc is None or proc.poll() is not None:
                    raise WorkerFailure('Cgroup clean VM did not start')
                result['execution'] = 'CLEAN_VM_BOOT_REQUESTED'

                def deadline():
                    expired.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog = threading.Timer(230, deadline)
                watchdog.daemon = True
                watchdog.start()
                ssh = SSHControl(config, temp_path, port, key)
                ssh.wait_ssh(worker, seconds=95)
                source = Path(__file__).with_name('d5_cgroup_guest_probe.py').read_bytes()
                response = ssh.ssh('python3', '-', input=source, timeout=95)
                parsed = _parse(response.stdout) if response.returncode == 0 else None
                if parsed is None:
                    error = 'INVALID_OR_FAILED_CGROUP_GUEST_PROBE'
                    result['guest_probe_returncode'] = response.returncode
                    result['guest_probe_stderr_tail'] = response.stderr.decode('utf-8','replace')[-350:]
                else:
                    result['checks'], result['probe_diagnostics'] = parsed
                    result['execution'] = 'TRUSTED_CGROUP_PROBE_EXECUTED_IN_CLEAN_VM'
                    if proc.poll() is not None:
                        error = 'VM_EXITED_EARLY'
            except Exception as exc:
                error = 'CGROUP_PROBE_' + type(exc).__name__.upper()
                result['error_detail'] = str(exc)[:200] if isinstance(exc, WorkerFailure) else None
            finally:
                if watchdog:
                    watchdog.cancel()
                if worker:
                    try:
                        worker.stop()
                        stopped = proc is None or proc.poll() is not None
                    except Exception:
                        stopped = False
    except OSError:
        error = 'CGROUP_TEMPORARY_STORAGE_CLEANUP_FAILED'
    result['cleanup'] = ('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                         if (temp_path is not None and temp_path.exists()) or
                         (worker is not None and not stopped)
                         else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if expired.is_set():
        error = 'CGROUP_VM_WALL_TIMEOUT'
    if error:
        result['error_code'] = error
        result['status'] = 'CGROUP_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(v == 'FAIL' for v in result['checks'].values()):
        result['status'] = 'CGROUP_KERNEL_OR_PRIVILEGE_CHECK_FAILED'
    elif any(v == 'NOT_VERIFIED' for v in result['checks'].values()):
        result['status'] = 'CGROUP_ENFORCEMENT_NOT_VERIFIED'
    else:
        result['status'] = 'CGROUP_PROBE_OBSERVED_NOT_ATTESTED'
    if result['cleanup'] == 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        result['status'] = 'CLEANUP_FAILED'
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5/B3b clean-VM cgroup enforcement pilot')
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-clean-vm', action='store_true')
    parser.add_argument('--work-root', type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(load_config(args.vm_config), run_clean_vm=args.run_clean_vm,
                     work_root=args.work_root)
    except Exception as exc:
        result = _base_report()
        result['status'] = 'HOST_GATES_BLOCKED'
        result['error_code'] = 'INSPECTION_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5/B3b — ' + result['status'])
    print('  Attestation: ' + result['security_attestation'])
    print('  Report: ' + str(args.out))
    return (0 if result['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
                                      'CGROUP_PROBE_OBSERVED_NOT_ATTESTED') else 1)


if __name__ == '__main__':
    raise SystemExit(main())
