"""D5d Gate 4: independently inspect the LIVE Windows QEMU process and TCP listener.

One trusted, empty disposable VM. No student source, network dependency fetch,
or claim that inspecting Windows process arguments attests a hypervisor boundary.
The actual process records come from Win32_Process and Get-NetTCPConnection,
not QemuWorker.proc.args or a predicted network_spec() string.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   _tool, effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   sha256_file, verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
SUCCESS = 'D5D_GATE4_LIVE_WINDOWS_QEMU_POSTURE_OBSERVED_NOT_ATTESTED'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
MEMORY_MIB = 1536
WORKSPACE_MIB = 96
WALL_SECONDS = 220
CHECKS = (
    'host_toolchain_and_pinned_base_image',
    'operator_qemu_binary_digest_matches',
    'live_windows_process_identity_and_parent',
    'live_qemu_executable_matches_operator_binary',
    'live_qemu_executable_digest_matches',
    'live_qemu_arguments_match_restricted_policy',
    'host_ssh_listener_owned_by_qemu_loopback_only',
    'live_qemu_in_controller_job',
    'trusted_guest_ssh_reachable',
)

# PowerShell is *only* an independent, read-only Windows process/network sensor.
# Input is a bounded, numeric PID/port environment value, never interpolated
# project source or a user-controlled PowerShell expression.
_WINDOWS_INSPECT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = ConvertFrom-Json -InputObject $env:PROJECTIQ_D5D4_PROCESS_QUERY
$targetPid = [int]$request.pid
$targetPort = [int]$request.port
if ($targetPid -le 0 -or $targetPort -lt 1024 -or $targetPort -gt 65535) { throw 'BAD_QUERY' }
$processes = @(Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction Stop)
if ($processes.Count -ne 1) { throw 'PROCESS_ABSENT_OR_AMBIGUOUS' }
$proc = $processes[0]
$owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwnerSid -ErrorAction Stop
if ([int]$owner.ReturnValue -ne 0 -or [string]::IsNullOrEmpty($owner.Sid)) { throw 'OWNER_UNAVAILABLE' }
$ours = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $targetPort -ErrorAction Stop)
$ports = @()
foreach ($item in $listeners) {
  $ports += [pscustomobject]@{ address = [string]$item.LocalAddress; owner_pid = [int]$item.OwningProcess; local_port = [int]$item.LocalPort }
}
$result = [pscustomobject]@{
  pid = [int]$proc.ProcessId
  parent_pid = [int]$proc.ParentProcessId
  owner_same = [bool]($owner.Sid -eq $ours)
  image = [string]$proc.ExecutablePath
  command_line = [string]$proc.CommandLine
  listeners = @($ports)
}
ConvertTo-Json -InputObject $result -Compress -Depth 5
'''


def _base() -> dict:
    return {
        'schema_version': '0.1', 'stage': 'd5d_gate4_live_windows_qemu_runtime_posture',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'security_attestation': ATTESTATION, 'student_code_executed': False,
        'cleanup': 'NOT_STARTED',
        'checks': {name: 'NOT_VERIFIED' for name in CHECKS},
        'limitations': [
            'OPERATOR_BINARY_HASH_IS_NOT_PUBLISHER_SIGNATURE_OR_PROVENANCE',
            'WINDOWS_PROCESS_ARGUMENTS_ARE_NOT_HYPERVISOR_OR_VT_ATTESTATION',
            'WHPX_AVAILABILITY_AND_LIVE_VM_DO_NOT_PROVE_HYPERVISOR_ESCAPE_RESISTANCE',
            'SAME_WINDOWS_ACCOUNT_CAN_CONTROL_OR_TAMPER_WITH_OPERATOR_RUNS',
            'NO_UNTRUSTED_STUDENT_CODE_EXECUTION_APPROVAL',
        ],
    }


def _valid_pin(value: str) -> bool:
    return type(value) is str and re.fullmatch('[0-9a-fA-F]{64}', value) is not None


def _observed_windows_process(pid: int, port: int) -> dict:
    """Read actual Windows kernel-backed CIM process and TCP endpoint state."""
    if os.name != 'nt' or type(pid) is not int or pid <= 0 or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('WINDOWS_PROCESS_QUERY_INVALID')
    ps = Path(os.environ.get('SystemRoot') or r'C:\Windows') / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    if not ps.is_file():
        raise OSError('TRUSTED_WINDOWS_INSPECTOR_UNAVAILABLE')
    env = os.environ.copy()
    env['PROJECTIQ_D5D4_PROCESS_QUERY'] = json.dumps({'pid': pid, 'port': port})
    encoded = base64.b64encode(_WINDOWS_INSPECT.encode('utf-16-le')).decode('ascii')
    try:
        proc = subprocess.run([str(ps), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                              env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError('WINDOWS_PROCESS_INSPECTOR_UNAVAILABLE') from exc
    if proc.returncode != 0 or not 0 < len(proc.stdout) <= 32_000:
        raise OSError('WINDOWS_PROCESS_INSPECTOR_FAILED')
    try:
        return json.loads(proc.stdout.decode('utf-8-sig', 'strict'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('WINDOWS_PROCESS_INSPECTOR_INVALID_JSON') from exc


def _windows_argv(command_line: str) -> list[str]:
    """Use the Windows argv parser; POSIX shlex is incorrect for Win32 quotes."""
    if os.name != 'nt' or type(command_line) is not str or not 0 < len(command_line) <= 16000:
        raise ValueError('INVALID_WINDOWS_COMMAND_LINE')
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int(0)
    words = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not words:
        raise ValueError('WINDOWS_COMMAND_LINE_PARSE_FAILED')
    try:
        if not 1 <= count.value <= 80:
            raise ValueError('WINDOWS_COMMAND_LINE_ARGUMENT_COUNT')
        return [words[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(words, ctypes.c_void_p))


def _canonical(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _allowed_qemu_argv(config: dict, work: Path, seed: Path, port: int) -> list[str]:
    """Independent, exact runtime policy; never copies Popen.args or worker.network_spec."""
    binary = _tool('qemu-system-x86_64', config.get('qemu_binary'))
    overlay = work / 'run.qcow2'
    workspace = work / 'workspace.raw'
    args = [
        binary, '-accel', 'whpx', '-m', str(MEMORY_MIB), '-smp', '1',
        '-drive', f'file={overlay},if=none,format=qcow2,id=bootdisk',
        '-device', 'virtio-blk-pci,drive=bootdisk,bootindex=1',
        '-drive', f'file={seed.resolve()},media=cdrom,readonly=on',
        '-netdev', f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22',
        '-device', 'virtio-net-pci,netdev=net0',
        '-display', 'none', '-monitor', 'none', '-serial', 'none', '-no-reboot',
        '-drive', f'file={workspace},if=none,format=raw,id=workdisk',
        '-device', 'virtio-blk-pci,drive=workdisk,serial=PIQWORK0001,bootindex=2',
    ]
    if config.get('uefi_firmware'):
        args.extend(['-bios', str(Path(config['uefi_firmware']).resolve())])
    return args


def _validate_snapshot(snapshot: dict, *, process_pid: int, controller_pid: int,
                       port: int, binary: Path, expected: list[str],
                       pin: str, observed_argv: list[str]) -> dict[str, bool]:
    """Pure, fail-closed policy assessment of independent OS observations."""
    if type(snapshot) is not dict or set(snapshot) != {
            'pid', 'parent_pid', 'owner_same', 'image', 'command_line', 'listeners'}:
        raise ValueError('INVALID_WIN32_PROCESS_SNAPSHOT')
    if (type(snapshot['pid']) is not int or type(snapshot['parent_pid']) is not int or
            type(snapshot['owner_same']) is not bool or
            type(snapshot['image']) is not str or not 0 < len(snapshot['image']) <= 2048 or
            type(snapshot['command_line']) is not str or not 0 < len(snapshot['command_line']) <= 16000 or
            type(snapshot['listeners']) is not list or len(snapshot['listeners']) > 12 or
            type(observed_argv) is not list or not 1 <= len(observed_argv) <= 80 or
            any(type(arg) is not str for arg in observed_argv)):
        raise ValueError('INVALID_WIN32_PROCESS_SNAPSHOT_FIELDS')
    listeners = snapshot['listeners']
    for item in listeners:
        if (type(item) is not dict or set(item) != {'address', 'owner_pid', 'local_port'} or
                type(item['address']) is not str or not 0 < len(item['address']) <= 64 or
                type(item['owner_pid']) is not int or type(item['local_port']) is not int):
            raise ValueError('INVALID_LISTENER_RECORD')
    live = snapshot['pid'] == process_pid and snapshot['owner_same'] and snapshot['parent_pid'] == controller_pid
    exact_image = _canonical(snapshot['image']) == _canonical(binary)
    # A different or inaccessible live image is NOT considered a passing hash.
    digest = False
    if exact_image:
        try:
            digest = sha256_file(Path(snapshot['image'])).lower() == pin.lower()
        except OSError:
            pass
    exact_args = (len(observed_argv) == len(expected) and
                  observed_argv[1:] == expected[1:] and
                  observed_argv[0] == expected[0])
    loopback = (len(listeners) == 1 and
                listeners[0]['address'] == '127.0.0.1' and
                listeners[0]['owner_pid'] == process_pid and
                listeners[0]['local_port'] == port)
    return {
        'live_windows_process_identity_and_parent': live,
        'live_qemu_executable_matches_operator_binary': exact_image,
        'live_qemu_executable_digest_matches': digest,
        'live_qemu_arguments_match_restricted_policy': exact_args,
        'host_ssh_listener_owned_by_qemu_loopback_only': loopback,
    }


def run(config: dict, pin: str, *, run_clean_vm: bool = False,
        work_root: Path | None = None) -> dict:
    report = _base()
    if os.name != 'nt':
        report.update(status='HOST_GATES_BLOCKED', failure_code='WINDOWS_WHPX_HOST_REQUIRED')
        return report
    if config.get('accelerator', 'whpx') != 'whpx':
        report.update(status='HOST_GATES_BLOCKED', failure_code='WHPX_PROVIDER_REQUIRED')
        return report
    if not _valid_pin(pin):
        report.update(status='HOST_GATES_BLOCKED', failure_code='OPERATOR_QEMU_SHA256_REQUIRED')
        return report
    try:
        tools = verify_host_toolchain(config)
        if tools['status'] != 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED':
            raise WorkerFailure('HOST_TOOLS_OR_IMAGE_NOT_VERIFIED')
        report['checks']['host_toolchain_and_pinned_base_image'] = 'PASS'
        binary = Path(_tool('qemu-system-x86_64', config.get('qemu_binary'))).resolve(strict=True)
        if not binary.is_file() or sha256_file(binary).lower() != pin.lower():
            raise WorkerFailure('OPERATOR_QEMU_BINARY_DIGEST_MISMATCH')
        report['checks']['operator_qemu_binary_digest_matches'] = 'PASS'
    except (OSError, WorkerFailure, ValueError) as exc:
        report.update(status='HOST_GATES_BLOCKED', failure_code=(
            str(exc) if type(exc) is WorkerFailure and str(exc) in {
                'HOST_TOOLS_OR_IMAGE_NOT_VERIFIED', 'OPERATOR_QEMU_BINARY_DIGEST_MISMATCH'}
            else type(exc).__name__))
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
    expired = threading.Event()
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5d4_clean_', dir=parent) as td:
            workspace = Path(td)
            step = 'TRUSTED_VM_SETUP'
            try:
                key, pubkey = make_ssh_key(config, workspace)
                seed = make_nocloud_iso(
                    workspace, pubkey, 'projectiq-d5d4-' + uuid.uuid4().hex,
                    workspace_disk_mib=WORKSPACE_MIB, app_cgroup=True,
                )
                port = _free_loopback_port()
                worker = QemuWorker({**config, 'workspace_disk_mib': WORKSPACE_MIB},
                                    workspace, port, ram_mib=MEMORY_MIB,
                                    cpus=effective_vm_vcpus(config, 2))
                step = 'QEMU_LAUNCH'
                worker.start(seed)
                vm_process = worker.proc
                if vm_process is None or vm_process.poll() is not None:
                    raise WorkerFailure('QEMU_EXITED_DURING_LAUNCH')
                report['execution'] = 'TRUSTED_WINDOWS_CLEAN_VM_BOOT_REQUESTED'

                def timeout_vm():
                    expired.set()
                    if worker is not None and worker.proc is not None and worker.proc.poll() is None:
                        worker.proc.kill()

                watchdog = threading.Timer(WALL_SECONDS, timeout_vm)
                watchdog.daemon = True
                watchdog.start()
                from .d5c_windows_job import verify_child_in_job
                if verify_child_in_job(vm_process):
                    report['checks']['live_qemu_in_controller_job'] = 'PASS'
                # Successful SSH here proves the VM reached a usable boot state,
                # not that hardware acceleration or isolation has been attested.
                step = 'TRUSTED_GUEST_SSH_BOOT'
                SSHControl(config, workspace, port, key).wait_ssh(worker, seconds=105)
                if vm_process.poll() is not None:
                    raise WorkerFailure('QEMU_EXITED_BEFORE_HOST_INSPECTION')
                step = 'WINDOWS_PROCESS_AND_TCP_SNAPSHOT'
                snapshot = _observed_windows_process(vm_process.pid, port)
                step = 'WINDOWS_COMMAND_LINE_PARSER'
                argv = _windows_argv(snapshot.get('command_line')) if type(snapshot) is dict else []
                step = 'INDEPENDENT_LIVE_RUNTIME_POLICY'
                findings = _validate_snapshot(
                    snapshot, process_pid=vm_process.pid, controller_pid=os.getpid(),
                    port=port, binary=binary, expected=_allowed_qemu_argv(config, workspace, seed, port),
                    pin=pin, observed_argv=argv,
                )
                for name, good in findings.items():
                    report['checks'][name] = 'PASS' if good else 'NOT_VERIFIED'
                if vm_process.poll() is None:
                    report['checks']['trusted_guest_ssh_reachable'] = 'PASS'
                report['execution'] = 'TRUSTED_LIVE_WINDOWS_QEMU_POSTURE_OBSERVED'
                report['host_diagnostics'] = {
                    'process_identity': 'MATCH' if findings['live_windows_process_identity_and_parent'] else 'MISMATCH',
                    'image_identity': 'MATCH' if findings['live_qemu_executable_matches_operator_binary'] else 'MISMATCH',
                    'binary_digest': 'MATCH' if findings['live_qemu_executable_digest_matches'] else 'MISMATCH',
                    'argv_policy': 'EXACT' if findings['live_qemu_arguments_match_restricted_policy'] else 'MISMATCH',
                    'ssh_listener': 'LOOPBACK_QEMU_OWNED' if findings['host_ssh_listener_owned_by_qemu_loopback_only'] else 'NOT_VERIFIED',
                }
            except Exception as exc:
                # Never put raw PowerShell stderr, command lines, paths, SIDs or
                # exception messages into the persisted acceptance JSON.
                error = 'D5D4_' + type(exc).__name__.upper()
                report['failed_step'] = step
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
        error = 'D5D4_TEMP_STORAGE_CLEANUP_FAILED'
    report['cleanup'] = (CLEAN if (workspace is None or not workspace.exists()) and
                         (worker is None or stopped)
                         else 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
    if expired.is_set():
        error = 'D5D4_VM_WALL_TIMEOUT'
    if error:
        report['status'] = 'D5D_GATE4_HOST_INSPECTION_ERROR'
        report['failure_code'] = error
    elif all(v == 'PASS' for v in report['checks'].values()):
        report['status'] = SUCCESS
    else:
        report['status'] = 'D5D_GATE4_LIVE_HOST_POSTURE_INCOMPLETE'
    if report['cleanup'] != CLEAN:
        report['status'] = 'CLEANUP_FAILED'
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Gate 4: independently audit live Windows QEMU runtime posture')
    ap.add_argument('--vm-config', required=True, type=Path)
    ap.add_argument('--qemu-sha256', required=True, help='Operator-reviewed SHA256 of installed QEMU executable')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--run-clean-vm', action='store_true', help='Opt-in to exactly one clean VM; no project source')
    ap.add_argument('--work-root', type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(load_config(args.vm_config), args.qemu_sha256,
                     run_clean_vm=args.run_clean_vm, work_root=args.work_root)
    except (OSError, ValueError, WorkerFailure) as exc:
        result = _base()
        result.update(status='HOST_GATES_BLOCKED', failure_code=type(exc).__name__)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('ProjectIQ D5d Gate 4 - ' + result['status'])
    print('  Report: ' + str(args.out))
    return 0 if result['status'] in (SUCCESS, 'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
