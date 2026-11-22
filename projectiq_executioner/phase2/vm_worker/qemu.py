"""QEMU native-VM controller: no Docker socket, student subprocesses or host mounts.

Security is conditional on an operator-approved sealed guest image, QEMU build,
network tests and VM boundaries. This is a development provider, not attestation.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import shlex
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



def effective_vm_vcpus(config: dict, requested: int) -> int:
    """Use a single vCPU for WHPX until SMP boot works on the validated host.

    The Windows WHPX pilot booted Ubuntu with one vCPU, while its two-vCPU
    boot stalled during secondary CPU startup. This lowers (never raises)
    the candidate's CPU allocation and leaves Linux/KVM limits unchanged.
    Reassess with a real two-vCPU boot before removing this workaround.
    """
    if type(requested) is not int or requested < 1:
        raise WorkerFailure('VM vCPU request must be a positive integer')
    accelerator = config.get('accelerator') or ('whpx' if os.name == 'nt' else 'kvm')
    return 1 if accelerator == 'whpx' else requested


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


# This code is operator-owned and runs as root ONCE through cloud-init, before
# any reviewed project archive is transferred. No project bytes or shell input
# are embedded in the script. It provisions only a throwaway DB-scoped account.
MYSQL_BOOTSTRAP_SCRIPT = r'''#!/bin/sh
set -eu
umask 077
# Apply root-owned service limits before generating any application credentials.
# A failed install, restart, or kernel check prevents the bootstrap from
# publishing the DB-scoped user secret, so the PHP adapter cannot proceed.
/bin/mkdir -p /etc/systemd/system/mysql.service.d
/bin/cat > /etc/systemd/system/mysql.service.d/90-projectiq-d5d-limits.conf <<'MYSQL_LIMITS'
[Service]
MemoryAccounting=yes
MemoryMax=536870912
TasksAccounting=yes
TasksMax=64
IOAccounting=yes
IOWriteBandwidthMax=/var/lib/mysql 8388608
MYSQL_LIMITS
/usr/bin/timeout 30 /usr/bin/systemctl daemon-reload
/usr/bin/timeout 90 /usr/bin/systemctl restart mysql.service
/usr/bin/python3 - <<'MYSQL_KERNEL_VERIFY'
import os
import pwd
import re
import subprocess
from pathlib import Path

def property_value(prop):
    return subprocess.run(['/usr/bin/systemctl', 'show', '-P', prop, 'mysql.service'],
                          check=True, capture_output=True, text=True, timeout=8).stdout.strip()

def require(condition):
    # Explicit check: `assert` can be disabled by Python optimization flags.
    if not condition:
        raise SystemExit(1)

require(property_value('ActiveState') == 'active')
require(property_value('ControlGroup') == '/system.slice/mysql.service')
pid_text = property_value('MainPID')
require(pid_text.isascii() and pid_text.isdecimal() and int(pid_text) > 1)
pid = int(pid_text)
cgroup = Path('/sys/fs/cgroup/system.slice/mysql.service')
require(cgroup.is_dir() and not cgroup.is_symlink())
require((Path('/proc') / str(pid) / 'cgroup').read_text('ascii').splitlines() == [
    '0::/system.slice/mysql.service'])
require(pid_text in (cgroup / 'cgroup.procs').read_text('ascii').split())
status = (Path('/proc') / str(pid) / 'status').read_text('utf-8')
uids = [line.split()[1:] for line in status.splitlines() if line.startswith('Uid:')]
mysql_uid = pwd.getpwnam('mysql').pw_uid
require(mysql_uid not in (0, os.getuid()))
require(len(uids) == 1 and len(uids[0]) == 4)
require(all(x.isdecimal() and int(x) == mysql_uid for x in uids[0]))

def bounded(key, limit):
    raw = (cgroup / key).read_text('ascii').strip()
    return raw.isascii() and raw.isdecimal() and 0 < int(raw) <= limit

require(bounded('memory.max', 536870912))
require(bounded('pids.max', 64))
rootdev = Path('/var/lib/mysql').stat().st_dev
device = f'{os.major(rootdev)}:{os.minor(rootdev)}'
targets = {device}
# On a partition-backed root disk, the io.max key may be the whole disk.
# Never accept a write cap on an unrelated device.
try:
    mapped = (Path('/sys/dev/block') / device).resolve(strict=True)
    if mapped.is_relative_to(Path('/sys/devices')):
        part = (mapped / 'partition').read_text('ascii').strip()
        parent_dev = (mapped.parent / 'dev').read_text('ascii').strip()
        if (part.isascii() and part.isdecimal() and int(part) > 0 and
                re.fullmatch(r'[0-9]{1,5}:[0-9]{1,7}', parent_dev)):
            targets.add(parent_dev)
except (OSError, UnicodeError, ValueError, RuntimeError):
    pass
io_lines = [line.split() for line in (cgroup / 'io.max').read_text('ascii').splitlines()]
require(any(row and row[0] in targets and 'wbps=8388608' in row[1:]
            for row in io_lines))
MYSQL_KERNEL_VERIFY
ready=0
i=0
while [ "$i" -lt 75 ]; do
    if /usr/bin/mysql --batch --skip-column-names -e 'SELECT 1;' >/dev/null 2>&1; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 1
done
[ "$ready" = 1 ] || exit 1
secret=$(/usr/bin/openssl rand -hex 24)
[ "${#secret}" -eq 48 ] || exit 1
/usr/bin/mysql --batch <<SQL
CREATE DATABASE IF NOT EXISTS \`projectiq_run\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'projectiq_app'@'localhost' IDENTIFIED BY '$secret';
CREATE USER IF NOT EXISTS 'projectiq_app'@'127.0.0.1' IDENTIFIED BY '$secret';
GRANT ALL ON \`projectiq_run\`.* TO 'projectiq_app'@'localhost';
GRANT ALL ON \`projectiq_run\`.* TO 'projectiq_app'@'127.0.0.1';
SQL
file=/home/projectiq/.projectiq_mysql_credentials.json
printf '{"host":"127.0.0.1","database":"projectiq_run","user":"projectiq_app","password":"%s"}\n' "$secret" > "$file.tmp"
/usr/bin/chown projectiq:projectiq "$file.tmp"
/bin/chmod 0600 "$file.tmp"
/bin/mv "$file.tmp" "$file"
'''


# Boot-time, operator-owned storage provisioning. The additional disk is an
# ephemeral *raw image with a fixed virtual capacity*, NOT the QCOW2 root disk.
# The script is fixed; neither project content nor runtime command arguments
# can influence its device, filesystem or mount options.
WORKSPACE_MOUNT = '/srv/projectiq-workspace'
WORKSPACE_SERIAL = 'PIQWORK0001'
WORKSPACE_DEFAULT_MIB = 1536
WORKSPACE_MIN_MIB = 64
WORKSPACE_MAX_MIB = 2048

WORKSPACE_BOOTSTRAP_SCRIPT = r'''#!/bin/sh
set -eu
umask 077
device=/dev/disk/by-id/virtio-PIQWORK0001
ready=0
n=0
while [ "$n" -lt 80 ]; do
    if [ -b "$device" ]; then ready=1; break; fi
    n=$((n + 1))
    sleep 1
done
[ "$ready" = 1 ] || exit 1
# Never format the root device. The serial is attached to a fresh disposable
# disk by the trusted QEMU launcher and does not come from student metadata.
[ "$(readlink -f "$device")" != /dev/vda ] || exit 1
/usr/sbin/mkfs.ext4 -F -q -m 0 "$device"
/bin/mkdir -p /srv/projectiq-workspace
/bin/mount -t ext4 -o rw,nosuid,nodev,noatime "$device" /srv/projectiq-workspace
/usr/bin/chown projectiq:projectiq_exec /srv/projectiq-workspace
/bin/chmod 2770 /srv/projectiq-workspace
/usr/bin/touch /run/projectiq-workspace-bootstrap.ready
'''


# D5b/B3b preflight: an operator-owned systemd user slice. The root-owned
# bootstrap is installed ONLY in a clean VM before ANY reviewed source arrives.
# The controller, not the student application user, owns the user manager;
# neither user is granted root sudo. Exact kernel cgroup file values must be
# checked in a real VM before this can be enabled for actual application runs.
CGROUP_PROBE_SLICE = 'projectiq-probe.slice'
CGROUP_PROBE_MEMORY_BYTES = 192 * 1024 * 1024
CGROUP_PROBE_PIDS = 32
CGROUP_PROBE_IO_BPS = 4 * 1024 * 1024
CGROUP_BOOTSTRAP_SCRIPT = r'''#!/bin/sh
set -eu
uid=$(/usr/bin/id -u projectiq)
[ -n "$uid" ] || exit 1
# Only the trusted controller receives delegated cgroup management authority.
# projectiq_run has neither a systemd user manager nor unit write permissions.
/bin/mkdir -p "/etc/systemd/system/user@${uid}.service.d"
/bin/cat > "/etc/systemd/system/user@${uid}.service.d/90-projectiq-delegation.conf" <<EOF
[Service]
Delegate=cpu cpuset io memory pids
IOAccounting=yes
EOF
/bin/mkdir -p /etc/systemd/user
/bin/cat > /etc/systemd/user/projectiq-probe.slice <<EOF
[Unit]
Description=ProjectIQ cgroup v2 acceptance slice (trusted probes only)
[Slice]
MemoryAccounting=yes
MemoryMax=192M
TasksAccounting=yes
TasksMax=32
IOAccounting=yes
# Use the same exact bytes as CGROUP_PROBE_IO_BPS; systemd 4M is decimal.
IOWriteBandwidthMax=/srv/projectiq-workspace 4194304
EOF
# Load system-level drop-ins before linger can start user@UID.service.
/usr/bin/systemctl daemon-reload
/usr/bin/loginctl enable-linger projectiq
# An SSH/PAM login may already have started this user manager before the
# delegation drop-in was installed. `start` is a no-op for an active manager;
# show(1) may display new properties without renewing runtime cgroup ownership.
# This restart is ONLY inside the trusted clean-VM acceptance bootstrap.
/usr/bin/systemctl restart "user@${uid}.service"
n=0
until /usr/sbin/runuser -u projectiq -- /usr/bin/env \
  "XDG_RUNTIME_DIR=/run/user/${uid}" \
  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus" \
  /usr/bin/systemctl --user daemon-reload; do
    n=$((n + 1))
    [ "$n" -lt 15 ] || exit 1
    sleep 1
done
# This root-owned marker means the trusted bootstrap reached its end.
/usr/bin/touch /run/projectiq-cgroup-bootstrap.ready
'''


# B3b Gate 2A: separate evaluation workload slice (NOT the Gate 1 probe).
# The trusted VM controller owns systemd; projectiq_run has no user manager.
APP_CGROUP_SLICE = 'projectiq-eval.slice'
APP_CGROUP_MEMORY_BYTES = 2147483648
APP_CGROUP_PIDS = 80
APP_CGROUP_IO_BPS = 16777216
APP_CGROUP_BOOTSTRAP_SCRIPT = r'''#!/bin/sh
set -eu
uid=$(/usr/bin/id -u projectiq)
[ -n "$uid" ] || exit 1
/bin/mkdir -p "/etc/systemd/system/user@${uid}.service.d"
/bin/cat > "/etc/systemd/system/user@${uid}.service.d/90-projectiq-delegation.conf" <<EOF
[Service]
Delegate=cpu cpuset io memory pids
IOAccounting=yes
EOF
/bin/mkdir -p /etc/systemd/user
/bin/cat > /etc/systemd/user/projectiq-eval.slice <<EOF
[Unit]
Description=ProjectIQ bounded evaluation workload (trusted controller only)
[Slice]
MemoryAccounting=yes
MemoryMax=2147483648
TasksAccounting=yes
TasksMax=80
IOAccounting=yes
IOWriteBandwidthMax=/srv/projectiq-workspace 16777216
EOF
/usr/bin/systemctl daemon-reload
/usr/bin/loginctl enable-linger projectiq
# SSH/PAM may have started an older user manager; refresh its delegation.
/usr/bin/systemctl restart "user@${uid}.service"
n=0
until /usr/sbin/runuser -u projectiq -- /usr/bin/env \
  "XDG_RUNTIME_DIR=/run/user/${uid}" \
  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${uid}/bus" \
  /usr/bin/systemctl --user daemon-reload; do
    n=$((n + 1))
    [ "$n" -lt 15 ] || exit 1
    sleep 1
done
# Apply app-UID-only root-disk restriction before advertising readiness.
# Missing or unsupported POSIX ACLs fail the root bootstrap closed.
/usr/bin/python3 -I /usr/local/sbin/projectiq-storage-guard
# Readiness marker only; the guest independently checks kernel cgroup values.
/usr/bin/touch /run/projectiq-eval-bootstrap.ready
'''


def _validate_workspace_mib(mib: int) -> int:
    if type(mib) is not int or not WORKSPACE_MIN_MIB <= mib <= WORKSPACE_MAX_MIB:
        raise WorkerFailure('Workspace disk budget must be an integer between 64 and 2048 MiB')
    return mib


def make_nocloud_iso(workdir: Path, pubkey: str, instance_id: str, *, template_setup: bool = False,
                      mysql_bootstrap: bool = False, workspace_disk_mib: int = 0,
                      cgroup_probe: bool = False, app_cgroup: bool = False) -> Path:
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
    if template_setup and mysql_bootstrap:
        raise WorkerFailure('Cannot bootstrap application database while baking template')
    if cgroup_probe and (template_setup or mysql_bootstrap or not workspace_disk_mib):
        raise WorkerFailure('Cgroup probe requires a separate bounded workspace disk')
    if app_cgroup and (template_setup or cgroup_probe or not workspace_disk_mib):
        raise WorkerFailure('App cgroup requires a runtime workspace and no probe/template mode')
    if workspace_disk_mib:
        _validate_workspace_mib(workspace_disk_mib)
        if template_setup:
            raise WorkerFailure('Workspace disk is not used during template baking')
    sudo_rule = ('ALL=(ALL) NOPASSWD:ALL' if template_setup
                 else 'ALL=(projectiq_run) NOPASSWD: ALL')
    data = (
        '#cloud-config\n'
        'ssh_pwauth: false\n'
        'disable_root: true\n'
        'ssh_deletekeys: true\n'
        'groups: [projectiq_exec]\n' if not template_setup else
        '#cloud-config\nssh_pwauth: false\ndisable_root: true\nssh_deletekeys: true\n'
    )
    data += (
        'users:\n'
        '  - name: projectiq\n'
        '    lock_passwd: true\n'
        '    shell: /bin/bash\n'
        + ('    groups: [projectiq_exec]\n' if not template_setup else '')
        + f'    sudo: ["{sudo_rule}"]\n'
        '    ssh_authorized_keys:\n'
        f'      - {pubkey}\n'
    )
    if not template_setup:
        data += (
            '  - name: projectiq_run\n'
            '    lock_passwd: true\n'
            '    shell: /usr/sbin/nologin\n'
            '    groups: [projectiq_exec]\n'
            '    sudo: false\n'
        )
    bootstrap_tasks = []
    if workspace_disk_mib or mysql_bootstrap or cgroup_probe or app_cgroup:
        data += 'write_files:\n'
    if workspace_disk_mib:
        encoded = base64.b64encode(WORKSPACE_BOOTSTRAP_SCRIPT.encode('utf-8')).decode('ascii')
        data += (
            '  - path: /usr/local/sbin/projectiq-workspace-bootstrap\n'
            '    owner: root:root\n'
            "    permissions: '0700'\n"
            '    encoding: b64\n'
            f'    content: {encoded}\n'
        )
        bootstrap_tasks.append('/usr/local/sbin/projectiq-workspace-bootstrap')
    if cgroup_probe:
        encoded = base64.b64encode(CGROUP_BOOTSTRAP_SCRIPT.encode('utf-8')).decode('ascii')
        data += (
            '  - path: /usr/local/sbin/projectiq-cgroup-probe-bootstrap\n'
            '    owner: root:root\n'
            "    permissions: '0700'\n"
            '    encoding: b64\n'
            f'    content: {encoded}\n'
        )
        bootstrap_tasks.append('/usr/local/sbin/projectiq-cgroup-probe-bootstrap')
    if app_cgroup:
        guard_source = Path(__file__).with_name('d5d_storage_guard.py').read_bytes()
        if not 0 < len(guard_source) <= 16000:
            raise WorkerFailure('Trusted storage guard source exceeds bound')
        guard_encoded = base64.b64encode(guard_source).decode('ascii')
        data += (
            '  - path: /usr/local/sbin/projectiq-storage-guard\n'
            '    owner: root:root\n'
            "    permissions: '0700'\n"
            '    encoding: b64\n'
            f'    content: {guard_encoded}\n'
        )
        encoded = base64.b64encode(APP_CGROUP_BOOTSTRAP_SCRIPT.encode('utf-8')).decode('ascii')
        data += (
            '  - path: /usr/local/sbin/projectiq-eval-bootstrap\n'
            '    owner: root:root\n'
            "    permissions: '0700'\n"
            '    encoding: b64\n'
            f'    content: {encoded}\n'
        )
        bootstrap_tasks.append('/usr/local/sbin/projectiq-eval-bootstrap')
    if mysql_bootstrap:
        encoded = base64.b64encode(MYSQL_BOOTSTRAP_SCRIPT.encode('utf-8')).decode('ascii')
        data += (
            '  - path: /usr/local/sbin/projectiq-mysql-bootstrap\n'
            '    owner: root:root\n'
            "    permissions: '0700'\n"
            '    encoding: b64\n'
            f'    content: {encoded}\n'
        )
        bootstrap_tasks.append('/usr/local/sbin/projectiq-mysql-bootstrap')
    if bootstrap_tasks:
        data += 'runcmd:\n'
        for task in bootstrap_tasks:
            data += f'  - [ {task} ]\n'
    data = data.encode('utf-8')
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
                 trusted_template_setup: bool = False,
                 trusted_dependency_fetch: bool = False,
                 diagnostic_serial: bool = False):
        self.config = config
        self.workdir = Path(workdir)
        self.port = port
        self.ram_mib = ram_mib
        self.cpus = cpus
        self.trusted_template_setup = trusted_template_setup
        if trusted_template_setup and trusted_dependency_fetch:
            raise WorkerFailure('Trusted template baking and dependency fetch cannot share a VM mode')
        self.trusted_dependency_fetch = trusted_dependency_fetch
        # Only explicit clean-VM probes opt in; normal project execution keeps
        # the guest serial console disabled to avoid collecting project output.
        self.diagnostic_serial = diagnostic_serial
        # Network-authorized dependency preparation remains a separate trust
        # mode. Do not attach an unused quota disk just because the operator's
        # normal-runtime config contains workspace_disk_mib.
        self.workspace_disk_mib = (0 if trusted_dependency_fetch
                                   else config.get('workspace_disk_mib', 0))
        if self.workspace_disk_mib:
            _validate_workspace_mib(self.workspace_disk_mib)
            if trusted_template_setup:
                raise WorkerFailure('Template baking must not attach a workspace disk')
        self.proc: subprocess.Popen | None = None
        self._log = None

    def network_spec(self) -> str:
        """One shared network contract for every application adapter.

        Networked modes are only for the explicitly trusted OS/dependency
        preparation workers. D5 reads this SAME method as the VM launcher.
        """
        forward = 'hostfwd=tcp:127.0.0.1:' + str(self.port) + '-:22'
        return ('user,id=net0,' + forward if
                self.trusted_template_setup or self.trusted_dependency_fetch else
                'user,id=net0,restrict=on,' + forward)

    def start(self, seed: Path) -> None:
        base = Path(self.config['base_image']).resolve()
        overlay = self.workdir / 'run.qcow2'
        img = _tool('qemu-img', self.config.get('qemu_img_binary'))
        result = _bounded([img, 'create', '-f', 'qcow2', '-F', 'qcow2',
                           '-b', str(base), str(overlay)], timeout=30)
        if result.returncode != 0:
            raise WorkerFailure('Unable to create disposable QEMU disk overlay')
        work_disk = None
        if self.workspace_disk_mib:
            work_disk = self.workdir / 'workspace.raw'
            created = _bounded([img, 'create', '-f', 'raw', str(work_disk),
                                f'{self.workspace_disk_mib}M'], timeout=30)
            if created.returncode != 0:
                raise WorkerFailure('Could not create bounded disposable workspace disk')
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
        # D5c: the controller itself joins the kill-on-close job *before*
        # QEMU starts. All normal child launches inherit it atomically.
        if os.name == 'nt':
            from .d5c_windows_job import JobGuardError, ensure_controller_job
            try:
                ensure_controller_job()
            except JobGuardError as exc:
                raise WorkerFailure('Windows VM lifetime guard unavailable') from exc
        self._log = (self.workdir / 'qemu.log').open('wb')
        netdev = self.network_spec()
        # Record the trusted template guest's serial console for sealing
        # diagnostics. Ordinary project execution does not expose a console.
        serial = ('file:' + (self.workdir / 'guest-serial.log').resolve().as_posix()
                  if self.trusted_template_setup or self.diagnostic_serial else 'none')
        # With a second disk present, explicitly mark the trusted Ubuntu root
        # image as boot target #1. The new blank workspace disk must never be
        # selected by PC BIOS/UEFI as the first boot device. Keep the known-
        # working single-disk machine configuration unchanged.
        root_drive = (['-drive', f'file={overlay},if=none,format=qcow2,id=bootdisk',
                       '-device', 'virtio-blk-pci,drive=bootdisk,bootindex=1']
                      if work_disk is not None else
                      ['-drive', f'file={overlay},if=virtio,format=qcow2'])
        args = [
            _tool('qemu-system-x86_64', self.config.get('qemu_binary')),
            '-accel', accelerator, '-m', str(self.ram_mib), '-smp', str(self.cpus),
            *root_drive,
            '-drive', f'file={Path(seed).resolve()},media=cdrom,readonly=on',
            '-netdev', netdev,
            '-device', 'virtio-net-pci,netdev=net0',
            '-display', 'none', '-monitor', 'none', '-serial', serial, '-no-reboot',
        ]
        if work_disk is not None:
            args.extend(['-drive', f'file={work_disk},if=none,format=raw,id=workdisk',
                         '-device', f'virtio-blk-pci,drive=workdisk,serial={WORKSPACE_SERIAL},bootindex=2'])
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
            if os.name == 'nt':
                from .d5c_windows_job import verify_child_in_job
                if not verify_child_in_job(self.proc):
                    # Never return a successful VM launch outside our lifetime job.
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=8)
                    except (OSError, subprocess.TimeoutExpired, AttributeError):
                        # AttributeError is possible only for an invalid mocked
                        # process; real Popen always exposes kill()/wait().
                        pass
                    finally:
                        # Windows refuses to remove an open qemu.log. This
                        # must also run when termination itself raises.
                        if self._log is not None:
                            self._log.close()
                            self._log = None
                    raise WorkerFailure('QEMU is not in the controller lifetime job')
        except OSError as exc:
            if self._log is not None:
                self._log.close()
                self._log = None
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
        # OpenSSH sends a *single shell command string* to the remote host.
        # Passing remote_argv as separate local argv elements does NOT preserve
        # their boundaries: e.g. mysql -e 'SELECT 1;' becomes mysql -e SELECT 1;
        # and MySQL treats '1' as a database name. Quote each trusted argument
        # for the guest's POSIX shell before passing one command to ssh.exe.
        # These arguments must remain controller-owned, not student-supplied.
        if not remote_argv or not all(isinstance(arg, str) for arg in remote_argv):
            raise WorkerFailure('Invalid trusted SSH command arguments')
        remote_command = shlex.join(remote_argv)
        cmd = [_tool('ssh', self.config.get('ssh_binary')), *self.options(), '-T', '-p', str(self.port),
               'projectiq@127.0.0.1', remote_command]
        return _bounded(cmd, timeout=timeout, input=input)

    def copy(self, local: Path, remote_filename: str, timeout: int = 60) -> None:
        if remote_filename not in {
            'snapshot.zip', 'guest_agent.py', 'request.json', 'template_seal.sh',
            'guest_http.py', 'guest_api.py', 'guest_db_fixture.py',
            'guest_db_postconditions.py', 'guest_workflow.py',
        }:
            raise WorkerFailure('Unapproved destination filename')
        content = local.read_bytes()
        if len(content) > 30_000_000:
            raise WorkerFailure('Upload exceeds bounded SSH transfer budget')
        command = 'of=/home/projectiq/' + remote_filename
        if self.ssh('dd', command, 'status=none', timeout=timeout, input=content).returncode != 0:
            raise WorkerFailure('Unable to copy trusted input into disposable VM')

    def copy_offline_asset(self, local: Path, remote_filename: str, timeout: int = 150) -> None:
        """Transfer only operator-pinned artifacts; never mount a host directory."""
        limits = {'node_runtime.tar.xz': 65_000_000, 'npm_cache.zip': 170_000_000,
                  'python_pip.whl': 5_000_000, 'python_wheelhouse.zip': 120_000_000}
        limit = limits.get(remote_filename)
        if limit is None or not Path(local).is_file() or not 0 < Path(local).stat().st_size <= limit:
            raise WorkerFailure('Unapproved or oversized offline runtime asset')
        # Stream the file: do not hold a large dependency cache in Windows RAM.
        cmd = [_tool('ssh', self.config.get('ssh_binary')), *self.options(), '-T',
               '-p', str(self.port), 'projectiq@127.0.0.1',
               shlex.join(['dd', 'of=/home/projectiq/' + remote_filename, 'status=none'])]
        try:
            with Path(local).open('rb') as src:
                result = subprocess.run(cmd, stdin=src, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkerFailure('Offline runtime artifact transfer unavailable or timed out') from exc
        if result.returncode:
            raise WorkerFailure('Offline runtime artifact transfer failed')

    def receive_offline_cache(self, local: Path, timeout: int = 180,
                              remote_filename: str = 'npm_cache.zip') -> None:
        """Download only bounded cache data into the throwaway host work directory."""
        import threading
        limits = {'npm_cache.zip': 170_000_000, 'python_wheelhouse.zip': 120_000_000}
        if remote_filename not in limits:
            raise WorkerFailure('Unapproved offline cache destination')
        cmd = [_tool('ssh', self.config.get('ssh_binary')), *self.options(), '-T',
               '-p', str(self.port), 'projectiq@127.0.0.1',
               shlex.join(['cat', '/home/projectiq/' + remote_filename])]
        proc = None
        expired = threading.Event()
        def kill_if_late():
            expired.set()
            if proc is not None and proc.poll() is None:
                proc.kill()
        timer = threading.Timer(timeout, kill_if_late)
        timer.daemon = True
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            timer.start()
            total = 0
            with Path(local).open('xb') as output:
                while True:
                    data = proc.stdout.read(64 * 1024)
                    if not data:
                        break
                    total += len(data)
                    if total > limits[remote_filename]:
                        raise WorkerFailure('Offline cache exceeds transfer limit')
                    output.write(data)
            if proc.wait(timeout=10) != 0 or expired.is_set() or total == 0:
                raise WorkerFailure('Unable to retrieve approved offline npm cache')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkerFailure('Offline npm cache download failed or timed out') from exc
        finally:
            timer.cancel()
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()

    def receive(self, remote_filename: str, local: Path, timeout: int = 30) -> None:
        if remote_filename != 'result.json':
            raise WorkerFailure('Unapproved VM output name')
        result = self.ssh('cat', '/home/projectiq/result.json', timeout=timeout)
        if result.returncode != 0 or len(result.stdout) > 64_000:
            raise WorkerFailure('Invalid or oversized VM report')
        local.write_bytes(result.stdout)

    def wait_ssh(self, vm: QemuWorker, seconds: int = 80, *,
                 cancel_requested=None) -> None:
        """Bounded SSH readiness with optional trusted cooperative cancellation.

        Existing callers without a cancellation callback retain their behavior.
        A callback that raises (e.g. unreadable cancellation marker) propagates:
        an I/O/permission error must never be mistaken for no cancellation.
        Individual SSH attempts retain their existing six-second bound.
        """
        if cancel_requested is not None and not callable(cancel_requested):
            raise WorkerFailure('Invalid trusted VM cancellation callback')

        def check_cancel():
            if cancel_requested is not None and cancel_requested():
                raise WorkerFailure('Worker VM startup cancelled')

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            check_cancel()
            if vm.proc is None or vm.proc.poll() is not None:
                raise WorkerFailure('Worker VM exited before SSH became ready')
            try:
                if self.ssh('true', timeout=6).returncode == 0:
                    check_cancel()
                    return
            except WorkerFailure:
                pass
            check_cancel()
            if cancel_requested is None:
                time.sleep(2)
            else:
                for _ in range(10):
                    check_cancel()
                    time.sleep(0.2)
        check_cancel()
        raise WorkerFailure('Worker VM SSH readiness timed out')
