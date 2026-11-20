"""D5b/B3b Gate 2B.4: bounded hard-VM-kill and fresh-worker recovery.

A trusted, explicit two-VM acceptance experiment. The first VM is killed
while a verified app-UID process is still alive; the second must be fresh.
This DOES NOT attest host-crash persistence or arbitrary student execution.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_recovery_guest_probe import _validated_guest_report, _bounded_diagnostics
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
WALL_SECONDS = 400
CHECKS = (
    'trusted_bootstraps_complete', 'evaluation_slice_kernel_policy',
    'controlled_crash_scope_drained', 'controlled_cancel_scope_drained',
    'first_vm_worker_verified', 'forced_vm_termination_observed',
    'first_vm_storage_removed', 'fresh_vm_booted',
    'fresh_vm_workspace_and_slice_clean', 'fresh_vm_storage_removed',
)
REMAINING = (
    'D5C_HOST_CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_PENDING',
    'BUILD_DESCENDANT_LIFECYCLE_NOT_FULLY_VERIFIED',
    'MYSQL_SERVICE_RESOURCES_NOT_IN_EVALUATION_SLICE',
    'GUEST_ROOT_QCOW2_AND_OTHER_GUEST_WRITES_NOT_CONFINED',
    'INDEPENDENT_ISOLATION_AND_HYPERVISOR_ATTESTATION_PENDING',
)


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2,
        'stage': 'd5b_b3b_gate2b4_vm_recovery',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'student_code_executed': False, 'security_attestation': ATTESTATION,
        'cleanup': 'NOT_STARTED', 'guest_ram_mib': VM_RAM_MIB,
        'workspace_disk_mib': WORKSPACE_MIB,
        'checks': {name: 'NOT_VERIFIED' for name in CHECKS},
        'observations': {}, 'remaining_security_gates': list(REMAINING),
    }


def _parse_guest(stdout: bytes, mode: str) -> dict | None:
    if type(stdout) is not bytes or not 0 < len(stdout) <= 2000:
        return None
    try:
        raw = json.loads(stdout.decode('utf-8', 'strict'))
        return raw if _validated_guest_report(raw, mode) else None
    except (UnicodeError, ValueError, TypeError):
        return None


def _start_vm(config: dict, work: Path, tag: str):
    """One fresh instance, one fresh overlay and workspace, restricted network."""
    key, pub = make_ssh_key(config, work)
    seed = make_nocloud_iso(work, pub, 'projectiq-b3b-b4-' + uuid.uuid4().hex,
                            workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True)
    port = _free_loopback_port()
    worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB}, work,
                        port, ram_mib=VM_RAM_MIB,
                        cpus=effective_vm_vcpus(config, 2))
    expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
    try:
        if worker.network_spec() != expected:
            raise WorkerFailure('Unexpected recovery-probe VM network policy')
        worker.start(seed)
        if worker.proc is None or worker.proc.poll() is not None:
            raise WorkerFailure('Recovery-probe VM failed to boot')
        ssh = SSHControl(config, work, port, key)
        return worker, ssh
    except Exception:
        # A failure after opening the QEMU log must not leave Windows with
        # an open overlay/scratch directory or a stray live VM.
        worker.stop()
        raise


def _guest_evidence(ssh: SSHControl, mode: str, token: str,
                    observations: dict | None = None) -> dict | None:
    source = Path(__file__).with_name('d5_recovery_guest_probe.py').read_bytes()
    if len(source) > 40000:
        raise WorkerFailure('Trusted recovery probe exceeds source budget')
    response = ssh.ssh('python3', '-', mode, token, input=source, timeout=135)
    if response.returncode != 0:
        return None
    # Even a failed guest probe may provide a bounded stage, but NEVER
    # promote that diagnostic to positive acceptance evidence.
    if type(response.stdout) is bytes and 0 < len(response.stdout) <= 2000:
        try:
            raw = json.loads(response.stdout.decode('utf-8', 'strict'))
            hint = _bounded_diagnostics(raw, mode)
            if hint is not None and observations is not None:
                observations[mode + '_guest_probe_stage'] = hint['probe_stage']
                observations[mode + '_crash_exit_code'] = hint['crash_exit_code']
                observations[mode + '_cancel_exit_code'] = hint['cancel_exit_code']
        except (ValueError, UnicodeError, TypeError):
            pass
    return _parse_guest(response.stdout, mode)


def run(config: dict, *, run_clean_vm: bool = False,
        acknowledge_hard_vm_kill: bool = False,
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
    if not acknowledge_hard_vm_kill:
        result['status'] = 'HARD_VM_TERMINATION_CONFIRMATION_REQUIRED'
        return result

    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    run1 = None; run2 = None
    first_worker = None; second_worker = None
    first_proc = None; second_proc = None
    first_stopped = False; second_stopped = False
    error = None; expired = threading.Event(); watchdog = None
    current = [None]
    token = uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_b3b_b4_', dir=parent) as temp:
            run1 = Path(temp) / 'vm_aborted'
            run2 = Path(temp) / 'vm_fresh'
            run1.mkdir()
            run2.mkdir()
            def expire():
                expired.set()
                worker = current[0]
                if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                    worker.proc.kill()
            watchdog = threading.Timer(WALL_SECONDS, expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                first_worker, first_ssh = _start_vm(config, run1, 'arm')
                first_proc = first_worker.proc
                current[0] = first_worker
                result['execution'] = 'FIRST_CLEAN_VM_BOOTED'
                first_ssh.wait_ssh(first_worker, seconds=110)
                armed = _guest_evidence(first_ssh, 'arm', token, result['observations'])
                if armed is None:
                    raise WorkerFailure('First guest fixed worker not independently verified')
                result['checks']['trusted_bootstraps_complete'] = 'PASS'
                result['checks']['evaluation_slice_kernel_policy'] = 'PASS'
                result['checks']['controlled_crash_scope_drained'] = 'PASS'
                result['checks']['controlled_cancel_scope_drained'] = 'PASS'
                result['checks']['first_vm_worker_verified'] = 'PASS'
                result['observations']['controlled_crash_and_cancel_cleaned'] = True
                result['observations']['first_vm_worker_armed'] = True
                result['observations']['first_vm_evaluation_tasks'] = armed['eval_tasks_current']
                if first_proc is None or first_proc.poll() is not None:
                    raise WorkerFailure('First VM exited before deliberate kill')
                # Unlike cooperative cleanup, this intentionally terminates
                # QEMU while the verified app worker is still running.
                first_proc.kill()
                first_proc.wait(timeout=18)
                if first_proc.poll() is None:
                    raise WorkerFailure('Forced QEMU termination not observed')
                result['checks']['forced_vm_termination_observed'] = 'PASS'
                result['observations']['forced_vm_termination_observed'] = True
                first_worker.stop()
                first_stopped = first_proc.poll() is not None
                current[0] = None
                if not first_stopped:
                    raise WorkerFailure('First QEMU process remains alive')
                # The aborted VM's separate disks and keys must be gone before
                # launching its replacement. Do not reuse an overlay.
                import shutil
                shutil.rmtree(run1)
                if run1.exists():
                    raise WorkerFailure('Aborted VM scratch directory survived')
                result['checks']['first_vm_storage_removed'] = 'PASS'
                result['observations']['first_vm_storage_removed'] = True

                second_worker, second_ssh = _start_vm(config, run2, 'recover')
                second_proc = second_worker.proc
                current[0] = second_worker
                result['execution'] = 'FRESH_VM_BOOTED_AFTER_FORCED_TERMINATION'
                second_ssh.wait_ssh(second_worker, seconds=110)
                recovered = _guest_evidence(second_ssh, 'recover', token, result['observations'])
                if recovered is None:
                    raise WorkerFailure('Fresh guest failed workspace/cgroup recovery')
                result['checks']['fresh_vm_booted'] = 'PASS'
                result['checks']['fresh_vm_workspace_and_slice_clean'] = 'PASS'
                result['observations']['fresh_vm_sentinel_absent'] = True
                result['observations']['fresh_vm_evaluation_tasks'] = recovered['eval_tasks_current']
            except Exception as exc:
                error = 'RECOVERY_PROBE_' + type(exc).__name__.upper()
                if isinstance(exc, WorkerFailure):
                    result['failure_stage'] = result['execution']
            finally:
                if second_worker is not None:
                    try:
                        second_worker.stop()
                        second_stopped = second_proc is not None and second_proc.poll() is not None
                    except Exception:
                        second_stopped = False
                current[0] = None
                if first_worker is not None and not first_stopped:
                    try:
                        first_worker.stop()
                        first_stopped = first_proc is not None and first_proc.poll() is not None
                    except Exception:
                        first_stopped = False
                if watchdog:
                    watchdog.cancel()
            # TemporaryDirectory owns all copies. We require the second VM
            # process stopped and its entire per-VM directory removed.
            if (result['checks']['fresh_vm_workspace_and_slice_clean'] == 'PASS'
                    and second_stopped and run2.exists()):
                import shutil
                shutil.rmtree(run2)
                if not run2.exists():
                    result['checks']['fresh_vm_storage_removed'] = 'PASS'
                    result['observations']['fresh_vm_storage_removed'] = True
    except Exception as exc:
        if error is None:
            error = 'RECOVERY_TEMPORARY_STORAGE_' + type(exc).__name__.upper()

    # The directories must be gone; if a failed run left an open overlay,
    # never claim cleanup even if some of the guest checks passed.
    clean = bool(run1 is not None and run2 is not None
                 and not run1.exists() and not run2.exists()
                 and (first_worker is None or first_stopped)
                 and (second_worker is None or second_stopped))
    result['cleanup'] = ('VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' if clean
                         else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
    if expired.is_set():
        error = 'RECOVERY_VM_WALL_TIMEOUT'
    if error:
        result['error_code'] = error
        result['status'] = 'VM_RECOVERY_INCONCLUSIVE_OR_FAILED'
    elif all(v == 'PASS' for v in result['checks'].values()) and clean:
        result['status'] = 'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED'
    else:
        result['status'] = 'VM_RECOVERY_NOT_VERIFIED'
    if not clean:
        result['status'] = 'CLEANUP_FAILED'
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5b/B3b Gate 2B.4 disposable VM hard-stop recovery')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--run-clean-vm', action='store_true')
    p.add_argument('--acknowledge-hard-vm-kill', action='store_true')
    p.add_argument('--work-root', type=Path)
    args = p.parse_args(argv)
    try:
        result = run(load_config(args.vm_config), run_clean_vm=args.run_clean_vm,
                     acknowledge_hard_vm_kill=args.acknowledge_hard_vm_kill,
                     work_root=args.work_root)
    except Exception as exc:
        result = _base_report()
        result['status'] = 'HOST_GATES_BLOCKED'
        result['error_code'] = 'INSPECTION_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5b/B3b Gate 2B.4 - ' + result['status'])
    print('  Attestation: ' + result['security_attestation'])
    print('  Cleanup:     ' + result['cleanup'])
    print('  Report:      ' + str(args.out))
    return 0 if result['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
              'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
