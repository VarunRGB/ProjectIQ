"""D5b/B3b Gate 2B.4: fixed, trusted VM hard-stop/recovery probe.

Sent through SSH standard input. Never executes a repository, fetches
packages, mounts host paths or treats guest-asserted flags as attestation.
"""
from __future__ import annotations

import json
import hashlib
import os
try:
    import pwd
except ImportError:  # Imported on Windows by offline unit tests only
    pwd = None
import re
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path

SLICE = 'projectiq-eval.slice'
CGROUP = Path('/sys/fs/cgroup')
WORKSPACE = Path('/srv/projectiq-workspace')
SENTINEL = WORKSPACE / '.projectiq-b3b-recovery-sentinel'
RAM_MAX = 2 * 1024**3
TASKS_MAX = 80
WRITE_BPS = 16 * 1024**2
WORKSPACE_MIB = 96
STAGES = frozenset({
    'INITIAL', 'BOOTSTRAPS_READY', 'WORKSPACE_READY',
    'EVAL_POLICY_VERIFIED', 'CRASH_WORKER_LAUNCHING',
    'CRASH_WRAPPER_EXITED', 'CRASH_SCOPE_DRAINED',
    'CANCEL_WORKER_LAUNCHING', 'CANCEL_WRAPPER_EXITED',
    'CANCEL_SCOPE_DRAINED', 'HARD_WORKER_LAUNCHING',
    'HARD_WORKER_ARMED', 'FRESH_WORKSPACE_CHECKED', 'FRESH_VM_CLEAN',
})
# Deliberately non-cooperative. VM hard termination, NOT systemd stop,
# is the sole cleanup of this fixed 1-process workload.
WORKER = 'import time\nwhile True:\n time.sleep(1)\n'
CRASH_WORKER = 'import os,time\ntime.sleep(15)\nos._exit(42)\n'
CANCEL_WORKER = r'''import os,pwd,stat,sys,time
from pathlib import Path
marker=Path(sys.argv[1])
owner=pwd.getpwnam('projectiq').pw_uid
for _ in range(300):
    try:
        st=marker.lstat()
        if stat.S_ISREG(st.st_mode) and st.st_uid==owner and not st.st_mode & 0o022:
            sys.exit(0)
    except FileNotFoundError:
        pass
    except OSError:
        sys.exit(72)
    time.sleep(.1)
sys.exit(71)
''' 


def _ready_marker(path: str) -> bool:
    try:
        node = Path(path)
        st = node.lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid == 0
                and not st.st_mode & 0o022 and not node.is_symlink())
    except OSError:
        return False


def _workspace_ok(uid: int) -> bool:
    try:
        if (WORKSPACE.is_symlink() or not WORKSPACE.is_dir()
                or not os.path.ismount(WORKSPACE)):
            return False
        import grp
        st = WORKSPACE.stat()
        if st.st_uid != uid or st.st_gid != grp.getgrnam('projectiq_exec').gr_gid:
            return False
        mount_rows = [s.split() for s in Path('/proc/mounts').read_text().splitlines()]
        matches = [r for r in mount_rows if len(r) >= 4 and r[1] == str(WORKSPACE)]
        if (len(matches) != 1 or matches[0][2] != 'ext4'
                or not {'rw', 'nosuid', 'nodev'} <= set(matches[0][3].split(','))):
            return False
        info = os.statvfs(WORKSPACE)
        capacity = info.f_blocks * info.f_frsize
        return 48 * 1024**2 <= capacity <= WORKSPACE_MIB * 1024**2
    except (OSError, ValueError, KeyError):
        return False


def _policy_path(uid: int, control: str) -> Path | None:
    parts = control.strip('/').split('/')
    expected = ['user.slice', f'user-{uid}.slice', f'user@{uid}.service',
                'projectiq.slice', SLICE]
    if parts != expected:
        return None
    path = CGROUP.joinpath(*parts)
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        if ((path / 'memory.max').read_text().strip() != str(RAM_MAX)
                or (path / 'pids.max').read_text().strip() != str(TASKS_MAX)):
            return None
        dev = WORKSPACE.stat().st_dev
        tag = f'{os.major(dev)}:{os.minor(dev)}'
        entries = [line.split() for line in (path / 'io.max').read_text().splitlines()]
        if not any(row and row[0] == tag and f'wbps={WRITE_BPS}' in row[1:]
                   for row in entries):
            return None
        return path
    except (OSError, ValueError):
        return None


def _scope_member(path: Path, app_uid: int, expected_args: list[str],
                  proc_root: Path = Path('/proc')) -> bool:
    """Independently match kernel UID, actual cgroup and exact argv."""
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        pids = (path / 'cgroup.procs').read_text(encoding='ascii').split()
        if not 0 < len(pids) < 30:
            return False
        group = '/' + path.relative_to(CGROUP).as_posix()
        expected = [a.encode('utf-8') for a in expected_args]
        for pid in pids:
            if not pid.isascii() or not pid.isdecimal() or len(pid) > 10:
                return False
            task = proc_root / pid
            try:
                rows = (task / 'status').read_text(encoding='utf-8')[:12000].splitlines()
                uids = next((r.split()[1:] for r in rows if r.startswith('Uid:')), [])
                if len(uids) != 4 or not all(s.isdecimal() for s in uids):
                    continue
                if int(uids[0]) != app_uid or int(uids[1]) != app_uid:
                    continue
                groups = [r.split(':', 2)[2] for r in
                          (task / 'cgroup').read_text(encoding='ascii').splitlines()
                          if r.startswith('0::')]
                if groups != [group]:
                    continue
                actual = [s for s in (task / 'cmdline').read_bytes()[:8192].split(b'\x00') if s]
                if actual == expected:
                    return True
            except (OSError, UnicodeError, ValueError):
                continue
        return False
    except (OSError, ValueError):
        return False



def _scope_drained(path: Path) -> bool:
    try:
        try:
            st = path.lstat()
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(st.st_mode) or path.is_symlink():
            return False
        if (path / 'cgroup.procs').read_text(encoding='ascii').split():
            return False
        lines = (path / 'cgroup.events').read_text(encoding='ascii').splitlines()
        states = [parts[1] for row in lines if len(parts := row.split()) == 2
                  and parts[0] == 'populated']
        return states == ['0']
    except (OSError, UnicodeError, ValueError):
        return False


def _await_scope_drained(path: Path, seconds: float = 10) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if _scope_drained(path):
            return True
        time.sleep(.1)
    return _scope_drained(path)


def _prepare_controller_home(uid: int) -> None:
    home = Path('/home/projectiq')
    st = home.lstat()
    if not stat.S_ISDIR(st.st_mode) or home.is_symlink() or st.st_uid != uid or st.st_mode & 0o022:
        raise ValueError('Untrusted controller home')
    home.chmod(0o711)
    st2 = home.lstat()
    if st2.st_uid != uid or stat.S_IMODE(st2.st_mode) != 0o711:
        raise ValueError('Controller home traversal not established')


def _trusted_marker(path: Path, uid: int) -> None:
    if path.parent != Path('/home/projectiq') or path.exists() or path.is_symlink():
        raise ValueError('Invalid fixed cancellation marker')
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != uid or not stat.S_ISREG(st.st_mode):
            raise ValueError('Invalid marker ownership')
        os.fchmod(fd, 0o644)
    finally:
        os.close(fd)


def _launch_fault_worker(slice_path: Path, env: dict, app_uid: int,
                         token: str, label: str, command: list[str]):
    if label not in ('crash', 'cancel') or not re.fullmatch('[0-9a-f]{16}', token):
        raise ValueError('Invalid fixed fault scope')
    unit = 'piq-b4-' + token
    argv = ['/usr/bin/python3', '-I', '-c', *command]
    launch = ['/usr/bin/systemd-run','--user','--scope','--quiet',
              '--slice='+SLICE,'--unit='+unit,
              '/usr/bin/sudo','-n','-u','projectiq_run','--',*argv]
    proc = subprocess.Popen(launch, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    scope = slice_path / (unit + '.scope')
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if _scope_member(scope, app_uid, argv):
            return proc, scope
        if proc.poll() is not None:
            break
        time.sleep(.1)
    raise ValueError('Fixed fault worker scope not independently verified')


def _exercise_controlled_failures(slice_path: Path, env: dict,
                                  app_uid: int, uid: int, token: str,
                                  report: dict) -> None:
    # First: verified app process exits deliberately with nonzero status.
    report['probe_stage'] = 'CRASH_WORKER_LAUNCHING'
    proc, scope = _launch_fault_worker(slice_path, env, app_uid,
                                        token[:16], 'crash', [CRASH_WORKER])
    code = proc.wait(timeout=26)
    report['crash_exit_code'] = code
    report['probe_stage'] = 'CRASH_WRAPPER_EXITED'
    report['crash_observed'] = code == 42
    report['crash_scope_drained'] = _await_scope_drained(scope)
    if report['crash_scope_drained']:
        report['probe_stage'] = 'CRASH_SCOPE_DRAINED'
    if not report['crash_observed'] or not report['crash_scope_drained']:
        return
    # Second: verified app process obeys an operator-owned stop marker. The
    # app UID has no write access to its controller-owned home directory.
    stop = Path('/home/projectiq') / ('.projectiq-b4-stop-' + token[16:])
    try:
        report['probe_stage'] = 'CANCEL_WORKER_LAUNCHING'
        proc, scope = _launch_fault_worker(slice_path, env, app_uid,
                                            token[16:], 'cancel', [CANCEL_WORKER, str(stop)])
        _trusted_marker(stop, uid)
        code = proc.wait(timeout=17)
        report['cancel_exit_code'] = code
        report['probe_stage'] = 'CANCEL_WRAPPER_EXITED'
        report['cancel_observed'] = code == 0
        report['cancel_scope_drained'] = _await_scope_drained(scope)
        if report['cancel_scope_drained']:
            report['probe_stage'] = 'CANCEL_SCOPE_DRAINED'
    finally:
        try:
            stop.unlink(missing_ok=True)
        except OSError:
            pass


def _bounded_diagnostics(raw: dict, mode: str) -> dict | None:
    if type(raw) is not dict or set(raw) != {
        'schema_version', 'mode', 'bootstrap_ready', 'workspace_verified',
        'slice_policy_verified', 'scope_verified', 'sentinel_written',
        'sentinel_absent', 'eval_tasks_current', 'outcome', 'probe_stage',
        'crash_observed', 'crash_scope_drained', 'cancel_observed',
        'cancel_scope_drained', 'crash_exit_code', 'cancel_exit_code'}:
        return None
    if raw['schema_version'] != '0.1' or raw['mode'] != mode:
        return None
    if any(type(raw[k]) is not bool for k in (
            'bootstrap_ready', 'workspace_verified', 'slice_policy_verified',
            'scope_verified', 'sentinel_written', 'sentinel_absent',
            'crash_observed', 'crash_scope_drained',
            'cancel_observed', 'cancel_scope_drained')):
        return None
    if (type(raw['eval_tasks_current']) is not int or
            not 0 <= raw['eval_tasks_current'] <= TASKS_MAX):
        return None
    if (type(raw['probe_stage']) is not str or raw['probe_stage'] not in STAGES or
            any(type(raw[k]) is not int or not -64 <= raw[k] <= 255 for k in
                ('crash_exit_code', 'cancel_exit_code')) or
            type(raw['outcome']) is not str or
            raw['outcome'] not in ('NOT_VERIFIED','WORKER_ARMED','FRESH_VM_CLEAN')):
        return None
    return {'probe_stage': raw['probe_stage'],
            'crash_exit_code': raw['crash_exit_code'],
            'cancel_exit_code': raw['cancel_exit_code']}


def _validated_guest_report(raw: dict, mode: str) -> bool:
    """Strict positive-evidence contract, used again on the operator host."""
    if _bounded_diagnostics(raw, mode) is None:
        return False
    if mode == 'arm':
        return (raw['outcome'] == 'WORKER_ARMED' and raw['bootstrap_ready']
                and raw['workspace_verified'] and raw['slice_policy_verified']
                and raw['scope_verified'] and raw['sentinel_written']
                and not raw['sentinel_absent'] and raw['eval_tasks_current'] > 0
                and raw['probe_stage'] == 'HARD_WORKER_ARMED'
                and raw['crash_exit_code'] == 42 and raw['cancel_exit_code'] == 0
                and all(raw[k] for k in ('crash_observed','crash_scope_drained',
                                         'cancel_observed','cancel_scope_drained')))
    return (mode == 'recover' and raw['outcome'] == 'FRESH_VM_CLEAN'
            and raw['bootstrap_ready'] and raw['workspace_verified']
            and raw['slice_policy_verified'] and not raw['scope_verified']
            and not raw['sentinel_written'] and raw['sentinel_absent']
            and raw['eval_tasks_current'] == 0
            and raw['probe_stage'] == 'FRESH_VM_CLEAN'
            and raw['crash_exit_code'] == -1 and raw['cancel_exit_code'] == -1
            and not any(raw[k] for k in ('crash_observed','crash_scope_drained',
                                        'cancel_observed','cancel_scope_drained')))


def _sentinel_is_absent() -> bool:
    try:
        SENTINEL.lstat()
        return False
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _report(mode: str) -> dict:
    return {
        'schema_version': '0.1', 'mode': mode, 'bootstrap_ready': False,
        'workspace_verified': False, 'slice_policy_verified': False,
        'scope_verified': False, 'sentinel_written': False,
        'sentinel_absent': False, 'eval_tasks_current': 0,
        'outcome': 'NOT_VERIFIED',
        'crash_observed': False, 'crash_scope_drained': False,
        'cancel_observed': False, 'cancel_scope_drained': False,
        'probe_stage': 'INITIAL', 'crash_exit_code': -1, 'cancel_exit_code': -1,
    }


def _run(mode: str, token: str) -> dict:
    report = _report(mode)
    if mode not in ('arm', 'recover') or re.fullmatch(r'[0-9a-f]{32}', token) is None:
        return report
    if pwd is None:
        return report
    try:
        uid = os.getuid()
        controller = pwd.getpwnam('projectiq').pw_uid
        app_uid = pwd.getpwnam('projectiq_run').pw_uid
        if uid == 0 or uid != os.geteuid() or uid != controller or app_uid in (0, uid):
            return report
        for _ in range(95):
            if (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                    _ready_marker('/run/projectiq-eval-bootstrap.ready')):
                break
            time.sleep(1)
        if not (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                _ready_marker('/run/projectiq-eval-bootstrap.ready')):
            return report
        report['bootstrap_ready'] = True
        report['probe_stage'] = 'BOOTSTRAPS_READY'
        if not _workspace_ok(uid):
            return report
        report['workspace_verified'] = True
        report['probe_stage'] = 'WORKSPACE_READY'
        env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{uid}',
                   DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
        def systemd(args, timeout):
            return subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=timeout, check=False)
        if systemd(['/usr/bin/systemctl', '--user', 'start', SLICE], 15).returncode:
            return report
        shown = systemd(['/usr/bin/systemctl', '--user', 'show', '-P', 'ControlGroup', SLICE], 10)
        if shown.returncode:
            return report
        scope_root = _policy_path(uid, shown.stdout.decode('ascii').strip())
        if scope_root is None:
            return report
        report['slice_policy_verified'] = True
        report['probe_stage'] = 'EVAL_POLICY_VERIFIED'
        if mode == 'recover':
            report['probe_stage'] = 'FRESH_WORKSPACE_CHECKED'
            report['sentinel_absent'] = _sentinel_is_absent()
            raw = (scope_root / 'pids.current').read_text(encoding='ascii').strip()
            report['eval_tasks_current'] = int(raw) if raw.isascii() and raw.isdecimal() else TASKS_MAX
            leftovers = list(scope_root.glob('piq-b4-*.scope'))
            if report['sentinel_absent'] and report['eval_tasks_current'] == 0 and not leftovers:
                report['outcome'] = 'FRESH_VM_CLEAN'
                report['probe_stage'] = 'FRESH_VM_CLEAN'
            return report
        _prepare_controller_home(uid)
        _exercise_controlled_failures(scope_root, env, app_uid, uid, token, report)
        if not all(report[k] for k in ('crash_observed', 'crash_scope_drained',
                                        'cancel_observed', 'cancel_scope_drained')):
            return report
        report['probe_stage'] = 'HARD_WORKER_LAUNCHING'
        # Exactly one sentinel, on the bounded second disk. NEVER write it to
        # the guest root image. A subsequent fresh VM must not inherit it.
        with SENTINEL.open('xb') as f:
            f.write(token.encode('ascii'))
            f.flush()
            os.fsync(f.fileno())
        report['sentinel_written'] = SENTINEL.is_file()
        if not report['sentinel_written']:
            return report
        # A failed crash scope may remain registered by systemd. NEVER reuse
        # its unit name for the final intentionally persistent worker.
        arm_suffix = hashlib.sha256(('arm:' + token).encode('ascii')).hexdigest()[:16]
        if arm_suffix in (token[:16], token[16:]):
            return report
        unit = 'piq-b4-' + arm_suffix
        argv = ['/usr/bin/python3', '-I', '-c', WORKER]
        launch = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet',
                  '--slice=' + SLICE, '--unit=' + unit,
                  '/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--', *argv]
        # Worker waits without cooperating; the HOST deliberately kills QEMU.
        wrapper = subprocess.Popen(launch, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        scope = scope_root / (unit + '.scope')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _scope_member(scope, app_uid, argv):
                report['scope_verified'] = True
                break
            if wrapper.poll() is not None:
                break
            time.sleep(.1)
        raw = (scope_root / 'pids.current').read_text(encoding='ascii').strip()
        report['eval_tasks_current'] = int(raw) if raw.isascii() and raw.isdecimal() else 0
        if report['scope_verified'] and report['eval_tasks_current'] > 0 and wrapper.poll() is None:
            report['outcome'] = 'WORKER_ARMED'
            report['probe_stage'] = 'HARD_WORKER_ARMED'
    except (OSError, KeyError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return report


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) >= 2 else ''
    token = sys.argv[2] if len(sys.argv) >= 3 else ''
    result = _run(mode, token)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
