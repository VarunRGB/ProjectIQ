"""One-time trusted Ubuntu VM template baker (NO student repository is present).

Explicitly permits networking ONLY during trusted OS package preparation.
The resulting sealed QCOW2 image is tested with the production network-denied
VM mode before being pinned. Requires operator-supplied official cloud image.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .qemu import (QemuWorker, SSHControl, WorkerFailure, _bounded, _free_loopback_port,
                   _tool, effective_vm_vcpus, make_nocloud_iso, make_ssh_key, sha256_file)

SEAL_SCRIPT = r'''#!/bin/sh
set -eu
# Executed as root inside a trusted template setup VM only.
# Report fixed milestone names to the trusted VM's private serial log. Never
# include credentials or student data. The host requires the last milestone
# before it can accept this image for sealed-template validation.
seal_stage() {
  printf 'PROJECTIQ_SEAL_STAGE:%s\n' "$1" > /dev/ttyS0 2>/dev/null || true
}
seal_stage started
# Root-owned transient service runs separately from the SSH login. Allow
# systemd-run to return before the seal deletes the projectiq user.
sleep 4
systemctl enable mysql >/dev/null
systemctl start mysql >/dev/null
seal_stage mysql_started
rm -f /etc/sudoers.d/90-cloud-init-users
seal_stage sudoers_removed
# All setup authorization must disappear from the sealed image.
if id projectiq >/dev/null 2>&1; then
  pkill -KILL -u projectiq >/dev/null 2>&1 || true
  userdel -r -f projectiq >/dev/null 2>&1 || true
fi
rm -rf /home/projectiq
seal_stage projectiq_removed
cloud-init clean --logs --seed
seal_stage cloud_init_cleaned
rm -f /etc/ssh/ssh_host_*
seal_stage ssh_host_keys_removed
sync
seal_stage poweroff_requested
systemctl poweroff
'''


def _write_seal_script(path: Path) -> str:
    """Write POSIX-executable bytes without Windows newline translation.

    A CRLF shebang (``#!/bin/sh\r\n``) fails as systemd 203/EXEC on Linux.
    The trusted script must have LF-only line endings on every host OS.
    """
    content = SEAL_SCRIPT.encode('utf-8')
    if not content.startswith(b'#!/bin/sh\n') or b'\r' in content:
        raise WorkerFailure('Trusted seal script must have a POSIX LF shebang')
    path.write_bytes(content)
    return sha256_file(path)


def _ok(ssh: SSHControl, *args: str, timeout: int = 160) -> bytes:
    result = ssh.ssh(*args, timeout=timeout)
    if result.returncode:
        # All arguments here are fixed, trusted provisioning commands (never
        # student-supplied shell). Report the failed operation and bounded
        # output so Windows/WHPX failures can be diagnosed without bypassing
        # security checks or retaining VM SSH keys and temporary disk images.
        command = ' '.join(args)
        stderr = result.stderr.decode('utf-8', errors='replace').strip()[-1800:]
        stdout = result.stdout.decode('utf-8', errors='replace').strip()[-600:]
        raise WorkerFailure(
            f'Trusted guest provisioning failed (exit={result.returncode}) '
            f'at: {command}\n'
            f'stderr (last 1800 chars): {stderr or "<empty>"}\n'
            f'stdout (last 600 chars): {stdout or "<empty>"}'
        )
    return result.stdout



def _mysql_guest_diagnostics(ssh: SSHControl) -> str:
    """Collect bounded service evidence from the *trusted* setup guest only."""
    checks = (
        ('service', ('systemctl', 'show', 'mysql.service',
                     '--property=ActiveState', '--property=SubState',
                     '--property=Result', '--property=ExecMainStatus',
                     '--property=MainPID')),
        ('journal', ('sudo', '-n', 'journalctl', '-u', 'mysql.service',
                     '--no-pager', '--lines=24', '--output=short')),
        ('socket', ('sudo', '-n', 'ls', '-ld', '/run/mysqld')),
    )
    evidence = []
    for label, command in checks:
        try:
            result = ssh.ssh(*command, timeout=12)
            output = ((result.stdout or b'') + b'\n' + (result.stderr or b'')).decode(
                'utf-8', errors='replace').strip()
            evidence.append(f'{label} (exit={result.returncode}): {output[-1600:] or "<empty>"}')
        except (WorkerFailure, OSError) as exc:
            evidence.append(f'{label}: unavailable ({type(exc).__name__})')
    return '\n'.join(evidence)


def _wait_mysql_ready(ssh: SSHControl, *, seconds: int = 60) -> None:
    """Require a working local MySQL query before sealing or accepting a VM.

    systemctl start can return before a database is ready, or mysqld may exit
    shortly afterward. Neither condition is a reason to skip the database gate.
    """
    if seconds <= 0:
        raise WorkerFailure('MySQL readiness window must be positive')
    deadline = time.monotonic() + seconds
    last_error = '<no attempt>'
    while True:
        try:
            result = ssh.ssh('sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;', timeout=12)
            if result.returncode == 0:
                return
            detail = (result.stderr or b'').decode('utf-8', errors='replace').strip()
            last_error = f'exit={result.returncode}: {detail[-500:] or "<no stderr>"}'
        except (WorkerFailure, OSError) as exc:
            last_error = f'{type(exc).__name__}: {str(exc)[-300:]}'
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(3.0, remaining))
    details = _mysql_guest_diagnostics(ssh)
    raise WorkerFailure(
        f'Trusted guest MySQL never became ready within {seconds}s '
        f'(last probe: {last_error}).\n{details}'
    )


def _seal_progress(vm: QemuWorker) -> list[str]:
    """Read only known milestone tokens from the trusted setup guest console."""
    serial = vm.workdir / 'guest-serial.log'
    try:
        output = serial.read_text('utf-8', errors='replace')
    except OSError:
        return []
    milestones = ('started', 'mysql_started', 'sudoers_removed',
                  'projectiq_removed', 'cloud_init_cleaned',
                  'ssh_host_keys_removed', 'poweroff_requested')
    return [stage for stage in milestones
            if 'PROJECTIQ_SEAL_STAGE:' + stage in output]


def _check_seal_serial(ssh: SSHControl, vm: QemuWorker) -> None:
    """Prove the guest's serial-stage channel works before destructive sealing."""
    _ok(ssh, 'sudo', '-n', '/usr/bin/sh', '-c',
        "printf 'PROJECTIQ_SEAL_DIAG:serial_ready\\n' > /dev/ttyS0", timeout=12)
    end = time.monotonic() + 8
    while time.monotonic() < end:
        try:
            if 'PROJECTIQ_SEAL_DIAG:serial_ready' in (
                    vm.workdir / 'guest-serial.log').read_text('utf-8', errors='replace'):
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise WorkerFailure('Guest serial seal-stage channel unavailable; refusing to seal')


def _launch_seal(ssh: SSHControl) -> None:
    """Ask PID 1 to run the trusted seal independently of SSH or guest timers."""
    _ok(ssh, 'sudo', '-n', 'systemd-run',
        '--unit=projectiq-template-seal', '--no-block', '--service-type=exec',
        '/usr/local/sbin/projectiq-template-seal', timeout=20)


def _seal_unit_status(ssh: SSHControl | None) -> str:
    if ssh is None:
        return 'not requested'
    try:
        result = ssh.ssh(
            'sudo', '-n', 'systemctl', 'show', 'projectiq-template-seal.service',
            '--property=ActiveState', '--property=SubState', '--property=Result',
            '--property=ExecMainStatus', timeout=9)
    except (WorkerFailure, OSError):
        return 'SSH unavailable'
    if result.returncode:
        return f'systemctl failed (exit={result.returncode})'
    return result.stdout.decode('utf-8', errors='replace').strip().replace('\n', ', ')[:350]


def _wait_qemu_shutdown(vm: QemuWorker, timeout: int = 150,
                        *, ssh: SSHControl | None = None) -> None:
    # QEMU exit alone does not prove the seal script ran. Require a known
    # milestone before proceeding to the second-boot restricted-sudo check.
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if vm.proc is not None and vm.proc.poll() is not None:
            progress = _seal_progress(vm)
            if vm.proc.returncode != 0 or 'poweroff_requested' not in progress:
                raise WorkerFailure('Template VM exited without confirmed seal completion '
                                    f'(exit={vm.proc.returncode}, stages={progress})')
            return
        time.sleep(2)
    progress = _seal_progress(vm)
    status = _seal_unit_status(ssh)
    raise WorkerFailure('Template VM did not power off after the seal request '
                        f'(last confirmed stage: {progress[-1] if progress else "none"}; '
                        f'timeout={timeout}s; seal unit: {status})')


def bake_template(source_image: Path, image_sha256: str, target: Path, config: dict,
                  *, explicitly_allow_setup_network: bool = False) -> dict:
    if not explicitly_allow_setup_network:
        raise WorkerFailure('Trusted-image setup network must be explicitly authorized')
    source_image = Path(source_image).resolve()
    target = Path(target).resolve()
    if not source_image.is_file() or sha256_file(source_image) != image_sha256.lower():
        raise WorkerFailure('Official Ubuntu cloud image hash does not match expected SHA256')
    if target.exists() or target == source_image:
        raise WorkerFailure('Will not overwrite an existing or original image')
    target.parent.mkdir(parents=True, exist_ok=True)
    image_tool = _tool('qemu-img', config.get('qemu_img_binary'))
    summary = {'status': 'NOT_VERIFIED', 'output_image': str(target), 'sha256': None,
               'trusted_setup_network_only': True, 'production_guest_egress_default': 'DENY'}
    with tempfile.TemporaryDirectory(prefix='projectiq_template_', dir=target.parent) as folder:
        work = Path(folder)
        key, pubkey = make_ssh_key(config, work)
        seed = make_nocloud_iso(work, pubkey, 'projectiq-template-' + uuid.uuid4().hex,
                                template_setup=True)
        port = _free_loopback_port()
        vm = QemuWorker({'base_image': str(source_image), **config}, work, port,
                        ram_mib=3072, cpus=effective_vm_vcpus(config, 2), trusted_template_setup=True)
        try:
            vm.start(seed)
            ssh = SSHControl(config, work, port, key)
            ssh.wait_ssh(vm, seconds=120)
            # This is operator-managed Ubuntu package installation before any
            # student snapshot is downloaded or transferred to a VM.
            _ok(ssh, 'sudo', '-n', 'apt-get', 'update', timeout=240)
            _ok(ssh, 'sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive',
                'apt-get', 'install', '-y', '--no-install-recommends',
                'openssh-server', 'cloud-init', 'python3', 'php-cli',
                'php-mysql', 'mysql-server', 'sudo', 'ca-certificates', timeout=300)
            _ok(ssh, 'sudo', '-n', 'systemctl', 'enable', 'mysql')
            _ok(ssh, 'sudo', '-n', 'systemctl', 'start', 'mysql')
            _ok(ssh, 'sudo', '-n', 'apt-get', 'clean', timeout=30)
            check = _ok(ssh, '/usr/bin/php', '-m').lower()
            if b'pdo_mysql' not in check:
                raise WorkerFailure('PHP PDO MySQL extension missing in trusted template')
            _wait_mysql_ready(ssh, seconds=60)
            script = work / 'template_seal.sh'
            script_sha256 = _write_seal_script(script)
            ssh.copy(script, 'template_seal.sh')
            _ok(ssh, 'sudo', '-n', 'install', '-m', '0700',
                '/home/projectiq/template_seal.sh', '/usr/local/sbin/projectiq-template-seal')
            # Detect corrupt/translated transfer *before* launching a service
            # that deletes its own setup user and eventually powers off the VM.
            installed_sha256 = _ok(
                ssh, 'sudo', '-n', '/usr/bin/sha256sum',
                '/usr/local/sbin/projectiq-template-seal', timeout=15
            ).split(maxsplit=1)[0].decode('ascii', errors='replace').lower()
            if installed_sha256 != script_sha256:
                raise WorkerFailure('Installed trusted seal script differs from LF-encoded source')
            # Timers can be unreliable on this WHPX pilot. Launch a root-owned,
            # independent system service instead; its script waits briefly
            # before deleting the SSH login. Verify stage-channel observability
            # first and still require a confirmed seal and clean VM exit.
            _check_seal_serial(ssh, vm)
            _launch_seal(ssh)
            _wait_qemu_shutdown(vm, ssh=ssh)
        finally:
            vm.stop()
        staged = work / 'sealed_candidate.qcow2'
        converted = _bounded([image_tool, 'convert', '-f', 'qcow2', '-O', 'qcow2',
                              str(work / 'run.qcow2'), str(staged)], timeout=180)
        if converted.returncode != 0 or not staged.is_file():
            raise WorkerFailure('Trusted template disk conversion failed')
        # Reboot with a fresh seed, no internet, and a *restricted* sudo profile.
        with tempfile.TemporaryDirectory(prefix='projectiq_validation_', dir=work) as verified_path:
            validate_root = Path(verified_path)
            check_key, check_pub = make_ssh_key(config, validate_root)
            check_seed = make_nocloud_iso(validate_root, check_pub,
                                          'projectiq-verify-' + uuid.uuid4().hex)
            check_port = _free_loopback_port()
            validation_vm = QemuWorker({'base_image': str(staged), **config}, validate_root,
                                       check_port, ram_mib=2048, cpus=effective_vm_vcpus(config, 2))
            try:
                validation_vm.start(check_seed)
                check_ssh = SSHControl(config, validate_root, check_port, check_key)
                check_ssh.wait_ssh(validation_vm, seconds=120)
                _ok(check_ssh, 'python3', '--version')
                if b'pdo_mysql' not in _ok(check_ssh, 'php', '-m').lower():
                    raise WorkerFailure('Sealed worker PHP PDO validation failed')
                _wait_mysql_ready(check_ssh, seconds=60)
                # Regression gate: the privileged bootstrap sudo ALL capability
                # must NOT survive into the runtime image.
                if check_ssh.ssh('sudo', '-n', '/usr/bin/id').returncode == 0:
                    raise WorkerFailure('Template sealing failed: runtime user has unrestricted sudo')
            finally:
                validation_vm.stop()
        shutil.move(str(staged), str(target))
        summary['sha256'] = sha256_file(target)
        summary['status'] = 'SEALED_TEMPLATE_BOOT_VALIDATED_NOT_PRODUCTION_ATTESTED'
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description='Bake a trusted Ubuntu QCOW2 worker template (NO student code)')
    p.add_argument('--ubuntu-image', type=Path, required=True)
    p.add_argument('--ubuntu-sha256', required=True)
    p.add_argument('--out-template', type=Path, required=True)
    p.add_argument('--qemu-binary')
    p.add_argument('--qemu-img-binary')
    p.add_argument('--accelerator', choices=['whpx', 'kvm'])
    p.add_argument('--uefi-firmware', help='Optional UEFI firmware ROM path, if cloud image requires UEFI')
    p.add_argument('--uefi-sha256', help='Pin the SHA256 of --uefi-firmware')
    p.add_argument('--allow-trusted-template-network', action='store_true',
                   help='Explicitly allow internet ONLY while baking trusted OS packages')
    args = p.parse_args()
    config = {k: getattr(args, k) for k in ('qemu_binary', 'qemu_img_binary', 'accelerator', 'uefi_firmware', 'uefi_sha256')
              if getattr(args, k) is not None}
    try:
        report = bake_template(args.ubuntu_image, args.ubuntu_sha256,
                               args.out_template, config,
                               explicitly_allow_setup_network=args.allow_trusted_template_network)
    except WorkerFailure as exc:
        p.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
