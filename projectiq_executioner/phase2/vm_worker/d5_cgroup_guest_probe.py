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
CHILD = r'''import json,os,threading,time
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
    def event_max():
        for item in (stem/'pids.events').read_text().splitlines():
            k,_,v=item.partition(' ')
            if k=='max': return int(v)
        raise ValueError('missing pids events')
    before=event_max()
    try:
        with (stem/'memory.max').open('w') as f: f.write('max')
        out['tamper_denied']=False
    except PermissionError:
        out['tamper_denied']=True
    # Deliberately exceed a 32-task cgroup using at most 64 short-lived threads.
    active=[]
    ready=threading.Event()
    try:
        for i in range(64):
            t=threading.Thread(target=lambda: ready.wait(2.0), daemon=True)
            t.start(); active.append(t)
    except RuntimeError:
        out['thread_denied']=True
    finally:
        ready.set()
        for t in active: t.join(timeout=2.5)
    out['pids_events_delta']=event_max()-before
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
        # cloud-init may still be completing after SSH becomes reachable.
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
        group = str(child.get('membership', '')).split('/')
        r['checks']['app_process_inside_slice'] = ('PASS' if SLICE in group
                                                        and 'piq-b3b-observe.scope' in group
                                                        else 'FAIL')
        if 'error' in child:
            diag['child_error'] = str(child['error'])[:80]
            diag['child_error_detail'] = str(
                child.get('error_detail', '')
            )[:240]
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
        r['checks']['pids_limit_enforced'] = ('PASS' if child.get('thread_denied') is True
                                               and type(child.get('pids_events_delta')) is int
                                               and child['pids_events_delta'] > 0 else 'FAIL')
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        diag['probe_error'] = type(exc).__name__
    print(json.dumps(r,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
