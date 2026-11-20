"""Phase 2 / 2D / D5b / B3b / Gate 2B.1: 2GiB cgroup memory OOM acceptance.

Runs ONLY trusted fixed probes in an explicitly opted-in disposable VM.
Does not authorize unreviewed student repositories or attest production safety.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_memory_guest_probe import CHECKS, MEMORY_LIMIT
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
WORKSPACE_MIB = 96
VM_RAM_MIB = 3584  # Greater than 2GiB cgroup ceiling + a separate guest OS reserve.
WALL_SECONDS = 300
REMAINING = (
    'COMPLETE_BUILD_DESCENDANT_CONFINEMENT_NOT_VERIFIED',
    'MYSQL_SERVICE_RESOURCES_NOT_IN_EVALUATION_SLICE',
    'PIDS_LIMIT_STRESS_GATE_2B2_PENDING',
    'DEVICE_IO_THROTTLE_GATE_2B3_PENDING',
    'GUEST_ROOT_QCOW2_AND_OTHER_GUEST_WRITES_NOT_CONFINED',
    'INDEPENDENT_HOST_AND_HYPERVISOR_ISOLATION_ATTESTATION_PENDING',
    'CRASH_AND_ORPHAN_RECOVERY_PENDING',
)


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2,
        'stage': 'd5b_b3b_gate2b1_eval_memory',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'student_code_executed': False,
        'security_attestation': ATTESTATION,
        'cleanup': 'NOT_STARTED',
        'guest_ram_mib': VM_RAM_MIB,
        'expected_slice_memory_max_bytes': MEMORY_LIMIT,
        'checks': {x: 'NOT_VERIFIED' for x in CHECKS},
        'remaining_security_gates': list(REMAINING),
    }


def _parse_guest(stdout: bytes) -> tuple[dict, dict] | None:
    """Never accept an untyped result, forged PASS or oversized guest report."""
    if type(stdout) is not bytes or len(stdout) > 4200:
        return None
    try:
        record = json.loads(stdout.decode('utf-8', 'strict'))
        if (type(record) is not dict or set(record) != {'schema_version', 'checks', 'diagnostics'}
                or record['schema_version'] != '0.1'):
            return None
        checks = record['checks']
        if (type(checks) is not dict or set(checks) != set(CHECKS)
                or any(type(v) is not str or v not in {'PASS', 'FAIL', 'NOT_VERIFIED'}
                       for v in checks.values())):
            return None
        diag = record['diagnostics']
        if type(diag) is not dict or len(diag) > 20:
            return None
        int_names = {'guest_mem_total_kib', 'guest_mem_available_before_kib',
                     'guest_mem_available_after_kib', 'verified_worker_count',
                     'memory_max_events_delta', 'memory_oom_events_delta',
                     'memory_oom_kill_delta', 'memory_peak_bytes',
                     'controller_home_mode_before', 'controller_home_mode_after',
                     'cooperative_stop_markers_published'}
        bool_names = {'scope_cleanup_observed', 'release_markers_visible_to_app'}
        str_names = {'probe_error', 'scope_cleanup_failure'}
        enum_names = {'probe_stage': {'STARTING_WORKERS', 'WORKER_LAUNCH_FAILED',
                                     'SCOPES_VERIFIED', 'RELEASE_VISIBILITY_CHECK',
                                     'RELEASE_VISIBILITY_FAILED', 'MEMORY_STRESS_RUNNING',
                                     'KERNEL_COUNTERS_COLLECTED'},
                      'stress_monitor_outcome': {'KERNEL_OOM_EVENT',
                                                 'ALL_WRAPPERS_EXITED', 'MONITOR_TIMEOUT'},
                      'launch_failure_reason': {'SCOPE_NOT_OBSERVED', 'APP_UID_NOT_OBSERVED',
                                                'CGROUP_MEMBERSHIP_NOT_OBSERVED',
                                                'EXACT_COMMAND_NOT_OBSERVED',
                                                'LAUNCH_VERIFICATION_TIMEOUT',
                                                'WRAPPER_EXITED', 'WRAPPER_SIGNALLED',
                                                *('WORKER_EXIT_' + str(i) for i in
                                                  (111, 112, 113, 114, 115, 116))}}
        if not set(diag) <= int_names | bool_names | str_names | set(enum_names) | {
                'worker_launch_observations', 'wrapper_exit_codes_before_cleanup',
                'scope_cleanup_observations'}:
            return None
        if any(type(v) is not int or not 0 <= v <= 2**45
               for k, v in diag.items() if k in int_names):
            return None
        if any(type(v) is not bool for k, v in diag.items() if k in bool_names):
            return None
        if any(type(v) is not str or len(v) > 90 or not v.isidentifier()
               for k, v in diag.items() if k in str_names):
            return None
        if any(type(diag[k]) is not str or diag[k] not in choices
               for k, choices in enum_names.items() if k in diag):
            return None
        codes = diag.get('wrapper_exit_codes_before_cleanup')
        if codes is not None and (type(codes) is not list or len(codes) != 2 or
                                  any(type(c) is not int or not -64 <= c <= 255
                                      for c in codes)):
            return None
        cleanup = diag.get('scope_cleanup_observations')
        if cleanup is not None:
            if type(cleanup) is not list or len(cleanup) > 2:
                return None
            for index, row in enumerate(cleanup, start=1):
                if type(row) is not dict or set(row) != {
                        'worker', 'kill_returncode', 'stop_returncode',
                        'scope_state', 'remaining_tasks', 'wrapper_exit_code_after',
                        'stop_marker_published', 'kill_failure_reason'}:
                    return None
                if type(row['worker']) is not int or row['worker'] != index:
                    return None
                if (type(row['remaining_tasks']) is not int or
                        not 0 <= row['remaining_tasks'] <= 30 or
                        type(row['scope_state']) is not str or
                        row['scope_state'] not in {'ABSENT', 'DRAINED', 'POPULATED', 'UNKNOWN'}):
                    return None
                if any(type(row[key]) is not int or not -64 <= row[key] <= 255
                       for key in ('kill_returncode', 'stop_returncode',
                                   'wrapper_exit_code_after')):
                    return None
                if (type(row['stop_marker_published']) is not bool or
                        row['kill_failure_reason'] not in {
                            'NOT_ATTEMPTED', 'NONE', 'UNSUPPORTED_OPTION',
                            'UNIT_NOT_FOUND', 'PERMISSION_DENIED',
                            'NO_SUCH_PROCESS', 'OTHER_ERROR', 'COMMAND_TIMEOUT',
                            'COMMAND_UNAVAILABLE'}):
                    return None
                if (row['kill_returncode'] == -2 and
                        row['kill_failure_reason'] != 'NOT_ATTEMPTED'):
                    return None
            if (checks['worker_scopes_cleaned'] == 'PASS' and
                    any(row['scope_state'] not in {'ABSENT', 'DRAINED'} or
                        row['wrapper_exit_code_after'] == -1
                        for row in cleanup)):
                return None
        if (checks['two_unprivileged_scoped_workers'] == 'PASS' and
                checks['worker_scopes_cleaned'] == 'PASS' and
                (cleanup is None or len(cleanup) != 2)):
            return None
        if (checks['worker_scopes_cleaned'] == 'PASS' and
                (cleanup is None or
                 diag.get('cooperative_stop_markers_published') != len(cleanup) or
                 not all(row['stop_marker_published'] for row in cleanup))):
            return None
        if (diag.get('release_markers_visible_to_app') is True and
                diag.get('controller_home_mode_after') != 0o711):
            return None
        if (diag.get('stress_monitor_outcome') is not None and
                diag.get('release_markers_visible_to_app') is not True):
            return None
        observations = diag.get('worker_launch_observations')
        if observations is not None:
            if type(observations) is not list or len(observations) > 2:
                return None
            for index, row in enumerate(observations, start=1):
                if type(row) is not dict or set(row) != {
                        'worker', 'scope_seen', 'max_cgroup_procs', 'app_uid_seen',
                        'cgroup_seen', 'argv_seen', 'wrapper_exit_code', 'verified'}:
                    return None
                if type(row['worker']) is not int or row['worker'] != index:
                    return None
                if (type(row['max_cgroup_procs']) is not int or
                        not 0 <= row['max_cgroup_procs'] <= 30 or
                        type(row['wrapper_exit_code']) is not int or
                        not -64 <= row['wrapper_exit_code'] <= 255):
                    return None
                if any(type(row[k]) is not bool for k in
                       ('scope_seen', 'app_uid_seen', 'cgroup_seen', 'argv_seen', 'verified')):
                    return None
                if row['verified'] and not (row['scope_seen'] and row['app_uid_seen'] and
                                            row['cgroup_seen'] and row['argv_seen']):
                    return None
            if checks['two_unprivileged_scoped_workers'] == 'PASS' and (
                    len(observations) != 2 or not all(row['verified'] for row in observations)):
                return None
        # Gate claims need a confirmed accessible release and real workers,
        # not merely a kernel counter inherited from an earlier observation.
        if checks['memory_events_cgroup_oom_kill'] == 'PASS' and (
                diag.get('release_markers_visible_to_app') is not True or
                diag.get('stress_monitor_outcome') != 'KERNEL_OOM_EVENT' or
                type(diag.get('wrapper_exit_codes_before_cleanup')) is not list):
            return None
        if checks['memory_peak_near_policy_limit'] == 'PASS' and (
                diag.get('release_markers_visible_to_app') is not True):
            return None
        # The kernel's OOM signal and peak must *jointly* back any claim of
        # enforcement. This prevents fabricated success from a stale counter.
        if checks['memory_events_cgroup_oom_kill'] == 'PASS' and (
                diag.get('memory_max_events_delta', 0) < 1 or
                diag.get('memory_oom_kill_delta', 0) < 1):
            return None
        if checks['memory_peak_near_policy_limit'] == 'PASS' and not (
                MEMORY_LIMIT * 9 // 10 <= diag.get('memory_peak_bytes', 0)
                <= MEMORY_LIMIT + 64 * 1024**2):
            return None
        if checks['two_unprivileged_scoped_workers'] == 'PASS' and diag.get('verified_worker_count') != 2:
            return None
        if checks['worker_scopes_cleaned'] == 'PASS' and diag.get('scope_cleanup_observed') is not True:
            return None
        return checks, diag
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


def run(config: dict, *, run_clean_vm: bool = False, acknowledge_memory: bool = False,
        work_root: Path | None = None) -> dict:
    result = _base_report()
    host = verify_host_toolchain(config)
    result['host_tools_and_image'] = ('PASS' if host['status'] ==
        'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' else 'FAIL')
    if result['host_tools_and_image'] != 'PASS':
        result['status'] = 'HOST_GATES_BLOCKED'
        return result
    if not run_clean_vm:
        result['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return result
    if acknowledge_memory is not True:
        result['status'] = 'HIGH_MEMORY_VM_CONFIRMATION_REQUIRED'
        return result
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    work_path = None
    worker = None
    process = None
    stopped = False
    expired = threading.Event()
    watchdog = None
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b3b_memory_', dir=parent) as temporary:
            work_path = Path(temporary)
            try:
                key, public_key = make_ssh_key(config, work_path)
                seed = make_nocloud_iso(work_path, public_key,
                    'projectiq-b3b-memory-' + uuid.uuid4().hex,
                    workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True)
                port = _free_loopback_port()
                worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                                    work_path, port, ram_mib=VM_RAM_MIB,
                                    cpus=effective_vm_vcpus(config, 2))
                expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
                if worker.network_spec() != expected:
                    raise WorkerFailure('Unexpected network policy for clean memory probe')
                worker.start(seed)
                process = worker.proc
                if process is None or process.poll() is not None:
                    raise WorkerFailure('Clean memory VM did not start')
                result['execution'] = 'CLEAN_VM_BOOT_REQUESTED'

                def expire() -> None:
                    expired.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog = threading.Timer(WALL_SECONDS, expire)
                watchdog.daemon = True
                watchdog.start()
                ssh = SSHControl(config, work_path, port, key)
                ssh.wait_ssh(worker, seconds=110)
                source = Path(__file__).with_name('d5_memory_guest_probe.py').read_bytes()
                if len(source) > 90_000:
                    raise WorkerFailure('Trusted guest memory probe exceeds size budget')
                reply = ssh.ssh('python3', '-', input=source, timeout=175)
                parsed = _parse_guest(reply.stdout) if reply.returncode == 0 else None
                if parsed is None:
                    error = 'INVALID_OR_FAILED_MEMORY_GUEST_PROBE'
                    result['guest_probe_returncode'] = reply.returncode
                    # No student output is collected; only an error category.
                else:
                    result['checks'], result['kernel_observations'] = parsed
                    result['execution'] = 'TRUSTED_MEMORY_PROBE_IN_CLEAN_VM'
                    if process.poll() is not None:
                        error = 'VM_EXITED_EARLY'
            except Exception as exc:
                error = 'MEMORY_PROBE_' + type(exc).__name__.upper()
                if isinstance(exc, WorkerFailure):
                    result['operator_hint'] = str(exc)[:160]
            finally:
                if watchdog:
                    watchdog.cancel()
                if worker:
                    try:
                        worker.stop()
                        stopped = process is None or process.poll() is not None
                    except Exception:
                        stopped = False
    except OSError:
        error = 'MEMORY_TEMPORARY_STORAGE_CLEANUP_FAILED'
    result['cleanup'] = ('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                         if (work_path is not None and work_path.exists()) or
                         (worker is not None and not stopped)
                         else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if expired.is_set():
        error = 'MEMORY_VM_WALL_TIMEOUT'
    if error:
        result['error_code'] = error
        result['status'] = 'MEMORY_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(v == 'FAIL' for v in result['checks'].values()):
        result['status'] = 'MEMORY_ENFORCEMENT_FAILED'
    elif any(v == 'NOT_VERIFIED' for v in result['checks'].values()):
        result['status'] = 'MEMORY_ENFORCEMENT_NOT_VERIFIED'
    else:
        result['status'] = 'EVALUATION_SLICE_MEMORY_ENFORCED_NOT_ATTESTED'
    if result['cleanup'] == 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        result['status'] = 'CLEANUP_FAILED'
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5b/B3b Gate 2B.1: clean VM 2GiB cgroup OOM probe')
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-clean-vm', action='store_true')
    parser.add_argument('--acknowledge-3584mib-vm', action='store_true',
                        help='Explicit operator approval to launch a 3584 MiB guest')
    parser.add_argument('--work-root', type=Path)
    args = parser.parse_args(argv)
    try:
        record = run(load_config(args.vm_config), run_clean_vm=args.run_clean_vm,
                     acknowledge_memory=args.acknowledge_3584mib_vm,
                     work_root=args.work_root)
    except Exception as exc:
        record = _base_report()
        record['status'] = 'HOST_GATES_BLOCKED'
        record['error_code'] = 'INSPECTION_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5b/B3b Gate 2B.1 — ' + record['status'])
    print('  VM RAM:       ' + str(record['guest_ram_mib']) + ' MiB')
    print('  Attestation:  ' + record['security_attestation'])
    print('  Cleanup:      ' + record['cleanup'])
    print('  Report:       ' + str(args.out))
    return 0 if record['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
                                      'EVALUATION_SLICE_MEMORY_ENFORCED_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
