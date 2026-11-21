"""D5d Gate 3: independent clean-VM storage and local-host network test.

No student code, external Internet request, or host mount is involved.
A host-controlled loopback listener is used to detect slirp guest-to-host
access; the guest tests bounded workspace and non-workspace write permission.
This is an observation stage, NOT a go decision for untrusted projects.
"""
from __future__ import annotations

import argparse
import json
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .d5d_boundary_guest_probe import FIELDS, NETWORK_STATES, WRITE_STATES
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
WALL_SECONDS = 245
STATUS_PASS = 'D5D_GATE3_STORAGE_NETWORK_BOUNDARIES_OBSERVED_NOT_ATTESTED'
STATUS_GAP = 'D5D_GATE3_NONWORKSPACE_GUEST_WRITES_OBSERVED'
STATUS_INCOMPLETE = 'D5D_GATE3_BOUNDARY_EVIDENCE_INCOMPLETE'
HOST_CHECKS = ('restricted_network_spec', 'host_loopback_sentinel_not_reached')
ALL_CHECKS = FIELDS + HOST_CHECKS


def _base() -> dict:
    return {
        'schema_version': '0.1', 'stage': 'd5d_gate3_storage_network_boundary_baseline',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED', 'student_code_executed': False,
        'security_attestation': ATTESTATION,
        'checks': {key: 'NOT_VERIFIED' for key in ALL_CHECKS},
        'limitations': [
            'ROOT_OWNED_APP_UID_ACL_ENFORCEMENT_NOT_FULL_MOUNT_NAMESPACE',
            'ROOT_QCOW2_SIZE_AND_ALL_GUEST_WRITES_NOT_GLOBALLY_ATTESTED',
            'SINGLE_HOST_LOOPBACK_SENTINEL_DOES_NOT_PROVE_ALL_EGRESS_BLOCKED',
            'NO_UNTRUSTED_STUDENT_EXECUTION_APPROVAL',
        ],
    }


def _parse_guest(data: bytes) -> dict | None:
    if type(data) is not bytes or not 0 < len(data) <= 2500:
        return None
    try:
        obj = json.loads(data.decode('utf-8', 'strict'))
        if type(obj) is not dict or set(obj) != {'schema', 'observations', 'diagnostics'} or obj['schema'] != 'd5d-boundary-1':
            return None
        checks, diag = obj['observations'], obj['diagnostics']
        if (type(checks) is not dict or set(checks) != set(FIELDS) or
                any(type(v) is not bool for v in checks.values())):
            return None
        required = {'workspace_write', 'root_tmp_write', 'var_tmp_write', 'root_home_write',
                    'gateway_connect', 'storage_guard_bootstrap'}
        if (type(diag) is not dict or set(diag) != required or
                any(type(diag[key]) is not str for key in required) or
                any(diag[key] not in WRITE_STATES for key in required - {'gateway_connect', 'storage_guard_bootstrap'}) or
                diag['gateway_connect'] not in NETWORK_STATES or
                diag['storage_guard_bootstrap'] not in ('NOT_RUN', 'READY', 'MISSING', 'UNTRUSTED')):
            return None
        if checks['trusted_storage_guard_bootstrap'] != (diag['storage_guard_bootstrap'] == 'READY'):
            return None
        if checks['app_can_write_workspace'] != (diag['workspace_write'] == 'CREATED_CLEANED'):
            return None
        if checks['app_root_tmp_write_denied'] != (diag['root_tmp_write'] == 'DENIED'):
            return None
        if checks['app_var_tmp_write_denied'] != (diag['var_tmp_write'] == 'DENIED'):
            return None
        if checks['app_root_home_write_denied'] != (diag['root_home_write'] == 'DENIED'):
            return None
        if checks['host_gateway_connection_denied'] != (diag['gateway_connect'] == 'DENIED' and
                                                         checks['guest_network_interface_present']):
            return None
        if any(checks[key] for key in FIELDS[1:]) and not checks['controller_identity']:
            return None
        if any(checks[k] for k in (
                'bounded_workspace_disk', 'distinct_root_and_workspace_devices')) and not checks['trusted_workspace_bootstrap']:
            return None
        if any(diag[k] != 'NOT_RUN' for k in ('workspace_write', 'root_tmp_write', 'var_tmp_write', 'root_home_write')) and not all(
                checks[k] for k in ('trusted_workspace_bootstrap', 'bounded_workspace_disk',
                                    'distinct_root_and_workspace_devices')):
            return None
        if (not checks['trusted_storage_guard_bootstrap'] and any(
                diag[k] != 'NOT_RUN' for k in (
                    'workspace_write', 'root_tmp_write', 'var_tmp_write', 'root_home_write'))):
            return None
        if checks['host_gateway_connection_denied'] and not checks['guest_network_interface_present']:
            return None
        return obj
    except (KeyError, ValueError, TypeError, UnicodeError):
        return None


def _classify(report: dict) -> None:
    checks = report['checks']
    # An actual successful non-workspace write is affirmative evidence of a
    # gap. Missing/error-only evidence must never be labelled an observed gap.
    diag = report.get('guest_diagnostics', {})
    if any(diag.get(k) == 'CREATED_CLEANED' for k in ('root_tmp_write', 'var_tmp_write', 'root_home_write')):
        report['status'] = STATUS_GAP
        return
    if all(v == 'PASS' for v in checks.values()):
        report['status'] = STATUS_PASS
    else:
        report['status'] = STATUS_INCOMPLETE


def _sentinel_loop(sock: socket.socket, stop: threading.Event, contacted: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        contacted.set()
        with conn:
            try:
                conn.settimeout(0.5)
                conn.recv(128)
            except OSError:
                pass


def run(config: dict, *, run_clean_vm: bool = False, work_root: Path | None = None) -> dict:
    report = _base()
    tools = verify_host_toolchain(config)
    if tools['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
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
    vm_process = None
    stopped = False
    watchdog = None
    timed_out = threading.Event()
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5d3_clean_', dir=parent) as temp:
            workspace = Path(temp)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sentinel:
                    sentinel.bind(('127.0.0.1', 0))
                    sentinel.listen(2)
                    sentinel.settimeout(0.2)
                    sentinel_port = sentinel.getsockname()[1]
                    stop_listener = threading.Event()
                    contacted = threading.Event()
                    listener = threading.Thread(
                        target=_sentinel_loop, args=(sentinel, stop_listener, contacted), daemon=True)
                    listener.start()
                    try:
                        # Positive host-side control: prove the listener can
                        # detect a connection before asserting guest denial.
                        with socket.create_connection(('127.0.0.1', sentinel_port),
                                                      timeout=2) as control:
                            control.sendall(b'projectiq-d5d3-host-control\n')
                        if not contacted.wait(timeout=2):
                            raise WorkerFailure('Host sentinel positive control failed')
                        contacted.clear()
                        key, pubkey = make_ssh_key(config, workspace)
                        seed = make_nocloud_iso(
                            workspace, pubkey, 'projectiq-d5d3-' + uuid.uuid4().hex,
                            workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True,
                        )
                        ssh_port = _free_loopback_port()
                        worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                                            workspace, ssh_port, ram_mib=VM_RAM_MIB,
                                            cpus=effective_vm_vcpus(config, 2))
                        expected = f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{ssh_port}-:22'
                        if worker.network_spec() != expected:
                            raise WorkerFailure('Expected restricted user networking')
                        report['checks']['restricted_network_spec'] = 'PASS'
                        worker.start(seed)
                        vm_process = worker.proc
                        if vm_process is None or vm_process.poll() is not None:
                            raise WorkerFailure('Disposable VM exited before probe')
                        report['execution'] = 'TRUSTED_CLEAN_VM_BOOT_REQUESTED'

                        def expire() -> None:
                            timed_out.set()
                            if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                                worker.proc.kill()

                        watchdog = threading.Timer(WALL_SECONDS, expire)
                        watchdog.daemon = True
                        watchdog.start()
                        ssh = SSHControl(config, workspace, ssh_port, key)
                        ssh.wait_ssh(worker, seconds=105)
                        source = Path(__file__).with_name('d5d_boundary_guest_probe.py').read_bytes()
                        if not 0 < len(source) <= 22_000:
                            raise WorkerFailure('Unexpected guest boundary probe size')
                        # The guest waits for the *evaluation* bootstrap, not merely
                        # the earlier workspace mount. Give that bounded wait time.
                        reply = ssh.ssh('python3', '-', str(sentinel_port), input=source, timeout=115)
                        observation = _parse_guest(reply.stdout) if reply.returncode == 0 else None
                        if observation is None:
                            error = 'INVALID_OR_FAILED_GUEST_BOUNDARY_PROBE'
                        else:
                            for key in FIELDS:
                                report['checks'][key] = 'PASS' if observation['observations'][key] else 'NOT_VERIFIED'
                            report['guest_diagnostics'] = observation['diagnostics']
                            report['execution'] = 'TRUSTED_STORAGE_NETWORK_PROBE_IN_CLEAN_VM'
                            time.sleep(0.1)  # Flush any last accepted connection before classification.
                            if (not contacted.is_set() and
                                    observation['observations']['guest_network_interface_present'] and
                                    observation['observations']['host_gateway_connection_denied']):
                                report['checks']['host_loopback_sentinel_not_reached'] = 'PASS'
                            if vm_process.poll() is not None:
                                error = 'VM_EXITED_DURING_PROBE'
                    finally:
                        stop_listener.set()
                        listener.join(timeout=2)
            except Exception as exc:
                error = 'D5D3_PROBE_' + type(exc).__name__.upper()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if worker is not None:
                    try:
                        worker.stop()
                        stopped = vm_process is None or vm_process.poll() is not None
                    except Exception:
                        stopped = False
    except OSError:
        error = 'D5D3_TEMP_STORAGE_CLEANUP_FAILED'
    report['cleanup'] = (CLEAN if not (workspace is not None and workspace.exists())
                         and (worker is None or stopped)
                         else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
    if timed_out.is_set():
        error = 'D5D3_VM_WALL_TIMEOUT'
    if error:
        report['status'] = 'D5D_GATE3_PROBE_ERROR'
        report['failure_code'] = error
    else:
        _classify(report)
    if report['cleanup'] != CLEAN:
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='D5d Gate 3 source-free storage/network boundary baseline')
    p.add_argument('--vm-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--run-clean-vm', action='store_true', help='Boot exactly ONE trusted clean VM')
    p.add_argument('--work-root', type=Path)
    args = p.parse_args(argv)
    try:
        report = run(load_config(args.vm_config), run_clean_vm=args.run_clean_vm,
                     work_root=args.work_root)
    except (OSError, ValueError, WorkerFailure) as exc:
        report = _base()
        report.update(status='HOST_GATES_BLOCKED', failure_code=type(exc).__name__)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('ProjectIQ D5d Gate 3 - ' + report['status'])
    print('  Report: ' + str(args.out))
    return 0 if report['status'] in (STATUS_PASS, 'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
