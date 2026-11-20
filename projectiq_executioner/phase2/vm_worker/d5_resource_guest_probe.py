"""Trusted D5b.3a kernel resource-limit observation, clean VM ONLY.

Controller inserts exact launcher source from guest_agent into the marked
constant before transfer. No student repository, build or install is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

APP_LAUNCHER = '__PROJECTIQ_LAUNCHER_EXPR__'

APP_OBSERVE = r'''import json,os,resource
limits={}
for name, key, expected in [('nproc',resource.RLIMIT_NPROC,96),
                            ('nofile',resource.RLIMIT_NOFILE,512),
                            ('core',resource.RLIMIT_CORE,0)]:
    soft,hard=resource.getrlimit(key)
    matches=soft==hard==expected
    attempt=expected+1
    try:
        resource.setrlimit(key,(attempt,attempt))
        immutable=False
    except (ValueError,PermissionError,OSError):
        immutable=True
    limits[name]={'bounded':matches,'immutable':immutable}
print(json.dumps({'uid':os.getuid(),'euid':os.geteuid(),'limits':limits}))
'''

CHECKS = ('app_identity_nonroot', 'app_nproc_hard_cap',
          'app_nofile_hard_cap', 'app_core_dumps_disabled',
          'app_cannot_raise_hard_caps')


def main() -> int:
    checks = {k: 'NOT_VERIFIED' for k in CHECKS}
    diagnostics = {'cgroup_v2_available': 'NOT_VERIFIED',
                   'memory_controller_available': 'NOT_VERIFIED',
                   'pids_controller_available': 'NOT_VERIFIED',
                   'io_controller_available': 'NOT_VERIFIED'}
    try:
        cgroups = Path('/sys/fs/cgroup/cgroup.controllers')
        if cgroups.is_file():
            controllers = set(cgroups.read_text(encoding='ascii').split())
            diagnostics['cgroup_v2_available'] = 'YES'
            for name in ('memory', 'pids', 'io'):
                diagnostics[name + '_controller_available'] = ('YES' if name in controllers else 'NO')
        else:
            diagnostics['cgroup_v2_available'] = 'NO'
    except OSError:
        pass
    try:
        import pwd
        target = pwd.getpwnam('projectiq_run').pw_uid
        controller = os.getuid()
        p = subprocess.run(['sudo', '-n', '-u', 'projectiq_run', '--',
                            '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin',
                            '/usr/bin/python3', '-I', '-c', APP_LAUNCHER,
                            '/usr/bin/python3', '-I', '-c', APP_OBSERVE],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=12, check=False)
        diagnostics['app_probe_exit_code'] = p.returncode
        if p.returncode == 0 and len(p.stdout) < 2048:
            payload = json.loads(p.stdout.decode('utf-8'))
            checks['app_identity_nonroot'] = ('PASS' if target not in (0, controller)
                                              and payload.get('uid') == target
                                              and payload.get('euid') == target else 'FAIL')
            for name, check in [('nproc', 'app_nproc_hard_cap'),
                                ('nofile', 'app_nofile_hard_cap'),
                                ('core', 'app_core_dumps_disabled')]:
                limits = payload['limits'][name]
                checks[check] = 'PASS' if limits['bounded'] is True else 'FAIL'
            checks['app_cannot_raise_hard_caps'] = ('PASS' if all(
                item['immutable'] is True for item in payload['limits'].values()) else 'FAIL')
        else:
            diagnostics['app_probe_status'] = 'NONZERO_OR_OVERSIZED'
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.TimeoutExpired):
        diagnostics['app_probe_status'] = 'EXCEPTION_OR_INVALID_OUTPUT'
    print(json.dumps({'schema_version': '0.1', 'checks': checks,
                      'diagnostics': diagnostics}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
