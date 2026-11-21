"""D5c Gate 2: cooperative cancellation at four trusted VM startup boundaries.

Windows-only opt-in clean-VM acceptance. No student source, network downloads,
shared host mounts or production untrusted execution. A separate controller owns
QEMU and joins the Gate 1 kill-on-close Windows Job Object before launch.
This gate does not attest full cancellation of running arbitrary guest code.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .d5c_host_interruption_acceptance import (
    _WindowsProcessHandle, _atomic_record, _expected_qemu, _read_record,
)
from .d5c_windows_job import ensure_controller_job, verify_child_in_job
from .orchestrator import load_config
from .qemu import (
    QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
    effective_vm_vcpus, make_nocloud_iso, make_ssh_key, verify_host_toolchain,
)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
STATUS_PASS = 'D5C_STARTUP_CANCELLATION_VERIFIED_NOT_ATTESTED'
SCENARIOS = ('pre_launch', 'post_launch', 'ssh_wait', 'guest_ready')
STAGE_TIMEOUT = 170
EXIT_TIMEOUT = 35
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
CANCEL_BYTES = b'CANCEL\n'
CHECKS = (
    'pinned_toolchain_and_template',
    'windows_controller_job_guard',
    'pre_launch_cancel_no_vm',
    'post_launch_cancel_vm_exited',
    'ssh_wait_cancel_vm_exited',
    'guest_ready_cancel_vm_exited',
    'all_controllers_exited',
    'all_disposable_storage_removed',
)


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2,
        'stage': 'd5c_gate2_startup_cancellation',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED', 'security_attestation': ATTESTATION,
        'student_code_executed': False,
        'checks': {key: 'NOT_VERIFIED' for key in CHECKS},
        'scenarios': {key: 'NOT_VERIFIED' for key in SCENARIOS},
        'remaining_security_gates': [
            'D5C_ORPHANED_STORAGE_SWEEP_NOT_VERIFIED',
            'D5C_PARTIAL_CLEANUP_RACES_NOT_VERIFIED',
            'D5C_KILL_DURING_SPAWN_ATOMICITY_NOT_FULLY_VERIFIED',
            'D5C_LINUX_PARENT_DEATH_AND_POWER_LOSS_NOT_VERIFIED',
            'D5D_INDEPENDENT_ISOLATION_AND_GUEST_DESCENDANTS_PENDING',
            'D5E_FINAL_SECURITY_GO_NO_GO_PENDING',
        ],
    }


def _cancel_requested(path: Path) -> bool:
    """Only exact controller-owned cancellation markers authorize success.

    Unreadable and symlink paths raise; they are never equivalent to absence.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != len(CANCEL_BYTES):
        raise ValueError('INVALID_CANCELLATION_MARKER')
    if path.read_bytes() != CANCEL_BYTES:
        raise ValueError('INVALID_CANCELLATION_MARKER')
    return True


def _publish_cancel(path: Path) -> None:
    """Atomically publish a complete trusted cancellation marker."""
    if path.exists() or path.is_symlink():
        raise ValueError('UNEXPECTED_EXISTING_CANCELLATION_MARKER')
    scratch = path.with_name(path.name + '.writing')
    with scratch.open('xb') as output:
        output.write(CANCEL_BYTES)
    os.replace(scratch, path)


def _wait_cancel(path: Path, seconds: int = 25) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _cancel_requested(path):
            return
        time.sleep(0.1)
    raise RuntimeError('CANCELLATION_MARKER_TIMEOUT')


def _child(config_path: Path, workspace: Path, scenario: str) -> int:
    """A cancellable *trusted* controller, never a student-workload runner."""
    if scenario not in SCENARIOS:
        return 2
    worker = None
    vm_proc = None
    marker = workspace / 'cancel.marker'
    cancellation_seen = False
    qemu_started = False
    try:
        config = load_config(config_path)
        if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            raise RuntimeError('PINNED_HOST_PREFLIGHT_FAILED')
        ensure_controller_job()
        _atomic_record(workspace / 'job_ready.json', {'schema_version': '0.1', 'armed': True})
        work = workspace / 'vm'
        work.mkdir(mode=0o700)
        key, pub = make_ssh_key(config, work)
        seed = make_nocloud_iso(work, pub, 'projectiq-d5c2-' + uuid.uuid4().hex,
                                workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True)
        port = _free_loopback_port()
        worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                            work, port, ram_mib=VM_RAM_MIB,
                            cpus=effective_vm_vcpus(config, 2))
        expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
        if worker.network_spec() != expected:
            raise RuntimeError('VM_NETWORK_POLICY_MISMATCH')
        if scenario == 'pre_launch':
            _atomic_record(workspace / 'stage.json',
                           {'schema_version': '0.1', 'scenario': scenario, 'ready': True})
            _wait_cancel(marker)
            cancellation_seen = True
        else:
            worker.start(seed)
            vm_proc = worker.proc
            if vm_proc is None or vm_proc.poll() is not None or not verify_child_in_job(vm_proc):
                raise RuntimeError('QEMU_NOT_ALIVE_AND_GUARDED')
            qemu_started = True
            _atomic_record(workspace / 'launched.json', {
                'schema_version': '0.1', 'vm_pid': vm_proc.pid, 'guarded': True,
            })
            ssh = SSHControl(config, work, port, key)
            if scenario == 'post_launch':
                _atomic_record(workspace / 'stage.json',
                               {'schema_version': '0.1', 'scenario': scenario, 'ready': True})
                _wait_cancel(marker)
                cancellation_seen = True
            elif scenario == 'ssh_wait':
                # Deliberately make a real SSH connection attempt to a CLOSED
                # loopback port while the separate verified QEMU stays live.
                # An actual failed readiness probe is required before the
                # parent is permitted to inject cancellation.
                closed_port = _free_loopback_port()
                if closed_port == port:
                    raise RuntimeError('SSH_TEST_PORT_COLLISION')
                ssh.port = closed_port
                original_ssh = ssh.ssh
                failed_once = False

                def deliberately_unready(*args, **kwargs):
                    nonlocal failed_once
                    reply = original_ssh(*args, **kwargs)
                    if reply.returncode != 0 and not failed_once:
                        failed_once = True
                        _atomic_record(workspace / 'stage.json', {
                            'schema_version': '0.1', 'scenario': scenario, 'ready': True,
                        })
                    return reply

                ssh.ssh = deliberately_unready
                try:
                    ssh.wait_ssh(worker, seconds=80,
                                 cancel_requested=lambda: _cancel_requested(marker))
                except WorkerFailure as exc:
                    if str(exc) != 'Worker VM startup cancelled' or not _cancel_requested(marker):
                        raise
                    cancellation_seen = True
                else:
                    raise RuntimeError('UNEXPECTED_SSH_READINESS')
                if not failed_once:
                    raise RuntimeError('SSH_PROBE_NOT_PERFORMED')
            else:
                ssh.wait_ssh(worker, seconds=110)
                if ssh.ssh('true', timeout=10).returncode != 0:
                    raise RuntimeError('GUEST_NOT_READY')
                # Boundary after SSH boot confirmation, before any project
                # bootstrap/install/run command: cancel must skip guest setup.
                _atomic_record(workspace / 'stage.json', {
                    'schema_version': '0.1', 'scenario': scenario, 'ready': True,
                })
                _wait_cancel(marker)
                cancellation_seen = True
        if not cancellation_seen:
            raise RuntimeError('NO_VERIFIED_CANCELLATION')
    except Exception as exc:
        try:
            _atomic_record(workspace / 'child_error.json', {
                'schema_version': '0.1', 'reason': type(exc).__name__,
            })
        except OSError:
            pass
        return_code = 2
    else:
        return_code = 0
    finally:
        # This is a cooperative cancellation path. The Gate 1 OS job remains
        # mandatory fallback if this controller is hard-killed mid-cleanup.
        clean = True
        if worker is not None:
            try:
                worker.stop()
            except (OSError, subprocess.TimeoutExpired, WorkerFailure):
                clean = False
        if vm_proc is not None and vm_proc.poll() is None:
            clean = False
        if not clean:
            return_code = 2
        if return_code == 0 and cancellation_seen and clean:
            try:
                _atomic_record(workspace / 'cancelled.json', {
                    'schema_version': '0.1', 'scenario': scenario,
                    'cancellation_seen': True,
                    'qemu_started': qemu_started,
                    'qemu_exited': (vm_proc is None or vm_proc.poll() is not None),
                })
            except OSError:
                return_code = 2
    return return_code


def _stage_valid(raw: dict | None, scenario: str) -> bool:
    return (type(raw) is dict and set(raw) == {'schema_version', 'scenario', 'ready'}
            and raw['schema_version'] == '0.1' and raw['scenario'] == scenario
            and raw['ready'] is True)


def _cancelled_valid(raw: dict | None, scenario: str) -> bool:
    return (type(raw) is dict and set(raw) == {
            'schema_version', 'scenario', 'cancellation_seen', 'qemu_started', 'qemu_exited'}
            and raw['schema_version'] == '0.1' and raw['scenario'] == scenario
            and raw['cancellation_seen'] is True and raw['qemu_exited'] is True
            and raw['qemu_started'] is (scenario != 'pre_launch'))


def _launch_valid(raw: dict | None) -> bool:
    return (type(raw) is dict and set(raw) == {'schema_version', 'vm_pid', 'guarded'}
            and raw['schema_version'] == '0.1' and raw['guarded'] is True
            and type(raw['vm_pid']) is int and 0 < raw['vm_pid'] < 2**31)


class _CaseRejected(Exception):
    """Abort a scenario while allowing guaranteed teardown and evidence inspection."""


def _case(config: Path, root: Path, scenario: str, expected_image: str) -> tuple[bool, bool, str]:
    """Return scenario pass, storage cleanup pass, bounded failure code."""
    directory = None
    controller = None
    qemu = None
    success = False
    reason = 'STAGE_NOT_REACHED'
    storage_removed = False
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5c2_', dir=root) as td:
            directory = Path(td)
            try:
                cmd = [sys.executable, '-m',
                       'projectiq_executioner.phase2.vm_worker.d5c_startup_cancel_acceptance',
                       '--_trusted-child', '--vm-config', str(config.resolve()),
                       '--_workspace', str(directory), '--_scenario', scenario]
                with (directory / 'controller.log').open('wb') as sink:
                    controller = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                                  stdout=sink, stderr=subprocess.STDOUT,
                                                  cwd=str(Path(__file__).resolve().parents[3]))
                deadline = time.monotonic() + STAGE_TIMEOUT
                while time.monotonic() < deadline:
                    if controller.poll() is not None:
                        reason = 'CONTROLLER_EXITED_BEFORE_STAGE'
                        break
                    stage = _read_record(directory / 'stage.json')
                    if _stage_valid(stage, scenario):
                        break
                    if _read_record(directory / 'child_error.json') is not None:
                        reason = 'CHILD_ERROR_BEFORE_STAGE'
                        break
                    time.sleep(0.2)
                else:
                    reason = 'STAGE_TIMEOUT'
                if not _stage_valid(_read_record(directory / 'stage.json'), scenario):
                    raise _CaseRejected(reason)
                guard = _read_record(directory / 'job_ready.json')
                if guard != {'schema_version': '0.1', 'armed': True}:
                    reason = 'JOB_GUARD_UNVERIFIED'
                elif scenario == 'pre_launch':
                    if _read_record(directory / 'launched.json') is not None:
                        reason = 'QEMU_UNEXPECTEDLY_LAUNCHED'
                    else:
                        reason = 'READY_TO_CANCEL'
                else:
                    launch = _read_record(directory / 'launched.json')
                    if not _launch_valid(launch):
                        reason = 'LAUNCH_IDENTITY_MISSING'
                    else:
                        try:
                            qemu = _WindowsProcessHandle(launch['vm_pid'], expected_image)
                            if qemu.is_live():
                                reason = 'READY_TO_CANCEL'
                            else:
                                reason = 'QEMU_EXITED_BEFORE_CANCEL'
                        except (ValueError, OSError):
                            reason = 'QEMU_PROCESS_IDENTITY_INVALID'
                if reason != 'READY_TO_CANCEL':
                    raise _CaseRejected(reason)
                # Controller-only owner and exact bytes; no student access.
                if (directory / 'cancel.marker').exists():
                    raise _CaseRejected('UNEXPECTED_EXISTING_CANCEL_MARKER')
                _publish_cancel(directory / 'cancel.marker')
                try:
                    exit_code = controller.wait(timeout=EXIT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    reason = 'CANCEL_NOT_ACKNOWLEDGED_BEFORE_DEADLINE'
                else:
                    done = _read_record(directory / 'cancelled.json')
                    if exit_code != 0:
                        reason = 'CONTROLLER_CANCEL_EXIT_NONZERO'
                    elif not _cancelled_valid(done, scenario):
                        reason = 'CANCELLATION_COMPLETION_NOT_VERIFIED'
                    elif qemu is not None and not qemu.wait_exited(seconds=20):
                        reason = 'QEMU_SURVIVED_COOPERATIVE_CANCEL'
                    else:
                        success = True
                        reason = 'CANCELLATION_AND_QEMU_EXIT_VERIFIED'
            finally:
                try:
                    if controller is not None and controller.poll() is None:
                        controller.kill()
                        controller.wait(timeout=12)
                except (OSError, subprocess.SubprocessError):
                    success = False
                    reason = 'CONTROLLER_EMERGENCY_STOP_FAILED'
                finally:
                    if qemu is not None:
                        try:
                            if qemu.is_live():
                                # Emergency-only: never promote this to PASS.
                                success = False
                                reason = 'EMERGENCY_QEMU_HANDLE_CLEANUP_REQUIRED'
                                qemu.emergency_terminate()
                        finally:
                            qemu.close()
        storage_removed = directory is not None and not directory.exists()
    except _CaseRejected as exc:
        reason = str(exc)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = 'HOST_' + type(exc).__name__.upper()
    storage_removed = directory is not None and not directory.exists()
    return success and storage_removed, storage_removed, reason


def run(config_path: Path, *, execute: bool = False, acknowledge: bool = False,
        work_root: Path | None = None) -> dict:
    result = _base_report()
    if os.name != 'nt':
        result['status'] = 'WINDOWS_JOB_ACCEPTANCE_REQUIRES_WINDOWS'
        return result
    try:
        config = load_config(config_path)
        if verify_host_toolchain(config)['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            result['status'] = 'HOST_GATES_BLOCKED'
            return result
        expected_image = _expected_qemu(config)
        result['checks']['pinned_toolchain_and_template'] = 'PASS'
    except (OSError, KeyError, ValueError) as exc:
        result['status'] = 'HOST_GATES_BLOCKED'
        result['failure_code'] = 'PREFLIGHT_' + type(exc).__name__.upper()
        return result
    if not execute:
        result['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
        return result
    if not acknowledge:
        result['status'] = 'STARTUP_CANCELLATION_ACKNOWLEDGMENT_REQUIRED'
        return result
    parent = Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    clean = True
    for scenario, check in zip(SCENARIOS, CHECKS[2:6]):
        good, removed, reason = _case(config_path, parent, scenario, expected_image)
        result['scenarios'][scenario] = 'PASS' if good else 'FAIL'
        if not removed:
            clean = False
        if not good:
            result['failure_stage'] = scenario
            result['failure_code'] = reason
            break
        result['checks'][check] = 'PASS'
    if all(result['scenarios'][s] == 'PASS' for s in SCENARIOS):
        result['checks']['windows_controller_job_guard'] = 'PASS'
        result['checks']['all_controllers_exited'] = 'PASS'
    if clean and all(result['scenarios'][s] == 'PASS' for s in SCENARIOS):
        result['checks']['all_disposable_storage_removed'] = 'PASS'
        result['cleanup'] = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
    else:
        result['cleanup'] = ('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED' if not clean
                             else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if all(v == 'PASS' for v in result['checks'].values()) and clean:
        result['status'] = STATUS_PASS
        result['execution'] = 'FOUR_STARTUP_CANCELLATION_SCENARIOS_VERIFIED'
    elif not clean:
        result['status'] = 'CLEANUP_FAILED'
    else:
        result['status'] = 'STARTUP_CANCELLATION_NOT_VERIFIED'
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5c Gate 2 Windows startup cancellation acceptance')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--out', type=Path)
    p.add_argument('--run-clean-vm', action='store_true')
    p.add_argument('--acknowledge-startup-cancellation', action='store_true')
    p.add_argument('--work-root', type=Path)
    p.add_argument('--_trusted-child', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--_workspace', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--_scenario', choices=SCENARIOS, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args._trusted_child:
        if (args._workspace is None or not args._workspace.is_dir()
                or args._scenario not in SCENARIOS):
            return 2
        return _child(args.vm_config, args._workspace, args._scenario)
    if args.out is None:
        p.error('--out is required')
    try:
        result = run(args.vm_config, execute=args.run_clean_vm,
                     acknowledge=args.acknowledge_startup_cancellation,
                     work_root=args.work_root)
    except Exception as exc:
        result = _base_report()
        result['status'] = 'HOST_GATES_BLOCKED'
        result['failure_code'] = 'TOP_LEVEL_' + type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('ProjectIQ D5c Gate 2 - ' + result['status'])
    print('  Cleanup: ' + result['cleanup'])
    print('  Report: ' + str(args.out))
    return 0 if result['status'] in (STATUS_PASS, 'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
