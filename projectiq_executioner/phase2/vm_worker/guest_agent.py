"""Trusted, offline agent for a disposable Ubuntu worker VM.

Intended to be copied in by the controller and executed inside the guest ONLY.
Never execute this guest agent on the ProjectIQ/Windows host.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import shutil
import stat
import tarfile
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote
import zipfile
from pathlib import Path, PurePosixPath

# Windows imports this module for unit tests only; the executable guest is Linux.
try:
    import grp
    import pwd
except ImportError:
    grp = None
    pwd = None


class GuestFailure(RuntimeError):
    pass


class NpmPreparationDiagnostic(GuestFailure):
    """Trusted preparation-only npm failure with optional sanitized context."""

    def __init__(self, code: str, safe_hint: str):
        super().__init__(code)
        self.safe_hint = safe_hint


DENIED_SQL = re.compile(
    r'\b(?:CREATE\s+(?:USER|FUNCTION|PROCEDURE|TRIGGER|EVENT|DATABASE|SERVER)|'
    r'DROP\s+DATABASE|ALTER\s+USER|GRANT|REVOKE|INTO\s+OUTFILE|INTO\s+DUMPFILE|'
    r'LOAD\s+DATA|INSTALL\s+PLUGIN|SET\s+GLOBAL|USE\s+|DELIMITER\b|'
    r'SOURCE\b|SYSTEM\b|EXECUTE\b|PREPARE\b)\b|\\[!.]|\b(?:SLEEP|BENCHMARK)\s*\(',
    re.IGNORECASE,
)


def safe_extract(archive: Path, into: Path, *, max_total: int = 120_000_000) -> Path:
    """Extract bounded GitHub ZIP with no symlinks, zip-slip or executable host hooks."""
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        if len(entries) > 12000:
            raise GuestFailure('Too many source files')
        expanded = 0
        root = None
        seen = set()
        targets = []
        for entry in entries:
            path = entry.filename
            if (not path or '\\' in path or '\x00' in path or path.startswith('/') or '//'
                    in path or len(path) > 350):
                raise GuestFailure('Unsafe source archive path')
            parts = path.rstrip('/').split('/')
            if len(parts) < 2 or any(p in {'.', '..', ''} or ':' in p for p in parts):
                if entry.is_dir() and len(parts) == 1:
                    continue
                raise GuestFailure('Unsafe source archive structure')
            if root is None:
                root = parts[0]
            if parts[0] != root:
                raise GuestFailure('Multiple source roots')
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise GuestFailure('Symlinks and special files are not allowed')
            if entry.is_dir():
                continue
            if entry.file_size > 20_000_000:
                raise GuestFailure('Oversized source member')
            expanded += entry.file_size
            if expanded > max_total:
                raise GuestFailure('Expanded snapshot exceeds limit')
            sub = '/'.join(parts[1:])
            if sub.casefold() in seen:
                raise GuestFailure('Duplicate source paths')
            seen.add(sub.casefold())
            targets.append((entry, sub))
        if not root or not targets:
            raise GuestFailure('Empty or invalid repository')
        into.mkdir(parents=True, exist_ok=False)
        for entry, sub in targets:
            target = into.joinpath(*PurePosixPath(sub).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, target.open('xb') as out:
                copied = 0
                while data := src.read(65536):
                    copied += len(data)
                    if copied > entry.file_size:
                        raise GuestFailure('Archive size mismatch')
                    out.write(data)
                if copied != entry.file_size:
                    raise GuestFailure('Truncated archive')
        return into


APP_USER = 'projectiq_run'
SHARED_GROUP = 'projectiq_exec'

# D5b.3a: stable, stack-agnostic *per real UID* process-resource caps.
# Executed AFTER sudo has dropped privileges to projectiq_run and BEFORE
# any student dependency install, framework build or server entrypoint.
# RLIMIT_NPROC counts Linux tasks for the real UID (not a per-service cgroup);
# RLIMIT_NOFILE is per process. Memory, aggregate I/O and cgroup-wide PID
# isolation remain explicitly unresolved until D5b.3b.
APP_NPROC_HARD = 96
APP_NOFILE_HARD = 512
APP_LIMIT_LAUNCHER = r'''import os, resource, sys
if sys.platform != 'linux' or os.geteuid() == 0 or len(sys.argv) < 2:
    sys.exit(111)
for limit, maximum in ((resource.RLIMIT_NPROC, 96),
                       (resource.RLIMIT_NOFILE, 512),
                       (resource.RLIMIT_CORE, 0)):
    _soft, old_hard = resource.getrlimit(limit)
    target = maximum if old_hard == resource.RLIM_INFINITY else min(old_hard, maximum)
    resource.setrlimit(limit, (target, target))
# A trusted marker is inserted only for reviewed runtime VMs. The program
# cannot run if systemd-run placed its process outside the evaluation scope.
if os.environ.get('PROJECTIQ_EVAL_REQUIRED') == '1':
    try:
        with open('/proc/self/cgroup', encoding='utf-8') as _f:
            rows = [line.split(':', 2)[2].strip() for line in _f if line.startswith('0::')]
        if len(rows) != 1:
            sys.exit(112)
        parts = rows[0].strip('/').split('/')
        if (len(parts) != 6 or parts[-3] != 'projectiq.slice' or
                parts[-2] != 'projectiq-eval.slice' or
                not parts[-1].startswith('piq-app-') or
                not parts[-1].endswith('.scope')):
            sys.exit(112)
    except (OSError, ValueError):
        sys.exit(112)
# Controller's pre-exec handshake for approved build/install commands.
# The untrusted command has not run; never pause without the verified slice.
if os.environ.get('PROJECTIQ_ATTEST_PAUSE') == '1':
    if os.environ.get('PROJECTIQ_EVAL_REQUIRED') != '1':
        sys.exit(113)
    import pwd, stat, time
    _gate = os.environ.get('PROJECTIQ_ATTEST_GATE', '')
    _prefix = '/home/projectiq/.projectiq-build-release-'
    if (not _gate.startswith(_prefix) or len(_gate) != len(_prefix) + 32 or
            any(c not in '0123456789abcdef' for c in _gate[len(_prefix):])):
        sys.exit(113)
    try:
        _controller_uid = pwd.getpwnam('projectiq').pw_uid
        _home = os.lstat('/home/projectiq')
        if (not stat.S_ISDIR(_home.st_mode) or _home.st_uid != _controller_uid or
                _home.st_mode & 0o022):
            sys.exit(113)
    except (OSError, KeyError):
        sys.exit(113)
    _deadline = time.monotonic() + 28
    while time.monotonic() < _deadline:
        try:
            _release = os.lstat(_gate)
            if (not stat.S_ISREG(_release.st_mode) or
                    _release.st_uid != _controller_uid or
                    _release.st_mode & 0o022 or _release.st_nlink != 1):
                sys.exit(113)
            with open(_gate, 'rb') as _r:
                if _r.read(4) != b'GO\n':
                    sys.exit(113)
            break
        except FileNotFoundError:
            time.sleep(0.04)
        except OSError:
            sys.exit(113)
    else:
        sys.exit(113)
    os.environ.pop('PROJECTIQ_ATTEST_PAUSE', None)
    os.environ.pop('PROJECTIQ_ATTEST_GATE', None)
# No shell; the program and arguments originate from the trusted adapter.
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
'''



# D5b.2: the only supported application work tree is a separate, bounded
# ext4 disk. Root filesystem /tmp, root QCOW2 and host disk remain distinct
# security gaps; never claim global guest disk confinement from this check.
WORKSPACE_PATH = Path('/srv/projectiq-workspace')
WORKSPACE_READY_MARKER = Path('/run/projectiq-workspace-bootstrap.ready')


def _stage_bounded_workspace(request: dict, *, base: Path) -> Path:
    """Fail closed unless the operator-provisioned workspace is truly mounted.

    Runs as the trusted guest controller before project code is started. Moving
    verified input archives may copy them across mounts. Only fixed filenames
    are accepted and only the designated mount holds project/runtime writes.
    """
    mib = request.get('workspace_disk_mib')
    if type(mib) is not int or not 64 <= mib <= 2048:
        raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
    # Mount visibility is not completion: the root bootstrap must still
    # chown/chmod the ext4 root. A separate root-owned marker is published
    # only AFTER these operations succeed, preventing a startup-time race.
    # Never accept an unmounted or incorrectly configured workspace.
    until = time.monotonic() + 85
    while not WORKSPACE_READY_MARKER.is_file() and time.monotonic() < until:
        time.sleep(1)
    try:
        marker_stat = WORKSPACE_READY_MARKER.lstat()
        if (WORKSPACE_READY_MARKER.is_symlink() or
                not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_uid != 0 or
                marker_stat.st_mode & 0o022):
            raise GuestFailure('WORKSPACE_BOOTSTRAP_NOT_READY')
    except OSError as exc:
        raise GuestFailure('WORKSPACE_BOOTSTRAP_NOT_READY') from exc
    if not WORKSPACE_PATH.is_dir() or WORKSPACE_PATH.is_symlink() or not os.path.ismount(WORKSPACE_PATH):
        raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
    try:
        with Path('/proc/mounts').open(encoding='utf-8') as f:
            rows = [row.split() for row in f if len(row.split()) >= 4]
        matches = [parts for parts in rows if parts[1] == str(WORKSPACE_PATH)]
        if (len(matches) != 1 or matches[0][2] != 'ext4'
                or not {'rw', 'nosuid', 'nodev'}.issubset(set(matches[0][3].split(',')))):
            raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
        stats = os.statvfs(WORKSPACE_PATH)
        total = stats.f_blocks * stats.f_frsize
        if not 48 * 1024**2 <= total <= mib * 1024**2:
            raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
        if pwd is None or grp is None or WORKSPACE_PATH.stat().st_uid != os.geteuid() \
                or WORKSPACE_PATH.stat().st_gid != grp.getgrnam(SHARED_GROUP).gr_gid:
            raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
        for name in ('snapshot.zip', 'node_runtime.tar.xz', 'npm_cache.zip',
                     'python_pip.whl', 'python_wheelhouse.zip'):
            src = base / name
            if src.exists():
                if not src.is_file() or src.is_symlink():
                    raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE')
                shutil.move(str(src), str(WORKSPACE_PATH / name))
        for name in ('app_home', 'tmp'):
            path = WORKSPACE_PATH / name
            path.mkdir(mode=0o2770, exist_ok=False)
            _share_tree(path, writable=True)
        os.environ['PROJECTIQ_QUOTA_HOME'] = str(WORKSPACE_PATH / 'app_home')
        os.environ['PROJECTIQ_QUOTA_TMP'] = str(WORKSPACE_PATH / 'tmp')
        return WORKSPACE_PATH
    except GuestFailure:
        raise
    except (OSError, KeyError) as exc:
        if getattr(exc, 'errno', None) == 28:
            raise GuestFailure('WORKSPACE_DISK_QUOTA_EXCEEDED') from exc
        raise GuestFailure('WORKSPACE_DISK_QUOTA_UNAVAILABLE') from exc



def _verify_execution_identity(base: Path) -> None:
    """Fail closed if cloud-init did not provision the separate unprivileged UID."""
    if pwd is None or grp is None or not hasattr(os, 'geteuid') or os.geteuid() == 0:
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE')
    try:
        controller = pwd.getpwnam('projectiq')
        student = pwd.getpwnam(APP_USER)
        group = grp.getgrnam(SHARED_GROUP)
    except KeyError as exc:
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE') from exc
    if (controller.pw_uid != os.geteuid() or student.pw_uid in (0, controller.pw_uid)
            or controller.pw_uid not in (os.geteuid(),) or
            'projectiq' not in group.gr_mem or APP_USER not in group.gr_mem):
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE')
    # This user can only execute commands AS the less-privileged app account.
    runner = _trusted_process(['sudo', '-n', '-u', APP_USER, '/usr/bin/id', '-u'], timeout=7)
    root = _trusted_process(['sudo', '-n', '-u', 'root', '/usr/bin/id', '-u'], timeout=7)
    student_root = _trusted_process(['sudo', '-n', '-u', APP_USER, '/usr/bin/sudo', '-n',
                                     '-u', 'root', '/usr/bin/id', '-u'], timeout=7)
    if (runner.returncode or runner.stdout.strip() != str(student.pw_uid).encode()
            or root.returncode == 0 or student_root.returncode == 0):
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE')
    # Permit traversal of exact shared paths, but never make private files writable.
    base.chmod(0o711)


def _share_tree(path: Path, *, writable: bool) -> None:
    """Allow the app UID access ONLY to an explicitly selected scratch tree.

    Source ZIP extraction already denies symlinks. Pinned runtime archives may
    include symlinks, which must never be followed when adjusting permissions.
    Windows only imports this module for UNIT TESTS. _verify_execution_identity
    rejects non-POSIX guests before running any application.
    """
    if sys.platform != 'linux' or pwd.getpwuid(os.geteuid()).pw_name != 'projectiq':
        return
    try:
        gid = grp.getgrnam(SHARED_GROUP).gr_gid
    except KeyError as exc:
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE') from exc
    items = [path] + sorted(path.rglob('*')) if path.is_dir() else [path]
    for item in items:
        if item.is_symlink():
            continue
        if not item.is_file() and not item.is_dir():
            raise GuestFailure('RUNTIME_SHARED_PATH_UNSUPPORTED')
        if item.stat().st_uid != os.geteuid():
            # Do not manipulate objects owned by the app once its code has run.
            continue
        os.chown(item, -1, gid)
        current = item.stat().st_mode & 0o777
        if item.is_dir():
            item.chmod((current | (0o2770 if writable else 0o2750)) & 0o7777)
        else:
            mode = 0o660 if writable else 0o640
            # Honor existing execute bits (Node runtime needs them).
            item.chmod((current | mode | (0o110 if current & 0o111 else 0)) & 0o777)


# B3b Gate 2A: static guest-side copy of the operator-owned policy. The
# separately copied guest agent does not import the host's qemu.py.
APP_CGROUP_SLICE = 'projectiq-eval.slice'
APP_CGROUP_MEMORY_BYTES = 2147483648
APP_CGROUP_PIDS = 80
APP_CGROUP_IO_BPS = 16777216
_APP_CGROUP_ACTIVE = False


def _evaluation_policy_matches(group: str, workspace: Path, *, uid: int,
                               cgroup_root: Path = Path('/sys/fs/cgroup')) -> bool:
    """Require exact user-manager slice and real kernel cgroup limits."""
    parts = group.strip('/').split('/')
    expected = ['user.slice', f'user-{uid}.slice', f'user@{uid}.service',
                'projectiq.slice', APP_CGROUP_SLICE]
    if parts != expected:
        return False
    try:
        node = cgroup_root.joinpath(*parts)
        if node.is_symlink() or node.joinpath('memory.max').read_text().strip() != str(APP_CGROUP_MEMORY_BYTES):
            return False
        if node.joinpath('pids.max').read_text().strip() != str(APP_CGROUP_PIDS):
            return False
        device = workspace.stat().st_dev
        expected_dev = f'{os.major(device)}:{os.minor(device)}'
        io_rows = [line.split() for line in node.joinpath('io.max').read_text().splitlines()]
        if not any(row and row[0] == expected_dev and
                   f'wbps={APP_CGROUP_IO_BPS}' in row[1:] for row in io_rows):
            return False
        return True
    except (OSError, ValueError):
        return False


def _verify_evaluation_cgroup(workspace: Path) -> None:
    """Fail closed before any evaluation code if bootstrap/slice is missing."""
    marker = Path('/run/projectiq-eval-bootstrap.ready')
    deadline = time.monotonic() + 85
    while not marker.is_file() and time.monotonic() < deadline:
        time.sleep(1)
    try:
        st = marker.lstat()
        if marker.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise GuestFailure('APP_CGROUP_ENFORCEMENT_UNAVAILABLE')
        uid = os.getuid()
        env = dict(os.environ,
                   XDG_RUNTIME_DIR=f'/run/user/{uid}',
                   DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
        started = _trusted_process(['/usr/bin/systemctl', '--user', 'start', APP_CGROUP_SLICE],
                                   timeout=15, env=env)
        shown = _trusted_process(['/usr/bin/systemctl', '--user', 'show', '-P',
                                  'ControlGroup', APP_CGROUP_SLICE], timeout=10, env=env)
        if (started.returncode != 0 or shown.returncode != 0 or
                not _evaluation_policy_matches(shown.stdout.decode('utf-8').strip(),
                                                workspace, uid=uid)):
            raise GuestFailure('APP_CGROUP_ENFORCEMENT_UNAVAILABLE')
    except (OSError, UnicodeError) as exc:
        raise GuestFailure('APP_CGROUP_ENFORCEMENT_UNAVAILABLE') from exc


# B3b Gate 2A: independent, controller-side observation of live app server
# processes. This does NOT yet prove short-lived npm/Vite/build processes or
# daemonized MySQL are inside this slice. Never report more than observed.
_APP_SERVER_PLACEMENTS: list[str] = []


def _server_pid_in_scope(unit: str, command: list[str], *, controller_uid: int,
                         app_uid: int, cgroup_root: Path = Path('/sys/fs/cgroup'),
                         proc_root: Path = Path('/proc')) -> bool:
    """Read Linux's actual task PID, UID, argv and unified cgroup.

    Called by trusted projectiq controller, not projectiq_run; no student
    writeable evidence files or user-controlled paths are accepted.
    """
    if (not isinstance(unit, str) or not re.fullmatch(r'piq-app-[0-9a-f]{16}', unit)
            or not command or any(not isinstance(x, str) for x in command)):
        return False
    parts = ['user.slice', f'user-{controller_uid}.slice',
             f'user@{controller_uid}.service', 'projectiq.slice',
             APP_CGROUP_SLICE, unit + '.scope']
    expected_group = '/' + '/'.join(parts)
    try:
        scope = cgroup_root.joinpath(*parts)
        if scope.is_symlink() or not scope.is_dir():
            return False
        raw = (scope / 'cgroup.procs').read_text(encoding='ascii')
        pids = raw.split()
        if len(pids) > 128:
            return False
        for pid in pids:
            if not pid.isascii() or not pid.isdecimal() or len(pid) > 10:
                return False
            task = proc_root / pid
            try:
                status = (task / 'status').read_text(encoding='utf-8')[:16000]
                line = next((x for x in status.splitlines() if x.startswith('Uid:')), '')
                uids = line.split()[1:]
                if len(uids) != 4 or any(not n.isdecimal() for n in uids):
                    continue
                if int(uids[0]) != app_uid or int(uids[1]) != app_uid:
                    continue
                group_rows = [(x.split(':', 2)[2]) for x in
                              (task / 'cgroup').read_text(encoding='utf-8').splitlines()
                              if x.startswith('0::')]
                if group_rows != [expected_group]:
                    continue
                arguments = (task / 'cmdline').read_bytes()[:8192].split(b'\x00')
                arguments = [s for s in arguments if s]
                wanted = [x.encode('utf-8') for x in command]
                # Ignore transient sudo, systemd-run, and pre-exec Python.
                # The intended entrypoint must actually have been exec'd.
                if arguments == wanted:
                    return True
            except (OSError, UnicodeError, ValueError):
                # A task may exit while we inspect it. Retry until deadline.
                continue
        return False
    except (OSError, UnicodeError, ValueError):
        return False


def _verify_live_server_process(launch: list[str], command: list[str],
                                proc: subprocess.Popen) -> None:
    """Observe the real app UID's running server in its exact systemd scope."""
    units = [part.split('=', 1)[1] for part in launch if part.startswith('--unit=')]
    if len(units) != 1 or not re.fullmatch(r'piq-app-[0-9a-f]{16}', units[0]):
        raise GuestFailure('APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED')
    try:
        controller_uid = os.getuid()
        app_uid = pwd.getpwnam(APP_USER).pw_uid
        until = time.monotonic() + 15
        while time.monotonic() < until:
            if proc.poll() is not None:
                break
            if _server_pid_in_scope(units[0], command,
                                    controller_uid=controller_uid, app_uid=app_uid):
                _APP_SERVER_PLACEMENTS.append(units[0])
                return
            time.sleep(0.1)
    except (OSError, KeyError, AttributeError) as exc:
        raise GuestFailure('APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED') from exc
    raise GuestFailure('APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED')


# B3b Gate 2A.3: trusted controller observations of the stopped pre-exec
# resource launcher for selected dependency installations and builds. This
# proves the kernel placement/UID at the exec handoff, not a full post-exec
# descendant graph or resource-exhaustion behavior.
_APP_BUILD_HANDOFFS: list[str] = []
_BUILD_KINDS = frozenset(('npm_ci', 'vite_build', 'pip_install'))
_BUILD_REQUIRED = {
    'static_html_v1': {},
    'php_mysql_v1': {},
    'node_vite_offline_v1': {'npm_ci': 1, 'vite_build': 1},
    'node_express_offline_v1': {'npm_ci': 1},
    'python_web_offline_v1': {'pip_install': 1},
    'node_fullstack_offline_v1': {'npm_ci': 2, 'vite_build': 1},
}


def _gated_build_pid(unit: str, command: list[str], *, controller_uid: int,
                      app_uid: int, cgroup_root: Path = Path('/sys/fs/cgroup'),
                      proc_root: Path = Path('/proc')) -> int | None:
    """Return only a kernel-verified, SIGSTOPped trusted pre-exec Python PID."""
    if not re.fullmatch(r'piq-app-[0-9a-f]{16}', unit):
        return None
    if not command or any(type(a) is not str or '\x00' in a for a in command):
        return None
    group = ('/user.slice/user-{uid}.slice/user@{uid}.service/projectiq.slice/'
             'projectiq-eval.slice/{unit}.scope').format(uid=controller_uid, unit=unit)
    args = ['/usr/bin/python3', '-I', '-c', APP_LIMIT_LAUNCHER, *command]
    wanted = [arg.encode('utf-8') for arg in args]
    try:
        scope = cgroup_root.joinpath(*group.strip('/').split('/'))
        if scope.is_symlink() or not scope.is_dir():
            return None
        pids = (scope / 'cgroup.procs').read_text(encoding='ascii').split()
        if len(pids) > 128:
            return None
        for raw in pids:
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
                return None
            pid = int(raw)
            if pid <= 1:
                return None
            try:
                task = proc_root / raw
                status = (task / 'status').read_text(encoding='utf-8')[:16000]
                uid_rows = [line.split()[1:] for line in status.splitlines()
                            if line.startswith('Uid:')]
                states = [line.split()[1] for line in status.splitlines()
                          if line.startswith('State:')]
                if (len(uid_rows) != 1 or len(uid_rows[0]) != 4 or
                        any(not v.isdecimal() or int(v) != app_uid for v in uid_rows[0]) or
                        states != ['S']):
                    continue
                cg_rows = [(line.split(':', 2)[2]) for line in
                           (task / 'cgroup').read_text(encoding='utf-8').splitlines()
                           if line.startswith('0::')]
                if cg_rows != [group]:
                    continue
                actual = (task / 'cmdline').read_bytes()[:16384].split(b'\x00')
                if [v for v in actual if v] == wanted:
                    return pid
            except (OSError, UnicodeError, ValueError):
                continue
        return None
    except (OSError, UnicodeError, ValueError):
        return None


def _stop_build_scope(unit: str, uid: int) -> None:
    """Best-effort cleanup on any failed handoff; VM teardown is the backstop."""
    try:
        env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{uid}',
                   DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
        subprocess.run(['/usr/bin/systemctl', '--user', 'stop', unit + '.scope'],
                       env=env, timeout=5, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _publish_build_release(path: Path) -> None:
    """Atomic controller-only permission to exec; no stop signals to sudo."""
    temporary = path.with_name(path.name + '.tmp')
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or
                parent.st_mode & 0o022 or path.exists() or path.is_symlink()):
            raise GuestFailure('APP_CGROUP_BUILD_GATE_RELEASE_FAILED')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(temporary, flags, 0o644)
        try:
            # Overrides a possibly restrictive SSH/controller umask (e.g. 0077).
            # App UID can read the marker but cannot write or replace it.
            os.fchmod(fd, 0o644)
            if os.write(fd, b'GO\n') != 3:
                raise OSError('Incomplete release marker')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
    except OSError as exc:
        raise GuestFailure('APP_CGROUP_BUILD_GATE_RELEASE_FAILED') from exc


def _audited_build_process(args: list[str], *, env: dict | None, cwd: Path | None,
                           input: bytes | None, timeout: int, kind: str) -> subprocess.CompletedProcess:
    """Verify a waiting, scoped app-UID launcher before atomically releasing it."""
    if not _APP_CGROUP_ACTIVE or kind not in _BUILD_KINDS:
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
    launch = _project_command(args, env, audit_preexec=True)
    units = [x.partition('=')[2] for x in launch if x.startswith('--unit=')]
    gates = [x.partition('=')[2] for x in launch if x.startswith('PROJECTIQ_ATTEST_GATE=')]
    if (len(units) != 1 or not re.fullmatch(r'piq-app-[0-9a-f]{16}', units[0]) or
            len(gates) != 1 or
            not re.fullmatch(r'/home/projectiq/\.projectiq-build-release-[0-9a-f]{32}', gates[0])):
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
    unit = units[0]
    gate = Path(gates[0])
    temporary = gate.with_name(gate.name + '.tmp')
    controller_uid = os.getuid()
    try:
        app_uid = pwd.getpwnam(APP_USER).pw_uid
        started_at = time.monotonic()
        child = subprocess.Popen(launch, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    except (OSError, KeyError, AttributeError) as exc:
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED') from exc
    try:
        deadline = min(started_at + timeout, time.monotonic() + 15)
        pid = None
        while time.monotonic() < deadline:
            pid = _gated_build_pid(unit, args, controller_uid=controller_uid, app_uid=app_uid)
            if pid is not None or child.poll() is not None:
                break
            time.sleep(0.03)
        if pid is None:
            raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
        # The controller, not the app UID, alone can publish into its 0711 home.
        # This avoids stopping sudo's monitor process via SIGSTOP/SIGCONT.
        _publish_build_release(gate)
        remaining = timeout - (time.monotonic() - started_at)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(launch, timeout)
        try:
            stdout, stderr = child.communicate(input=input, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise GuestFailure('APP_CGROUP_BUILD_EXEC_TIMEOUT') from exc
        if child.returncode == 0:
            _APP_BUILD_HANDOFFS.append(kind)
        return subprocess.CompletedProcess(launch, child.returncode, stdout, stderr)
    except (OSError, subprocess.TimeoutExpired, GuestFailure) as exc:
        _stop_build_scope(unit, controller_uid)
        if child.poll() is None:
            child.kill()
        try:
            child.communicate(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if isinstance(exc, subprocess.TimeoutExpired):
            raise GuestFailure('APP_CGROUP_BUILD_EXEC_TIMEOUT') from exc
        if isinstance(exc, GuestFailure):
            raise
        raise GuestFailure('APP_CGROUP_BUILD_GATE_RELEASE_FAILED') from exc
    finally:
        # Only controller-owned, exact non-user-writable gate paths are used.
        for path in (gate, temporary):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _build_handoff_report(adapter: str) -> dict:
    """Require exact expected successful handoffs; no silent missing builds."""
    expected = _BUILD_REQUIRED.get(adapter)
    if expected is None:
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
    actual = {kind: _APP_BUILD_HANDOFFS.count(kind) for kind in _BUILD_KINDS}
    if any(actual[k] != expected.get(k, 0) for k in _BUILD_KINDS):
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
    return {'status': 'PREEXEC_KERNEL_HANDOFF_VERIFIED' if expected else 'NOT_APPLICABLE',
            'npm_ci': actual['npm_ci'], 'vite_build': actual['vite_build'],
            'pip_install': actual['pip_install']}


def _project_command(command: list[str], env: dict | None = None, *, audit_preexec: bool = False) -> list[str]:
    """Only project-owned subprocesses use sudo, exclusively AS APP_USER."""
    allowed = {'PATH', 'PORT', 'HOST', 'NODE_ENV', 'CI', 'PYTHONPATH',
               'PYTHONUNBUFFERED', 'PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE',
               'PIP_CONFIG_FILE', 'PIP_DISABLE_PIP_VERSION_CHECK', 'LC_ALL',
               'PROJECTIQ_PDO_CONNECTOR'}
    env_vars = {'HOME': os.environ.get('PROJECTIQ_QUOTA_HOME', '/home/' + APP_USER),
                'PATH': '/usr/local/bin:/usr/bin:/bin'}
    if 'PROJECTIQ_QUOTA_TMP' in os.environ:
        env_vars['TMPDIR'] = os.environ['PROJECTIQ_QUOTA_TMP']
        env_vars['TMP'] = os.environ['PROJECTIQ_QUOTA_TMP']
        env_vars['TEMP'] = os.environ['PROJECTIQ_QUOTA_TMP']
    for k, v in (env or {}).items():
        if k in allowed or k.startswith(('NPM_CONFIG_', 'PROJECTIQ_DB_')):
            if isinstance(v, str) and '\x00' not in v and '\n' not in k and '=' not in k:
                env_vars[k] = v
    # The resource launcher itself runs as APP_USER and cannot raise its hard
    # ceilings. It execs the adapter's approved command without a shell, so
    # all descendants inherit the same kernel-enforced limits.
    if not command or any(not isinstance(arg, str) or '\x00' in arg for arg in command):
        raise GuestFailure('APP_RESOURCE_LAUNCH_INVALID_COMMAND')
    if _APP_CGROUP_ACTIVE:
        env_vars['PROJECTIQ_EVAL_REQUIRED'] = '1'
        if audit_preexec:
            env_vars['PROJECTIQ_ATTEST_PAUSE'] = '1'
            env_vars['PROJECTIQ_ATTEST_GATE'] = ('/home/projectiq/.projectiq-build-release-'
                                                 + secrets.token_hex(16))
    elif audit_preexec:
        raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
    runas = ['sudo', '-n', '-u', APP_USER, '--', '/usr/bin/env', '-i',
             *[key + '=' + value for key, value in sorted(env_vars.items())],
             '/usr/bin/python3', '-I', '-c', APP_LIMIT_LAUNCHER, *command]
    if not _APP_CGROUP_ACTIVE:
        return runas  # Isolated cache-preparation VMs only; no evaluation source.
    uid = os.getuid()
    return ['/usr/bin/env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
            f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
            '/usr/bin/systemd-run', '--user', '--scope', '--quiet',
            '--slice=' + APP_CGROUP_SLICE, '--unit=piq-app-' + secrets.token_hex(8),
            *(['/usr/bin/sudo'] + runas[1:])]


def _trusted_process(args: list[str], *, input: bytes | None = None, timeout: int = 15,
                     env: dict | None = None, cwd: Path | None = None,
                     project_code: bool = False, audit_kind: str | None = None) -> subprocess.CompletedProcess:
    if audit_kind is not None:
        if not project_code or audit_kind not in _BUILD_KINDS:
            raise GuestFailure('APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED')
        if _APP_CGROUP_ACTIVE:
            return _audited_build_process(args, env=env, cwd=cwd, input=input,
                                          timeout=timeout, kind=audit_kind)
        # Only the dedicated cache-preparation VM runs without the eval slice.
    try:
        return subprocess.run(_project_command(args, env) if project_code else args,
                              input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False,
                              env=None if project_code else env, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        # Distinguish an unrelated trusted-tool timeout from the audited
        # stop/resume handshake. Do not include argv, logs or secrets.
        if _APP_CGROUP_ACTIVE:
            raise GuestFailure('APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT') from exc
        raise GuestFailure('Trusted guest tool missing or timed out') from exc
    except OSError as exc:
        raise GuestFailure('Trusted guest tool missing or timed out') from exc


def _project_popen(command: list[str], *, cwd: Path, env: dict, log) -> subprocess.Popen:
    try:
        launch = _project_command(command, env)
        process = subprocess.Popen(launch, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, cwd=cwd,
                                   start_new_session=True)
    except OSError as exc:
        raise GuestFailure('RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE') from exc
    if _APP_CGROUP_ACTIVE:
        try:
            _verify_live_server_process(launch, command, process)
        except GuestFailure:
            # The controller must not continue an unverified server. Stop its
            # whole process group, then the disposable VM supplies a backstop.
            _d4_stop_process(process)
            raise
    return process


def _php_pdo_rebind(checkout: Path) -> None:
    """Generalized, conservative PDO 4-variable adapter; VM copy ONLY."""
    candidate = checkout / 'db' / 'db.php'
    if not candidate.is_file() or candidate.stat().st_size > 30_000:
        raise GuestFailure('PHP PDO connection adapter not recognized')
    content = candidate.read_text(encoding='utf-8-sig')
    if not re.search(r'new\s+PDO\s*\(', content):
        raise GuestFailure('No recognizable PDO connection')
    mapping = {
        'host': 'PROJECTIQ_DB_HOST', 'dbname': 'PROJECTIQ_DB_NAME',
        'user': 'PROJECTIQ_DB_USER', 'pass': 'PROJECTIQ_DB_PASSWORD',
    }
    for var, env in mapping.items():
        pattern = re.compile(r'(?m)^(\s*\$' + var + r'\s*=\s*)([^;\r\n]{1,250})(;\s*)$')
        matches = list(pattern.finditer(content))
        if len(matches) != 1:
            raise GuestFailure('PDO connection field not uniquely identifiable')
        content = pattern.sub(lambda m: m.group(1) + "getenv('" + env + "')" + m.group(3), content)
    candidate.write_text(content, encoding='utf-8')


def _check_sql(path: Path) -> bytes:
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise GuestFailure('Expected one bounded SQL dump')
    contents = path.read_bytes()
    # MySQL dumps may be UTF-16 with a byte-order mark (for example,
    # exports produced by a Windows client). A byte-level NUL check rejects
    # valid UTF-16 text, so decode *strictly* before checking text content.
    # Require the BOM for UTF-16; do not guess encodings for binary inputs.
    encoding = 'utf-16' if contents.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        decoded = contents.decode(encoding)
    except UnicodeError as exc:
        raise GuestFailure('SQL dump text encoding is not supported') from exc
    if '\x00' in decoded:
        raise GuestFailure('SQL binary content is not supported')
    # MySQL mysqldump version comments are accepted but the regex also scans them.
    if DENIED_SQL.search(decoded):
        raise GuestFailure('SQL dump uses operations outside the pilot allowlist')
    return decoded.encode('utf-8')


def _verify_mysql_service_cgroup(*, cgroup_root: Path = Path('/sys/fs/cgroup'),
                                 proc_root: Path = Path('/proc'),
                                 mysql_data: Path = Path('/var/lib/mysql')) -> None:
    """Fail closed on missing MySQL service limits before using DB credentials.

    Trust is established from the live daemon UID, kernel membership, and
    numeric cgroup-v2 files, not from systemd property names alone. The
    operator-controlled clean VM bootstrap installs these ceilings before
    publishing any credentials; this runtime check closes ordering gaps.
    """
    error = 'MYSQL_RESOURCE_ENFORCEMENT_UNAVAILABLE'
    try:
        if pwd is None:
            raise ValueError('Linux identity database unavailable')
        def systemd_value(property_name: str) -> str:
            result = _trusted_process(['/usr/bin/systemctl', 'show', '-P',
                                       property_name, 'mysql.service'], timeout=8)
            if result.returncode != 0 or len(result.stdout) > 256:
                raise ValueError('Systemd service property unavailable')
            return result.stdout.decode('ascii', 'strict').strip()
        if (systemd_value('ActiveState') != 'active' or
                systemd_value('ControlGroup') != '/system.slice/mysql.service'):
            raise ValueError('Untrusted MySQL unit state')
        raw_pid = systemd_value('MainPID')
        if not raw_pid.isascii() or not raw_pid.isdecimal() or not 1 < int(raw_pid) < 2**22:
            raise ValueError('Untrusted MySQL daemon identity')
        pid = int(raw_pid)
        group = cgroup_root / 'system.slice' / 'mysql.service'
        if group.is_symlink() or not group.is_dir():
            raise ValueError('Missing MySQL kernel cgroup')
        proc = proc_root / str(pid)
        if (proc / 'cgroup').read_text('ascii').splitlines() != [
                '0::/system.slice/mysql.service']:
            raise ValueError('MySQL daemon outside system cgroup')
        if raw_pid not in (group / 'cgroup.procs').read_text('ascii').split():
            raise ValueError('MySQL daemon not in verified cgroup')
        uid_rows = [row.split()[1:] for row in
                    (proc / 'status').read_text('utf-8').splitlines()
                    if row.startswith('Uid:')]
        mysql_uid = pwd.getpwnam('mysql').pw_uid
        if (mysql_uid in (0, os.getuid()) or len(uid_rows) != 1 or
                len(uid_rows[0]) != 4 or
                any(not x.isdecimal() or int(x) != mysql_uid for x in uid_rows[0])):
            raise ValueError('MySQL daemon UID mismatch')
        for filename, max_value in (('memory.max', 536870912), ('pids.max', 64)):
            value = (group / filename).read_text('ascii').strip()
            if not value.isascii() or not value.isdecimal() or not 0 < int(value) <= max_value:
                raise ValueError('MySQL kernel cap unavailable')
        disk = mysql_data.stat().st_dev
        source = f'{os.major(disk)}:{os.minor(disk)}'
        targets = {source}
        # cgroup io.max may use the whole-disk major:minor for a filesystem
        # on a partition. Accept it only when sysfs proves that parent.
        try:
            sysdev = Path('/sys/dev/block')
            node = (sysdev / source).resolve(strict=True)
            if node.is_relative_to(Path('/sys/devices')):
                part = (node / 'partition').read_text('ascii').strip()
                dev = (node.parent / 'dev').read_text('ascii').strip()
                if (part.isascii() and part.isdecimal() and int(part) > 0 and
                        re.fullmatch(r'[0-9]{1,5}:[0-9]{1,7}', dev)):
                    targets.add(dev)
        except (OSError, UnicodeError, ValueError, RuntimeError):
            pass
        rows = [line.split() for line in (group / 'io.max').read_text('ascii').splitlines()]
        if not any(row and row[0] in targets and 'wbps=8388608' in row[1:]
                   for row in rows):
            raise ValueError('MySQL kernel IO write cap unavailable')
    except (OSError, UnicodeError, ValueError, KeyError, AttributeError,
            subprocess.TimeoutExpired) as exc:
        raise GuestFailure(error) from exc


def _wait_mysql_runtime_ready(*, seconds: int = 75,
                              credential_path: Path = Path('/home/projectiq/.projectiq_mysql_credentials.json')) -> dict:
    """Wait for the ROOT-owned, cloud-init bootstrap to publish scoped DB creds.

    No sudo MySQL command can execute from the controller or application user.
    This file is written atomically with permission 0600 by trusted cloud-init.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            st = credential_path.stat()
            if st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0 and st.st_size < 512:
                meta = json.loads(credential_path.read_text('utf-8'))
                if (meta.get('host') == '127.0.0.1' and meta.get('database') == 'projectiq_run'
                        and meta.get('user') == 'projectiq_app'
                        and re.fullmatch(r'[0-9a-f]{48}', meta.get('password', ''))):
                    _verify_mysql_service_cgroup()
                    return meta
        except (OSError, ValueError, TypeError):
            pass
        time.sleep(1)
    raise GuestFailure('MySQL trusted cloud-init bootstrap unavailable')


def _provision_mysql(checkout: Path, schema_relpath: str) -> tuple[dict, str]:
    """Import a reviewed dump using a disposable DATABASE-SCOPED user only."""
    sql_path = checkout.joinpath(*PurePosixPath(schema_relpath).parts)
    sql_data = _check_sql(sql_path)
    meta = _wait_mysql_runtime_ready()
    dbname = meta['database']
    env = dict(os.environ)
    env.update({'PROJECTIQ_DB_HOST': meta['host'], 'PROJECTIQ_DB_NAME': dbname,
                'PROJECTIQ_DB_USER': meta['user'], 'PROJECTIQ_DB_PASSWORD': meta['password']})
    with tempfile.NamedTemporaryFile(mode='w', prefix='projectiq_mysql_', delete=False,
                                     dir='/tmp') as cfg:
        cfg.write('[client]\nuser=' + meta['user'] + '\npassword=' + meta['password'] +
                  '\nhost=127.0.0.1\n')
        config_path = Path(cfg.name)
    try:
        config_path.chmod(0o600)
        # Controller owns the temporary config; database import is controller-
        # managed but has ONLY database-scoped MySQL privileges, never root.
        imported = _trusted_process(['/usr/bin/mysql', f'--defaults-extra-file={config_path}',
                                     '--binary-mode', '--batch', '--database', dbname],
                                    input=sql_data, timeout=30)
        if imported.returncode:
            raise GuestFailure('Schema import failed (SQL compatibility or permissions)')
        tables = _trusted_process(['/usr/bin/mysql', f'--defaults-extra-file={config_path}',
                                   '--batch', '--skip-column-names', '-D', dbname,
                                   '-e', 'SHOW TABLES;'])
        count = len([x for x in tables.stdout.decode('utf-8', 'replace').splitlines() if x.strip()])
        if tables.returncode or count < 1:
            raise GuestFailure('SQL import produced no accessible tables')
    finally:
        config_path.unlink(missing_ok=True)
    _php_pdo_rebind(checkout)
    check_env = dict(env)
    check_env['PROJECTIQ_PDO_CONNECTOR'] = str(checkout / 'db' / 'db.php')
    # Execute student-controlled PHP connector AS the unprivileged APP_USER.
    php_check = _trusted_process([
        '/usr/bin/php', '-r',
        'require getenv("PROJECTIQ_PDO_CONNECTOR"); '
        'if (!isset($pdo) || (int)$pdo->query("SELECT 1")->fetchColumn() !== 1) exit(3);',
    ], env=check_env, timeout=15, project_code=True)
    if php_check.returncode:
        raise GuestFailure('PHP PDO failed to connect to disposable MySQL')
    return env, f'{count} SQL table(s) initialized; PHP PDO connectivity verified'


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _node_tar_extract(archive: Path, version: str, base: Path) -> tuple[Path, Path]:
    """Unpack only an operator-pinned official linux-x64 Node.js tarball."""
    expected_root = f'node-{version}-linux-x64'
    if not (archive.is_file() and 0 < archive.stat().st_size <= 65_000_000):
        raise GuestFailure('NODE_VITE_NODE_ARCHIVE_INVALID')
    try:
        with tarfile.open(archive, 'r:xz') as bundle:
            members = bundle.getmembers()
            if (not 0 < len(members) <= 20000
                    or sum(x.size for x in members) > 350_000_000
                    or any((x.name != expected_root and not x.name.startswith(expected_root + '/'))
                           or x.isdev() or x.isfifo() for x in members)):
                raise GuestFailure('NODE_VITE_NODE_ARCHIVE_INVALID')
            # Python 3.12+ 'data' filter rejects traversal and unsafe links.
            bundle.extractall(base, filter='data')
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise GuestFailure('NODE_VITE_RUNTIME_EXTRACTION_FAILED') from exc
    node_dir = base / expected_root
    node = node_dir / 'bin' / 'node'
    npm = node_dir / 'bin' / 'npm'
    if not node.is_file() or not npm.is_file() or node.is_symlink():
        raise GuestFailure('NODE_VITE_RUNTIME_EXTRACTION_FAILED')
    return node, npm


def _check_vite_manifest(root: Path, expected_lock_sha: str) -> None:
    pkg = root / 'package.json'
    lock = root / 'package-lock.json'
    if (not pkg.is_file() or pkg.stat().st_size > 150_000 or
            not lock.is_file() or lock.stat().st_size > 4_000_000
            or (root / '.npmrc').exists() or (root / 'npm-shrinkwrap.json').exists()):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    if _sha256(lock) != expected_lock_sha:
        raise GuestFailure('NODE_VITE_LOCKFILE_HASH_MISMATCH')
    try:
        package = json.loads(pkg.read_text('utf-8'))
        locked = json.loads(lock.read_text('utf-8'))
    except (UnicodeError, ValueError, OSError) as exc:
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED') from exc
    if (not isinstance(package, dict) or not isinstance(locked, dict)
            or locked.get('lockfileVersion') not in (2, 3)
            or not isinstance(locked.get('packages'), dict)
            or len(locked['packages']) > 12000 or 'workspaces' in package):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    direct = package.get('dependencies') or {}
    dev = package.get('devDependencies') or {}
    if not isinstance(direct, dict) or not isinstance(dev, dict):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    deps = {**direct, **dev}
    if 'vite' not in deps or any(x in deps for x in ('next', 'nuxt')):
        raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    # No git:, file:, link:, HTTP or alternate registries during the pilot.
    # Offline cache still contains third-party packages and the build executes
    # reviewed code, exclusively inside the disposable guest.
    for value in locked['packages'].values():
        if not isinstance(value, dict) or value.get('link') is True:
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        version = value.get('version')
        if isinstance(version, str) and version.startswith(('file:', 'link:', 'git:', 'git+', 'http:', 'https:')):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        resolved = value.get('resolved')
        if resolved is not None and (not isinstance(resolved, str) or
                                     not resolved.startswith('https://registry.npmjs.org/')):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved and (not isinstance(value.get('integrity'), str)
                         or not value['integrity'].startswith(('sha512-', 'sha384-', 'sha256-'))):
            raise GuestFailure('NODE_VITE_MANIFEST_OR_LOCKFILE_UNSUPPORTED')



def _check_express_manifest(root: Path, expected_lock_sha: str) -> str:
    """Verify supported Express/npm-lock project and choose a fixed JS entry.

    No package.json scripts are executed. Other entrypoint layouts are a future
    adapter capability, not permission to run a user-supplied command.
    """
    pkg, lock = root / 'package.json', root / 'package-lock.json'
    if (not pkg.is_file() or pkg.stat().st_size > 150_000
            or not lock.is_file() or lock.stat().st_size > 4_000_000
            or (root / '.npmrc').exists() or (root / 'npm-shrinkwrap.json').exists()):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    if not isinstance(expected_lock_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_lock_sha):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    if _sha256(lock) != expected_lock_sha:
        raise GuestFailure('NODE_EXPRESS_LOCKFILE_HASH_MISMATCH')
    try:
        package = json.loads(pkg.read_text('utf-8'))
        locked = json.loads(lock.read_text('utf-8'))
    except (UnicodeError, ValueError, OSError) as exc:
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED') from exc
    if (not isinstance(package, dict) or not isinstance(locked, dict)
            or locked.get('lockfileVersion') not in (2, 3)
            or not isinstance(locked.get('packages'), dict)
            or not 0 < len(locked['packages']) <= 12000
            or 'workspaces' in package
            or package.get('type', 'commonjs') not in ('module', 'commonjs')):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    direct = package.get('dependencies') or {}
    dev = package.get('devDependencies') or {}
    if (not isinstance(direct, dict) or not isinstance(dev, dict)
            or not isinstance(direct.get('express'), str)
            or any(x in {**direct, **dev} for x in ('vite', 'next', 'nuxt'))):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    # Accept only ordinary registry semver specifications. Even with an
    # apparently valid lockfile, do not let npm resolve URL/git/file/alias specs.
    all_direct = {**direct, **dev}
    version_spec = re.compile(r'^[~^]?[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$')
    if (not all(isinstance(k, str) and isinstance(v, str)
                and version_spec.fullmatch(v) for k, v in all_direct.items())
            or not isinstance(locked['packages'].get(''), dict)
            or locked['packages'][''].get('dependencies', {}) != direct
            or locked['packages'][''].get('devDependencies', {}) != dev):
        raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    for value in locked['packages'].values():
        if not isinstance(value, dict) or value.get('link') is True:
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        version, resolved = value.get('version'), value.get('resolved')
        if isinstance(version, str) and version.startswith(('file:', 'link:', 'git:', 'git+', 'http:', 'https:')):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved is not None and (not isinstance(resolved, str) or
                                     not resolved.startswith('https://registry.npmjs.org/')):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
        if resolved and (not isinstance(value.get('integrity'), str)
                         or not value['integrity'].startswith(('sha512-', 'sha384-', 'sha256-'))):
            raise GuestFailure('NODE_EXPRESS_MANIFEST_OR_LOCKFILE_UNSUPPORTED')
    candidates = ('server.js', 'app.js', 'index.js')
    explicit = package.get('main')
    if explicit is not None:
        if not isinstance(explicit, str) or explicit not in candidates:
            raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
        chosen = explicit
    else:
        present = [name for name in candidates if (root / name).is_file()]
        if len(present) != 1:
            raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
        chosen = present[0]
    entry = root / chosen
    if (not entry.is_file() or entry.is_symlink()
            or not 0 < entry.stat().st_size <= 300_000):
        raise GuestFailure('NODE_EXPRESS_ENTRYPOINT_UNSUPPORTED')
    return chosen


def _node_express_root(checkout: Path, req: dict) -> Path:
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str)
            and re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', root_rel)
            and all(s not in ('.', '..', '') for s in root_rel.split('/'))):
        raise GuestFailure('NODE_EXPRESS_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_EXPRESS_INVALID_PROJECT_ROOT')
    return root


def _prepare_express(checkout: Path, base: Path, req: dict, *, port: int = 8765) -> tuple[list[str], dict, str]:
    if req.get('adapter') != 'node_express_offline_v1':
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    root = _node_express_root(checkout, req)
    keys = ('node_sha256', 'npm_cache_sha256', 'lockfile_sha256')
    if any(not isinstance(req.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', req[k]) for k in keys):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    entry = _check_express_manifest(root, req['lockfile_sha256'])
    node_archive, cache_archive = base / 'node_runtime.tar.xz', base / 'npm_cache.zip'
    if not node_archive.is_file() or not cache_archive.is_file():
        raise GuestFailure('NODE_EXPRESS_INVALID_REQUEST')
    if _sha256(node_archive) != req['node_sha256']:
        raise GuestFailure('NODE_EXPRESS_NODE_HASH_MISMATCH')
    if _sha256(cache_archive) != req['npm_cache_sha256']:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH')
    runtime_dir = base / 'node_runtime'
    runtime_dir.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime_dir)
    node_archive.unlink(missing_ok=True)
    _share_tree(runtime_dir, writable=False)
    check = _trusted_process([str(node), '--version'], timeout=15)
    if check.returncode or check.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_EXPRESS_RUNTIME_VERSION_MISMATCH')
    if not 0 < cache_archive.stat().st_size <= 170_000_000:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH')
    try:
        npm_cache = safe_extract(cache_archive, base / 'npm_cache', max_total=250_000_000)
    except (GuestFailure, zipfile.BadZipFile) as exc:
        raise GuestFailure('NODE_EXPRESS_CACHE_HASH_MISMATCH') from exc
    cache_archive.unlink(missing_ok=True)
    _share_tree(npm_cache, writable=True)
    # Do not allow inherited npm / Node injection knobs or host credentials.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base), **_isolated_npm_config_paths(base),
                'NPM_CONFIG_CACHE': str(npm_cache), 'NPM_CONFIG_OFFLINE': 'true',
                'NPM_CONFIG_IGNORE_SCRIPTS': 'true', 'NPM_CONFIG_AUDIT': 'false',
                'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false',
                'CI': 'true', 'PORT': str(port), 'HOST': '127.0.0.1',
                'NODE_ENV': 'production'})
    installed = _trusted_process([str(npm), 'ci', '--offline', '--ignore-scripts',
                                  '--no-audit', '--no-fund', '--no-progress',
                                  '--cache', str(npm_cache)], timeout=185, env=env, cwd=root,
                                  project_code=True, audit_kind='npm_ci')
    if installed.returncode:
        raise GuestFailure('NODE_EXPRESS_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    if not (root / 'node_modules' / 'express' / 'package.json').is_file():
        raise GuestFailure('NODE_EXPRESS_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    return [str(node), str(root / entry)], env, f'Express offline npm ci completed with {version}; fixed entrypoint {entry}'


def _probe_express(path: str, *, port: int = 8765) -> tuple[int, str]:
    if path not in ('/health', '/api/health', '/'):
        raise GuestFailure('NODE_EXPRESS_HTTP_PROBE_FAILED')
    for _ in range(30):
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{port}{path}',
                headers={'User-Agent': 'ProjectIQ-Express-Probe'})
            with urllib.request.urlopen(request, timeout=2) as response:
                data = response.read(65_536)
                if response.status != 200 or not data:
                    continue
                # D3 API readiness requires JSON, but no invented business
                # assertions or test-generated expected responses.
                parsed = json.loads(data)
                if isinstance(parsed, (dict, list)):
                    return 200, 'HTTP_RESPONSE_OK'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('NODE_EXPRESS_HTTP_PROBE_FAILED')


def _isolated_npm_config_paths(base: Path) -> dict[str, str]:
    """Give npm distinct, empty user/global config files outside the checkout.

    npm 11 rejects loading the same path for both scopes, even when that path
    is /dev/null. Both files are guest-local and cannot contain host secrets.
    """
    config_dir = base / '.projectiq-npm-config'
    config_dir.mkdir(mode=0o700)
    result = {}
    for setting, filename in (
        ('NPM_CONFIG_USERCONFIG', 'user.npmrc'),
        ('NPM_CONFIG_GLOBALCONFIG', 'global.npmrc'),
    ):
        path = config_dir / filename
        with path.open('xb'):
            pass
        path.chmod(0o600)
        result[setting] = str(path)
    _share_tree(config_dir, writable=True)
    return result


def _prepare_vite(checkout: Path, base: Path, req: dict) -> tuple[Path, str]:
    """Offline npm ci and Vite build; NEVER execute student commands on host."""
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch('[A-Za-z0-9_./-]{1,180}', root_rel)
            and not any(x in ('.', '..', '') for x in root_rel.split('/'))):
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    digests = ('node_sha256', 'npm_cache_sha256', 'lockfile_sha256')
    if any(not isinstance(req.get(k), str) or not re.fullmatch('[0-9a-f]{64}', req[k]) for k in digests):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_archive = base / 'node_runtime.tar.xz'
    cache_archive = base / 'npm_cache.zip'
    if not node_archive.is_file() or not cache_archive.is_file():
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    if _sha256(node_archive) != req['node_sha256']:
        raise GuestFailure('NODE_VITE_NODE_HASH_MISMATCH')
    if _sha256(cache_archive) != req['npm_cache_sha256']:
        raise GuestFailure('NODE_VITE_CACHE_HASH_MISMATCH')
    _check_vite_manifest(root, req['lockfile_sha256'])
    runtime_dir = base / 'node_runtime'
    runtime_dir.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime_dir)
    node_archive.unlink(missing_ok=True)
    _share_tree(runtime_dir, writable=False)
    node_version = _trusted_process([str(node), '--version'], timeout=15)
    if node_version.returncode or node_version.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_VITE_RUNTIME_VERSION_MISMATCH')
    if not 0 < cache_archive.stat().st_size <= 170_000_000:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
    try:
        npm_cache = safe_extract(cache_archive, base / 'npm_cache', max_total=250_000_000)
    except (GuestFailure, zipfile.BadZipFile) as exc:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID') from exc
    cache_archive.unlink(missing_ok=True)
    _share_tree(npm_cache, writable=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base), **_isolated_npm_config_paths(base), 'NPM_CONFIG_CACHE': str(npm_cache),
                'NPM_CONFIG_OFFLINE': 'true', 'NPM_CONFIG_IGNORE_SCRIPTS': 'true',
                'NPM_CONFIG_AUDIT': 'false', 'NPM_CONFIG_FUND': 'false',
                'NPM_CONFIG_UPDATE_NOTIFIER': 'false', 'CI': 'true'})
    installed = _trusted_process([str(npm), 'ci', '--offline', '--ignore-scripts',
                                  '--no-audit', '--no-fund', '--no-progress',
                                  '--cache', str(npm_cache)], timeout=185, env=env, cwd=root,
                                  project_code=True, audit_kind='npm_ci')
    if installed.returncode:
        raise GuestFailure('NODE_VITE_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    vite = root / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if not vite.is_file() or not vite.resolve().is_relative_to(root.resolve()):
        raise GuestFailure('NODE_VITE_BUILD_FAILED')
    dist = root / 'dist'
    if dist.is_dir():
        shutil.rmtree(dist)
    built = _trusted_process([str(node), str(vite), 'build', '--outDir', 'dist'],
                             timeout=125, env=env, cwd=root, project_code=True,
                             audit_kind='vite_build')
    if built.returncode:
        raise GuestFailure('NODE_VITE_BUILD_FAILED')
    index = dist / 'index.html'
    if not index.is_file() or index.stat().st_size > 2_000_000 or b'<html' not in index.read_bytes().lower():
        raise GuestFailure('NODE_VITE_BUILD_OUTPUT_MISSING')
    return dist, f'Vite offline npm ci + build completed with {version}; dist/index.html generated'



def _classify_npm_cache_failure(stderr: bytes) -> str:
    """Return only a fixed, non-sensitive diagnostic for failed npm ci.

    npm output may contain package URLs, server messages, token-bearing URLs,
    or arbitrary text from an approved repository. It MUST NOT be sent to the
    host or persisted in reports. Inspect a bounded tail and return fixed codes.
    """
    sample = (stderr or b'')[-24_000:].decode('utf-8', errors='replace').lower()
    categories = (
        ('NODE_VITE_NPM_TLS_CERTIFICATE',
         ('self_signed_cert_in_chain', 'unable_to_verify_leaf_signature',
          'unable_to_get_issuer_cert', 'cert_has_expired', 'err_tls_',
          'certificate verify failed', 'depth_zero_self_signed_cert')),
        ('NODE_VITE_NPM_NETWORK_DNS', ('eai_again', 'enotfound', 'getaddrinfo',
                                      'name or service not known', 'temporary failure in name resolution')),
        ('NODE_VITE_NPM_NETWORK_TIMEOUT', ('etimedout', 'esockettimedout', 'fetch timeout')),
        ('NODE_VITE_NPM_NETWORK_CONNECTIVITY',
         ('econnreset', 'econnrefused', 'enetunreach', 'ehostunreach', 'econnaborted')),
        ('NODE_VITE_NPM_DISK_SPACE', ('enospc', 'no space left on device')),
        ('NODE_VITE_NPM_FILE_PERMISSION', ('eacces', 'eperm', 'permission denied')),
        ('NODE_VITE_NPM_INTEGRITY', ('eintegrity', 'integrity checksum failed')),
        ('NODE_VITE_NPM_LOCKFILE_INCONSISTENT',
         ('npm ci can only install packages when your package.json',
          'missing from lock file', 'invalid: ', 'npm error code eusage',
          'npm err! code eusage')),
        ('NODE_VITE_NPM_DEPENDENCY_CONFLICT', ('eresolve', 'unable to resolve dependency tree')),
        ('NODE_VITE_NPM_REGISTRY_DENIED', ('npm error code e403', 'npm err! code e403',
                                          '403 forbidden')),
        ('NODE_VITE_NPM_REGISTRY_NOT_FOUND', ('npm error code e404', 'npm err! code e404',
                                             '404 not found')),
        ('NODE_VITE_NPM_PLATFORM_UNSUPPORTED', ('ebadplatform', 'ebadengine')),
        ('NODE_VITE_NPM_AUTH_REQUIRED', ('npm error code e401', 'npm err! code e401')),
        ('NODE_VITE_NPM_MISSING_FILE', ('npm error code enoent', 'npm err! code enoent',
                                        'cannot find module', 'err_module_not_found')),
        ('NODE_VITE_NPM_INVALID_PACKAGE', ('ejsonparse', 'invalid version:',
                                           'invalid tag name', 'invalid package name')),
        ('NODE_VITE_NPM_UNSUPPORTED_PROTOCOL', ('eunsupportedprotocol', 'unsupported url type')),
    )
    for code, indicators in categories:
        if any(indicator in sample for indicator in indicators):
            return code
    return 'NODE_VITE_NPM_FAILURE_UNCLASSIFIED'


def _npm_operator_safe_hint(proc: subprocess.CompletedProcess, cache: Path) -> str:
    """Opt-in, bounded npm process diagnostics for an approved fetch-only VM.

    The old filter required an explicit `npm error` prefix and discarded shell,
    loader, signal and Node.js runtime failures. Return fixed numeric metadata
    plus at most three aggressively scrubbed *error-like* line fragments.
    Never return complete logs, URLs, paths, credentials or source snippets.
    """
    logdir = cache / '_logs'
    channels = [('stderr', bytes(proc.stderr or b'')[-12_000:]),
                ('stdout', bytes(proc.stdout or b'')[-6_000:])]
    logs = 0
    if logdir.is_dir() and not logdir.is_symlink():
        try:
            for log in sorted(logdir.glob('*-debug-0.log'), reverse=True)[:2]:
                if log.is_symlink() or not log.is_file():
                    continue
                with log.open('rb') as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 24_000))
                    channels.append(('log', f.read(24_000)))
                    logs += 1
        except OSError:
            pass
    try:
        exit_status = int(proc.returncode)
    except (ValueError, TypeError):
        exit_status = 999
    # Numeric metadata is safe even if npm writes nothing recognizable.
    facts = (f'npm exit {max(-999, min(exit_status, 999))} '
             f'stdout bytes {len(proc.stdout or b"")} '
             f'stderr bytes {len(proc.stderr or b"")} debug logs {logs}')
    seen = set()
    highlights = []
    for channel, data in channels:
        for raw in data.decode('utf-8', 'replace').splitlines()[-80:]:
            # Only infrastructure/process error phrases, not arbitrary source
            # output, are candidates for the opt-in diagnostic.
            if not re.search(r'(?i)\b(?:npm|error|err!|fatal|fail(?:ed|ure)?|killed|'
                             r'exception|exit|heap|module|libc|denied|signal|segfault)\b', raw):
                continue
            if re.search(r'(?i)token|password|secret|credential|authorization|bearer|'
                         r'api[_-]?key|_auth|npmrc|private', raw):
                continue
            if 'complete log of this run' in raw.lower():
                continue
            phrase = re.sub(r'(?i)https?://\S+', '[URL]', raw)
            phrase = re.sub(r'(?i)(?:[a-z]:[\\/]|/(?:home|tmp|var|root|etc|mnt|usr)/)\S*',
                            '[PATH]', phrase)
            phrase = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', phrase)
            phrase = re.sub(r'\x22[^\x22]{1,180}\x22|\x27[^\x27]{1,180}\x27|\x60[^\x60]{1,180}\x60',
                            '[QUOTED]', phrase)
            phrase = re.sub(r'[^A-Za-z0-9 _.,!?:;()\[\]\-|]', ' ', phrase)
            phrase = ' '.join(phrase.split())[:115]
            if phrase and phrase not in seen:
                highlights.append((channel, phrase))
                seen.add(phrase)
    # Preserve last error-like lines where npm normally places the summary.
    selected = highlights[-3:]
    parts = [facts] + [f'{channel} {line}' for channel, line in selected]
    return ' | '.join(parts)[:640]


def _npm_failure_with_debug_context(proc: subprocess.CompletedProcess, cache: Path) -> str:
    """Classify npm's three possible output channels without exporting log text.

    npm can write meaningful diagnostics only to its local _logs/*debug*.log.
    Read at most two bounded log tails, which remain entirely inside the VM.
    All return values are fixed literals; never include package names, URLs,
    process output, or credentials in the host-visible result.
    """
    output = [bytes(proc.stderr or b'')[-24_000:], bytes(proc.stdout or b'')[-8_000:]]
    logdir = cache / '_logs'
    if logdir.is_dir() and not logdir.is_symlink():
        try:
            candidates = sorted(logdir.glob('*-debug-0.log'), reverse=True)[:2]
            for path in candidates:
                if path.is_symlink() or not path.is_file():
                    continue
                with path.open('rb') as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 32_000))
                    output.append(stream.read(32_000))
        except OSError:
            # Logging is a diagnostic only; it must not cause the fetch to pass.
            pass
    for sample in output:
        category = _classify_npm_cache_failure(sample)
        if category != 'NODE_VITE_NPM_FAILURE_UNCLASSIFIED':
            return category
    if proc.returncode in (-9, 137):
        return 'NODE_VITE_NPM_PROCESS_TERMINATED'
    # The operator explicitly allowed network ONLY in this preparation VM.
    # A fixed public registry reachability probe separates basic network
    # failure from npm/package issues without leaking any untrusted output.
    try:
        with urllib.request.urlopen('https://registry.npmjs.org/-/ping', timeout=10) as response:
            if 200 <= response.status < 400:
                return 'NODE_VITE_NPM_UNCLASSIFIED_REGISTRY_REACHABLE'
            return 'NODE_VITE_NPM_REGISTRY_PROBE_HTTP_ERROR'
    except urllib.error.HTTPError:
        return 'NODE_VITE_NPM_REGISTRY_PROBE_HTTP_ERROR'
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            return 'NODE_VITE_NPM_NETWORK_DNS'
        if isinstance(exc.reason, (ssl.SSLError, ssl.CertificateError)):
            return 'NODE_VITE_NPM_TLS_CERTIFICATE'
        return 'NODE_VITE_NPM_REGISTRY_PROBE_CONNECTION_FAILED'
    except (OSError, ValueError, TimeoutError):
        return 'NODE_VITE_NPM_REGISTRY_PROBE_CONNECTION_FAILED'


def prepare_npm_cache(request_path: Path, *, base_dir: Path = Path('/home/projectiq')) -> dict:
    """Separate, explicitly authorized disposable VM: downloads npm tarballs.

    No student build or lifecycle scripts run here. Unlike runtime VMs this
    trusted fetch VM has outbound network, but still no host filesystem mounts,
    no real credentials, no root sudo, and only a reviewed pinned fixture.
    """
    req = json.loads(request_path.read_text('utf-8'))
    if req.get('operation') != 'prepare_npm_cache' or req.get('adapter') not in ('node_vite_offline_v1', 'node_express_offline_v1'):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    checkout = safe_extract(snapshot, base_dir / 'checkout')
    _share_tree(checkout, writable=True)
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch('[A-Za-z0-9_./-]{1,180}', root_rel) and
            not any(x in ('.', '..', '') for x in root_rel.split('/'))):
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir() or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_VITE_INVALID_PROJECT_ROOT')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_sha = req.get('node_sha256')
    lock_sha = req.get('lockfile_sha256')
    if any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in (node_sha, lock_sha)):
        raise GuestFailure('NODE_VITE_INVALID_REQUEST')
    node_archive = base_dir / 'node_runtime.tar.xz'
    if not node_archive.is_file() or _sha256(node_archive) != node_sha:
        raise GuestFailure('NODE_VITE_NODE_HASH_MISMATCH')
    if req['adapter'] == 'node_express_offline_v1':
        _check_express_manifest(root, lock_sha)
    else:
        _check_vite_manifest(root, lock_sha)
    runtime = base_dir / 'node_runtime'
    runtime.mkdir(mode=0o700)
    node, npm = _node_tar_extract(node_archive, version, runtime)
    node_archive.unlink(missing_ok=True)
    ver = _trusted_process([str(node), '--version'], timeout=15)
    if ver.returncode or ver.stdout.decode('utf-8', errors='replace').strip() != version:
        raise GuestFailure('NODE_VITE_RUNTIME_VERSION_MISMATCH')
    cache = base_dir / 'npm-cache'
    cache.mkdir(mode=0o700)
    _share_tree(cache, writable=True)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base_dir), **_isolated_npm_config_paths(base_dir), 'NPM_CONFIG_CACHE': str(cache),
                'NPM_CONFIG_IGNORE_SCRIPTS': 'true', 'NPM_CONFIG_AUDIT': 'false',
                'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false',
                'NPM_CONFIG_REGISTRY': 'https://registry.npmjs.org/', 'CI': 'true'})
    proc = _trusted_process([str(npm), 'ci', '--ignore-scripts', '--no-audit',
                             '--no-fund', '--no-progress', '--cache', str(cache)],
                            timeout=210, env=env, cwd=root, project_code=True)
    if proc.returncode:
        code = _npm_failure_with_debug_context(proc, cache)
        if req['adapter'] == 'node_express_offline_v1':
            code = code.replace('NODE_VITE_', 'NODE_EXPRESS_')
        # Only the specifically authorized preparation VM can return a short,
        # filtered, operator-only diagnostic. The normal report remains a code.
        if req.get('include_npm_diagnostic') is True:
            # Verify npm's own CLI and HTTPS registry access, not just the
            # Python urllib probe. These fixed commands cannot run project
            # install scripts and are limited to the authorized fetch VM.
            try:
                cli = _trusted_process([str(npm), '--version'], timeout=15, env=env, cwd=root)
                cli_status = 'PASS' if cli.returncode == 0 else 'FAIL'
            except GuestFailure:
                cli_status = 'TIMEOUT_OR_MISSING'
            ping_status = 'SKIPPED'
            if cli_status == 'PASS':
                try:
                    ping = _trusted_process([str(npm), 'ping', '--no-progress',
                                             '--fetch-retries=0', '--fetch-timeout=15000'],
                                            timeout=30, env=env, cwd=root)
                    ping_status = 'PASS' if ping.returncode == 0 else 'FAIL'
                except GuestFailure:
                    ping_status = 'TIMEOUT_OR_MISSING'
            meta = (' | npm cli ' + cli_status + ' | npm registry ping ' + ping_status)
            raise NpmPreparationDiagnostic(code, (_npm_operator_safe_hint(proc, cache) + meta)[:640])
        raise GuestFailure(code)
    archive = base_dir / 'npm_cache.zip'
    total = 0
    count = 0
    # Store cache only: no node_modules, checkout source, credentials, logs or
    # outputs are transferred to the Windows host.
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=5, allowZip64=False) as zf:
        for item in sorted(cache.rglob('*')):
            if item.is_dir():
                continue
            if item.is_symlink() or not item.is_file():
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            rel = item.relative_to(cache)
            if '_logs' in rel.parts:
                continue
            if item.stat().st_size > 20_000_000:
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            count += 1
            total += item.stat().st_size
            if count > 12000 or total > 250_000_000:
                raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
            zf.write(item, arcname='npm-cache/' + rel.as_posix())
    if count < 1 or archive.stat().st_size > 170_000_000:
        raise GuestFailure('NODE_VITE_CACHE_ARCHIVE_INVALID')
    return {'status': 'CACHE_PREPARED', 'adapter': req['adapter'],
            'lockfile_sha256': lock_sha, 'npm_cache_sha256': _sha256(archive),
            'node_version': version, 'notes': ['Offline npm cache archived; no student scripts executed']}


# D2 Python/SQLite pilot: invoked only in the disposable Linux guest.
# pip is imported directly from a SHA-pinned wheel, not installed into Ubuntu.
_PYTHON_SHA = re.compile(r'^[0-9a-f]{64}$')
_PYTHON_REQ = re.compile(r'^([A-Za-z][A-Za-z0-9._-]{0,63})==([0-9]+(?:\.[0-9]+){1,3}(?:[a-zA-Z0-9.]*)?)$')
_PYTHON_WHEEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,180}\.whl$')
_PIP_WHEEL_FILE = 'pip-25.2-py3-none-any.whl'


def _check_python_requirements(root: Path, sha256: str, framework: str) -> Path:
    """Conservative single-file requirements: pinned versions, no pip options or URLs."""
    req = root / 'requirements.txt'
    if (not isinstance(sha256, str) or not _PYTHON_SHA.fullmatch(sha256)
            or framework not in ('flask', 'fastapi') or not req.is_file()
            or req.is_symlink() or req.stat().st_size > 16_000):
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
    content = req.read_bytes()
    if _sha256(req) != sha256:
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_HASH_MISMATCH')
    try:
        lines = content.decode('utf-8-sig').splitlines()
    except UnicodeError as exc:
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED') from exc
    packages = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = _PYTHON_REQ.fullmatch(line)
        if not match:
            raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
        name = re.sub('[-_.]+', '-', match.group(1)).lower()
        if name in packages:
            raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
        packages.add(name)
    if (not 1 <= len(packages) <= 25 or framework not in packages
            or framework == 'fastapi' and 'uvicorn' not in packages):
        raise GuestFailure('PYTHON_WEB_REQUIREMENTS_UNSUPPORTED')
    return req


def _python_guest_root(checkout: Path, req: dict) -> Path:
    root_rel = req.get('project_root')
    if not (root_rel == '.' or isinstance(root_rel, str) and
            re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', root_rel) and
            not any(p in ('.', '..', '') for p in root_rel.split('/'))):
        raise GuestFailure('PYTHON_WEB_INVALID_PROJECT_ROOT')
    root = checkout if root_rel == '.' else checkout.joinpath(*PurePosixPath(root_rel).parts)
    if not root.is_dir():
        raise GuestFailure('PYTHON_WEB_INVALID_PROJECT_ROOT')
    return root


def _check_guest_python312() -> None:
    if sys.version_info[:2] != (3, 12):
        raise GuestFailure('PYTHON_WEB_RUNTIME_VERSION_MISMATCH')


def _python_pip_wheel(base: Path, req: dict) -> Path:
    pip_file = base / 'python_pip.whl'
    expected = req.get('pip_sha256')
    if (not isinstance(expected, str) or not _PYTHON_SHA.fullmatch(expected)
            or not pip_file.is_file() or pip_file.stat().st_size > 5_000_000
            or _sha256(pip_file) != expected):
        raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID')
    try:
        with zipfile.ZipFile(pip_file) as z:
            names = set(z.namelist())
            if (len(names) > 4000 or 'pip/__main__.py' not in names
                    or 'pip/__init__.py' not in names
                    or not any(n.startswith('pip-25.2.dist-info/') for n in names)):
                raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID')
    except zipfile.BadZipFile as exc:
        raise GuestFailure('PYTHON_WEB_PIP_BOOTSTRAP_INVALID') from exc
    _share_tree(pip_file, writable=False)
    return pip_file


def _python_pip_env(base: Path, pip_wheel: Path) -> dict:
    """No host or project Python/pip environment variables; distinct config scope."""
    return {'PATH': '/usr/bin:/bin', 'HOME': str(base),
            'PYTHONPATH': str(pip_wheel), 'PYTHONNOUSERSITE': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PIP_CONFIG_FILE': '/dev/null',
            'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'LC_ALL': 'C.UTF-8'}


def _verify_python_pip(base: Path, pip_wheel: Path) -> dict:
    env = _python_pip_env(base, pip_wheel)
    check = _trusted_process(['/usr/bin/python3', '-m', 'pip', '--version'],
                             timeout=15, env=env)
    if check.returncode or not check.stdout.startswith(b'pip 25.2 '):
        raise GuestFailure('PYTHON_WEB_PIP_VERSION_MISMATCH')
    return env


def _check_python_entry(root: Path, framework: str) -> None:
    entry = root / ('main.py' if framework == 'fastapi' else 'app.py')
    if not entry.is_file() or entry.is_symlink() or entry.stat().st_size > 150_000:
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED')
    try:
        body = entry.read_text('utf-8-sig')
    except UnicodeError as exc:
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED') from exc
    marker = 'FastAPI' if framework == 'fastapi' else 'Flask'
    if not re.search(r'\bapp\s*=\s*' + marker + r'\s*\(', body):
        raise GuestFailure('PYTHON_WEB_ENTRYPOINT_UNSUPPORTED')


def _unpack_python_wheelhouse(archive: Path, into: Path) -> list[Path]:
    if not archive.is_file() or not 0 < archive.stat().st_size <= 120_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    into.mkdir(mode=0o700)
    files = []
    total = 0
    seen = set()
    try:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if not 1 <= len(infos) <= 250:
                raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
            for entry in infos:
                if entry.is_dir() and entry.filename == 'wheels/':
                    continue
                name = entry.filename
                if (not name.startswith('wheels/') or name.count('/') != 1
                        or not _PYTHON_WHEEL.fullmatch(name[7:])
                        or entry.is_dir() or entry.flag_bits & 1
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or entry.file_size > 35_000_000 or entry.file_size <= 0
                        or name.casefold() in seen):
                    raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                seen.add(name.casefold())
                total += entry.file_size
                if total > 180_000_000:
                    raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                target = into / name[7:]
                with z.open(entry) as source, target.open('xb') as dest:
                    copied = 0
                    while block := source.read(65536):
                        copied += len(block)
                        if copied > entry.file_size:
                            raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                        dest.write(block)
                    if copied != entry.file_size:
                        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
                files.append(target)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID') from exc
    if not files:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    return files


def prepare_python_wheelhouse(request_path: Path, *, base_dir: Path = Path('/home/projectiq')) -> dict:
    """Operator-approved, networked, fetch-only VM. Only wheel downloads."""
    req = json.loads(request_path.read_text('utf-8'))
    if (req.get('operation') != 'prepare_python_wheels'
            or req.get('adapter') != 'python_web_offline_v1'
            or req.get('python_version') != '3.12'
            or req.get('python_framework') not in ('flask', 'fastapi')):
        raise GuestFailure('PYTHON_WEB_INVALID_REQUEST')
    _check_guest_python312()
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    checkout = safe_extract(snapshot, base_dir / 'checkout')
    _share_tree(checkout, writable=True)
    root = _python_guest_root(checkout, req)
    requirements = _check_python_requirements(root, req.get('requirements_sha256'),
                                             req['python_framework'])
    _check_python_entry(root, req['python_framework'])
    pip_file = _python_pip_wheel(base_dir, req)
    env = _verify_python_pip(base_dir, pip_file)
    wheels = base_dir / 'python-wheels'
    wheels.mkdir(mode=0o700)
    _share_tree(wheels, writable=True)
    command = ['/usr/bin/python3', '-m', 'pip', '--isolated', 'download',
               '--only-binary=:all:', '--no-cache-dir', '--disable-pip-version-check',
               '--dest', str(wheels), '--index-url', 'https://pypi.org/simple',
               '-r', str(requirements)]
    result = _trusted_process(command, timeout=210, env=env, cwd=base_dir, project_code=True)
    if result.returncode:
        # Do not send package names, URLs, logs or requirement contents to host.
        raise GuestFailure('PYTHON_WEB_WHEEL_DOWNLOAD_FAILED')
    members = sorted(wheels.iterdir())
    if not members or len(members) > 150 or any(
        not p.is_file() or p.is_symlink() or not _PYTHON_WHEEL.fullmatch(p.name)
        or not 0 < p.stat().st_size <= 35_000_000 for p in members
    ) or sum(p.stat().st_size for p in members) > 180_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    archive = base_dir / 'python_wheelhouse.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=5, allowZip64=False) as z:
        for member in members:
            z.write(member, arcname='wheels/' + member.name)
    if archive.stat().st_size > 120_000_000:
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_INVALID')
    return {'status': 'PYTHON_WHEELHOUSE_PREPARED',
            'python_version': '3.12', 'framework': req['python_framework'],
            'requirements_sha256': req['requirements_sha256'],
            'wheelhouse_sha256': _sha256(archive),
            'notes': ['Offline Python wheelhouse prepared; no project installed or executed']}


def _prepare_python_web(checkout: Path, base_dir: Path, req: dict,
                        *, port: int = 8765) -> tuple[list[str], dict, str]:
    if (req.get('adapter') != 'python_web_offline_v1' or req.get('python_version') != '3.12'
            or req.get('python_framework') not in ('flask', 'fastapi')):
        raise GuestFailure('PYTHON_WEB_INVALID_REQUEST')
    _check_guest_python312()
    root = _python_guest_root(checkout, req)
    framework = req['python_framework']
    requirements = _check_python_requirements(root, req.get('requirements_sha256'), framework)
    _check_python_entry(root, framework)
    pip_wheel = _python_pip_wheel(base_dir, req)
    env = _verify_python_pip(base_dir, pip_wheel)
    expected = req.get('wheelhouse_sha256')
    archive = base_dir / 'python_wheelhouse.zip'
    if (not isinstance(expected, str) or not _PYTHON_SHA.fullmatch(expected)
            or not archive.is_file() or _sha256(archive) != expected):
        raise GuestFailure('PYTHON_WEB_WHEELHOUSE_HASH_MISMATCH')
    wheels = base_dir / 'python-wheels'
    _unpack_python_wheelhouse(archive, wheels)
    _share_tree(wheels, writable=False)
    archive.unlink(missing_ok=True)
    target = base_dir / 'python-site'
    target.mkdir(mode=0o700)
    _share_tree(target, writable=True)
    cmd = ['/usr/bin/python3', '-m', 'pip', '--isolated', 'install',
           '--no-index', '--find-links', str(wheels), '--no-cache-dir',
           '--only-binary=:all:', '--no-compile', '--disable-pip-version-check',
           '--target', str(target), '-r', str(requirements)]
    result = _trusted_process(cmd, timeout=150, env=env, cwd=base_dir, project_code=True,
                              audit_kind='pip_install')
    if result.returncode:
        raise GuestFailure('PYTHON_WEB_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    env = {**env, 'PYTHONPATH': str(target), 'PYTHONUNBUFFERED': '1'}
    check = _trusted_process(['/usr/bin/python3', '-c',
                              'import sqlite3; import flask' if framework == 'flask'
                              else 'import sqlite3; import fastapi, uvicorn'],
                             timeout=15, env=env, cwd=base_dir, project_code=True)
    if check.returncode:
        raise GuestFailure('PYTHON_WEB_IMPORT_OR_SQLITE_FAILED')
    if framework == 'flask':
        program = ['/usr/bin/python3', '-m', 'flask', '--app', 'app', 'run',
                   '--no-reload', '--host', '127.0.0.1', '--port', str(port)]
    else:
        program = ['/usr/bin/python3', '-m', 'uvicorn', 'main:app',
                   '--host', '127.0.0.1', '--port', str(port), '--workers', '1']
    # The caller must start in the validated backend root.
    return program, env, 'Offline Python 3.12 wheels installed; SQLite module available'


def _probe_python_web(path: str, framework: str, *, port: int = 8765) -> tuple[int, str]:
    if path != ('/openapi.json' if framework == 'fastapi' else '/'):
        raise GuestFailure('PYTHON_WEB_HTTP_PROBE_FAILED')
    for _ in range(30):
        try:
            request = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                             headers={'User-Agent': 'ProjectIQ-Python-Probe'})
            with urllib.request.urlopen(request, timeout=2) as response:
                data = response.read(65_536)
                if response.status != 200 or not data:
                    continue
                if framework == 'fastapi':
                    try:
                        obj = json.loads(data)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(obj, dict) or 'openapi' not in obj or 'paths' not in obj:
                        continue
                return 200, 'HTTP_RESPONSE_OK'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('PYTHON_WEB_HTTP_PROBE_FAILED')


def _probe_vite_asset(dist: Path, port: int) -> str:
    """Verify a built JS bundle can actually be served, not only index.html."""
    candidates = [p for p in (dist / 'assets').rglob('*.js')
                  if p.is_file() and p.resolve().is_relative_to(dist.resolve())
                  and p.stat().st_size > 0]
    if not candidates:
        raise GuestFailure('NODE_VITE_BUILD_OUTPUT_MISSING')
    asset = sorted(candidates, key=lambda x: str(x))[0]
    path = '/' + quote(asset.relative_to(dist).as_posix(), safe='/')
    try:
        with urllib.request.urlopen('http://127.0.0.1:' + str(port) + path, timeout=5) as response:
            if response.status == 200 and response.read(128):
                return 'Built JS asset HTTP 200 verified'
    except (urllib.error.URLError, OSError, ValueError):
        pass
    raise GuestFailure('NODE_VITE_ASSET_PROBE_FAILED')


def _probe(url_path: str, port: int = 8765, deadline: int = 25) -> tuple[int, str]:
    last = 'CONNECTION_UNAVAILABLE'
    for _ in range(deadline):
        try:
            req = urllib.request.Request('http://127.0.0.1:' + str(port) + url_path,
                                         headers={'User-Agent': 'ProjectIQ-Readiness-Probe'})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = resp.read(8192)
                status = int(resp.status)
            if status == 200 and b'<html' in data.lower():
                return status, 'HTML_RESPONSE_OK'
            last = 'UNEXPECTED_HTTP_RESPONSE'
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    raise GuestFailure('Application HTTP readiness not established: ' + last)


def run(request_path: Path, *, base_dir: Path = Path('/home/projectiq'), port: int = 8765) -> dict:
    req = json.loads(request_path.read_text('utf-8'))
    adapter = req.get('adapter')
    if adapter not in ('static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1', 'node_express_offline_v1', 'python_web_offline_v1', 'node_fullstack_offline_v1'):
        raise GuestFailure('Unapproved guest adapter')
    path = req.get('probe_path')
    if not isinstance(path, str) or not (path == '/' and adapter in ('python_web_offline_v1', 'node_express_offline_v1') or re.fullmatch(r'/[A-Za-z0-9_./-]{1,150}', path)) or '..' in path.split('/'):
        raise GuestFailure('Invalid readiness path')
    snapshot = base_dir / 'snapshot.zip'
    if not snapshot.is_file() or snapshot.stat().st_size > 30_000_000:
        raise GuestFailure('Pinned snapshot missing or oversized')
    workspace = base_dir / 'checkout'
    checkout = safe_extract(snapshot, workspace)
    _share_tree(checkout, writable=True)
    if adapter == 'node_fullstack_offline_v1':
        return run_fullstack(checkout, base_dir, req, port=port)
    if not (checkout / 'index.html').is_file() and adapter == 'static_html_v1':
        raise GuestFailure('Static site has no index.html')
    env = dict(os.environ)
    notes = []
    if adapter == 'node_vite_offline_v1':
        dist, note = _prepare_vite(checkout, base_dir, req)
        notes.append(note)
        program = [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1',
                   '--directory', str(dist)]
    elif adapter == 'node_express_offline_v1':
        program, env, note = _prepare_express(checkout, base_dir, req, port=port)
        notes.append(note)
    elif adapter == 'python_web_offline_v1':
        program, env, note = _prepare_python_web(checkout, base_dir, req, port=port)
        notes.append(note)
    elif adapter == 'php_mysql_v1':
        schema_path = req.get('schema_path')
        if not isinstance(schema_path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,180}', schema_path) or '..' in schema_path.split('/'):
            raise GuestFailure('Invalid schema path')
        env, note = _provision_mysql(checkout, schema_path)
        notes.append(note)
        program = ['/usr/bin/php', '-S', '127.0.0.1:' + str(port), '-t', str(checkout)]
    else:
        program = [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1',
                   '--directory', str(checkout)]
    server_log = base_dir / 'server.log'
    with server_log.open('wb') as log:
        server = _project_popen(program, cwd=(_python_guest_root(checkout, req)
                                  if adapter == 'python_web_offline_v1' else
                                  _node_express_root(checkout, req) if adapter == 'node_express_offline_v1' else checkout),
                                  env=env, log=log)
    try:
        status, detail = (_probe_python_web(path, req['python_framework'], port=port)
                          if adapter == 'python_web_offline_v1' else
                          _probe_express(path, port=port) if adapter == 'node_express_offline_v1' else
                          _probe(path, port=port, deadline=35 if adapter in ('php_mysql_v1', 'node_vite_offline_v1') else 20))
        if adapter == 'node_vite_offline_v1':
            notes.append(_probe_vite_asset(dist, port))
        result = {'status': 'STARTUP_VERIFIED', 'adapter': adapter, 'http_status': status,
                  'readiness_path': path, 'readiness': detail, 'notes': notes}
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    # Return only a bounded, redacted tail; guest credentials must never be logged.
    return result


# D4 reviewed dual-service pilot. Only bounded, fixed commands are permitted.


def _d4_roots(checkout: Path, req: dict) -> tuple[Path, Path]:
    roots = req.get('service_roots')
    if not isinstance(roots, dict) or set(roots) != {'frontend', 'backend'}:
        raise GuestFailure('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
    out = {}
    for role in ('frontend', 'backend'):
        rel = roots[role]
        if not (isinstance(rel, str) and rel and len(rel) <= 180
                and re.fullmatch(r'[A-Za-z0-9_./-]+', rel)
                and all(part not in ('', '.', '..') for part in rel.split('/'))):
            raise GuestFailure('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
        target = checkout.joinpath(*PurePosixPath(rel).parts)
        if not target.is_dir() or not target.resolve().is_relative_to(checkout.resolve()):
            raise GuestFailure('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
        # npm searches parent folders for .npmrc, so reject configuration
        # in *every* ancestor inside the checkout, not just the final root.
        cursor = target
        while cursor != checkout:
            if (cursor / '.npmrc').exists() or (cursor / 'npm-shrinkwrap.json').exists():
                raise GuestFailure('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
            cursor = cursor.parent
        out[role] = target
    if out['frontend'] == out['backend'] or (checkout / '.npmrc').exists():
        raise GuestFailure('NODE_FULLSTACK_INVALID_SERVICE_ROOTS')
    return out['frontend'], out['backend']


def _d4_verify_manifests(frontend: Path, backend: Path, req: dict) -> str:
    keys = ('node_sha256', 'npm_cache_sha256', 'frontend_lockfile_sha256',
            'backend_lockfile_sha256')
    # Preparation has no cache hash yet.
    for key in keys:
        if key == 'npm_cache_sha256' and req.get('operation') == 'prepare_fullstack_cache':
            continue
        if not isinstance(req.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', req[key]):
            raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    _check_vite_manifest(frontend, req['frontend_lockfile_sha256'])
    entry = _check_express_manifest(backend, req['backend_lockfile_sha256'])
    # Stronger D4 direct dependency constraints than older D1 Vite profile.
    try:
        package = json.loads((frontend / 'package.json').read_text('utf-8'))
        lock = json.loads((frontend / 'package-lock.json').read_text('utf-8'))
    except (ValueError, OSError, UnicodeError) as exc:
        raise GuestFailure('NODE_FULLSTACK_UNSUPPORTED_MANIFEST') from exc
    direct, dev = package.get('dependencies') or {}, package.get('devDependencies') or {}
    version = re.compile(r'^[~^]?[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$')
    if (not isinstance(direct, dict) or not isinstance(dev, dict)
            or not all(isinstance(k, str) and isinstance(v, str) and version.fullmatch(v)
                       for k, v in {**direct, **dev}.items())
            or not isinstance(lock.get('packages', {}).get(''), dict)
            or lock['packages'][''].get('dependencies', {}) != direct
            or lock['packages'][''].get('devDependencies', {}) != dev):
        raise GuestFailure('NODE_FULLSTACK_UNSUPPORTED_MANIFEST')
    return entry


def _d4_node_env(base: Path, node: Path, npm_cache: Path, *, online: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_')
           and k.upper() not in ('NODE_OPTIONS', 'NODE_PATH', 'PYTHONPATH')}
    env['PATH'] = str(node.parent) + ':/usr/local/bin:/usr/bin:/bin'
    env.update({'HOME': str(base), **_isolated_npm_config_paths(base),
                'NPM_CONFIG_CACHE': str(npm_cache),
                'NPM_CONFIG_IGNORE_SCRIPTS': 'true', 'NPM_CONFIG_AUDIT': 'false',
                'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false',
                'CI': 'true'})
    if online:
        env['NPM_CONFIG_REGISTRY'] = 'https://registry.npmjs.org/'
    else:
        env['NPM_CONFIG_OFFLINE'] = 'true'
    return env


def _d4_npm_ci(npm: Path, cache: Path, env: dict, root: Path, *, online: bool) -> None:
    args = [str(npm), 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
            '--no-progress', '--cache', str(cache)]
    if not online:
        args.insert(2, '--offline')
    result = _trusted_process(args, timeout=230 if online else 185, env=env, cwd=root,
                              project_code=True, audit_kind='npm_ci')
    if result.returncode:
        raise GuestFailure('NODE_FULLSTACK_CACHE_FETCH_FAILED' if online
                           else 'NODE_FULLSTACK_OFFLINE_DEPENDENCY_INSTALL_FAILED')


def prepare_fullstack_cache(request_path: Path, *, base_dir: Path = Path('/home/projectiq')) -> dict:
    """One networked *preparation* VM, two manifests, no project scripts."""
    req = json.loads(request_path.read_text('utf-8'))
    if (req.get('operation') != 'prepare_fullstack_cache'
            or req.get('adapter') != 'node_fullstack_offline_v1'):
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    snapshot, tar = base_dir / 'snapshot.zip', base_dir / 'node_runtime.tar.xz'
    if not snapshot.is_file() or not 0 < snapshot.stat().st_size <= 30_000_000:
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    checkout = safe_extract(snapshot, base_dir / 'checkout')
    _share_tree(checkout, writable=True)
    frontend, backend = _d4_roots(checkout, req)
    _d4_verify_manifests(frontend, backend, req)
    if not tar.is_file() or _sha256(tar) != req['node_sha256']:
        raise GuestFailure('NODE_FULLSTACK_NODE_HASH_MISMATCH')
    runtime = base_dir / 'node_runtime'
    runtime.mkdir(mode=0o700)
    node, npm = _node_tar_extract(tar, version, runtime)
    tar.unlink(missing_ok=True)
    _share_tree(runtime, writable=False)
    result = _trusted_process([str(node), '--version'], timeout=15)
    if result.returncode or result.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_FULLSTACK_NODE_VERSION_MISMATCH')
    cache = base_dir / 'npm-cache'
    cache.mkdir(mode=0o700)
    _share_tree(cache, writable=True)
    env = _d4_node_env(base_dir, node, cache, online=True)
    # Shared content-addressed npm cache accumulates both dependency graphs.
    _d4_npm_ci(npm, cache, env, frontend, online=True)
    _d4_npm_ci(npm, cache, env, backend, online=True)
    archive = base_dir / 'npm_cache.zip'
    count = size = 0
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=5, allowZip64=False) as zf:
        for item in sorted(cache.rglob('*')):
            if item.is_dir():
                continue
            if item.is_symlink() or not item.is_file() or item.stat().st_size > 20_000_000:
                raise GuestFailure('NODE_FULLSTACK_CACHE_ARCHIVE_INVALID')
            rel = item.relative_to(cache)
            if '_logs' in rel.parts:
                continue
            count += 1
            size += item.stat().st_size
            if count > 12000 or size > 250_000_000:
                raise GuestFailure('NODE_FULLSTACK_CACHE_ARCHIVE_INVALID')
            zf.write(item, arcname='npm-cache/' + rel.as_posix())
    if count < 1 or archive.stat().st_size > 170_000_000:
        raise GuestFailure('NODE_FULLSTACK_CACHE_ARCHIVE_INVALID')
    return {'status': 'CACHE_PREPARED', 'adapter': 'node_fullstack_offline_v1',
            'node_version': version,
            'frontend_lockfile_sha256': req['frontend_lockfile_sha256'],
            'backend_lockfile_sha256': req['backend_lockfile_sha256'],
            'npm_cache_sha256': _sha256(archive),
            'notes': ['Two offline dependency graphs cached; student scripts disabled']}


def _d4_install_and_build(checkout: Path, base_dir: Path, req: dict) -> tuple[Path, list[str], dict, Path]:
    """Install two lockfiles from one verified cache, build Vite; no npm scripts."""
    frontend, backend = _d4_roots(checkout, req)
    entry = _d4_verify_manifests(frontend, backend, req)
    version = req.get('node_version')
    if not isinstance(version, str) or not re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}', version):
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    tar, cache_zip = base_dir / 'node_runtime.tar.xz', base_dir / 'npm_cache.zip'
    if not tar.is_file() or _sha256(tar) != req['node_sha256']:
        raise GuestFailure('NODE_FULLSTACK_NODE_HASH_MISMATCH')
    if not cache_zip.is_file() or _sha256(cache_zip) != req['npm_cache_sha256']:
        raise GuestFailure('NODE_FULLSTACK_CACHE_HASH_MISMATCH')
    runtime = base_dir / 'node_runtime'
    runtime.mkdir(mode=0o700)
    node, npm = _node_tar_extract(tar, version, runtime)
    tar.unlink(missing_ok=True)
    _share_tree(runtime, writable=False)
    ver = _trusted_process([str(node), '--version'], timeout=15)
    if ver.returncode or ver.stdout.decode('utf-8', 'replace').strip() != version:
        raise GuestFailure('NODE_FULLSTACK_NODE_VERSION_MISMATCH')
    if not 0 < cache_zip.stat().st_size <= 170_000_000:
        raise GuestFailure('NODE_FULLSTACK_CACHE_HASH_MISMATCH')
    try:
        cache = safe_extract(cache_zip, base_dir / 'npm_cache', max_total=250_000_000)
    except (GuestFailure, zipfile.BadZipFile) as exc:
        raise GuestFailure('NODE_FULLSTACK_CACHE_ARCHIVE_INVALID') from exc
    cache_zip.unlink(missing_ok=True)
    _share_tree(cache, writable=True)
    env = _d4_node_env(base_dir, node, cache, online=False)
    _d4_npm_ci(npm, cache, env, frontend, online=False)
    _d4_npm_ci(npm, cache, env, backend, online=False)
    if not (backend / 'node_modules' / 'express' / 'package.json').is_file():
        raise GuestFailure('NODE_FULLSTACK_OFFLINE_DEPENDENCY_INSTALL_FAILED')
    vite = frontend / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if not vite.is_file() or not vite.resolve().is_relative_to(frontend.resolve()):
        raise GuestFailure('NODE_FULLSTACK_VITE_BUILD_FAILED')
    dist = frontend / 'dist'
    if dist.exists():
        if not dist.is_dir() or dist.is_symlink():
            raise GuestFailure('NODE_FULLSTACK_VITE_BUILD_FAILED')
        shutil.rmtree(dist)
    built = _trusted_process([str(node), str(vite), 'build', '--outDir', 'dist'],
                             timeout=150, env=env, cwd=frontend, project_code=True,
                             audit_kind='vite_build')
    if built.returncode:
        raise GuestFailure('NODE_FULLSTACK_VITE_BUILD_FAILED')
    index = dist / 'index.html'
    if not index.is_file() or not index.stat().st_size <= 2_000_000 or b'<html' not in index.read_bytes().lower():
        raise GuestFailure('NODE_FULLSTACK_BUILD_OUTPUT_INVALID')
    env.update({'PORT': '8766', 'HOST': '127.0.0.1', 'NODE_ENV': 'production'})
    return dist, [str(node), str(backend / entry)], env, backend


def _d4_frontend_serve(dist: Path, frontend_port: int, backend_port: int) -> None:
    """Trusted loopback static server; only /api/* GETs are forwarded locally."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from http.client import HTTPConnection

    if not dist.is_dir() or not (dist / 'index.html').is_file():
        raise GuestFailure('NODE_FULLSTACK_BUILD_OUTPUT_INVALID')
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def do_GET(self):
            if not self.path.startswith('/api/'):
                return super().do_GET()
            # Fixed destination, no redirects, no arbitrary host/URL parsing.
            if not re.fullmatch(r'/api/[A-Za-z0-9_/-]{1,96}', self.path):
                self.send_error(400)
                return
            conn = HTTPConnection('127.0.0.1', backend_port, timeout=3)
            try:
                conn.request('GET', self.path, headers={'Host': '127.0.0.1'})
                response = conn.getresponse()
                payload = response.read(65_537)
                if len(payload) > 65_536 or response.status != 200:
                    self.send_error(502)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)
            except (OSError, TimeoutError):
                self.send_error(502)
            finally:
                conn.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', frontend_port), Handler)
    server.serve_forever(poll_interval=0.5)


def _d4_json(path: str, port: int) -> dict:
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                      headers={'User-Agent': 'ProjectIQ-D4-Health'})
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.status != 200:
                raise GuestFailure('NODE_FULLSTACK_CROSS_SERVICE_PROBE_FAILED')
            parsed = json.loads(response.read(65_537))
        if not isinstance(parsed, dict):
            raise ValueError('JSON object required')
        return parsed
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise GuestFailure('NODE_FULLSTACK_CROSS_SERVICE_PROBE_FAILED') from exc


def _d4_wait_json(path: str, port: int) -> dict:
    for _ in range(30):
        try:
            return _d4_json(path, port)
        except GuestFailure:
            time.sleep(1)
    raise GuestFailure('NODE_FULLSTACK_BACKEND_NOT_READY')


def _d4_stop_process(proc: subprocess.Popen) -> None:
    import signal
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=5)


def run_fullstack(checkout: Path, base_dir: Path, req: dict, *, port: int = 8765) -> dict:
    if req.get('adapter') != 'node_fullstack_offline_v1' or port != 8765:
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    api = req.get('probe_path')
    health = req.get('backend_probe_path')
    if api not in ('/api/sqlite-check', '/api/health') or health not in ('/health', '/api/health'):
        raise GuestFailure('NODE_FULLSTACK_INVALID_REQUEST')
    dist, program, env, backend = _d4_install_and_build(checkout, base_dir, req)
    processes = []
    try:
        with (base_dir / 'd4_backend.log').open('wb') as log:
            process = _project_popen(program, cwd=backend, env=env, log=log)
        processes.append(process)
        _d4_wait_json(health, 8766)
        with (base_dir / 'd4_frontend.log').open('wb') as log:
            process = _project_popen(
                [sys.executable, str(Path(__file__).resolve()),
                 '--serve-d4-frontend', str(dist), str(port), '8766'],
                cwd=base_dir, env={}, log=log)
        processes.append(process)
        status, kind = _probe('/index.html', port=port, deadline=30)
        if status != 200 or kind != 'HTML_RESPONSE_OK':
            raise GuestFailure('NODE_FULLSTACK_FRONTEND_NOT_READY')
        _probe_vite_asset(dist, port)
        backend_api = _d4_json(api, 8766)
        through_frontend = _d4_json(api, port)
        if backend_api != through_frontend:
            raise GuestFailure('NODE_FULLSTACK_CROSS_SERVICE_PROBE_FAILED')
        sqlite_verified = (api == '/api/sqlite-check' and through_frontend.get('sqlite') is True)
        if api == '/api/sqlite-check' and not sqlite_verified:
            raise GuestFailure('NODE_FULLSTACK_SQLITE_CHECK_FAILED')
        return {'status': 'STARTUP_VERIFIED', 'adapter': 'node_fullstack_offline_v1',
                'http_status': 200, 'readiness': 'HTTP_RESPONSE_OK', 'readiness_path': api,
                'service_results': {'frontend_http': 200, 'backend_http': 200,
                                    'frontend_proxy_api_http': 200,
                                    'built_asset_http': 200, 'sqlite_verified': sqlite_verified},
                'notes': ['Two offline npm ci installs and Vite build completed',
                          'Express started before frontend; internal API proxy returned matching JSON',
                          'SQLite CRUD evidence requires operator-reviewed API fixture']}
    finally:
        for proc in reversed(processes):
            _d4_stop_process(proc)


def main() -> int:
    global _APP_CGROUP_ACTIVE
    if len(sys.argv) == 5 and sys.argv[1] == '--serve-d4-frontend':
        _d4_frontend_serve(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
        return 0
    result = {'status': 'STARTUP_FAILED', 'error_code': 'UNKNOWN_FAILURE'}
    try:
        _verify_execution_identity(Path('/home/projectiq'))
        request = Path('/home/projectiq/request.json')
        meta = json.loads(request.read_text('utf-8'))
        # Only execution VMs receive a dedicated bounded disk. Trusted
        # dependency preparation retains its separate explicit network policy.
        app_base = (_stage_bounded_workspace(meta, base=Path('/home/projectiq'))
                    if 'workspace_disk_mib' in meta else Path('/home/projectiq'))
        if meta.get('operation') not in ('prepare_fullstack_cache',
                                         'prepare_npm_cache', 'prepare_python_wheels'):
            if meta.get('app_cgroup') is not True:
                raise GuestFailure('APP_CGROUP_ENFORCEMENT_UNAVAILABLE')
            _verify_evaluation_cgroup(app_base)
            _APP_SERVER_PLACEMENTS.clear()
            _APP_BUILD_HANDOFFS.clear()
            _APP_CGROUP_ACTIVE = True
        result = (prepare_fullstack_cache(request, base_dir=app_base) if meta.get('operation') == 'prepare_fullstack_cache'
                  else prepare_npm_cache(request, base_dir=app_base) if meta.get('operation') == 'prepare_npm_cache'
                  else prepare_python_wheelhouse(request, base_dir=app_base) if meta.get('operation') == 'prepare_python_wheels'
                  else run(request, base_dir=app_base))
        if _APP_CGROUP_ACTIVE and result.get('status') == 'STARTUP_VERIFIED':
            expected = 2 if meta.get('adapter') == 'node_fullstack_offline_v1' else 1
            if (len(_APP_SERVER_PLACEMENTS) != expected or
                    len(set(_APP_SERVER_PLACEMENTS)) != expected):
                raise GuestFailure('APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED')
            # Controller-side evidence collected while real servers were alive.
            # No PID or command line data is ever returned to the host.
            result['cgroup_placement'] = {
                'status': 'SERVER_PIDS_VERIFIED', 'server_count': expected,
                'builds_verified': False, 'database_service_verified': False,
            }
            # Count only successful, independently observed pre-exec handoffs.
            # The existing `builds_verified=False` still means no full process
            # graph/descendant verification has been performed.
            result['build_launch_evidence'] = _build_handoff_report(meta.get('adapter'))
    except OSError as exc:
        if getattr(exc, 'errno', None) in (28, 122):
            result = {'status': 'STARTUP_FAILED', 'error_code': 'WORKSPACE_DISK_QUOTA_EXCEEDED'}
        else:
            result = {'status': 'STARTUP_FAILED', 'error_code': 'UNEXPECTED_GUEST_FAILURE'}
    except GuestFailure as exc:
        # Intentionally avoid returning source strings, SQL, credentials or tracebacks.
        result = {'status': 'STARTUP_FAILED', 'error_code': str(exc)[:120]}
        if isinstance(exc, NpmPreparationDiagnostic):
            result['npm_operator_hint'] = exc.safe_hint
    except Exception:
        result = {'status': 'STARTUP_FAILED', 'error_code': 'UNEXPECTED_GUEST_FAILURE'}
    Path('/home/projectiq/result.json').write_text(json.dumps(result, indent=2) + '\n', 'utf-8')
    return 0 if result['status'] in ('STARTUP_VERIFIED', 'CACHE_PREPARED', 'PYTHON_WHEELHOUSE_PREPARED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
