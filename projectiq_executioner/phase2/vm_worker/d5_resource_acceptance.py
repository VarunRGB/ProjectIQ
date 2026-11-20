"""D5b.3a clean-VM resource ceiling observations; not sandbox attestation.

Verifies inherited Linux real-UID RLIMITs. It does NOT claim cgroup-wide
memory, I/O or PID ceilings. No student source is transferred.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .guest_agent import APP_LIMIT_LAUNCHER
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

CHECKS = ('app_identity_nonroot', 'app_nproc_hard_cap',
          'app_nofile_hard_cap', 'app_core_dumps_disabled',
          'app_cannot_raise_hard_caps')
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
REMAINING = (
    'CGROUP_V2_MEMORY_MAX_NOT_ENFORCED_OR_VERIFIED',
    'CGROUP_V2_PIDS_MAX_NOT_ENFORCED_OR_VERIFIED',
    'CGROUP_V2_IO_MAX_NOT_ENFORCED_OR_VERIFIED',
    'RLIMIT_NPROC_PER_UID_NOT_PROCESS_TREE_QUOTA',
    'ROOT_QCOW2_AND_NON_WORKSPACE_WRITES_UNBOUNDED',
    'PER_RUN_SSH_HOST_KEY_IDENTITY_NOT_VERIFIED',
    'QEMU_HYPERVISOR_ESCAPE_RESISTANCE_UNTESTED',
    'CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_NOT_VERIFIED',
)


def _base_report() -> dict:
    return {'schema_version': '0.1', 'phase': 2, 'stage': 'd5b3a_resource_limits',
            'status': 'NOT_EXECUTED', 'security_attestation': ATTESTATION,
            'student_code_executed': False, 'checks': {k: 'NOT_VERIFIED' for k in CHECKS},
            'remaining_security_gates': list(REMAINING), 'cleanup': 'NOT_STARTED'}


def _parse(raw: bytes) -> tuple[dict, dict] | None:
    if not isinstance(raw, bytes) or len(raw) > 4096:
        return None
    try:
        d = json.loads(raw.decode('utf-8'))
        if (d.get('schema_version') != '0.1' or not isinstance(d.get('checks'), dict)
                or set(d['checks']) != set(CHECKS)
                or any(type(v) is not str or v not in ('PASS', 'FAIL', 'NOT_VERIFIED')
                       for v in d['checks'].values())):
            return None
        diag = d.get('diagnostics')
        if not isinstance(diag, dict):
            return None
        names = ('cgroup_v2_available', 'memory_controller_available',
                 'pids_controller_available', 'io_controller_available')
        if any(diag.get(name) not in ('YES', 'NO', 'NOT_VERIFIED') for name in names):
            return None
        safe = {name: diag[name] for name in names}
        if type(diag.get('app_probe_exit_code')) is int:
            safe['app_probe_exit_code'] = diag['app_probe_exit_code']
        return d['checks'], safe
    except (ValueError, KeyError, TypeError, UnicodeError, AttributeError):
        return None


def run(config: dict, *, run_clean_vm: bool = False, work_root: Path | None = None) -> dict:
    report = _base_report()
    tools = verify_host_toolchain(config)
    report['host_tools_and_image'] = ('PASS' if tools['status'] ==
                                    'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'
                                     else 'FAIL')
    if report['host_tools_and_image'] != 'PASS':
        report['status'] = 'HOST_GATES_BLOCKED'
        return report
    if not run_clean_vm:
        report['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return report
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    worker = None
    proc = None
    stopped = False
    timeout_event = threading.Event()
    watchdog = None
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b3a_', dir=parent) as temp:
            temp_path = Path(temp)
            try:
                key, pub = make_ssh_key(config, temp_path)
                seed = make_nocloud_iso(temp_path, pub, 'projectiq-d5b3a-' + uuid.uuid4().hex)
                port = _free_loopback_port()
                # No added workspace disk: observe kernel limits separately
                # from the previously proven D5b.2 filesystem quota.
                worker = QemuWorker({**config, 'workspace_disk_mib': 0}, temp_path,
                                    port, ram_mib=1024,
                                    cpus=effective_vm_vcpus(config, 2))
                if worker.network_spec() != (
                    f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'):
                    raise WorkerFailure('Unsafe runtime network policy')
                worker.start(seed)
                proc = worker.proc
                if proc is None or proc.poll() is not None:
                    raise WorkerFailure('Clean VM exited before resource probe')
                report['execution'] = 'CLEAN_VM_BOOT_REQUESTED'

                def expire():
                    timeout_event.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog = threading.Timer(165, expire)
                watchdog.daemon = True
                watchdog.start()
                ssh = SSHControl(config, temp_path, port, key)
                ssh.wait_ssh(worker, seconds=90)
                source = Path(__file__).with_name('d5_resource_guest_probe.py').read_text('utf-8')
                marker = "'__PROJECTIQ_LAUNCHER_EXPR__'"
                if source.count(marker) != 1:
                    raise WorkerFailure('Resource probe source contract mismatch')
                trusted_code = source.replace(marker, repr(APP_LIMIT_LAUNCHER)).encode('utf-8')
                response = ssh.ssh('python3', '-', input=trusted_code, timeout=40)
                parsed = _parse(response.stdout) if response.returncode == 0 else None
                if parsed is None:
                    error = 'INVALID_OR_FAILED_GUEST_RESOURCE_PROBE'
                else:
                    report['checks'], report['cgroup_capabilities'] = parsed
                    report['execution'] = 'TRUSTED_RESOURCE_PROBE_EXECUTED_IN_CLEAN_VM'
                    if proc.poll() is not None:
                        error = 'CLEAN_VM_EXITED_BEFORE_STOP'
            except Exception as exc:
                error = 'RESOURCE_PROBE_' + type(exc).__name__.upper()
                report['error_detail'] = str(exc)[:200] if isinstance(exc, WorkerFailure) else None
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
        error = 'RESOURCE_TEMP_CLEANUP_FAILED'
    report['cleanup'] = ('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                         if (temp_path is not None and temp_path.exists()) or
                         (worker is not None and not stopped)
                         else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if timeout_event.is_set():
        error = 'RESOURCE_VM_WALL_TIMEOUT'
    if error:
        report['error_code'] = error
        report['status'] = 'RESOURCE_PROBE_INCONCLUSIVE_OR_FAILED'
    elif 'FAIL' in report['checks'].values():
        report['status'] = 'RESOURCE_CEILINGS_FAILED'
    elif 'NOT_VERIFIED' in report['checks'].values():
        report['status'] = 'RESOURCE_CEILINGS_NOT_VERIFIED'
    else:
        report['status'] = 'PER_UID_RESOURCE_CEILINGS_OBSERVED_NOT_ATTESTED'
    if report['cleanup'] == 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5b.3a clean VM process resource acceptance')
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-clean-vm', action='store_true')
    parser.add_argument('--work-root', type=Path)
    opts = parser.parse_args(argv)
    try:
        report = run(load_config(opts.vm_config), run_clean_vm=opts.run_clean_vm,
                     work_root=opts.work_root)
    except Exception as exc:
        report = _base_report()
        report['status'] = 'HOST_GATES_BLOCKED'
        report['error_code'] = 'RESOURCE_INSPECTION_' + type(exc).__name__.upper()
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', 'utf-8')
    print('ProjectIQ D5b.3a — ' + report['status'])
    print('  Attestation: ' + report['security_attestation'])
    print('  Report: ' + str(opts.out))
    return 0 if report['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
              'PER_UID_RESOURCE_CEILINGS_OBSERVED_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
