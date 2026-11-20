"""Trusted, bounded B3b cgroup-v2 observation; no project bytes are present.

This is the first gate, not an assertion that production jobs are yet placed
inside this slice. Exits with bounded JSON even if the user manager is absent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

SLICE = 'projectiq-probe.slice'
WORKSPACE = '/srv/projectiq-workspace'
MEMORY = 192 * 1024 * 1024
PIDS = 32
IO_BPS = 4 * 1024 * 1024
CHECKS = ('user_manager_reachable', 'app_process_inside_slice',
          'memory_max_kernel_value', 'pids_max_kernel_value',
          'io_max_workspace_device', 'app_cannot_modify_limits',
          'pids_limit_enforced')

# This code runs only inside the trusted slice and as the unprivileged app UID.
# It reads process membership and kernel control files; then attempts limited
# thread creation, not a fork bomb. Threads are joined before the process exits.
CHILD = r'''import json,os,threading,time,resource
from pathlib import Path
out={'membership':'', 'memory':'', 'pids':'', 'io':'',
     'workspace_dev':'', 'tamper_denied':False,'thread_denied':False,
     'pids_events_delta':0}
try:
    raw=Path('/proc/self/cgroup').read_text()
    rows=[line.split(':',2) for line in raw.splitlines() if line.startswith('0::')]
    if len(rows)!=1: raise ValueError('not unified hierarchy')
    group=rows[0][2]
    out['membership']=group
    parts=group.strip('/').split('/')
    if 'projectiq-probe.slice' not in parts: raise ValueError('not in scope')
    stem=Path('/sys/fs/cgroup', *parts[:parts.index('projectiq-probe.slice')+1])
    out['memory']=(stem/'memory.max').read_text().strip()
    out['pids']=(stem/'pids.max').read_text().strip()
    try:
        out['io'] = (stem / 'io.max').read_text().strip()[:800]
    except FileNotFoundError:
        # Only gather diagnostics. Missing io.max still fails closed.
        # Traverse the known systemd ancestors of the probe slice to
        # locate the point at which the I/O controller disappears.
        levels = [
            ('root', stem.parents[4]),
            ('user', stem.parents[3]),
            ('uid', stem.parents[2]),
            ('manager', stem.parents[1]),
            ('projectiq', stem.parent),
        ]
        # Read-only cgroup ownership and topology diagnostic. A user manager
        # without write access to its subtree_control cannot enable `io`.
        manager = stem.parents[1]
        try:
            st = (manager / 'cgroup.subtree_control').stat()
            out['manager_control_owner'] = (
                f'{st.st_uid}:{st.st_gid}:{oct(st.st_mode & 0o777)}'
            )
        except OSError as e:
            out['manager_control_owner'] = type(e).__name__
        try:
            out['manager_direct_procs'] = len(
                (manager / 'cgroup.procs').read_text().splitlines()
            )
        except OSError:
            out['manager_direct_procs'] = -1

        def io_flag(group, filename):
            path = group / filename
            if not path.is_file():
                return '?'
            return '1' if 'io' in path.read_text().split() else '0'

        out['error_detail'] = '; '.join(
            f'{name}:A={io_flag(path, "cgroup.controllers")},'
            f'E={io_flag(path, "cgroup.subtree_control")}'
            for name, path in levels
        )[:240]
        raise
    stat=os.stat('/srv/projectiq-workspace')
    out['workspace_dev']=f'{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}'
    def event_max(group):
        path=group/'pids.events'
        if not path.is_file(): return None
        for item in path.read_text().splitlines():
            k,_,v=item.partition(' ')
            if k=='max': return int(v)
        raise ValueError('missing pids events max')
    def maybe_int(path):
        try: return int(path.read_text().strip())
        except (OSError,ValueError): return None
    scope=Path('/sys/fs/cgroup',*parts)
    before=event_max(stem)
    if before is None: raise FileNotFoundError('slice pids.events missing')
    scope_before=event_max(scope)
    out['pids_current_before']=maybe_int(stem/'pids.current')
    out['scope_pids_max']=(scope/'pids.max').read_text().strip() if (scope/'pids.max').is_file() else 'MISSING'
    out['nproc_soft_hard']=list(resource.getrlimit(resource.RLIMIT_NPROC))
    try:
        with (stem/'memory.max').open('w') as f: f.write('max')
        out['tamper_denied']=False
    except PermissionError:
        out['tamper_denied']=True
    # Hold a bounded number of threads simultaneously. Observe cgroup counts
    # *before* releasing them, and inspect both parent and scope event counters.
    active=[]
    ready=threading.Event()
    try:
        for i in range(64):
            t=threading.Thread(target=ready.wait, daemon=True)
            t.start(); active.append(t)
    except RuntimeError as exc:
        out['thread_denied']=True
        out['thread_error']=str(exc)[:100]
    finally:
        out['threads_started']=len(active)
        try:
            out['pids_current_held']=maybe_int(stem/'pids.current')
            out['scope_pids_current_held']=maybe_int(scope/'pids.current')
            out['pids_events_held_delta']=event_max(stem)-before
            scope_held=event_max(scope)
            if scope_before is not None and scope_held is not None:
                out['scope_pids_events_delta']=scope_held-scope_before
        finally:
            ready.set()
            for t in active: t.join(timeout=2.5)
    out['pids_events_delta']=event_max(stem)-before
except Exception as e:
    out['error'] = type(e).__name__
    out.setdefault('error_detail', str(e)[:240])
print(json.dumps(out,sort_keys=True))
'''


def _result() -> dict:
    return {'schema_version': '0.1', 'checks': {c: 'NOT_VERIFIED' for c in CHECKS},
            'diagnostics': {}}


def _run(args: list[str], *, timeout: int = 25, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=False, env=env)


def _pid_enforcement_evidence(child: dict) -> bool:
    """Require a rejected thread AND observed kernel task-limit evidence.

    pids.events may be local-only with the cgroup2 pids_localevents mount option.
    Accept an event in the child scope if its own pids.max is unbounded or
    strictly higher than the parent's 32-task cap, with the parent at that cap.
    """
    if child.get('thread_denied') is not True:
        return False
    started = child.get('threads_started')
    held = child.get('pids_current_held')
    if type(started) is not int or not 0 <= started < 64:
        return False
    if type(held) is not int or not PIDS - 1 <= held <= PIDS:
        return False
    parent_events = child.get('pids_events_delta')
    if type(parent_events) is int and parent_events > 0:
        return True
    # A systemd-created scope may have its own generous (numeric) TasksMax.
    # That limit cannot explain a denial at the 32-task *parent* ceiling when
    # the scope's limit is strictly larger than the parent's observed ceiling.
    scope_max = child.get('scope_pids_max')
    if scope_max != 'max':
        if (type(scope_max) is not str or len(scope_max) > 12 or
                not scope_max.isascii() or not scope_max.isdecimal() or
                int(scope_max) <= PIDS):
            return False
    scope_events = child.get('scope_pids_events_delta')
    return type(scope_events) is int and scope_events > 0


def main() -> int:
    r = _result()
    diag = r['diagnostics']
    try:
        uid = os.getuid()
        if uid == 0 or os.geteuid() != uid or not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
            raise ValueError('Invalid probe identity/cgroup mode')
        user_env = dict(os.environ)
        user_env['XDG_RUNTIME_DIR'] = f'/run/user/{uid}'
        user_env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path=/run/user/{uid}/bus'
        # SSH readiness is earlier than cloud-init runcmd completion. The
        # root-owned marker is emitted only after both managers are reloaded.
        marker = Path('/run/projectiq-cgroup-bootstrap.ready')
        for _ in range(45):
            if marker.is_file():
                break
            time.sleep(1)
        diag['bootstrap_state'] = 'READY' if marker.is_file() else 'NOT_READY'
        if not marker.is_file():
            print(json.dumps(r, sort_keys=True)); return 0
        ready = False
        for _ in range(25):
            check = _run(['/usr/bin/systemctl', '--user', 'show', '-P',
                          'ControlGroup', 'projectiq-probe.slice'], timeout=5,
                         env=user_env)
            if check.returncode == 0:
                ready = True
                break
            time.sleep(1)
        r['checks']['user_manager_reachable'] = 'PASS' if ready else 'NOT_VERIFIED'
        if not ready:
            diag['manager_error'] = check.stderr.decode('utf-8','replace')[-240:]
            print(json.dumps(r,sort_keys=True)); return 0
        start = _run(['/usr/bin/systemctl', '--user', 'start', SLICE], timeout=15, env=user_env)
        if start.returncode:
            diag['slice_start_error'] = start.stderr.decode('utf-8','replace')[-240:]
            print(json.dumps(r,sort_keys=True)); return 0
        # Inspect the manager's *effective* properties, not just unit files.
        # These are read-only and never grant the app user cgroup write access.
        for label, unit, user in (
                ('system_user_service', f'user@{uid}.service', False),
                ('system_user_slice', 'user.slice', False),
                ('system_uid_slice', f'user-{uid}.slice', False),
                ('user_probe_slice', SLICE, True)):
            cmd = ['/usr/bin/systemctl']
            if user:
                cmd.append('--user')
            cmd.extend(['show', unit, '-p', 'IOAccounting', '-p',
                        'Delegate', '-p', 'DelegateControllers', '-p',
                        'DisableControllers', '-p', 'DropInPaths'])
            observed = _run(cmd, timeout=8, env=user_env if user else None)
            raw = observed.stdout if observed.returncode == 0 else observed.stderr
            diag[label] = ('rc=' + str(observed.returncode) + '; ' +
                           raw.decode('utf-8', 'replace')[-280:])[:320]
        io_view = _run(['/usr/bin/systemctl', '--user', 'show', SLICE,
                        '-p', 'IOWriteBandwidthMax', '-p', 'IOAccounting',
                        '-p', 'ControlGroup'], timeout=8, env=user_env)
        diag['user_probe_io_policy'] = (
            'rc=' + str(io_view.returncode) + '; ' +
            (io_view.stdout if io_view.returncode == 0 else io_view.stderr)
            .decode('utf-8', 'replace')[-320:]
        )[:350]
        # --scope keeps the command under a systemd-managed scope, in the
        # projectiq-probe.slice parent. sudo can ONLY switch to projectiq_run.
        command = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet',
                   '--slice=' + SLICE, '--unit=piq-b3b-observe',
                   '/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--',
                   '/usr/bin/python3', '-I', '-c', CHILD]
        trial = _run(command, timeout=40, env=user_env)
        diag['scope_exit_code'] = trial.returncode
        if trial.returncode != 0:
            diag['scope_error'] = trial.stderr.decode('utf-8','replace')[-350:]
            print(json.dumps(r,sort_keys=True)); return 0
        if len(trial.stdout) > 4096:
            raise ValueError('Oversized scoped observation')
        child = json.loads(trial.stdout)
        if not isinstance(child, dict):
            raise ValueError('Unexpected scoped result')
        diag['app_cgroup_tail'] = str(child.get('membership',''))[-240:]
        if type(child.get('pids_events_delta')) is int:
            diag['pids_events_delta'] = child['pids_events_delta']
        for key in ('threads_started', 'pids_current_before',
                    'pids_current_held', 'scope_pids_current_held',
                    'pids_events_held_delta', 'scope_pids_events_delta'):
            if type(child.get(key)) is int:
                diag[key] = child[key]
        if type(child.get('thread_denied')) is bool:
            diag['thread_denied'] = child['thread_denied']
        for key in ('scope_pids_max', 'thread_error'):
            if type(child.get(key)) is str:
                diag[key] = child[key][:100]
        if (type(child.get('nproc_soft_hard')) is list and
                len(child['nproc_soft_hard']) == 2 and
                all(type(x) is int for x in child['nproc_soft_hard'])):
            diag['nproc_soft_hard'] = str(child['nproc_soft_hard'])[:60]
        group = str(child.get('membership', '')).split('/')
        r['checks']['app_process_inside_slice'] = ('PASS' if SLICE in group
                                                        and 'piq-b3b-observe.scope' in group
                                                        else 'FAIL')
        if 'error' in child:
            diag['child_error'] = str(child['error'])[:80]
            diag['child_error_detail'] = str(
                child.get('error_detail', '')
            )[:240]
            if type(child.get('manager_control_owner')) is str:
                diag['manager_control_owner'] = child['manager_control_owner'][:80]
            if type(child.get('manager_direct_procs')) is int:
                diag['manager_direct_procs'] = child['manager_direct_procs']
            print(json.dumps(r, sort_keys=True))
            return 0
        r['checks']['memory_max_kernel_value'] = ('PASS' if child.get('memory') == str(MEMORY)
                                                    else 'FAIL')
        r['checks']['pids_max_kernel_value'] = ('PASS' if child.get('pids') == str(PIDS)
                                                 else 'FAIL')
        expected = child.get('workspace_dev')
        rows = [x.split() for x in str(child.get('io','')).splitlines()]
        io_ok = any(len(x) >= 2 and x[0] == expected and
                    f'wbps={IO_BPS}' in x[1:] for x in rows)
        r['checks']['io_max_workspace_device'] = 'PASS' if io_ok else 'FAIL'
        r['checks']['app_cannot_modify_limits'] = ('PASS' if child.get('tamper_denied') is True
                                                   else 'FAIL')
        r['checks']['pids_limit_enforced'] = ('PASS' if _pid_enforcement_evidence(child)
                                               else 'FAIL')
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        diag['probe_error'] = type(exc).__name__
    print(json.dumps(r,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
