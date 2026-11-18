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
                   _tool, make_nocloud_iso, make_ssh_key, sha256_file)

SEAL_SCRIPT = r'''#!/bin/sh
set -eu
# Executed as root inside a trusted template setup VM only.
systemctl enable mysql >/dev/null
systemctl start mysql >/dev/null
rm -f /etc/sudoers.d/90-cloud-init-users
# All setup authorization must disappear from the sealed image.
if id projectiq >/dev/null 2>&1; then
  pkill -KILL -u projectiq >/dev/null 2>&1 || true
  userdel -r -f projectiq >/dev/null 2>&1 || true
fi
rm -rf /home/projectiq
cloud-init clean --logs --seed
rm -f /etc/ssh/ssh_host_*
sync
systemctl poweroff
'''


def _ok(ssh: SSHControl, *args: str, timeout: int = 160) -> bytes:
    result = ssh.ssh(*args, timeout=timeout)
    if result.returncode:
        raise WorkerFailure('Trusted guest provisioning step failed: ' + args[0])
    return result.stdout


def _wait_qemu_shutdown(vm: QemuWorker, timeout: int = 80) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if vm.proc is not None and vm.proc.poll() is not None:
            return
        time.sleep(2)
    raise WorkerFailure('Template VM failed to power off after seal request')


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
                        ram_mib=3072, cpus=2, trusted_template_setup=True)
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
            _ok(ssh, 'sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;', timeout=20)
            script = work / 'template_seal.sh'
            script.write_text(SEAL_SCRIPT, encoding='utf-8')
            ssh.copy(script, 'template_seal.sh')
            _ok(ssh, 'sudo', '-n', 'install', '-m', '0700',
                '/home/projectiq/template_seal.sh', '/usr/local/sbin/projectiq-template-seal')
            _ok(ssh, 'sudo', '-n', 'systemd-run', '--unit=projectiq-template-seal',
                '--on-active=4s', '/usr/local/sbin/projectiq-template-seal')
            _wait_qemu_shutdown(vm)
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
                                       check_port, ram_mib=2048, cpus=2)
            try:
                validation_vm.start(check_seed)
                check_ssh = SSHControl(config, validate_root, check_port, check_key)
                check_ssh.wait_ssh(validation_vm, seconds=120)
                _ok(check_ssh, 'python3', '--version')
                if b'pdo_mysql' not in _ok(check_ssh, 'php', '-m').lower():
                    raise WorkerFailure('Sealed worker PHP PDO validation failed')
                _ok(check_ssh, 'sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;')
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
