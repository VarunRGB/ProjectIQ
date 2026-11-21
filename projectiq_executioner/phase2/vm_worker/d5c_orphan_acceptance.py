"""D5c Gate 3: opt-in, real Windows orphaned-storage recovery acceptance.

Only fixed trusted clean-VM workers. Hard-kills a separate Python controller,
observes Job Object teardown of its QEMU process, and recovers a signed,
leased disposable run. Never scans global TEMP or kills PIDs from records.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from pathlib import Path

from .d5c_orphan_registry import (
    RecoveryError, _record, create_run, exclusive_lease, initialize_root,
    recover_managed_root, update_run, windows_creation_time,
)
from .d5c_host_interruption_acceptance import _WindowsProcessHandle, _expected_qemu
from .d5c_windows_job import ensure_controller_job, verify_child_in_job
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

STATUS_PASS = 'D5C_ORPHAN_STORAGE_RECOVERY_VERIFIED_NOT_ATTESTED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CHECKS = (
    'pinned_toolchain_and_template',
    'managed_root_and_signed_records',
    'active_vm_storage_preserved',
    'controller_hard_termination_observed',
    'guarded_qemu_exited',
    'stale_run_storage_recovered',
    'tampered_run_refused',
    'repeat_sweep_idempotent',
    'fresh_disposable_vm_booted',
    'all_test_vm_storage_removed',
)
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
BOOT_TIMEOUT = 165
EXIT_TIMEOUT = 35


def _base() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2, 'stage': 'd5c_gate3_managed_orphan_recovery',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED', 'student_code_executed': False,
        'security_attestation': ATTESTATION,
        'checks': {c: 'NOT_VERIFIED' for c in CHECKS},
        'remaining_security_gates': [
            'D5C_GATE4_PARTIAL_CLEANUP_IDEMPOTENCY_AND_RUNTIME_INTEGRATION_PENDING',
            'D5D_INDEPENDENT_ISOLATION_PENDING',
            'D5E_FINAL_GO_NO_GO_PENDING',
            'HOST_POWER_LOSS_AND_ARBITRARY_UNTRUSTED_SUBMISSIONS_NOT_ATTESTED',
        ],
    }


def _child(config_file: Path, root: Path, run: Path, stop: Path) -> int:
    """A separate controller that owns the lease and Windows lifetime job."""
    worker = None
    try:
        config = load_config(config_file)
        if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            return 2
        ensure_controller_job()
        with exclusive_lease(run):
            # A partially launched VM without an authenticated PID is never
            # silently swept. The LAUNCHING state requires manual review.
            update_run(root, run, 'LAUNCHING')
            key, pub = make_ssh_key(config, run)
            seed = make_nocloud_iso(run, pub, 'projectiq-d5c3-' + run.name[-20:],
                                    workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True)
            port = _free_loopback_port()
            worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                                run, port, ram_mib=VM_RAM_MIB,
                                cpus=effective_vm_vcpus(config, 2))
            if worker.network_spec() != (
                f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
            ):
                return 3
            worker.start(seed)
            if (worker.proc is None or worker.proc.poll() is not None or
                    not verify_child_in_job(worker.proc)):
                return 4
            born = windows_creation_time(worker.proc)
            image = _expected_qemu(config)
            update_run(root, run, 'LAUNCHED', pid=worker.proc.pid,
                       born=born, image=image)
            ssh = SSHControl(config, run, port, key)
            ssh.wait_ssh(worker, seconds=115)
            if worker.proc.poll() is not None:
                return 5
            update_run(root, run, 'BOOTED', pid=worker.proc.pid,
                       born=born, image=image)
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline:
                if stop.is_file() and stop.read_bytes() == b'STOP\n':
                    worker.stop()
                    worker = None
                    update_run(root, run, 'STOPPED', pid=_record(root,run)['pid'],
                               born=born, image=image)
                    return 0
                if worker.proc.poll() is not None:
                    return 6
                time.sleep(0.1)
            return 7
    except Exception:
        return 8
    finally:
        # Hard termination bypasses finally; the parent's Job Object test
        # observes QEMU exit. Normal shutdown always attempts to stop QEMU.
        if worker is not None:
            try:
                worker.stop()
            except (OSError, subprocess.SubprocessError):
                pass


def _start_controller(config_path: Path, root: Path, run: Path, stop: Path,
                      child_log: Path):
    cmd = [sys.executable, '-m',
           'projectiq_executioner.phase2.vm_worker.d5c_orphan_acceptance',
           '--_trusted-child', '--vm-config', str(config_path.resolve()),
           '--_root', str(root), '--_run', str(run), '--_stop', str(stop)]
    with child_log.open('wb') as log:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT,
                                cwd=str(Path(__file__).resolve().parents[3]))


def _await_boot(root: Path, run: Path, proc, expected_image: str):
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RecoveryError('CONTROLLER_EXITED_BEFORE_BOOT')
        try:
            rec = _record(root, run)
            if rec['phase'] == 'BOOTED':
                if os.path.normcase(os.path.realpath(rec['image'])) != os.path.normcase(os.path.realpath(expected_image)):
                    raise RecoveryError('WRONG_QEMU_IMAGE')
                live = _WindowsProcessHandle(rec['pid'], expected_image)
                try:
                    actual_born = windows_creation_time(SimpleNamespace(_handle=live._handle))
                except Exception:
                    live.close()
                    raise
                if actual_born != rec['born']:
                    live.close()
                    raise RecoveryError('QEMU_CREATION_TIME_MISMATCH')
                return live, rec
        except (FileNotFoundError, PermissionError):
            # Atomic run.json replacements can briefly reject readers on
            # Windows. Retry while the authenticated controller is live;
            # never treat an unreadable or malformed record as valid.
            time.sleep(0.15)
            continue
        except OSError as exc:
            raise RecoveryError('RUN_RECORD_INACCESSIBLE') from exc
        time.sleep(0.15)
    raise RecoveryError('GUEST_BOOT_TIMEOUT')


def run(config_path: Path, *, execute: bool = False, acknowledge: bool = False,
        work_root: Path | None = None) -> dict:
    report = _base()
    if os.name != 'nt':
        report['status'] = 'WINDOWS_RECOVERY_ACCEPTANCE_REQUIRES_WINDOWS'
        return report
    try:
        config = load_config(config_path)
        host = verify_host_toolchain(config)
        if host['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            report['status'] = 'HOST_GATES_BLOCKED'
            return report
        expected_image = _expected_qemu(config)
        report['checks']['pinned_toolchain_and_template'] = 'PASS'
    except (OSError, KeyError, ValueError) as exc:
        report['status'] = 'HOST_GATES_BLOCKED'
        report['failure_stage'] = 'PREFLIGHT_' + type(exc).__name__.upper()
        return report
    if not execute:
        report['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return report
    if not acknowledge:
        report['status'] = 'ORPHAN_RECOVERY_ACKNOWLEDGMENT_REQUIRED'
        return report

    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    workspace = None
    controllers = []
    handles = []
    failure = None
    cleaned = False
    try:
        # Never let TemporaryDirectory.__exit__ erase a diagnostic or mask
        # an earlier failure while a controller still has files open.
        workspace = Path(tempfile.mkdtemp(prefix='projectiq_d5c3_', dir=parent))
        managed = initialize_root(workspace / 'managed')
        stale = create_run(managed)
        tampered = create_run(managed)
        (tampered / 'run.json').write_text('forged', encoding='ascii')
        unrelated = managed / 'unrelated.txt'
        unrelated.write_bytes(b'UNRELATED')
        report['checks']['managed_root_and_signed_records'] = 'PASS'
        first_stop = workspace / 'first-stop.marker'
        first = _start_controller(config_path, managed, stale, first_stop, workspace / 'controller1.log')
        controllers.append(first)
        first_handle, first_record = _await_boot(managed, stale, first, expected_image)
        handles.append(first_handle)
        active = recover_managed_root(managed, expected_image)
        first_status = next(x['status'] for x in active if x['run'] == stale.name)
        if first_status != 'SKIPPED_ACTIVE' or not stale.is_dir():
            raise RecoveryError('LIVE_RUN_NOT_PRESERVED')
        report['checks']['active_vm_storage_preserved'] = 'PASS'
        if not first_handle.is_live():
            raise RecoveryError('QEMU_DIED_BEFORE_CONTROLLER_KILL')
        first.kill()
        first.wait(timeout=12)
        if first.poll() is None:
            raise RecoveryError('CONTROLLER_NOT_TERMINATED')
        report['checks']['controller_hard_termination_observed'] = 'PASS'
        if not first_handle.wait_exited(EXIT_TIMEOUT):
            first_handle.emergency_terminate()
            raise RecoveryError('ORPHAN_QEMU_SURVIVED_CONTROLLER_DEATH')
        report['checks']['guarded_qemu_exited'] = 'PASS'
        sweep = recover_managed_root(managed, expected_image)
        if not any(x['run'] == stale.name and x['status'] == 'RECOVERED' for x in sweep):
            raise RecoveryError('STALE_RUN_NOT_RECOVERED')
        if stale.exists():
            raise RecoveryError('STALE_FILES_SURVIVED_SWEEP')
        report['checks']['stale_run_storage_recovered'] = 'PASS'
        if not any(x['run'] == tampered.name and x['status'] == 'MANUAL_REVIEW' for x in sweep):
            raise RecoveryError('TAMPERED_RUN_NOT_REJECTED')
        if (tampered / 'run.json').read_text('ascii') != 'forged' or unrelated.read_bytes() != b'UNRELATED':
            raise RecoveryError('UNRELATED_OR_TAMPERED_DATA_MODIFIED')
        report['checks']['tampered_run_refused'] = 'PASS'
        rerun = recover_managed_root(managed, expected_image)
        if any(x['status'] == 'RECOVERED' for x in rerun) or stale.exists():
            raise RecoveryError('RECOVERY_NOT_IDEMPOTENT')
        report['checks']['repeat_sweep_idempotent'] = 'PASS'
        # Verify another clean QEMU instance starts after stale recovery.
        fresh = create_run(managed)
        fresh_stop = workspace / 'fresh-stop.marker'
        second = _start_controller(config_path, managed, fresh, fresh_stop,
                                   workspace / 'controller2.log')
        controllers.append(second)
        fresh_handle, fresh_record = _await_boot(managed, fresh, second, expected_image)
        handles.append(fresh_handle)
        if fresh_record['id'] == first_record['id'] or not fresh_handle.is_live():
            raise RecoveryError('NEW_VM_NOT_INDEPENDENT')
        report['checks']['fresh_disposable_vm_booted'] = 'PASS'
        fresh_stop.write_bytes(b'STOP\n')
        if second.wait(timeout=15) != 0 or not fresh_handle.wait_exited(12):
            raise RecoveryError('FRESH_CONTROLLER_STOP_FAILED')
        final = recover_managed_root(managed, expected_image)
        if not any(x['run'] == fresh.name and x['status'] == 'RECOVERED' for x in final):
            raise RecoveryError('FRESH_VM_DISK_NOT_CLEANED')
        if fresh.exists():
            raise RecoveryError('FRESH_VM_STORAGE_SURVIVED')
        # Deliberately forged fixture is removed only by the outer trusted
        # test owner; the recovery sweep itself MUST refuse to touch it.
        shutil.rmtree(tampered)
        unrelated.unlink()
        if {p.name for p in managed.iterdir()} != {'.projectiq-d5c3-root-key', '.lease'}:
            raise RecoveryError('MANAGED_ROOT_NOT_EMPTY')
        report['execution'] = 'INTERRUPTED_CONTROLLER_RECOVERED_FRESH_VM_BOOTED'
    except (OSError, ValueError, RecoveryError, subprocess.SubprocessError) as exc:
        failure = type(exc).__name__ if not isinstance(exc, RecoveryError) else str(exc)
    finally:
        for controller in controllers:
            if controller.poll() is None:
                try:
                    controller.kill()
                    controller.wait(timeout=12)
                except (OSError, subprocess.SubprocessError):
                    failure = failure or 'CONTROLLER_EMERGENCY_STOP_FAILED'
        for handle in handles:
            try:
                if handle.is_live() and not handle.emergency_terminate():
                    failure = failure or 'QEMU_EMERGENCY_STOP_FAILED'
            except (OSError, ValueError):
                failure = failure or 'QEMU_EMERGENCY_STOP_UNVERIFIED'
            finally:
                handle.close()
        # Only remove the entire test workspace after *all* acceptance
        # conditions (except the final removal check) have been verified.
        # On any failure, retain its bounded test directory for manual
        # inspection instead of deleting uncertain VM files automatically.
        prior_checks_pass = all(
            value == 'PASS' for key, value in report['checks'].items()
            if key != 'all_test_vm_storage_removed'
        )
        if (workspace is not None and failure is None and prior_checks_pass and
                report['execution'] == 'INTERRUPTED_CONTROLLER_RECOVERED_FRESH_VM_BOOTED'):
            try:
                shutil.rmtree(workspace)
                cleaned = not workspace.exists()
            except OSError as exc:
                failure = type(exc).__name__
        if workspace is not None and workspace.exists():
            report['manual_review_workspace'] = str(workspace)
    if cleaned:
        report['cleanup'] = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
        report['checks']['all_test_vm_storage_removed'] = 'PASS'
    else:
        report['cleanup'] = 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
    if failure:
        report['failure_code'] = failure[:100]
    report['status'] = (STATUS_PASS if not failure and cleaned and
                        all(x == 'PASS' for x in report['checks'].values())
                        else 'D5C_ORPHAN_RECOVERY_NOT_VERIFIED')
    if not cleaned:
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5c Gate 3: trusted managed orphan recovery')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--run-clean-vm', action='store_true')
    p.add_argument('--acknowledge-orphan-recovery', action='store_true')
    p.add_argument('--work-root', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--_trusted-child', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--_root', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--_run', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--_stop', type=Path, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args._trusted_child:
        if not (args._root and args._run and args._stop):
            return 2
        return _child(args.vm_config, args._root, args._run, args._stop)
    if args.out is None:
        p.error('--out required')
    try:
        result = run(args.vm_config, execute=args.run_clean_vm,
                     acknowledge=args.acknowledge_orphan_recovery,
                     work_root=args.work_root)
    except Exception as exc:
        result = _base()
        result['status'] = 'HOST_GATES_BLOCKED'
        result['failure_code'] = 'TOP_LEVEL_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5c Gate 3 - ' + result['status'])
    print('  Cleanup: ' + result['cleanup'])
    print('  Report: ' + str(args.out))
    return 0 if result['status'] in (STATUS_PASS,'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
