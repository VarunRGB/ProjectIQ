"""D5a: adapter-independent, CLEAN-VM operator acceptance observations.

Never sends or executes a student repository. Never enables untrusted runs.
A probe is NOT proof of hypervisor isolation and never sets attestation true.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_guest_probe import __file__ as _GUEST_SCRIPT
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key, verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
GUEST_CHECKS = ('non_root_user', 'root_sudo_denied', 'no_host_shared_mounts_observed',
                'external_ipv4_tcp_attempt', 'external_ipv6_tcp_attempt',
                'external_dns_attempt', 'app_uid_distinct_from_controller',
                'controller_runas_app', 'app_root_sudo_denied')
GUEST_VALUES = frozenset(('PASS', 'FAIL', 'NOT_VERIFIED'))
# Distinct from passed VM observations. These require additional engineering,
# independent adversarial testing and a dedicated worker host.
REMAINING_GATES = (
    'QEMU_HYPERVISOR_ESCAPE_RESISTANCE_UNTESTED',
    'DYNAMIC_DISK_WRITE_QUOTA_NOT_ENFORCED',
    'GUEST_PROCESS_AND_IO_QUOTAS_NOT_ENFORCED',
    'PER_RUN_SSH_HOST_KEY_IDENTITY_NOT_VERIFIED',
    'RUNAS_BOUNDARY_REQUIRES_INDEPENDENT_ADVERSARIAL_VALIDATION',
    'CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_NOT_VERIFIED',
    'MULTI_TENANT_HOST_ISOLATION_AND_SCHEDULING_NOT_IMPLEMENTED',
    'GUEST_EGRESS_PROBES_ARE_OBSERVATIONAL_NOT_COMPREHENSIVE',
)


def _report_path(out: Path, report: dict) -> dict:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def _init_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2, 'stage': 'd5a_clean_vm_acceptance',
        'status': 'INSPECTION_ONLY_NOT_ATTESTED',
        'security_attestation': ATTESTATION,
        'student_code_executed': False,
        'guest_network_mode': 'RUNTIME_EGRESS_DENIED_BY_CONFIG',
        'checks': {}, 'remaining_security_gates': list(REMAINING_GATES),
        'cleanup': 'NOT_STARTED',
    }


def _policy_check(port: int) -> str:
    worker = QemuWorker({}, Path('.'), port, ram_mib=1024, cpus=1)
    return ('PASS' if worker.network_spec() ==
            f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
            else 'FAIL')


def _parse_guest(stdout: bytes) -> tuple[dict, str]:
    if len(stdout) > 8_192:
        return {}, 'OVERSIZED_GUEST_REPORT'
    try:
        payload = json.loads(stdout.decode('utf-8'))
        values = payload['checks']
        if payload.get('schema_version') != '0.1' or not isinstance(values, dict):
            raise ValueError('schema mismatch')
        if set(values) != set(GUEST_CHECKS) | {'mysql_sudo_exception'}:
            raise ValueError('missing checks')
        if any(values[k] not in GUEST_VALUES for k in GUEST_CHECKS):
            raise ValueError('unexpected check value')
        if values['mysql_sudo_exception'] not in ('PRESENT', 'NOT_OBSERVED', 'NOT_VERIFIED'):
            raise ValueError('unexpected sudo observation')
        return values, ''
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError):
        return {}, 'INVALID_GUEST_REPORT'


def _live_probe(config: dict, work_root: Path | None, report: dict) -> None:
    """Boot sealed image without ANY project snapshot or dependencies.

    All guest commands are trusted controller code sent via SSH stdin.
    Every path, error and stderr field remains private to the operator.
    """
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    worker = None
    launched_process = None
    watchdog = None
    watchdog_killed = threading.Event()
    error_code = None
    worker_stopped = False
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5_clean_', dir=parent) as tmp:
            temp_path = Path(tmp)
            try:
                key, pubkey = make_ssh_key(config, temp_path)
                seed = make_nocloud_iso(temp_path, pubkey, 'projectiq-d5-' + uuid.uuid4().hex)
                port = _free_loopback_port()
                worker = QemuWorker(config, temp_path, port, ram_mib=1024,
                                    cpus=effective_vm_vcpus(config, 2))
                if worker.network_spec() != (
                        f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'):
                    error_code = 'RUNTIME_NETWORK_CONFIG_UNSAFE'
                else:
                    worker.start(seed)
                    launched_process = worker.proc
                    if launched_process is None or launched_process.poll() is not None:
                        raise WorkerFailure('Clean VM process not running after launch')
                    report['execution'] = 'CLEAN_VM_BOOT_REQUESTED'

                    def expire():
                        watchdog_killed.set()
                        if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                            worker.proc.kill()

                    watchdog = threading.Timer(160, expire)
                    watchdog.daemon = True
                    watchdog.start()
                    ssh = SSHControl(config, temp_path, port, key)
                    ssh.wait_ssh(worker, seconds=90)
                    # No file transfer, package manager, GitHub data or student code.
                    trusted_script = Path(_GUEST_SCRIPT).read_bytes()
                    response = ssh.ssh('python3', '-', input=trusted_script, timeout=32)
                    if response.returncode != 0:
                        error_code = 'TRUSTED_GUEST_PROBE_FAILED'
                    else:
                        checks, invalid = _parse_guest(response.stdout)
                        if invalid:
                            error_code = invalid
                        else:
                            report['checks'].update(checks)
                            report['execution'] = 'TRUSTED_PROBE_EXECUTED_IN_CLEAN_VM'
                            if launched_process.poll() is not None:
                                error_code = 'CLEAN_VM_EXITED_BEFORE_STOP'
            except (Exception) as exc:
                # Never include raw SSH stderr or guest-reported free-form text.
                error_code = 'VM_PROBE_' + type(exc).__name__.upper()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if worker is not None:
                    try:
                        worker.stop()
                        worker_stopped = (launched_process is None or
                                          launched_process.poll() is not None)
                    except Exception:
                        worker_stopped = False
    except OSError:
        error_code = 'TEMP_DIR_CLEANUP_FAILED'
    if temp_path is not None and temp_path.exists():
        report['cleanup'] = 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
    elif worker is not None and not worker_stopped:
        report['cleanup'] = 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
    else:
        report['cleanup'] = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
    if watchdog_killed.is_set():
        error_code = 'CLEAN_VM_WALL_TIMEOUT'
    if error_code:
        report['error_code'] = error_code
        report['status'] = 'ACCEPTANCE_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(report['checks'].get(key) == 'FAIL' for key in GUEST_CHECKS):
        report['status'] = 'SECURITY_OBSERVATION_FAILED'
    elif report['checks'].get('mysql_sudo_exception') == 'PRESENT':
        report['status'] = 'RESTRICTED_SUDO_EXCEPTION_REQUIRES_REVIEW'
    elif any(report['checks'].get(key) == 'NOT_VERIFIED' for key in GUEST_CHECKS) or (
            report['checks'].get('mysql_sudo_exception') == 'NOT_VERIFIED'):
        report['status'] = 'OBSERVATIONS_PARTIAL_NOT_ATTESTED'
    else:
        report['status'] = 'CLEAN_VM_OBSERVATIONS_PASSED_NOT_ATTESTED'
    if report['cleanup'] == 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        report['status'] = 'CLEANUP_FAILED'


def inspect_and_probe(config: dict, *, run_clean_vm: bool = False,
                      work_root: Path | None = None) -> dict:
    """Runs no student code. Operator explicitly opts into a 1-GiB VM boot."""
    report = _init_report()
    tools = verify_host_toolchain(config)
    report['checks'] = {
        'host_tools': 'PASS' if all(tools['toolchain'].values()) else 'FAIL',
        'immutable_vm_template_sha256': 'PASS' if tools['template_sha256_matches'] else 'FAIL',
        'firmware_sha256': 'FAIL' if tools['uefi_firmware_sha256_matches'] is False else
                           'PASS' if tools['uefi_firmware_sha256_matches'] is True else 'NOT_APPLICABLE',
        'runtime_qemu_egress_restrict_and_loopback_ssh': _policy_check(41027),
        'd5_probe_ram_limit_requested': 'PASS',
        'd5_probe_vcpu_limit_requested': 'PASS',
        'dynamic_disk_write_quota': 'NOT_VERIFIED',
        'guest_process_quota': 'NOT_VERIFIED',
        'ssh_host_key_identity': 'NOT_VERIFIED',
    }
    if (tools['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' or
            report['checks']['runtime_qemu_egress_restrict_and_loopback_ssh'] != 'PASS'):
        report['status'] = 'HOST_GATES_BLOCKED'
    elif run_clean_vm:
        _live_probe(config, work_root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5a clean VM security observations, NOT attestation')
    parser.add_argument('--vm-config', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--run-clean-vm', action='store_true',
                        help='Explicitly boot a clean, offline 1-GiB VM; no student code')
    parser.add_argument('--work-root', type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.vm_config)
        report = inspect_and_probe(config, run_clean_vm=args.run_clean_vm,
                                   work_root=args.work_root)
    except (Exception) as exc:
        # The config or host may be invalid. Still produce a bounded report.
        report = _init_report()
        report['status'] = 'HOST_GATES_BLOCKED'
        report['error_code'] = 'INSPECTION_' + type(exc).__name__.upper()
    _report_path(args.out, report)
    print('ProjectIQ D5a — ' + report['status'])
    print('  Attestation: ' + report['security_attestation'])
    print('  Report: ' + str(args.out))
    return 0 if report['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
                                     'CLEAN_VM_OBSERVATIONS_PASSED_NOT_ATTESTED',
                                     'OBSERVATIONS_PARTIAL_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
