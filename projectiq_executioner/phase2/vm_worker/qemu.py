"""QEMU native-VM controller: no Docker socket, student subprocesses or host mounts.

Security is conditional on an operator-approved sealed guest image, QEMU build,
network tests and VM boundaries. This is a development provider, not attestation.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorkerFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for piece in iter(lambda: f.read(1024 * 1024), b''):
            h.update(piece)
    return h.hexdigest()


def _tool(name: str, path: str | None) -> str:
    result = path or shutil.which(name)
    if not result or not shutil.which(result) and not Path(result).is_file():
        raise WorkerFailure('Required operator tool unavailable: ' + name)
    return result


def verify_host_toolchain(config: dict, *, run_qemu_help: bool = False) -> dict:
    info = {'toolchain': {}, 'template_sha256_matches': False, 'status': 'NOT_VERIFIED'}
    for name, key in [('qemu-system-x86_64', 'qemu_binary'), ('qemu-img', 'qemu_img_binary'),
                      ('ssh', 'ssh_binary'), ('ssh-keygen', 'ssh_keygen_binary')]:
        try:
            info['toolchain'][name] = _tool(name, config.get(key))
        except WorkerFailure:
            info['toolchain'][name] = None
    base = Path(str(config.get('base_image', ''))).expanduser()
    expected = config.get('base_sha256')
    if base.is_file() and isinstance(expected, str) and len(expected) == 64:
        info['template_sha256_matches'] = sha256_file(base) == expected.lower()
    firmware = config.get('uefi_firmware')
    if firmware:
        fwpath = Path(str(firmware))
        fwsha = config.get('uefi_sha256')
        info['uefi_firmware_sha256_matches'] = (
            fwpath.is_file() and isinstance(fwsha, str) and len(fwsha) == 64
            and sha256_file(fwpath) == fwsha.lower())
    else:
        info['uefi_firmware_sha256_matches'] = None
    firmware_ok = info['uefi_firmware_sha256_matches'] is not False
    info['status'] = ('TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'
                      if all(info['toolchain'].values()) and info['template_sha256_matches']
                      and firmware_ok else 'BLOCKED')
    return info


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _bounded(args: list[str], *, timeout: int = 25, input: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkerFailure('Operator-controlled command unavailable or timed out') from exc


def make_ssh_key(config: dict, workdir: Path) -> tuple[Path, str]:
    key = workdir / 'controller_ed25519'
    result = _bounded([_tool('ssh-keygen', config.get('ssh_keygen_binary')),
                       '-q', '-t', 'ed25519', '-N', '', '-C', 'projectiq-ephemeral', '-f', str(key)])
    if result.returncode != 0:
        raise WorkerFailure('Failed to generate temporary SSH identity')
    pub = key.with_suffix('.pub').read_text('utf-8').strip()
    if not pub.startswith('ssh-ed25519 ') or '\n' in pub:
        raise WorkerFailure('Unexpected SSH key format')
    return key, pub


def make_nocloud_iso(workdir: Path, pubkey: str, instance_id: str, *, template_setup: bool = False) -> Path:
    """Generate a per-run cloud-init seed without executing anything from source.

    Dependency: ``pip install pycdlib`` on the operator host (not inside student
    repositories). A template must include cloud-init and have been cleaned.
    """
    try:
        import pycdlib
    except ImportError as exc:
        raise WorkerFailure('The trusted controller requires pycdlib (pip install pycdlib)') from exc
    if not pubkey.startswith('ssh-ed25519 ') or not instance_id.replace('-', '').isalnum():
        raise WorkerFailure('Invalid generated NoCloud identity')
    sudo_rule = ('ALL=(ALL) NOPASSWD:ALL' if template_setup
                 else 'ALL=(ALL) NOPASSWD: /usr/bin/mysql')
    data = (
        '#cloud-config\n'
        'ssh_pwauth: false\n'
        'disable_root: true\n'
        'ssh_deletekeys: true\n'
        'users:\n'
        '  - name: projectiq\n'
        '    lock_passwd: true\n'
        '    shell: /bin/bash\n'
        f'    sudo: ["{sudo_rule}"]\n'
        '    ssh_authorized_keys:\n'
        f'      - {pubkey}\n'
    ).encode('utf-8')
    metadata = f'instance-id: {instance_id}\nlocal-hostname: projectiq-worker\n'.encode('utf-8')
    iso_path = workdir / 'seed.iso'
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, vol_ident='cidata', joliet=3, rock_ridge='1.09')
    try:
        iso.add_fp(io.BytesIO(data), len(data), iso_path='/USERDATA.;1',
                   rr_name='user-data', joliet_path='/user-data')
        iso.add_fp(io.BytesIO(metadata), len(metadata), iso_path='/METADATA.;1',
                   rr_name='meta-data', joliet_path='/meta-data')
        iso.write(str(iso_path))
    finally:
        iso.close()
    return iso_path


class QemuWorker:
    def __init__(self, config: dict, workdir: Path, port: int, *, ram_mib: int = 1024, cpus: int = 2,
                 trusted_template_setup: bool = False):
        self.config = config
        self.workdir = Path(workdir)
        self.port = port
        self.ram_mib = ram_mib
        self.cpus = cpus
        self.trusted_template_setup = trusted_template_setup
        self.proc: subprocess.Popen | None = None
        self._log = None

    def start(self, seed: Path) -> None:
        base = Path(self.config['base_image']).resolve()
        overlay = self.workdir / 'run.qcow2'
        img = _tool('qemu-img', self.config.get('qemu_img_binary'))
        result = _bounded([img, 'create', '-f', 'qcow2', '-F', 'qcow2',
                           '-b', str(base), str(overlay)], timeout=30)
        if result.returncode != 0:
            raise WorkerFailure('Unable to create disposable QEMU disk overlay')
        if self.trusted_template_setup:
            resized = _bounded([img, 'resize', str(overlay), '12G'], timeout=35)
            if resized.returncode != 0:
                raise WorkerFailure('Unable to expand trusted template disk')
        accelerator = self.config.get('accelerator', 'whpx' if os.name == 'nt' else 'kvm')
        if accelerator not in ('whpx', 'kvm'):
            raise WorkerFailure('Only WHPX (Windows) or KVM (Linux) is enabled')
        if accelerator == 'whpx' and os.name != 'nt':
            raise WorkerFailure('WHPX requires Windows')
        if accelerator == 'kvm' and sys.platform != 'linux':
            raise WorkerFailure('KVM requires Linux')
        self._log = (self.workdir / 'qemu.log').open('wb')
        netdev = ('user,id=net0,hostfwd=tcp:127.0.0.1:' + str(self.port) + '-:22'
                  if self.trusted_template_setup else
                  'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:' + str(self.port) + '-:22')
        args = [
            _tool('qemu-system-x86_64', self.config.get('qemu_binary')),
            '-accel', accelerator, '-m', str(self.ram_mib), '-smp', str(self.cpus),
            '-drive', f'file={overlay},if=virtio,format=qcow2',
            '-drive', f'file={Path(seed).resolve()},media=cdrom,readonly=on',
            '-netdev', netdev,
            '-device', 'virtio-net-pci,netdev=net0',
            '-display', 'none', '-monitor', 'none', '-serial', 'none', '-no-reboot',
        ]
        fw = self.config.get('uefi_firmware')
        if fw:
            path = Path(str(fw)).resolve()
            fwsha = self.config.get('uefi_sha256')
            if not path.is_file() or not isinstance(fwsha, str) or sha256_file(path) != fwsha.lower():
                raise WorkerFailure('Configured UEFI firmware is not pinned or does not match SHA256')
            args.extend(['-bios', str(path)])
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                         stdout=self._log, stderr=subprocess.STDOUT,
                                         cwd=str(self.workdir))
        except OSError as exc:
            raise WorkerFailure('Could not launch the approved QEMU provider') from exc

    def stop(self) -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=8)
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None


class SSHControl:
    """Connect only to the QEMU loopback-forwarded SSH port with a per-run key.

    Host key is accepted at first contact on the per-run forward. This is a
    *development bootstrap trust model*, not remote VM attestation.
    """
    def __init__(self, config: dict, workdir: Path, port: int, key: Path):
        self.port = port
        self.key = key
        self.workdir = workdir
        self.config = config

    def options(self) -> list[str]:
        return ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
                '-o', 'UserKnownHostsFile=' + str(self.workdir / 'known_hosts'),
                '-i', str(self.key)]

    def ssh(self, *remote_argv: str, timeout: int = 20,
            input: bytes | None = None) -> subprocess.CompletedProcess:
        # Remote arguments are controller-owned literals; never pass repository strings.
        cmd = [_tool('ssh', self.config.get('ssh_binary')), *self.options(), '-T', '-p', str(self.port),
               'projectiq@127.0.0.1', *remote_argv]
        return _bounded(cmd, timeout=timeout, input=input)

    def copy(self, local: Path, remote_filename: str, timeout: int = 60) -> None:
        if remote_filename not in {'snapshot.zip', 'guest_agent.py', 'request.json', 'template_seal.sh'}:
            raise WorkerFailure('Unapproved destination filename')
        content = local.read_bytes()
        if len(content) > 30_000_000:
            raise WorkerFailure('Upload exceeds bounded SSH transfer budget')
        command = 'of=/home/projectiq/' + remote_filename
        if self.ssh('dd', command, 'status=none', timeout=timeout, input=content).returncode != 0:
            raise WorkerFailure('Unable to copy trusted input into disposable VM')

    def receive(self, remote_filename: str, local: Path, timeout: int = 30) -> None:
        if remote_filename != 'result.json':
            raise WorkerFailure('Unapproved VM output name')
        result = self.ssh('cat', '/home/projectiq/result.json', timeout=timeout)
        if result.returncode != 0 or len(result.stdout) > 64_000:
            raise WorkerFailure('Invalid or oversized VM report')
        local.write_bytes(result.stdout)

    def wait_ssh(self, vm: QemuWorker, seconds: int = 80) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if vm.proc is None or vm.proc.poll() is not None:
                raise WorkerFailure('Worker VM exited before SSH became ready')
            try:
                if self.ssh('true', timeout=6).returncode == 0:
                    return
            except WorkerFailure:
                pass
            time.sleep(2)
        raise WorkerFailure('Worker VM SSH readiness timed out')
