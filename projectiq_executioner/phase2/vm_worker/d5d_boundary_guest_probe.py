"""D5d Gate 3: trusted, source-free guest storage/network boundary observations.

Executed only inside an operator-controlled disposable Linux VM via SSH stdin.
All writes use O_EXCL and are immediately removed. No student source or Internet
address is used. This probe is diagnostic, NOT a sandbox policy enforcement.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
try:
    import pwd
except ImportError:  # imported by Windows unit tests only
    pwd = None
import re
import socket
import stat
import subprocess
import sys
import time

WORKSPACE = Path('/srv/projectiq-workspace')
FIELDS = (
    'controller_identity',
    'trusted_workspace_bootstrap',
    'trusted_storage_guard_bootstrap',
    'bounded_workspace_disk',
    'distinct_root_and_workspace_devices',
    'app_can_write_workspace',
    'app_root_tmp_write_denied',
    'app_var_tmp_write_denied',
    'app_root_home_write_denied',
    'guest_network_interface_present',
    'host_gateway_connection_denied',
)
WRITE_STATES = frozenset(('CREATED_CLEANED', 'DENIED', 'ERROR', 'NOT_RUN'))
NETWORK_STATES = frozenset(('DENIED', 'REACHED', 'ERROR', 'NOT_RUN'))

# Fixed trusted input, launched AS the app UID; no shell or student arguments.
# This intentionally measures the underlying unprivileged UID's write access.
# If an application-specific filesystem sandbox is added later, its launcher
# will need a separate production-path verification, not just this UID probe.
APP_WRITE_PROBE = r'''import errno, os, secrets
for name, parent in [('workspace','/srv/projectiq-workspace'),
                     ('root_tmp','/tmp'), ('var_tmp','/var/tmp'),
                     ('root_home','/home/projectiq_run')]:
    file = os.path.join(parent, '.piq-g3-' + secrets.token_hex(12))
    status = 'ERROR'
    try:
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            status = 'DENIED'
    else:
        try:
            os.write(fd, b'projectiq-gate3')
            status = 'CREATED_CLEANED'
        except OSError:
            status = 'ERROR'
        finally:
            os.close(fd)
            try:
                os.unlink(file)
            except OSError:
                status = 'ERROR'
    print(name + '=' + status)
'''


def _marker_trusted(marker: Path) -> bool:
    try:
        meta = marker.lstat()
        return (stat.S_ISREG(meta.st_mode) and meta.st_uid == 0
                and not (meta.st_mode & 0o022))
    except OSError:
        return False


def _workspace_observations(limit_mib: int = 96) -> tuple[bool, bool, bool]:
    """Check a real separately mounted, bounded ext4 workspace disk."""
    if not _marker_trusted(Path('/run/projectiq-workspace-bootstrap.ready')):
        return False, False, False
    try:
        if (WORKSPACE.is_symlink() or not os.path.ismount(WORKSPACE)
                or not WORKSPACE.is_dir()):
            return True, False, False
        entries = [line.split() for line in Path('/proc/mounts').read_text('utf-8').splitlines()]
        entries = [x for x in entries if len(x) >= 4 and x[1] == str(WORKSPACE)]
        if (len(entries) != 1 or entries[0][2] != 'ext4'
                or not {'rw', 'nosuid', 'nodev'}.issubset(entries[0][3].split(','))):
            return True, False, False
        fs = os.statvfs(WORKSPACE)
        bounded = 48 * 1024**2 <= fs.f_blocks * fs.f_frsize <= limit_mib * 1024**2
        distinct = WORKSPACE.stat().st_dev != Path('/').stat().st_dev
        return True, bounded, distinct
    except (OSError, ValueError):
        return True, False, False


def _guard_bootstrap_state(deadline: float, *,
                           marker: Path = Path('/run/projectiq-eval-bootstrap.ready')) -> str:
    """Wait for the *later* root-owned evaluation marker, not just disk mount.

    The cloud-init execution order is workspace -> eval cgroup -> root ACL guard
    -> eval ready marker. Observing the app UID before the final marker races
    the guard and produces a false apparent storage-policy gap. A missing or
    untrusted marker never authorizes an app write probe.
    """
    while True:
        try:
            st = marker.lstat()
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                return 'MISSING'
            time.sleep(0.5)
            continue
        except OSError:
            return 'UNTRUSTED'
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                st.st_mode & 0o022):
            return 'UNTRUSTED'
        return 'READY'


def _write_observations() -> dict[str, str]:
    default = {'workspace': 'NOT_RUN', 'root_tmp': 'NOT_RUN',
               'var_tmp': 'NOT_RUN', 'root_home': 'NOT_RUN'}
    try:
        result = subprocess.run(
            ['/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--',
             '/usr/bin/python3', '-I', '-c', APP_WRITE_PROBE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 240:
            return default
        rows = result.stdout.decode('ascii', 'strict').splitlines()
        if len(rows) != 4:
            return default
        parsed = {}
        for row in rows:
            key, sep, value = row.partition('=')
            if sep != '=' or key not in default or key in parsed or value not in WRITE_STATES:
                return default
            parsed[key] = value
        return parsed if set(parsed) == set(default) else default
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return default


def _network_interface() -> bool:
    """Require an up non-loopback NIC; a *default* route is not necessary.

    QEMU restrict=on may leave the guest with only a connected local route.
    We still try the slirp gateway and require an actual denied connection.
    """
    try:
        for index, name in socket.if_nameindex():
            if (type(index) is not int or index <= 0 or name == 'lo' or
                    not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,20}', name)):
                continue
            state = (Path('/sys/class/net') / name / 'operstate').read_text('ascii').strip()
            if state in ('up', 'unknown'):
                return True
    except (OSError, ValueError, UnicodeError):
        pass
    return False

def _gateway_connect(port: int) -> str:
    """Try only the QEMU slirp host gateway, never a public Internet target."""
    if type(port) is not int or not 1024 <= port <= 65535:
        return 'NOT_RUN'
    try:
        with socket.create_connection(('10.0.2.2', port), timeout=2.5) as target:
            target.sendall(b'projectiq-d5d3-forbidden\n')
        return 'REACHED'
    except OSError as exc:
        if isinstance(exc, TimeoutError) or exc.errno in (
                errno.ECONNREFUSED, errno.ETIMEDOUT, errno.EHOSTUNREACH,
                errno.ENETUNREACH, errno.EACCES, errno.EPERM):
            return 'DENIED'
        return 'ERROR'


def _observations(port: int) -> dict:
    report = {
        'schema': 'd5d-boundary-1',
        'observations': {key: False for key in FIELDS},
        'diagnostics': {'workspace_write': 'NOT_RUN', 'root_tmp_write': 'NOT_RUN',
                        'var_tmp_write': 'NOT_RUN', 'root_home_write': 'NOT_RUN',
                        'gateway_connect': 'NOT_RUN',
                        'storage_guard_bootstrap': 'NOT_RUN'},
    }
    observations = report['observations']
    if sys.platform != 'linux' or pwd is None:
        return report
    try:
        controller = pwd.getpwnam('projectiq')
        app = pwd.getpwnam('projectiq_run')
        if (os.getuid() != os.geteuid() or os.getuid() != controller.pw_uid or
                app.pw_uid in (0, controller.pw_uid)):
            return report
        observations['controller_identity'] = True
    except (OSError, KeyError, AttributeError):
        return report
    # Only the trusted controller runs this code; no reviewed project snapshot.
    deadline = time.monotonic() + 85
    while not _marker_trusted(Path('/run/projectiq-workspace-bootstrap.ready')) and time.monotonic() < deadline:
        time.sleep(1)
    bootstrap, bounded, distinct = _workspace_observations()
    observations['trusted_workspace_bootstrap'] = bootstrap
    observations['bounded_workspace_disk'] = bounded
    observations['distinct_root_and_workspace_devices'] = distinct
    if bootstrap and bounded and distinct:
        guard_state = _guard_bootstrap_state(deadline)
        report['diagnostics']['storage_guard_bootstrap'] = guard_state
        observations['trusted_storage_guard_bootstrap'] = guard_state == 'READY'
    if bootstrap and bounded and distinct and observations['trusted_storage_guard_bootstrap']:
        writes = _write_observations()
        report['diagnostics'].update(workspace_write=writes['workspace'],
                                     root_tmp_write=writes['root_tmp'],
                                     var_tmp_write=writes['var_tmp'],
                                     root_home_write=writes['root_home'])
        observations['app_can_write_workspace'] = writes['workspace'] == 'CREATED_CLEANED'
        observations['app_root_tmp_write_denied'] = writes['root_tmp'] == 'DENIED'
        observations['app_var_tmp_write_denied'] = writes['var_tmp'] == 'DENIED'
        observations['app_root_home_write_denied'] = writes['root_home'] == 'DENIED'
    observations['guest_network_interface_present'] = _network_interface()
    outcome = _gateway_connect(port)
    report['diagnostics']['gateway_connect'] = outcome
    observations['host_gateway_connection_denied'] = (
        observations['guest_network_interface_present'] and outcome == 'DENIED')
    return report


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isascii() or not sys.argv[1].isdecimal():
        return 2
    port = int(sys.argv[1])
    if not 1024 <= port <= 65535:
        return 2
    print(json.dumps(_observations(port), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
