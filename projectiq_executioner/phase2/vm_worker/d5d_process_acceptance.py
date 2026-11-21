"""D5d Gate 2: independently observe guest process and MySQL boundaries.

Runs only a trusted synthetic build in a clean disposable VM. This is a
*diagnostic*, not a production policy change or student-code attestation.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5d_process_guest_probe import (FIELDS, DIAG_KEYS, DIAG_LOCATIONS, DIAG_STATES)
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
WALL_SECONDS = 310
CORE = FIELDS[:-3]  # MySQL service resource ceilings are measured separately.
STATUS_OBSERVED = 'D5D_GATE2_SYNTHETIC_PROCESS_BOUNDARIES_OBSERVED_NOT_ATTESTED'
STATUS_MYSQL_PENDING = 'D5D_GATE2_MYSQL_RESOURCE_LIMITS_UNVERIFIED'
STATUS_INCOMPLETE = 'D5D_GATE2_KERNEL_EVIDENCE_INCOMPLETE'


def _base() -> dict:
    return {
        'schema_version': '0.1', 'stage': 'd5d_gate2_process_isolation_baseline',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'student_code_executed': False, 'security_attestation': ATTESTATION,
        'cleanup': 'NOT_STARTED',
        'checks': {name: 'NOT_VERIFIED' for name in FIELDS},
        'scope': 'TRUSTED_SYNTHETIC_BUILD_AND_CLEAN_VM_MYSQL_ONLY',
        'limitations': [
            'REAL_STUDENT_BUILD_DESCENDANTS_NOT_INDEPENDENTLY_VERIFIED',
            'MYSQL_RESOURCE_CEILINGS_NOT_APPLIED_BY_THIS_PROBE',
            'NO_UNTRUSTED_EXECUTION_APPROVAL',
        ],
    }


def _parse_guest(data: bytes) -> dict[str, bool] | None:
    """Typed observations, with no untrusted PID, pathname or free-form text."""
    if type(data) is not bytes or not 0 < len(data) < 1800:
        return None
    try:
        record = json.loads(data.decode('utf-8', 'strict'))
        if (type(record) is not dict or
                set(record) not in ({'schema', 'observations'},
                                    {'schema', 'observations', 'diagnostics'}) or
                record['schema'] != 'd5d-process-1'):
            return None
        observations = record['observations']
        if (type(observations) is not dict or set(observations) != set(FIELDS)
                or any(type(v) is not bool for v in observations.values())):
            return None
        diagnostic = record.get('diagnostics')
        if diagnostic is not None:
            if (type(diagnostic) is not dict or set(diagnostic) != set(DIAG_KEYS) or
                    any(type(diagnostic[key]) is not bool for key in DIAG_KEYS[:6]) or
                    type(diagnostic['child_cgroup_location']) is not str or
                    diagnostic['child_cgroup_location'] not in DIAG_LOCATIONS or
                    type(diagnostic['final_scope_state']) is not str or
                    diagnostic['final_scope_state'] not in DIAG_STATES):
                return None
            if observations['build_child_in_scope'] and not (
                    diagnostic['child_parent_verified'] and
                    diagnostic['child_uid_verified'] and
                    diagnostic['child_cgroup_member_verified'] and
                    diagnostic['child_cgroup_location'] in ('DIRECT','DESCENDANT')):
                return None
            if observations['build_scope_drained'] and diagnostic['final_scope_state'] not in (
                    'DRAINED', 'ABSENT'):
                return None
        if observations['build_child_in_scope'] and not observations['build_parent_in_scope']:
            return None
        if observations['build_scope_drained'] and not observations['build_child_in_scope']:
            return None
        if observations['mysql_separate_system_scope'] and not observations['mysql_daemon_identity']:
            return None
        if (any(observations[k] for k in (
                'build_parent_in_scope', 'build_child_in_scope', 'build_scope_drained')) and
                not observations['evaluation_slice_policy']):
            return None
        if (any(observations[k] for k in FIELDS[1:]) and
                not observations['controller_identity']):
            return None
        if (observations['evaluation_slice_policy'] and
                not observations['bootstrap_and_workspace']):
            return None
        return observations
    except (ValueError, TypeError, UnicodeError, KeyError):
        return None


def _classify(report: dict) -> None:
    checks = report['checks']
    if any(checks[k] != 'PASS' for k in CORE):
        report['status'] = STATUS_INCOMPLETE
    elif any(checks[k] != 'PASS' for k in FIELDS[-3:]):
        report['status'] = STATUS_MYSQL_PENDING
    else:
        report['status'] = STATUS_OBSERVED


def run(config: dict, *, run_clean_vm: bool = False,
        work_root: Path | None = None) -> dict:
    report = _base()
    host = verify_host_toolchain(config)
    report['host_tools_and_image'] = ('PASS' if host['status'] ==
        'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' else 'NOT_VERIFIED')
    if report['host_tools_and_image'] != 'PASS':
        report['status'] = 'HOST_GATES_BLOCKED'
        return report
    if not run_clean_vm:
        report['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return report

    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    if not parent.is_dir():
        report['status'] = 'INVALID_OPERATOR_WORK_ROOT'
        return report
    workspace = None
    worker = None
    started = None
    stopped = False
    watchdog = None
    expired = threading.Event()
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5d2_clean_', dir=parent) as temp:
            workspace = Path(temp)
            try:
                key, pubkey = make_ssh_key(config, workspace)
                seed = make_nocloud_iso(
                    workspace, pubkey, 'projectiq-d5d2-' + uuid.uuid4().hex,
                    mysql_bootstrap=True, workspace_disk_mib=WORKSPACE_MIB,
                    app_cgroup=True,
                )
                port = _free_loopback_port()
                worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                                    workspace, port, ram_mib=VM_RAM_MIB,
                                    cpus=effective_vm_vcpus(config, 2))
                expected_network = (
                    f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22')
                if worker.network_spec() != expected_network:
                    raise WorkerFailure('D5d requires network-restricted clean VM')
                worker.start(seed)
                started = worker.proc
                if started is None or started.poll() is not None:
                    raise WorkerFailure('Clean VM launch did not remain live')
                report['execution'] = 'TRUSTED_CLEAN_VM_BOOT_REQUESTED'

                def expire():
                    expired.set()
                    if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                        worker.proc.kill()

                watchdog = threading.Timer(WALL_SECONDS, expire)
                watchdog.daemon = True
                watchdog.start()
                ssh = SSHControl(config, workspace, port, key)
                ssh.wait_ssh(worker, seconds=105)
                source = Path(__file__).with_name('d5d_process_guest_probe.py').read_bytes()
                if not 0 < len(source) <= 35_000:
                    raise WorkerFailure('Trusted D5d guest source exceeds bound')
                response = ssh.ssh('python3', '-', input=source, timeout=195)
                observations = _parse_guest(response.stdout) if response.returncode == 0 else None
                if observations is None:
                    error = 'INVALID_OR_FAILED_D5D_GUEST_PROBE'
                else:
                    report['checks'] = {k: 'PASS' if observations[k] else 'NOT_VERIFIED'
                                        for k in FIELDS}
                    # The schema has already validated these fixed, non-sensitive
                    # diagnostic categories; never report raw PIDs/paths/argv.
                    raw = json.loads(response.stdout.decode('utf-8', 'strict'))
                    if 'diagnostics' in raw:
                        report['kernel_diagnostics'] = raw['diagnostics']
                    report['execution'] = 'TRUSTED_D5D_PROBE_IN_CLEAN_VM'
                    if started.poll() is not None:
                        error = 'VM_EXITED_DURING_PROBE'
            except Exception as exc:
                error = 'D5D_PROBE_' + type(exc).__name__.upper()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if worker is not None:
                    try:
                        worker.stop()
                        stopped = started is None or started.poll() is not None
                    except Exception:
                        stopped = False
    except OSError:
        error = 'D5D_TEMPORARY_STORAGE_CLEANUP_FAILED'

    report['cleanup'] = (CLEAN if not (workspace is not None and workspace.exists())
                         and (worker is None or stopped)
                         else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
    if expired.is_set():
        error = 'D5D_VM_WALL_TIMEOUT'
    if error:
        report['status'] = 'D5D_PROBE_ERROR'
        report['failure_code'] = error
    else:
        _classify(report)
    if report['cleanup'] != CLEAN:
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5d Gate 2 independent clean-VM process evidence')
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-clean-vm', action='store_true',
                        help='Explicitly boot ONE trusted clean disposable VM')
    parser.add_argument('--work-root', type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(load_config(args.vm_config), run_clean_vm=args.run_clean_vm,
                     work_root=args.work_root)
    except (OSError, ValueError, WorkerFailure) as exc:
        report = _base()
        report.update(status='HOST_GATES_BLOCKED', failure_code=type(exc).__name__)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('ProjectIQ D5d Gate 2 - ' + report['status'])
    print('  Report: ' + str(args.out))
    return 0 if report['status'] in (
        'INSPECTION_ONLY_NOT_ATTESTED', STATUS_OBSERVED, STATUS_MYSQL_PENDING,
    ) and report['cleanup'] != 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
