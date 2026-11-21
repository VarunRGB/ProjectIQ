"""D5d Gate 2: independent, trusted, source-free process-boundary probe.

Sent to a clean disposable Linux VM via SSH stdin. No student project, cache,
SQL dump, or host mount is provided. Kernel /proc and cgroup-v2 files, not
systemd-reported placement alone, determine positive observations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

CGROUP = Path('/sys/fs/cgroup')
WORKSPACE = Path('/srv/projectiq-workspace')
APP_SLICE = 'projectiq-eval.slice'
MEMORY_MAX = 2147483648
PIDS_MAX = 80
IO_BPS = 16777216
MYSQL_MEMORY_MAX = 536870912  # Target to discuss, NOT enforced by this probe.
MYSQL_PIDS_MAX = 64
MYSQL_IO_BPS = 8388608

FIELDS = (
    'controller_identity', 'bootstrap_and_workspace', 'evaluation_slice_policy',
    'build_parent_in_scope', 'build_child_in_scope', 'build_scope_drained',
    'mysql_daemon_identity', 'mysql_separate_system_scope',
    'mysql_memory_bounded', 'mysql_pids_bounded', 'mysql_io_bounded',
)

# Source-free fork+exec workload. The child stays alive until a controller-only
# stop marker appears. A child-side READY marker prevents early PID snapshots.
# The guest source is fixed; no project archive or user argument enters either
# process. Both must be independently found in their kernel cgroup.
SYNTHETIC_BUILD = r'''import os, subprocess, sys, time
from pathlib import Path
marker, stop = Path(sys.argv[1]), Path(sys.argv[2])
ready = marker.with_suffix('.child-ready')
child_source = ("import sys,time\nfrom pathlib import Path\n"
                "ready,stop=Path(sys.argv[1]),Path(sys.argv[2])\n"
                "ready.write_text('READY',encoding='ascii')\n"
                "until=time.monotonic()+42\n"
                "while time.monotonic()<until and not stop.is_file():\n"
                " time.sleep(.04)\n")
child = subprocess.Popen(['/usr/bin/python3', '-I', '-c', child_source,
                          str(ready), str(stop)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    until = time.monotonic() + 10
    while not ready.is_file() and child.poll() is None and time.monotonic() < until:
        time.sleep(.04)
    if not ready.is_file() or child.poll() is not None:
        raise RuntimeError('Synthetic child not ready')
    marker.write_text(str(os.getpid()) + ' ' + str(child.pid), encoding='ascii')
    until = time.monotonic() + 39
    while time.monotonic() < until and not stop.is_file() and child.poll() is None:
        time.sleep(.04)
finally:
    if child.poll() is None:
        # The trusted child normally exits on STOP; force a bounded fallback.
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
    else:
        child.wait()
'''

DIAG_KEYS = (
    'marker_ready', 'parent_verified', 'child_uid_verified',
    'child_parent_verified', 'child_cgroup_member_verified',
    'stop_marker_published', 'child_cgroup_location', 'final_scope_state',
)
DIAG_LOCATIONS = frozenset(('NOT_OBSERVED', 'DIRECT', 'DESCENDANT', 'OUTSIDE'))
DIAG_STATES = frozenset(('NOT_RUN', 'ABSENT', 'DRAINED', 'POPULATED', 'UNKNOWN'))


def _empty_diagnostics() -> dict:
    return {**{k: False for k in DIAG_KEYS[:6]},
            'child_cgroup_location': 'NOT_OBSERVED',
            'final_scope_state': 'NOT_RUN'}


def _prepare_stop_parent(controller_uid: int, home: Path = Path('/home/projectiq')) -> bool:
    """Make the trusted controller's home searchable, never writable, by APP_UID.

    The release marker remains owned by the controller and mode 0644.
    This runs in the clean acceptance VM; only after checking ownership and
    rejecting symlinks and group/other-writable controller directories.
    """
    try:
        st = home.lstat()
        if (not stat.S_ISDIR(st.st_mode) or home.is_symlink() or
                st.st_uid != controller_uid or st.st_mode & 0o022 or
                st.st_mode & 0o700 != 0o700):
            return False
        home.chmod(0o711)
        after = home.lstat()
        return (stat.S_ISDIR(after.st_mode) and after.st_uid == controller_uid and
                after.st_mode & 0o777 == 0o711 and not home.is_symlink())
    except OSError:
        return False


def _child_membership(scope: Path, child: int, group: str | None,
                      direct_members: set[int] | None) -> tuple[str, bool]:
    """Allow exact scope or cgroup descendants; never siblings or other slices.

    Nested cgroups inherit the ancestor slice's memory/pids/io enforcement.
    All positive claims still require the child's own cgroup.procs membership.
    """
    expected = '/' + scope.relative_to(CGROUP).as_posix()
    if group == expected:
        return 'DIRECT', direct_members is not None and child in direct_members
    if not group:
        return 'NOT_OBSERVED', False
    if not group.startswith(expected + '/'):
        return 'OUTSIDE', False
    components = group.lstrip('/').split('/')
    if (len(group) > 512 or any(not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,100}', part)
                                or part in ('.', '..') for part in components)):
        return 'OUTSIDE', False
    node = CGROUP.joinpath(*components)
    if node.is_symlink():
        return 'OUTSIDE', False
    members = _kernel_members(node)
    return 'DESCENDANT', members is not None and child in members


def _scope_state(path: Path) -> str:
    """A missing scope or populated=0 + no direct PIDs is drained."""
    try:
        if not path.exists() and not path.is_symlink():
            return 'ABSENT'
        if path.is_symlink() or not path.is_dir():
            return 'UNKNOWN'
        rows = [x.split() for x in (path / 'cgroup.events').read_text('ascii').splitlines()]
        state = [x[1] for x in rows if len(x) == 2 and x[0] == 'populated']
        members = _kernel_members(path)
        if state == ['0'] and members == set():
            return 'DRAINED'
        if state == ['1'] or (state == ['0'] and members):
            return 'POPULATED'
    except (OSError, UnicodeError, ValueError):
        pass
    return 'UNKNOWN'


def _cmd(*args: str, timeout: int = 10, env: dict | None = None) -> str | None:
    try:
        result = subprocess.run(args, timeout=timeout, env=env, check=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        if result.returncode or len(result.stdout) > 8192:
            return None
        return result.stdout.decode('utf-8', 'strict').strip()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None


def _root_marker(path: Path) -> bool:
    try:
        info = path.lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022 and not path.is_symlink())
    except OSError:
        return False


def _kernel_group(pid: int, proc: Path = Path('/proc')) -> str | None:
    if type(pid) is not int or pid <= 1:
        return None
    try:
        rows = [(line.split(':', 2)[2]) for line in
                (proc / str(pid) / 'cgroup').read_text('ascii').splitlines()
                if line.startswith('0::')]
        return rows[0] if len(rows) == 1 else None
    except (OSError, UnicodeError, IndexError):
        return None


def _proc_identity(pid: int, uid: int, *, ppid: int | None = None,
                   proc: Path = Path('/proc')) -> bool:
    try:
        data = (proc / str(pid) / 'status').read_text('utf-8')[:14000].splitlines()
        uids = [line.split()[1:] for line in data if line.startswith('Uid:')]
        parents = [line.split()[1:] for line in data if line.startswith('PPid:')]
        if (len(uids) != 1 or len(uids[0]) != 4 or
                not all(v.isascii() and v.isdecimal() for v in uids[0]) or
                int(uids[0][0]) != uid or int(uids[0][1]) != uid):
            return False
        return (ppid is None or (len(parents) == 1 and len(parents[0]) == 1 and
                                 parents[0][0].isdecimal() and int(parents[0][0]) == ppid))
    except (OSError, ValueError, UnicodeError):
        return False


def _kernel_members(path: Path) -> set[int] | None:
    try:
        if path.is_symlink() or not path.is_dir():
            return None
        members = (path / 'cgroup.procs').read_text('ascii').split()
        if len(members) > 128 or any(not x.isascii() or not x.isdecimal() for x in members):
            return None
        return {int(x) for x in members}
    except (OSError, UnicodeError):
        return None


def _scope_drained(path: Path) -> bool:
    """Require the kernel's recursive populated signal (not cgroup.procs alone)."""
    return _scope_state(path) in ('ABSENT', 'DRAINED')


def _write_cap(path: Path, limit: int) -> bool:
    try:
        value = (path / 'memory.max').read_text('ascii').strip()
        return value.isascii() and value.isdecimal() and 0 < int(value) <= limit
    except (OSError, ValueError, UnicodeError):
        return False


def _pids_cap(path: Path, limit: int) -> bool:
    try:
        value = (path / 'pids.max').read_text('ascii').strip()
        return value.isascii() and value.isdecimal() and 0 < int(value) <= limit
    except (OSError, ValueError, UnicodeError):
        return False


def _backing_io_devices(device: int, *, sysdev: Path = Path('/sys/dev/block')) -> set[str]:
    """Return the filesystem ID and its *kernel-proven* whole-disk parent.

    Root filesystems may live on /dev/vda1 while systemd writes io.max for
    /dev/vda. An unrelated capped device must never satisfy this check.
    """
    source = f'{os.major(device)}:{os.minor(device)}'
    allowed = {source}
    try:
        node = (sysdev / source).resolve(strict=True)
        if not node.is_relative_to(sysdev.parent.parent / 'devices'):
            return allowed
        partition = node / 'partition'
        number = partition.read_text('ascii').strip()
        if not number.isascii() or not number.isdecimal() or int(number) < 1:
            return allowed
        parent_dev = (node.parent / 'dev').read_text('ascii').strip()
        if re.fullmatch(r'[0-9]{1,5}:[0-9]{1,7}', parent_dev):
            allowed.add(parent_dev)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        pass
    return allowed


def _io_cap(path: Path, device: int, bound: int) -> bool:
    try:
        targets = _backing_io_devices(device)
        rows = [line.split() for line in (path / 'io.max').read_text('ascii').splitlines()]
        return any(len(row) >= 2 and row[0] in targets and
                   any(v.startswith('wbps=') and v[5:].isascii() and v[5:].isdecimal()
                       and 0 < int(v[5:]) <= bound for v in row[1:]) for row in rows)
    except (OSError, ValueError, UnicodeError):
        return False


def _slice_policy(uid: int, env: dict) -> Path | None:
    control = _cmd('/usr/bin/systemctl', '--user', 'show', '-P', 'ControlGroup',
                   APP_SLICE, env=env)
    expected = (f'/user.slice/user-{uid}.slice/user@{uid}.service/'
                f'projectiq.slice/{APP_SLICE}')
    if control != expected:
        return None
    node = CGROUP / expected.lstrip('/')
    try:
        if (node.is_symlink() or not node.is_dir() or
                (node / 'memory.max').read_text('ascii').strip() != str(MEMORY_MAX) or
                (node / 'pids.max').read_text('ascii').strip() != str(PIDS_MAX) or
                not _io_cap(node, WORKSPACE.stat().st_dev, IO_BPS)):
            return None
        return node
    except (OSError, UnicodeError):
        return None


def _synthetic_scope(uid: int, app_uid: int, env: dict, parent: Path,
                     obs: dict, diagnostics: dict | None = None) -> None:
    diag = diagnostics if diagnostics is not None else _empty_diagnostics()
    unit = 'piq-d5d-' + secrets.token_hex(8)
    marker = WORKSPACE / (unit + '.marker')
    ready = marker.with_suffix('.child-ready')
    stop = Path('/home/projectiq') / ('.projectiq-d5d-stop-' + unit[len('piq-d5d-'):])
    scope = parent / (unit + '.scope')
    argv = ['/usr/bin/python3', '-I', '-c', SYNTHETIC_BUILD, str(marker), str(stop)]
    cmd = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet',
           '--slice=' + APP_SLICE, '--unit=' + unit, '/usr/bin/sudo', '-n',
           '-u', 'projectiq_run', '--', *argv]
    proc = None
    if not _prepare_stop_parent(uid):
        return  # Fail closed; cannot publish a readable, controller-owned STOP.
    try:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        until = time.monotonic() + 18
        while time.monotonic() < until:
            if marker.is_file() and ready.is_file():
                try:
                    raw = marker.read_text('ascii').split()
                    if len(raw) == 2 and all(x.isdecimal() and len(x) <= 10 for x in raw):
                        first, second = map(int, raw)
                        if first <= 1 or second <= 1 or first == second:
                            raise ValueError('Invalid trusted synthetic identities')
                        diag['marker_ready'] = True
                        members = _kernel_members(scope)
                        group = '/' + str(scope.relative_to(CGROUP)).replace('\\', '/')
                        parent_ok = (members is not None and first in members and
                                     _kernel_group(first) == group and
                                     _proc_identity(first, app_uid))
                        child_uid = _proc_identity(second, app_uid)
                        child_ppid = _proc_identity(second, app_uid, ppid=first)
                        location, child_member = _child_membership(
                            scope, second, _kernel_group(second), members)
                        diag['parent_verified'] |= bool(parent_ok)
                        diag['child_uid_verified'] |= bool(child_uid)
                        diag['child_parent_verified'] |= bool(child_ppid)
                        diag['child_cgroup_member_verified'] |= bool(child_member)
                        diag['child_cgroup_location'] = location
                        obs['build_parent_in_scope'] |= bool(parent_ok)
                        obs['build_child_in_scope'] |= bool(
                            parent_ok and child_uid and child_ppid and child_member)
                except (OSError, UnicodeError, ValueError):
                    pass
                if obs['build_child_in_scope']:
                    break
            # A systemd-run wrapper can terminate independently of the scope.
            # Never treat its exit as kernel evidence that a live child is gone.
            if proc.poll() is not None and not marker.is_file():
                break
            time.sleep(0.06)
    finally:
        try:
            home = Path('/home/projectiq').lstat()
            if (not stat.S_ISDIR(home.st_mode) or home.st_uid != uid or
                    home.st_mode & 0o022):
                raise OSError('Untrusted controller home')
            fd = os.open(stop, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         getattr(os, 'O_NOFOLLOW', 0), 0o644)
            try:
                os.fchmod(fd, 0o644)
                os.write(fd, b'STOP\n')
            finally:
                os.close(fd)
            diag['stop_marker_published'] = True
            if proc is not None:
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    pass
        except OSError:
            pass
        # The trusted synthetic parent and child exit cooperatively first.
        # Fall back to unit-scoped systemd actions, never student-supplied PIDs.
        _cmd('/usr/bin/systemctl', '--user', 'stop', unit + '.scope', timeout=8, env=env)
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=7)
            except subprocess.TimeoutExpired:
                proc.kill()  # Only the trusted systemd-run wrapper.
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        until = time.monotonic() + 10
        while time.monotonic() < until:
            state = _scope_state(scope)
            diag['final_scope_state'] = state
            if obs['build_child_in_scope'] and state in ('ABSENT', 'DRAINED'):
                obs['build_scope_drained'] = True
                break
            # Preserve complete child evidence requirement for a PASS.
            # Still observe state for troubleshooting if child was not verified.
            if state in ('ABSENT', 'DRAINED'):
                break
            time.sleep(0.1)
        if diag['final_scope_state'] not in ('ABSENT', 'DRAINED'):
            # A bounded fallback is safe only for our unique synthetic scope.
            _cmd('/usr/bin/systemctl', '--user', 'kill', '--kill-who=all',
                 unit + '.scope', timeout=7, env=env)
            until = time.monotonic() + 6
            while time.monotonic() < until:
                state = _scope_state(scope)
                diag['final_scope_state'] = state
                if state in ('ABSENT', 'DRAINED'):
                    if obs['build_child_in_scope']:
                        obs['build_scope_drained'] = True
                    break
                time.sleep(0.1)
        for path in (marker, ready, stop):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _mysql(obs: dict) -> None:
    """Inspect real mysqld without altering its systemd unit or privileges."""
    import pwd
    main = _cmd('/usr/bin/systemctl', 'show', '-P', 'MainPID', 'mysql.service')
    group = _cmd('/usr/bin/systemctl', 'show', '-P', 'ControlGroup', 'mysql.service')
    active = _cmd('/usr/bin/systemctl', 'show', '-P', 'ActiveState', 'mysql.service')
    if (not main or not main.isdecimal() or not group or
            active != 'active' or not re.fullmatch(r'/system.slice/[A-Za-z0-9_.@-]+', group)):
        return
    pid = int(main)
    try:
        mysql_uid = pwd.getpwnam('mysql').pw_uid
    except KeyError:
        return
    node = CGROUP / group.lstrip('/')
    members = _kernel_members(node)
    identity = (mysql_uid not in (0, os.getuid()) and
                _proc_identity(pid, mysql_uid) and
                _kernel_group(pid) == group and members is not None and pid in members)
    obs['mysql_daemon_identity'] = bool(identity)
    obs['mysql_separate_system_scope'] = bool(identity and group.startswith('/system.slice/') and
                                               APP_SLICE not in group)
    if not obs['mysql_separate_system_scope']:
        return
    obs['mysql_memory_bounded'] = _write_cap(node, MYSQL_MEMORY_MAX)
    obs['mysql_pids_bounded'] = _pids_cap(node, MYSQL_PIDS_MAX)
    try:
        obs['mysql_io_bounded'] = _io_cap(node, Path('/var/lib/mysql').stat().st_dev,
                                          MYSQL_IO_BPS)
    except OSError:
        pass


def main() -> int:
    obs = {k: False for k in FIELDS}
    diag = _empty_diagnostics()
    try:
        import pwd
        uid = os.getuid()
        app_uid = pwd.getpwnam('projectiq_run').pw_uid
        obs['controller_identity'] = (os.geteuid() == uid == pwd.getpwnam('projectiq').pw_uid and
                                       uid != 0 and app_uid not in (uid, 0))
        if not obs['controller_identity']:
            print(json.dumps({'schema': 'd5d-process-1', 'observations': obs, 'diagnostics': diag}))
            return 0
        for _ in range(90):
            if (_root_marker(Path('/run/projectiq-workspace-bootstrap.ready')) and
                    _root_marker(Path('/run/projectiq-eval-bootstrap.ready')) and
                    Path('/home/projectiq/.projectiq_mysql_credentials.json').is_file()):
                break
            time.sleep(1)
        obs['bootstrap_and_workspace'] = (os.path.ismount(WORKSPACE) and
                                          _root_marker(Path('/run/projectiq-workspace-bootstrap.ready')) and
                                          _root_marker(Path('/run/projectiq-eval-bootstrap.ready')))
        if obs['bootstrap_and_workspace']:
            env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{uid}',
                       DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
            started = _cmd('/usr/bin/systemctl', '--user', 'start', APP_SLICE, timeout=15, env=env)
            # systemctl start usually prints nothing; None means it failed.
            parent = _slice_policy(uid, env) if started is not None else None
            obs['evaluation_slice_policy'] = parent is not None
            if parent is not None:
                _synthetic_scope(uid, app_uid, env, parent, obs, diag)
            _mysql(obs)
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
        pass
    print(json.dumps({'schema': 'd5d-process-1', 'observations': obs, 'diagnostics': diag}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
