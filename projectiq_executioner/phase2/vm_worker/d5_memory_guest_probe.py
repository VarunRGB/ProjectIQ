"""D5b/B3b Gate 2B.1: trusted clean-guest evaluation-slice memory stress.

No GitHub snapshots, caches, student code, or host mounts enter this VM.
This module is sent through SSH stdin and never runs on the Windows host.
"""
from __future__ import annotations

import json
import os
try:
    import pwd
except ImportError:  # Windows imports only the offline test helpers
    pwd = None
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
GUEST_RAM_MIN_KIB = 3200 * 1024
MIN_FREE_AFTER_KIB = 256 * 1024
WORKER_MIB = 1280
WORKER_COUNT = 2
CHECKS = (
    'trusted_bootstraps_complete',
    'evaluation_slice_kernel_policy',
    'guest_ram_headroom',
    'two_unprivileged_scoped_workers',
    'memory_events_cgroup_oom_kill',
    'memory_peak_near_policy_limit',
    'guest_memory_recovers',
    'worker_scopes_cleaned',
)

# A bounded, non-networked, non-student worker. The release file is owned and
# created exclusively by projectiq AFTER trusted /proc UID + cgroup inspection.
# No SIGSTOP (sudo/job-control deadlock). The worker cannot write its release.
WORKER = r'''import os,pwd,stat,sys,time
from pathlib import Path
try:
    marker=Path(sys.argv[1])
    stop_marker=marker.with_name(marker.name+'.stop')
    controller=pwd.getpwnam('projectiq').pw_uid
    app=pwd.getpwnam('projectiq_run').pw_uid
    if os.getuid()!=app or os.geteuid()!=app: sys.exit(111)
    row=[s.split(':',2)[2] for s in Path('/proc/self/cgroup').read_text().splitlines() if s.startswith('0::')]
    if len(row)!=1: sys.exit(112)
    parts=row[0].strip('/').split('/')
    if len(parts)!=6 or parts[-2]!='projectiq-eval.slice' or not parts[-1].startswith('piq-mem-') or not parts[-1].endswith('.scope'): sys.exit(112)
    def stop_requested():
        # Only a regular controller-owned, non-app-writable file can stop
        # this fixed trusted worker. App-owned files are not a stop request.
        try:
            st=stop_marker.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=controller or st.st_mode & 0o022:
                sys.exit(118)
            return True
        except FileNotFoundError: return False
        except PermissionError: sys.exit(117)
    ok=False
    # The controller may spend up to 16 seconds verifying EACH of two
    # scopes.  The previous 12-second gate expired before that budget and
    # caused a genuine early-exit race. Keep a bounded 60-second window.
    for _ in range(600):
        if stop_requested(): sys.exit(0)
        try:
            st=marker.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=controller or st.st_mode & 0o022: sys.exit(113)
            ok=True;break
        except FileNotFoundError: time.sleep(.1)
        except PermissionError: sys.exit(117)
    if not ok: sys.exit(114)
    hold=[]
    for _ in range(80):
        if stop_requested(): sys.exit(0)
        block=bytearray(16*1024*1024)
        for j in range(0,len(block),4096): block[j]=1
        hold.append(block)
        time.sleep(.025)
    # Interruptible hold: a peer may survive the intentional kernel OOM.
    for _ in range(240):
        if stop_requested(): sys.exit(0)
        time.sleep(.05)
except MemoryError: sys.exit(115)
except (OSError,ValueError,KeyError): sys.exit(116)
'''


def _report() -> dict:
    return {'schema_version': '0.1', 'checks': {key: 'NOT_VERIFIED' for key in CHECKS},
            'diagnostics': {}}


def _read_events(path: Path) -> dict[str, int]:
    entries: dict[str, int] = {}
    for line in path.read_text(encoding='ascii').splitlines():
        key, sep, value = line.partition(' ')
        if not sep or not key.isascii() or not value.isascii() or not value.isdecimal():
            raise ValueError('Invalid kernel event entry')
        entries[key] = int(value)
    if not {'max', 'oom', 'oom_kill'}.issubset(entries):
        raise ValueError('Required kernel memory events missing')
    return entries


def _meminfo(key: str) -> int:
    data = Path('/proc/meminfo').read_text(encoding='ascii')
    found = re.search(r'^' + re.escape(key) + r':\s+(\d+) kB$', data, re.MULTILINE)
    if not found:
        raise ValueError('Guest memory metric unavailable')
    return int(found.group(1))


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

    Ubuntu's cloud-init user homes may be 0700 or 0750. The clean memory
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
    """Never block indefinitely on systemd during memory-pressure recovery."""
    try:
        return _run(command, timeout=6, env=env).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


def _cleanup_scoped_workers(workers: list, env: dict[str, str], diag: dict, controller_uid: int) -> bool:
    """First request cooperative stop, then use systemd, and verify the kernel.

    The previous hard-kill attempt returned 1 on both verified scopes; even
    `systemctl stop --no-block` returning 0 left a live memory worker for the
    full 12s observation window. Unlike the systemd-run wrapper, the actual
    trusted Python worker now polls a controller-owned stop marker during both
    allocation and hold. Missing/invalid marker or incomplete scope shutdown
    never counts as successful cleanup.
    """
    # Fully validate both trusted identities BEFORE any lifecycle operations.
    for process, unit, path, release in workers:
        if (not re.fullmatch(r'piq-mem-[0-9a-f]{16}', unit)
                or path.name != unit + '.scope'
                or path.parent.name != SLICE
                or release != CONTROLLER_HOME / (
                    '.projectiq-mem-release-' + unit[len('piq-mem-'):])):
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


def _test_memory(slice_path: Path, uid: int, app_uid: int, env: dict[str, str],
                 diag: dict, checks: dict) -> None:
    events_path = slice_path / 'memory.events'
    baseline = _read_events(events_path)
    peak_path = slice_path / 'memory.peak'
    if not peak_path.is_file():
        return
    workers: list[tuple[subprocess.Popen, str, Path, Path]] = []
    tokens = set()
    release_created = []
    verified = False
    clean = False
    diag['probe_stage'] = 'STARTING_WORKERS'
    diag['worker_launch_observations'] = []
    try:
        for worker_index in range(1, WORKER_COUNT + 1):
            token = secrets.token_hex(8)
            if not re.fullmatch('[0-9a-f]{16}', token) or token in tokens:
                raise ValueError('Invalid unique scope token')
            tokens.add(token)
            unit = 'piq-mem-' + token
            release = CONTROLLER_HOME / ('.projectiq-mem-release-' + token)
            argv = ['/usr/bin/python3', '-I', '-c', WORKER, str(release)]
            launch = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet',
                      '--slice=' + SLICE, '--unit=' + unit,
                      '/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--', *argv]
            process = subprocess.Popen(launch, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
            path = slice_path / (unit + '.scope')
            workers.append((process, unit, path, release))
            observed = {'worker': worker_index, 'scope_seen': False,
                        'max_cgroup_procs': 0, 'app_uid_seen': False,
                        'cgroup_seen': False, 'argv_seen': False,
                        'wrapper_exit_code': -1, 'verified': False}
            diag['worker_launch_observations'].append(observed)
            found = False
            until = time.monotonic() + 16
            while time.monotonic() < until:
                sample = _scope_snapshot(path, app_uid, argv)
                for flag in ('scope_seen', 'app_uid_seen', 'cgroup_seen', 'argv_seen'):
                    observed[flag] = observed[flag] or sample[flag]
                observed['max_cgroup_procs'] = max(
                    observed['max_cgroup_procs'], sample['max_cgroup_procs'])
                if sample['argv_seen']:
                    found = True
                    observed['verified'] = True
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            if not found:
                observed['wrapper_exit_code'] = (
                    process.poll() if process.poll() is not None else -1)
                diag['launch_failure_reason'] = _launch_failure_reason(observed)
                diag['probe_stage'] = 'WORKER_LAUNCH_FAILED'
                return
        diag['probe_stage'] = 'SCOPES_VERIFIED'
        # Both app UIDs and commands were independently observed inside the
        # actual evaluation slice while blocked at the release gate.
        checks['two_unprivileged_scoped_workers'] = 'PASS'
        diag['verified_worker_count'] = len(workers)
        for _proc, _unit, _path, release in workers:
            _publish_release(release, uid)
            release_created.append(release)
        verified = True
        diag['probe_stage'] = 'RELEASE_VISIBILITY_CHECK'
        # The initial failure had both workers in scope, but only ~11 MiB
        # memory. Verify that the app UID can really traverse the controller's
        # home and read each controller-owned release marker before stressing.
        visible = all(_app_can_read_release(release, env)
                      for _p, _u, _path, release in workers)
        diag['release_markers_visible_to_app'] = visible
        if not visible:
            diag['probe_stage'] = 'RELEASE_VISIBILITY_FAILED'
            return
        diag['probe_stage'] = 'MEMORY_STRESS_RUNNING'
        # Monitor actual cgroup kernel events, not process exit alone.
        end = time.monotonic() + 42
        outcome = 'MONITOR_TIMEOUT'
        while time.monotonic() < end:
            measured = _read_events(events_path)
            if measured['oom_kill'] > baseline['oom_kill']:
                outcome = 'KERNEL_OOM_EVENT'
                break
            if all(p.poll() is not None for p, _u, _p, _r in workers):
                outcome = 'ALL_WRAPPERS_EXITED'
                break
            time.sleep(0.2)
        diag['stress_monitor_outcome'] = outcome
        # Recorded BEFORE teardown: not confused with our intentional kill.
        codes = [p.poll() for p, _u, _p, _r in workers]
        diag['wrapper_exit_codes_before_cleanup'] = [c if c is not None else -1
                                                     for c in codes]
    finally:
        # A cgroup OOM killed only one worker; its peer may still be resident.
        # Terminate the actual systemd scopes, not only systemd-run wrappers.
        try:
            clean = _cleanup_scoped_workers(workers, env, diag, uid)
        except (OSError, ValueError, subprocess.SubprocessError):
            clean = False
            diag['scope_cleanup_failure'] = 'UNVERIFIED'
        finally:
            for release in release_created:
                try: release.unlink()
                except OSError: pass
        checks['worker_scopes_cleaned'] = 'PASS' if clean else 'FAIL'
        diag['scope_cleanup_observed'] = clean
    if verified:
        diag['probe_stage'] = 'KERNEL_COUNTERS_COLLECTED'
        after = _read_events(events_path)
        diag['memory_max_events_delta'] = after['max'] - baseline['max']
        diag['memory_oom_events_delta'] = after['oom'] - baseline['oom']
        diag['memory_oom_kill_delta'] = after['oom_kill'] - baseline['oom_kill']
        peak = int(peak_path.read_text().strip())
        diag['memory_peak_bytes'] = peak
        checks['memory_events_cgroup_oom_kill'] = ('PASS' if
            after['max'] > baseline['max'] and after['oom_kill'] > baseline['oom_kill']
            else 'FAIL')
        checks['memory_peak_near_policy_limit'] = ('PASS' if
            MEMORY_LIMIT * 9 // 10 <= peak <= MEMORY_LIMIT + 64 * 1024 ** 2
            else 'FAIL')
        avail = _meminfo('MemAvailable')
        diag['guest_mem_available_after_kib'] = avail
        checks['guest_memory_recovers'] = ('PASS' if avail >= MIN_FREE_AFTER_KIB
                                            else 'FAIL')


def main() -> int:
    result = _report()
    checks = result['checks']
    diag = result['diagnostics']
    try:
        uid = os.getuid()
        if uid == 0 or uid != os.geteuid() or pwd.getpwnam('projectiq').pw_uid != uid:
            raise ValueError('Untrusted controller identity')
        app_uid = pwd.getpwnam('projectiq_run').pw_uid
        if app_uid in (0, uid):
            raise ValueError('Invalid application identity')
        _prepare_controller_home(uid, diag)
        for _ in range(90):
            if (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                    _ready_marker('/run/projectiq-eval-bootstrap.ready')):
                break
            time.sleep(1)
        if not (_ready_marker('/run/projectiq-workspace-bootstrap.ready') and
                _ready_marker('/run/projectiq-eval-bootstrap.ready')):
            print(json.dumps(result, sort_keys=True)); return 0
        checks['trusted_bootstraps_complete'] = 'PASS'
        if not os.path.ismount(WORKSPACE):
            print(json.dumps(result, sort_keys=True)); return 0
        env = _user_env(uid)
        if _run(['/usr/bin/systemctl', '--user', 'start', SLICE], timeout=15, env=env).returncode:
            print(json.dumps(result, sort_keys=True)); return 0
        control = _run(['/usr/bin/systemctl', '--user', 'show', '-P', 'ControlGroup', SLICE],
                       timeout=10, env=env)
        if control.returncode:
            print(json.dumps(result, sort_keys=True)); return 0
        slice_path = _policy_path(uid, control.stdout.decode('ascii', 'strict').strip())
        if slice_path is None:
            print(json.dumps(result, sort_keys=True)); return 0
        checks['evaluation_slice_kernel_policy'] = 'PASS'
        total = _meminfo('MemTotal')
        available = _meminfo('MemAvailable')
        diag['guest_mem_total_kib'] = total
        diag['guest_mem_available_before_kib'] = available
        # This is essential: host/guest OOM cannot prove a 2GiB cgroup ceiling.
        if total < GUEST_RAM_MIN_KIB or available < 2560 * 1024:
            checks['guest_ram_headroom'] = 'FAIL'
            print(json.dumps(result, sort_keys=True)); return 0
        checks['guest_ram_headroom'] = 'PASS'
        _test_memory(slice_path, uid, app_uid, env, diag, checks)
    except (OSError, ValueError, UnicodeError, KeyError, subprocess.TimeoutExpired) as exc:
        diag['probe_error'] = type(exc).__name__
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
