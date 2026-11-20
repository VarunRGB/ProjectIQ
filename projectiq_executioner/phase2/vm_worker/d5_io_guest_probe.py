"""D5b/B3b Gate 2B.3: trusted clean-guest aggregate block-device write throttling.

No GitHub snapshots, caches, student code, or host mounts enter this VM.
This module is sent through SSH stdin and never runs on the Windows host.
"""
from __future__ import annotations

import json
import os
try:
    import pwd
    import grp
except ImportError:  # Windows imports only the offline test helpers
    pwd = None
    grp = None
import re
import secrets
import stat
import subprocess
import time
from pathlib import Path

SLICE = 'projectiq-eval.slice'
MEMORY_LIMIT = 2 * 1024 ** 3
TASK_LIMIT = 80
WRITE_BPS = 16 * 1024 ** 2
CGROUP = Path('/sys/fs/cgroup')
WORKSPACE = Path('/srv/projectiq-workspace')
CONTROLLER_HOME = Path('/home/projectiq')
WORKER_COUNT = 2
WORKER_BYTES = 64 * 1024 * 1024
BASELINE_BYTES = 64 * 1024 * 1024
BASELINE_MIN_BPS = 24 * 1024 * 1024
THROTTLED_MAX_BPS = 20 * 1024 * 1024
IO_MIN_ACCOUNTED = 56 * 1024 * 1024
WORKSPACE_MIB = 256
CHECKS = (
    'trusted_bootstraps_complete',
    'evaluation_slice_kernel_policy',
    'unthrottled_baseline_exceeds_limit',
    'baseline_scope_cleaned',
    'two_unprivileged_scoped_workers',
    'both_worker_io_accounted',
    'kernel_workspace_writes_observed',
    'aggregate_write_rate_throttled',
    'worker_scopes_cleaned',
    'workspace_write_recovery',
)

WORKER = r'''
import mmap,os,pwd,stat,sys,time
from pathlib import Path
try:
    release=Path(sys.argv[1]);role=sys.argv[2]
    if role not in ('baseline','1','2'): sys.exit(111)
    stop=release.with_name(release.name+'.stop')
    uid=pwd.getpwnam('projectiq_run').pw_uid
    controller=pwd.getpwnam('projectiq').pw_uid
    if uid in (0,controller) or os.getuid()!=uid or os.geteuid()!=uid: sys.exit(111)
    rows=[s.split(':',2)[2] for s in Path('/proc/self/cgroup').read_text().splitlines() if s.startswith('0::')]
    if len(rows)!=1: sys.exit(112)
    parts=rows[0].strip('/').split('/')
    if len(parts) not in (5,6) or not parts[-1].startswith('piq-io-') or not parts[-1].endswith('.scope'): sys.exit(112)
    if role=='baseline' and parts[-2]!='projectiq.slice': sys.exit(112)
    if role!='baseline' and parts[-2]!='projectiq-eval.slice': sys.exit(112)
    def trusted(marker):
        try:
            st=marker.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=controller or st.st_mode&0o022: sys.exit(113)
            return True
        except FileNotFoundError: return False
        except PermissionError: sys.exit(117)
    for _ in range(600):
        if trusted(stop): sys.exit(0)
        if trusted(release): break
        time.sleep(.1)
    else: sys.exit(114)
    # O_DIRECT bypasses the page cache. Anonymous mmap provides page-aligned
    # 1MiB buffers; neither stdout nor a student-controlled file is evidence.
    block=1024*1024
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_DIRECT|os.O_CLOEXEC
    token=release.name.rsplit('-',1)[-1]
    if len(token)!=16 or any(c not in '0123456789abcdef' for c in token): sys.exit(115)
    target=Path('/srv/projectiq-workspace')/('piq-io-'+token+'.bin')
    size=64
    fd=-1
    try:
        fd=os.open(target,flags,0o600)
        with mmap.mmap(-1,block) as page:
            # All 64 writes must reach the real ext4 block device.
            for _ in range(size):
                if trusted(stop): sys.exit(118)
                if os.write(fd,page)!=block: sys.exit(119)
        os.fdatasync(fd)
        os.close(fd);fd=-1
        for _ in range(200):
            if trusted(stop): break
            time.sleep(.1)
    finally:
        if fd>=0: os.close(fd)
        try: target.unlink()
        except FileNotFoundError: pass
except (OSError,ValueError,KeyError,IndexError): sys.exit(116)
'''

def _report() -> dict:
    return {'schema_version': '0.1', 'checks': {key: 'NOT_VERIFIED' for key in CHECKS},
            'diagnostics': {}}


def _device_id() -> str:
    device=WORKSPACE.stat().st_dev
    return f'{os.major(device)}:{os.minor(device)}'


class IOStatFormatError(ValueError):
    """Fixed, non-sensitive reason for invalid kernel I/O counter format."""


def _io_stat(path: Path, device: str) -> tuple[int,int]:
    """Read ONLY the numeric kernel wbytes/wios for one exact device.

    cgroup io.stat may contain controller-specific extra nested fields, whose
    values are not necessarily plain unsigned decimals. Validate their bounded
    syntax, but do not interpret or rely on them for enforcement evidence.
    Required counters, duplicate keys and duplicate device rows remain strict.
    """
    raw=(path/'io.stat').read_text(encoding='ascii')
    if len(raw)>16000: raise IOStatFormatError('OVERSIZED_IOSTAT')
    found=[]
    for line in raw.splitlines():
        fields=line.split()
        if not fields or fields[0]!=device: continue
        seen=set();counters={}
        for pair in fields[1:]:
            key,sep,value=pair.partition('=')
            if not sep:
                raise IOStatFormatError('UNEXPECTED_BARE_TOKEN')
            if key in seen:
                raise IOStatFormatError('DUPLICATE_FIELD')
            if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,47}',key):
                raise IOStatFormatError('INVALID_OPTIONAL_KEY')
            if (not 1<=len(value)<=96 or not value.isascii()
                    or any(ch.isspace() for ch in value)):
                raise IOStatFormatError('INVALID_OPTIONAL_VALUE')
            seen.add(key)
            if key in ('wbytes','wios'):
                if not value.isdecimal() or len(value)>20:
                    raise IOStatFormatError('INVALID_REQUIRED_COUNTER')
                counters[key]=int(value)
        if not counters:
            # io.stat is a nested-keyed device record. A freshly started
            # baseline cgroup may have only read/discard/optional statistics,
            # before it has issued any writes. No write counters is a zero
            # starting point, NOT evidence that any writes occurred. Requiring
            # both counters AFTER the workload is enforced by the minimum
            # independently observed wbytes deltas (parent and scope).
            # An io.stat row can contain ONLY the device identifier before
            # any I/O is charged. Treat it as the zero baseline, never as
            # evidence of writes. Malformed nonempty tokens still fail above.
            found.append((0,0))
            continue
        if set(counters)!={'wbytes','wios'}:
            # Partial accounting is not a trustworthy write-count record.
            raise IOStatFormatError('MISSING_REQUIRED_COUNTER')
        found.append((counters['wbytes'],counters['wios']))
    if len(found)>1: raise IOStatFormatError('DUPLICATE_DEVICE')
    return found[0] if found else (0,0)


def _no_tighter_io_cap(path: Path, device: str) -> bool:
    """Baseline path must not inherit a 16 MiB/s cap at its own level."""
    for node in (path,path.parent):
        try: raw=(node/'io.max').read_text(encoding='ascii')
        except OSError: return False
        for line in raw.splitlines():
            fields=line.split()
            if not fields or fields[0]!=device: continue
            for pair in fields[1:]:
                if pair.startswith('wbps=') and pair[5:]!='max': return False
    return True


def _valid_io_evidence(diag: dict) -> bool:
    """No throughput claim without a faster control and real kernel bytes."""
    return (type(diag.get('baseline_wbytes_delta')) is int and
            diag['baseline_wbytes_delta']>=IO_MIN_ACCOUNTED and
            type(diag.get('baseline_bps')) is int and
            diag['baseline_bps']>=BASELINE_MIN_BPS and
            type(diag.get('eval_parent_wbytes_delta')) is int and
            diag['eval_parent_wbytes_delta']>=2*WORKER_BYTES and
            type(diag.get('eval_elapsed_ms')) is int and
            diag['eval_elapsed_ms']>=5000 and
            type(diag.get('eval_aggregate_bps')) is int and
            0<diag['eval_aggregate_bps']<=THROTTLED_MAX_BPS and
            type(diag.get('first_scope_wbytes')) is int and
            diag['first_scope_wbytes']>=IO_MIN_ACCOUNTED and
            type(diag.get('second_scope_wbytes')) is int and
            diag['second_scope_wbytes']>=IO_MIN_ACCOUNTED and
            diag.get('concurrent_scope_io_observed') is True and
            diag.get('release_markers_visible_to_app') is True)


def _ready_marker(path: str) -> bool:
    try:
        node = Path(path)
        st = node.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not (st.st_mode & 0o022)
    except OSError:
        return False


def _policy_path(uid: int, control_group: str) -> Path | None:
    parts = control_group.strip('/').split('/')
    expected = ['user.slice', f'user-{uid}.slice', f'user@{uid}.service',
                'projectiq.slice', SLICE]
    if parts != expected:
        return None
    path = CGROUP.joinpath(*parts)
    if path.is_symlink() or not path.is_dir():
        return None
    try:
        if (path / 'memory.max').read_text().strip() != str(MEMORY_LIMIT):
            return None
        if (path / 'pids.max').read_text().strip() != str(TASK_LIMIT):
            return None
        device = WORKSPACE.stat().st_dev
        tag = f'{os.major(device)}:{os.minor(device)}'
        rows = [line.split() for line in (path / 'io.max').read_text().splitlines()]
        if not any(row and row[0] == tag and f'wbps={WRITE_BPS}' in row[1:]
                   for row in rows):
            return None
        return path
    except (OSError, ValueError):
        return None


def _user_env(uid: int) -> dict[str, str]:
    return dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{uid}',
                DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')


def _scope_snapshot(path: Path, app_uid: int, expected_argv: list[str]) -> dict:
    """Classify kernel evidence without returning PIDs or student-controlled text.

    A scope can vanish during inspection; the caller accumulates positive
    evidence across polls instead of treating the final empty poll as truth.
    """
    observation = {'scope_seen': False, 'max_cgroup_procs': 0,
                   'app_uid_seen': False, 'cgroup_seen': False,
                   'argv_seen': False}
    try:
        if path.is_symlink() or not path.is_dir():
            return observation
        observation['scope_seen'] = True
        pids = (path / 'cgroup.procs').read_text(encoding='ascii').split()
        observation['max_cgroup_procs'] = min(len(pids), 30)
        if not 0 < len(pids) < 30:
            return observation
        expected_group = '/' + str(path.relative_to(CGROUP)).replace('\\', '/')
        wanted = [a.encode('utf-8') for a in expected_argv]
        for pid in pids:
            if not pid.isascii() or not pid.isdecimal() or len(pid) > 10:
                return observation
            task = Path('/proc') / pid
            try:
                statuses = (task / 'status').read_text(encoding='utf-8')[:12000].splitlines()
                uidline = next((x for x in statuses if x.startswith('Uid:')), '')
                uids = uidline.split()[1:]
                if len(uids) != 4 or any(not x.isdecimal() for x in uids):
                    continue
                if int(uids[0]) != app_uid or int(uids[1]) != app_uid:
                    continue
                observation['app_uid_seen'] = True
                groups = [(x.split(':', 2)[2]) for x in
                          (task / 'cgroup').read_text(encoding='ascii').splitlines()
                          if x.startswith('0::')]
                if groups != [expected_group]:
                    continue
                observation['cgroup_seen'] = True
                args = [x for x in (task / 'cmdline').read_bytes()[:8192].split(b'\0') if x]
                if args == wanted:
                    observation['argv_seen'] = True
                    return observation
            except (OSError, UnicodeError, ValueError):
                # A genuine process may race with our kernel observations.
                continue
        return observation
    except (OSError, ValueError, UnicodeError):
        return observation


def _scope_member(path: Path, app_uid: int, expected_argv: list[str]) -> bool:
    return _scope_snapshot(path, app_uid, expected_argv)['argv_seen']


def _launch_failure_reason(observation: dict) -> str:
    """Stable, bounded diagnostic code; never include stdout, paths or PIDs."""
    code = observation.get('wrapper_exit_code', -1)
    if code in (111, 112, 113, 114, 115, 116):
        return 'WORKER_EXIT_' + str(code)
    if code < -1:
        return 'WRAPPER_SIGNALLED'
    if code >= 0:
        return 'WRAPPER_EXITED'
    if not observation.get('scope_seen'):
        return 'SCOPE_NOT_OBSERVED'
    if not observation.get('app_uid_seen'):
        return 'APP_UID_NOT_OBSERVED'
    if not observation.get('cgroup_seen'):
        return 'CGROUP_MEMBERSHIP_NOT_OBSERVED'
    if not observation.get('argv_seen'):
        return 'EXACT_COMMAND_NOT_OBSERVED'
    return 'LAUNCH_VERIFICATION_TIMEOUT'


def _prepare_controller_home(controller_uid: int, diag: dict) -> None:
    """Match the working app runner's 0711 traversal-only home boundary.

    Ubuntu's cloud-init user homes may be 0700 or 0750. The clean PID
    probe (unlike guest_agent._verify_execution_identity) did not grant the
    unprivileged worker traversal, so its fixed 0644 release marker was
    inaccessible even when both worker PIDs were correctly verified.
    No project/user input selects this exact path. No directory listing or
    group/other write access is granted. The VM and change are disposable.
    """
    node = CONTROLLER_HOME.lstat()
    if (not stat.S_ISDIR(node.st_mode) or node.st_uid != controller_uid
            or node.st_mode & 0o022):
        raise ValueError('Controller home must be owned and non-writable by app')
    diag['controller_home_mode_before'] = stat.S_IMODE(node.st_mode)
    CONTROLLER_HOME.chmod(0o711)
    after = CONTROLLER_HOME.lstat()
    diag['controller_home_mode_after'] = stat.S_IMODE(after.st_mode)
    if (not stat.S_ISDIR(after.st_mode) or after.st_uid != controller_uid
            or stat.S_IMODE(after.st_mode) != 0o711):
        raise ValueError('Controller release-marker traversal setup failed')


def _app_can_read_release(path: Path, env: dict[str, str]) -> bool:
    """Independent app-UID read test, no shell, credentials or output."""
    result = _run(['/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--',
                   '/usr/bin/test', '-r', path.as_posix()], timeout=6, env=env)
    return result.returncode == 0


def _publish_release(path: Path, controller_uid: int) -> None:
    # Directory is controller-owned, 0711; app UID has no ability to create a
    # marker. O_NOFOLLOW + O_EXCL ensures a preexisting path is never reused.
    if path.parent != CONTROLLER_HOME or path.exists() or path.is_symlink():
        raise ValueError('Invalid release path')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o644)  # readable by app after publication, never writable
        st = os.fstat(fd)
        if st.st_uid != controller_uid or not stat.S_ISREG(st.st_mode):
            raise ValueError('Untrusted release owner')
    finally:
        os.close(fd)


def _run(cmd: list[str], *, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False, timeout=timeout)


def _scope_cleanup_state(path: Path) -> tuple[str, int]:
    """Observe a trusted scope after teardown, including any nested cgroups.

    cgroup.procs alone ignores descendants. cgroup.events populated=0 is
    kernel evidence that the scope and all descendants contain no live tasks.
    Missing scopes are also drained; unreadable/ambiguous scopes fail closed.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 'ABSENT', 0
    except OSError:
        return 'UNKNOWN', 0
    if not stat.S_ISDIR(info.st_mode):
        return 'UNKNOWN', 0
    try:
        pids = (path / 'cgroup.procs').read_text(encoding='ascii').split()
        content = (path / 'cgroup.events').read_text(encoding='ascii')
        states = [v for k, v in (line.split() for line in content.splitlines())
                  if k == 'populated' and v in ('0', '1')]
        if len(states) != 1:
            return 'UNKNOWN', min(len(pids), 30)
        return ('DRAINED' if not pids and states[0] == '0' else 'POPULATED',
                min(len(pids), 30))
    except (OSError, ValueError, UnicodeError):
        return 'UNKNOWN', 0


def _scope_command(command: list[str], env: dict[str, str]) -> int:
    """Never block indefinitely on systemd during PID-pressure recovery."""
    try:
        return _run(command, timeout=6, env=env).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def _cleanup_scoped_workers(workers: list, env: dict[str, str], diag: dict, controller_uid: int) -> bool:
    """First request cooperative stop, then use systemd, and verify the kernel.

    The previous hard-kill attempt returned 1 on both verified scopes; even
    `systemctl stop --no-block` returning 0 left a live PID worker for the
    full 12s observation window. Unlike the systemd-run wrapper, the actual
    trusted Python worker now polls a controller-owned stop marker during both
    allocation and hold. Missing/invalid marker or incomplete scope shutdown
    never counts as successful cleanup.
    """
    # Fully validate both trusted identities BEFORE any lifecycle operations.
    for process, unit, path, release in workers:
        if (not re.fullmatch(r'piq-io-[0-9a-f]{16}', unit)
                or path.name != unit + '.scope'
                or path.parent.name not in (SLICE,'projectiq.slice')
                or release != CONTROLLER_HOME / (
                    '.projectiq-io-release-' + unit[len('piq-io-'):])):
            raise ValueError('Unexpected trusted memory scope identity')
    observations = [
        {'worker': index, 'kill_returncode': -2, 'stop_returncode': -2,
         'scope_state': 'UNKNOWN', 'remaining_tasks': 0,
         'wrapper_exit_code_after': -1, 'stop_marker_published': False,
         'kill_failure_reason': 'NOT_ATTEMPTED'}
        for index in range(1, len(workers) + 1)
    ]
    created = []

    def observe() -> bool:
        clean = True
        for row, (process, unit, path, release) in zip(observations, workers):
            state, count = _scope_cleanup_state(path)
            row['scope_state'] = state
            row['remaining_tasks'] = count
            code = process.poll()
            row['wrapper_exit_code_after'] = -1 if code is None else code
            if state not in ('ABSENT', 'DRAINED') or code is None:
                clean = False
        return clean

    try:
        # The private controller home is already validated mode 0711. The
        # fresh stop files use O_EXCL/O_NOFOLLOW, user-only creation and app-
        # readable but not app-writable permissions, just like release files.
        for row, (_process, _unit, _path, release) in zip(observations, workers):
            stop_file = release.with_name(release.name + '.stop')
            _publish_release(stop_file, controller_uid)
            created.append(stop_file)
            row['stop_marker_published'] = True
        diag['cooperative_stop_markers_published'] = len(created)
        # Give the two Python workers a bounded opportunity to observe stop.
        until = time.monotonic() + 6
        while True:
            if observe():
                diag['scope_cleanup_observations'] = observations
                return True
            if time.monotonic() >= until:
                break
            time.sleep(0.1)
        # Fallback for unresponsive worker: call systemd on the exact validated
        # transient scope, not on the local systemd-run wrapper.
        for row, (_process, unit, path, _release) in zip(observations, workers):
            if row['scope_state'] in ('ABSENT', 'DRAINED'):
                continue
            scope = unit + '.scope'
            row['kill_returncode'], row['kill_failure_reason'] = _scope_kill_command(
                ['/usr/bin/systemctl', '--user', 'kill',
                 '--kill-whom=all', '--signal=SIGKILL', scope], env)
            row['stop_returncode'] = _scope_command(
                ['/usr/bin/systemctl', '--user', '--no-block', 'stop', scope], env)
        # The wrapper may be outside the app scope; killing it is strictly a
        # fallback. It is never treated as proof of application process death.
        for process, _unit, _path, _release in workers:
            if process.poll() is None:
                try: process.kill()
                except OSError: pass
            try: process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired): pass
        until = time.monotonic() + 12
        while True:
            if observe():
                diag['scope_cleanup_observations'] = observations
                return True
            if time.monotonic() >= until:
                diag['scope_cleanup_observations'] = observations
                return False
            time.sleep(0.25)
    finally:
        for stop_file in created:
            try: stop_file.unlink()
            except OSError: pass


def _scope_kill_command(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Bounded category only; never export arbitrary stderr, PIDs or paths."""
    try:
        process = subprocess.run(command, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, timeout=6, check=False)
        rc = process.returncode
        if rc == 0:
            return 0, 'NONE'
        err = process.stderr[:600].lower()
        if b'unrecognized option' in err or b'unknown option' in err:
            return rc, 'UNSUPPORTED_OPTION'
        if b'not loaded' in err or b'not found' in err:
            return rc, 'UNIT_NOT_FOUND'
        if b'permission denied' in err or b'access denied' in err:
            return rc, 'PERMISSION_DENIED'
        if b'no such process' in err:
            return rc, 'NO_SUCH_PROCESS'
        return rc, 'OTHER_ERROR'
    except subprocess.TimeoutExpired:
        return -1, 'COMMAND_TIMEOUT'
    except OSError:
        return -1, 'COMMAND_UNAVAILABLE'



def _launch_worker(parent: Path, uid: int, app_uid: int, env: dict[str,str],
                   role: str, diag: dict) -> tuple | None:
    """Only fixed trusted code runs; verify UID, cgroup and exact argv."""
    token=secrets.token_hex(8)
    if not re.fullmatch(r'[0-9a-f]{16}',token):
        raise ValueError('Unexpected scope token')
    unit='piq-io-'+token
    release=CONTROLLER_HOME/('.projectiq-io-release-'+token)
    argv=['/usr/bin/python3','-I','-c',WORKER,release.as_posix(),role]
    command=['/usr/bin/systemd-run','--user','--scope','--quiet',
             '--property=IOAccounting=yes',
             '--slice='+parent.name,'--unit='+unit,
             '/usr/bin/sudo','-n','-u','projectiq_run','--',*argv]
    process=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             start_new_session=True)
    path=parent/(unit+'.scope')
    worker=(process,unit,path,release)
    row={'worker':0 if role=='baseline' else int(role),
         'scope_seen':False,'max_cgroup_procs':0,'app_uid_seen':False,
         'cgroup_seen':False,'argv_seen':False,'wrapper_exit_code':-1,'verified':False}
    observations=diag.setdefault('worker_launch_observations',[])
    observations.append(row)
    until=time.monotonic()+16
    while time.monotonic()<until:
        sample=_scope_snapshot(path,app_uid,argv)
        for k in ('scope_seen','app_uid_seen','cgroup_seen','argv_seen'):
            row[k] = row[k] or sample[k]
        row['max_cgroup_procs']=max(row['max_cgroup_procs'],sample['max_cgroup_procs'])
        if sample['argv_seen']:
            row['verified']=True
            break
        if process.poll() is not None: break
        time.sleep(.1)
    if not row['verified']:
        code=process.poll()
        row['wrapper_exit_code']=-1 if code is None else code
        diag['launch_failure_reason']=_launch_failure_reason(row)
        diag['probe_stage']='WORKER_LAUNCH_FAILED'
    return worker


def _workspace_mount_ok(controller_uid: int) -> bool:
    """No fallthrough to guest root disk: verify ext4 and fixed 256 MiB mount."""
    try:
        if (grp is None or WORKSPACE.is_symlink() or not WORKSPACE.is_dir()
                or not os.path.ismount(WORKSPACE)):
            return False
        rows=[line.split() for line in Path('/proc/mounts').read_text(encoding='utf-8').splitlines()]
        found=[row for row in rows if len(row)>=4 and row[1]==WORKSPACE.as_posix()]
        if len(found)!=1 or found[0][2]!='ext4' or not {
                'rw','nosuid','nodev'}.issubset(set(found[0][3].split(','))):
            return False
        st=WORKSPACE.stat();size=os.statvfs(WORKSPACE)
        if (st.st_uid!=controller_uid or st.st_gid!=grp.getgrnam('projectiq_exec').gr_gid
                or (st.st_mode & 0o2777)!=0o2770):
            return False
        total=size.f_blocks*size.f_frsize
        return 200*1024*1024<=total<=WORKSPACE_MIB*1024*1024
    except (OSError,KeyError,ValueError,UnicodeError):
        return False


def _workspace_free() -> int:
    st=os.statvfs(WORKSPACE)
    return st.f_bavail*st.f_frsize


def _verify_app_marker_visibility(controller_uid: int, env: dict[str,str]) -> bool:
    """Check read visibility without prematurely releasing any I/O worker."""
    token=secrets.token_hex(8)
    if not re.fullmatch(r'[0-9a-f]{16}',token):raise ValueError('Invalid marker token')
    marker=CONTROLLER_HOME/('.projectiq-io-visibility-'+token)
    _publish_release(marker,controller_uid)
    try:return _app_can_read_release(marker,env)
    finally:
        try:marker.unlink()
        except OSError:pass


def _test_io(slice_path: Path, uid: int, app_uid: int, env: dict[str,str],
             diag: dict, checks: dict) -> None:
    device=_device_id()
    # The workspace disk is the one named by the enforced io.max entry.
    parent=slice_path.parent
    if (parent.name!='projectiq.slice' or
            not _no_tighter_io_cap(parent,device)):
        diag['probe_stage']='BASELINE_POLICY_NOT_UNBOUNDED'
        return
    diag['workspace_free_before']=_workspace_free()
    # Readability is tested on a separate, same-owner/mode marker BEFORE
    # any worker release, so the timer includes every actual disk write.
    visible=_verify_app_marker_visibility(uid,env)
    diag['release_markers_visible_to_app']=visible
    if not visible:
        diag['probe_stage']='BASELINE_RELEASE_UNREADABLE'
        return
    diag['probe_stage']='BASELINE_IOSTAT_PREFLIGHT'
    baseline=[];bdiag={};release_files=[]
    try:
        try:
            start_bytes=_io_stat(parent,device)[0]
        except IOStatFormatError as exc:
            diag['probe_stage']='BASELINE_IOSTAT_INVALID'
            diag['baseline_io_stat_problem']=str(exc)
            return
        except (OSError, UnicodeError):
            diag['probe_stage']='BASELINE_IOSTAT_UNREADABLE'
            return
        diag['probe_stage']='BASELINE_WORKER_LAUNCHING'
        worker=_launch_worker(parent,uid,app_uid,env,'baseline',bdiag)
        baseline.append(worker)
        diag['baseline_launch_verified']=bdiag['worker_launch_observations'][0]['verified']
        if not diag['baseline_launch_verified']:
            diag['probe_stage']='BASELINE_LAUNCH_FAILED'
            return
        if not _no_tighter_io_cap(worker[2],device):
            diag['probe_stage']='BASELINE_SCOPE_LIMITED'
            return
        release=worker[3]
        started=time.monotonic()
        _publish_release(release,uid);release_files.append(release)
        measured=0;scope_bytes=0
        until=started+18
        while time.monotonic()<until:
            measured=max(measured,_io_stat(parent,device)[0]-start_bytes)
            scope_bytes=max(scope_bytes,_io_stat(worker[2],device)[0])
            if measured>=IO_MIN_ACCOUNTED and scope_bytes>=IO_MIN_ACCOUNTED:
                break
            if worker[0].poll() is not None:break
            time.sleep(.05)
        elapsed=max(1,round(1000*(time.monotonic()-started)))
        diag['baseline_wbytes_delta']=measured
        diag['baseline_scope_wbytes']=scope_bytes
        diag['baseline_elapsed_ms']=elapsed
        diag['baseline_bps']=int(measured*1000//elapsed)
        diag['baseline_outcome']=('FASTER_THAN_EVAL_LIMIT' if
            measured>=IO_MIN_ACCOUNTED and scope_bytes>=IO_MIN_ACCOUNTED and
            diag['baseline_bps']>=BASELINE_MIN_BPS else 'INCONCLUSIVE')
        checks['unthrottled_baseline_exceeds_limit']=('PASS' if
            diag['baseline_outcome']=='FASTER_THAN_EVAL_LIMIT' else 'NOT_VERIFIED')
    finally:
        try:
            clean=_cleanup_scoped_workers(baseline,env,bdiag,uid) if baseline else False
        except (OSError,ValueError,subprocess.SubprocessError):clean=False
        if baseline:
            diag['baseline_scope_cleaned']=clean
        observed=bdiag.get('scope_cleanup_observations',[])
        if len(observed)==1:
            row=observed[0]
            diag['baseline_cleanup_scope_state']=row['scope_state']
            diag['baseline_cleanup_remaining_tasks']=row['remaining_tasks']
            diag['baseline_cleanup_wrapper_exit_code']=row['wrapper_exit_code_after']
            diag['baseline_cleanup_stop_marker_published']=row['stop_marker_published']
        checks['baseline_scope_cleaned']=(
            'PASS' if clean else 'FAIL') if baseline else 'NOT_VERIFIED'
        for release in release_files:
            try:release.unlink()
            except OSError:pass
        if baseline:
            token=baseline[0][1][-16:]
            if (WORKSPACE/('piq-io-'+token+'.bin')).exists():
                diag['baseline_scope_cleaned']=False
                checks['baseline_scope_cleaned']='FAIL'
    if checks['unthrottled_baseline_exceeds_limit']!='PASS' or checks['baseline_scope_cleaned']!='PASS':
        return
    if _io_stat(slice_path,device)[0]>2*1024*1024:
        diag['probe_stage']='UNEXPECTED_EXISTING_EVAL_WRITES';return
    diag['probe_stage']='EVAL_WORKERS_STARTING'
    workers=[];release_files=[];wdiag={};clean=False
    try:
        for role in ('1','2'):
            worker=_launch_worker(slice_path,uid,app_uid,env,role,wdiag)
            workers.append(worker)
            if not wdiag['worker_launch_observations'][-1]['verified']:
                diag['probe_stage']='WORKER_LAUNCH_FAILED'
                break
        diag['worker_launch_observations']=wdiag.get('worker_launch_observations',[])
        diag['verified_worker_count']=sum(1 for r in diag['worker_launch_observations'] if r['verified'])
        if len(workers)!=2 or diag['verified_worker_count']!=2:return
        checks['two_unprivileged_scoped_workers']='PASS'
        base=_io_stat(slice_path,device)[0]
        diag['probe_stage']='EVAL_RELEASE_VISIBILITY'
        # The clock starts before *either* release marker is published.
        started=time.monotonic()
        for worker in workers:
            release=worker[3]
            _publish_release(release,uid);release_files.append(release)
        diag['probe_stage']='EVAL_IO_RUNNING'
        amount=0;max1=0;max2=0;concurrent=False
        until=started+48
        finished=False
        while time.monotonic()<until:
            amount=max(amount,_io_stat(slice_path,device)[0]-base)
            first=_io_stat(workers[0][2],device)[0]
            second=_io_stat(workers[1][2],device)[0]
            max1=max(max1,first);max2=max(max2,second)
            if first>=8*1024*1024 and second>=8*1024*1024:
                concurrent=True
            if (amount>=2*WORKER_BYTES and
                    max1>=IO_MIN_ACCOUNTED and max2>=IO_MIN_ACCOUNTED):
                finished=True;break
            if any(w[0].poll() is not None for w in workers):
                diag['stress_monitor_outcome']='WORKER_EXITED_EARLY';break
            time.sleep(.05)
        elapsed=max(1,round(1000*(time.monotonic()-started)))
        diag['eval_parent_wbytes_delta']=amount
        diag['eval_elapsed_ms']=elapsed
        diag['eval_aggregate_bps']=int(amount*1000//elapsed)
        diag['first_scope_wbytes']=max1
        diag['second_scope_wbytes']=max2
        diag['concurrent_scope_io_observed']=concurrent
        if finished:
            diag['stress_monitor_outcome']='BLOCK_IO_WRITES_ACCOUNTED'
        else:
            diag.setdefault('stress_monitor_outcome','MONITOR_TIMEOUT')
        checks['both_worker_io_accounted']=('PASS' if
            max1>=IO_MIN_ACCOUNTED and max2>=IO_MIN_ACCOUNTED and concurrent
            else 'FAIL')
        checks['kernel_workspace_writes_observed']=('PASS' if
            amount>=2*WORKER_BYTES and finished else 'FAIL')
        checks['aggregate_write_rate_throttled']=('PASS' if
            finished and _valid_io_evidence(diag) else 'FAIL')
    finally:
        try:
            clean=_cleanup_scoped_workers(workers,env,wdiag,uid) if workers else False
        except (OSError,ValueError,subprocess.SubprocessError):clean=False
        diag['scope_cleanup_observed']=clean
        diag['scope_cleanup_observations']=wdiag.get('scope_cleanup_observations',[])
        diag['cooperative_stop_markers_published']=wdiag.get('cooperative_stop_markers_published',0)
        checks['worker_scopes_cleaned']='PASS' if len(workers)==2 and clean else 'FAIL'
        for release in release_files:
            try:release.unlink()
            except OSError:pass
        if len(workers)==2 and clean:
            files_absent=all(not (WORKSPACE/('piq-io-'+u[-16:]+'.bin')).exists()
                             for _p,u,_path,_release in workers)
            free_after=_workspace_free()
            diag['workspace_free_after']=free_after
            diag['workspace_files_removed']=files_absent
            checks['workspace_write_recovery']=('PASS' if files_absent and
                free_after>=diag['workspace_free_before']-8*1024*1024 else 'FAIL')
    diag['probe_stage']='KERNEL_COUNTERS_COLLECTED'


def main() -> int:
    result=_report();checks=result['checks'];diag=result['diagnostics']
    try:
        uid=os.getuid()
        if pwd is None or uid==0 or uid!=os.geteuid() or pwd.getpwnam('projectiq').pw_uid!=uid:
            raise ValueError('Untrusted controller identity')
        app_uid=pwd.getpwnam('projectiq_run').pw_uid
        if app_uid in (0,uid):raise ValueError('Untrusted app identity')
        _prepare_controller_home(uid,diag)
        for _ in range(90):
            if (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                _ready_marker('/run/projectiq-eval-bootstrap.ready')):break
            time.sleep(1)
        if not (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                _ready_marker('/run/projectiq-eval-bootstrap.ready')):
            print(json.dumps(result,sort_keys=True));return 0
        checks['trusted_bootstraps_complete']='PASS'
        if not _workspace_mount_ok(uid):
            print(json.dumps(result,sort_keys=True));return 0
        env=_user_env(uid)
        if _run(['/usr/bin/systemctl','--user','start',SLICE],timeout=15,env=env).returncode:
            print(json.dumps(result,sort_keys=True));return 0
        control=_run(['/usr/bin/systemctl','--user','show','-P','ControlGroup',SLICE],timeout=10,env=env)
        if control.returncode:
            print(json.dumps(result,sort_keys=True));return 0
        slice_path=_policy_path(uid,control.stdout.decode('ascii','strict').strip())
        if slice_path is None:
            print(json.dumps(result,sort_keys=True));return 0
        checks['evaluation_slice_kernel_policy']='PASS'
        _test_io(slice_path,uid,app_uid,env,diag,checks)
    except (OSError,ValueError,UnicodeError,KeyError,subprocess.TimeoutExpired) as exc:
        diag['probe_error']=type(exc).__name__
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':
    raise SystemExit(main())
