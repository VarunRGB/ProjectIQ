"""D5b.2 independent, clean-VM workspace quota acceptance; never attestation."""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key, verify_host_toolchain)

PROBE_MIB = 96
CHECKS = ('workspace_mount_bounded', 'app_write_over_quota_denied', 'app_write_after_denial')


def _bounded_boot_tail(path: Path, *, limit: int = 2400) -> str:
    """Keep only bounded clean-VM diagnostics; never persist guest disks/keys."""
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - limit))
            raw = source.read(limit)
    except OSError:
        return ''
    # Drop non-printable bytes rather than dumping arbitrary binary into JSON.
    return ''.join(c for c in raw.decode('utf-8', errors='replace')
                   if c in '\n\r\t' or c.isprintable())[-limit:]
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
REMAINING = [
    'ROOT_QCOW2_OVERLAY_GROWTH_NOT_QUOTA_ENFORCED',
    'NON_WORKSPACE_GUEST_WRITABLE_PATHS_NOT_CONFINED',
    'GUEST_PROCESS_MEMORY_IO_QUOTAS_PENDING_D5B3',
    'PER_RUN_SSH_HOST_KEY_IDENTITY_NOT_VERIFIED',
    'QEMU_HYPERVISOR_ESCAPE_RESISTANCE_UNTESTED',
    'CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_NOT_VERIFIED',
]


def _base_report() -> dict:
    return {'schema_version': '0.1', 'phase': 2, 'stage': 'd5b2_workspace_disk_acceptance',
            'status': 'NOT_EXECUTED', 'security_attestation': ATTESTATION,
            'student_code_executed': False, 'checks': {k: 'NOT_VERIFIED' for k in CHECKS},
            'workspace_disk_mib': PROBE_MIB, 'cleanup': 'NOT_STARTED',
            'remaining_security_gates': list(REMAINING)}


def _parse(stdout: bytes) -> dict | None:
    if len(stdout) > 2048:
        return None
    try:
        payload = json.loads(stdout.decode('utf-8'))
        checks = payload['checks']
        if (payload.get('schema_version') != '0.1'
                or payload.get('workspace_mib') != PROBE_MIB
                or not isinstance(checks, dict) or set(checks) != set(CHECKS)
                or any(checks[k] not in ('PASS', 'FAIL', 'NOT_VERIFIED') for k in CHECKS)):
            return None
        return checks
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        return None


def run(config: dict, *, run_clean_vm: bool = False, work_root: Path | None = None) -> dict:
    report = _base_report()
    tools = verify_host_toolchain(config)
    report['host_tools_and_image'] = ('PASS' if tools['status'] ==
                                   'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' else 'FAIL')
    if report['host_tools_and_image'] != 'PASS':
        report['status'] = 'HOST_GATES_BLOCKED'
        return report
    if not run_clean_vm:
        report['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return report
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    worker = None
    launched_process = None
    stopped = False
    expired = threading.Event()
    watchdog = None
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b2_', dir=parent) as temp:
            tmp_path = Path(temp)
            try:
                key, pubkey = make_ssh_key(config, tmp_path)
                seed = make_nocloud_iso(tmp_path, pubkey, 'projectiq-d5b2-' + uuid.uuid4().hex,
                                        workspace_disk_mib=PROBE_MIB)
                port = _free_loopback_port()
                worker = QemuWorker({**config, 'workspace_disk_mib': PROBE_MIB}, tmp_path,
                                    port, ram_mib=1024, cpus=effective_vm_vcpus(config, 2),
                                    diagnostic_serial=True)
                expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
                if worker.network_spec() != expected:
                    raise WorkerFailure('Unsafe clean VM network policy')
                worker.start(seed)
                launched_process = worker.proc
                if launched_process is None or launched_process.poll() is not None:
                    raise WorkerFailure('Clean VM exited before probe')
                report['execution'] = 'CLEAN_VM_BOOT_REQUESTED'
                def expire():
                    expired.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog = threading.Timer(205, expire)
                watchdog.daemon = True
                watchdog.start()
                ssh = SSHControl(config, tmp_path, port, key)
                ssh.wait_ssh(worker, seconds=90)
                trusted_script = Path(__file__).with_name('d5_disk_guest_probe.py').read_bytes()
                response = ssh.ssh('python3', '-', str(PROBE_MIB), input=trusted_script,
                                   timeout=135)
                checks = _parse(response.stdout) if response.returncode == 0 else None
                if checks is None:
                    error = 'INVALID_OR_FAILED_GUEST_DISK_PROBE'
                else:
                    # Diagnostics are optional for older guest probes. Allowlist
                    # only small, typed fields from the trusted clean-VM probe;
                    # never put project stderr or arbitrary guest output in reports.
                    try:
                        raw_diagnostics = json.loads(response.stdout).get('probe_diagnostics')
                        if isinstance(raw_diagnostics, dict):
                            allowed = {'app_probe_state': str, 'app_probe_exit_code': int,
                                       'app_probe_stdout_tail': str,
                                       'app_probe_stderr_tail': str,
                                       'app_probe_oserror_type': str}
                            bounded = {}
                            for k, ty in allowed.items():
                                val = raw_diagnostics.get(k)
                                if type(val) is ty:
                                    bounded[k] = val[-500:] if ty is str else val
                            if bounded:
                                report['probe_diagnostics'] = bounded
                    except (TypeError, ValueError):
                        pass
                    report['checks'] = checks
                    report['execution'] = 'TRUSTED_DISK_PROBE_EXECUTED_IN_CLEAN_VM'
                    if launched_process.poll() is not None:
                        error = 'CLEAN_VM_EXITED_BEFORE_STOP'
            except Exception as exc:
                error = 'DISK_PROBE_' + type(exc).__name__.upper()
                report['error_detail'] = str(exc)[:240]
                if worker is not None and worker.proc is not None:
                    report['vm_alive_at_error'] = worker.proc.poll() is None
                # A single extra local-only attempt distinguishes connection
                # refusal from authentication/cloud-init failures. Do not extend
                # the acceptance window or change the underlying security policy.
                if isinstance(exc, WorkerFailure) and 'SSH readiness timed out' in str(exc):
                    try:
                        diag_ssh = ssh.ssh('true', timeout=6)
                        report['ssh_last_returncode'] = diag_ssh.returncode
                        report['ssh_last_error'] = diag_ssh.stderr.decode(
                            'utf-8', errors='replace')[-500:]
                    except Exception as diag_exc:
                        report['ssh_diagnostic_error'] = type(diag_exc).__name__
            finally:
                if watchdog:
                    watchdog.cancel()
                if worker:
                    try:
                        worker.stop()
                        stopped = launched_process is None or launched_process.poll() is not None
                    except Exception:
                        stopped = False
                if error:
                    # Capture evidence while the temporary directory still
                    # exists, AFTER QEMU output is flushed/closed by stop().
                    for field, name in (('qemu_log_tail', 'qemu.log'),
                                        ('guest_serial_tail', 'guest-serial.log')):
                        tail = _bounded_boot_tail(tmp_path / name)
                        if tail:
                            report[field] = tail
    except OSError:
        error = 'TEMP_DIR_CLEANUP_FAILED'
    report['cleanup'] = ('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                         if (tmp_path is not None and tmp_path.exists()) or (worker and not stopped)
                         else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if expired.is_set():
        error = 'DISK_VM_WALL_TIMEOUT'
    if error:
        report['error_code'] = error
        report['status'] = 'DISK_QUOTA_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(v == 'FAIL' for v in report['checks'].values()):
        report['status'] = 'WORKSPACE_DISK_QUOTA_FAILED'
    elif any(v == 'NOT_VERIFIED' for v in report['checks'].values()):
        report['status'] = 'WORKSPACE_DISK_QUOTA_NOT_VERIFIED'
    else:
        report['status'] = 'WORKSPACE_QUOTA_OBSERVED_NOT_ATTESTED'
    if report['cleanup'] == 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5b.2 bounded workspace disk acceptance')
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
        report['error_code'] = 'DISK_INSPECTION_' + type(exc).__name__.upper()
    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', 'utf-8')
    print('ProjectIQ D5b.2 — ' + report['status'])
    print('  Attestation: ' + report['security_attestation'])
    print('  Report: ' + str(opts.out))
    return 0 if report['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
                                      'WORKSPACE_QUOTA_OBSERVED_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
