"""Trusted D5b.2 *workspace-only* quota proof; executed in a clean VM.

No student repo, package or executable is sent. This verifies only a mounted
size-bounded project workspace, NOT total guest/root QCOW2 or host disk usage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

WORK = Path('/srv/projectiq-workspace')

# Fixed trusted script executed AS projectiq_run. Fallocate forces allocation;
# sparse writes or truncate would NOT prove that the filesystem rejects writes.
APP_PROOF = '''import errno, os, sys
from pathlib import Path
# The clean-VM probe does not run guest_agent._stage_bounded_workspace(),
# so its normal application tmp/ directory does not exist yet. Allocate
# directly in the verified mounted workspace instead.
path = Path('/srv/projectiq-workspace/d5b2-quota-proof')
fd = None
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.posix_fallocate(fd, 0, int(sys.argv[1]) * 1048576 + 8 * 1048576)
        outcome = 'ALLOCATION_SUCCEEDED_UNEXPECTEDLY'
    except OSError as exc:
        outcome = ('ENOSPC' if exc.errno in (errno.ENOSPC, errno.EDQUOT)
                   else 'NOT_VERIFIED')
finally:
    if fd is not None: os.close(fd)
    try: path.unlink()
    except FileNotFoundError: pass
if outcome == 'ENOSPC':
    test = path.parent / 'd5b2-after-enospc'
    try:
        test.write_bytes(b'ok')
        recovered = test.read_bytes() == b'ok'
    finally:
        test.unlink(missing_ok=True)
else:
    recovered = False
print(outcome + ':' + ('RECOVERED' if recovered else 'NOT_RECOVERED'))
'''


def _mount_observation(mib: int) -> str:
    try:
        if not WORK.is_dir() or not os.path.ismount(WORK):
            return 'FAIL'
        with open('/proc/mounts', encoding='utf-8') as f:
            rows = [x.split() for x in f if len(x.split()) >= 4]
        found = [row for row in rows if row[1] == str(WORK)]
        if (len(found) != 1 or found[0][2] != 'ext4'
                or not {'rw', 'nosuid', 'nodev'}.issubset(set(found[0][3].split(',')))):
            return 'FAIL'
        info = os.statvfs(WORK)
        size = info.f_blocks * info.f_frsize
        return 'PASS' if 48 * 1048576 <= size <= mib * 1048576 else 'FAIL'
    except OSError:
        return 'NOT_VERIFIED'


def main() -> int:
    # Must be an integer supplied by the trusted controller, never a project.
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or not 64 <= int(sys.argv[1]) <= 2048:
        return 2
    mib = int(sys.argv[1])
    end = time.monotonic() + 90
    while not os.path.ismount(WORK) and time.monotonic() < end:
        time.sleep(1)
    checks = {'workspace_mount_bounded': _mount_observation(mib),
              'app_write_over_quota_denied': 'NOT_VERIFIED',
              'app_write_after_denial': 'NOT_VERIFIED'}
    # The clean VM runs only this fixed, trusted proof: diagnostics may retain a
    # small stderr excerpt. Never capture these bytes for student executions.
    diagnostics = {'app_probe_state': 'NOT_STARTED'}
    if checks['workspace_mount_bounded'] == 'PASS':
        try:
            p = subprocess.run(['sudo', '-n', '-u', 'projectiq_run',
                                '/usr/bin/python3', '-c', APP_PROOF, str(mib)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=35, check=False)
            value = p.stdout.decode('utf-8', errors='replace').strip()
            diagnostics['app_probe_exit_code'] = int(p.returncode)
            if p.returncode == 0:
                checks['app_write_over_quota_denied'] = ('PASS' if value.startswith('ENOSPC:')
                                                         else 'FAIL' if 'ALLOCATION_SUCCEEDED' in value
                                                         else 'NOT_VERIFIED')
                checks['app_write_after_denial'] = ('PASS' if value == 'ENOSPC:RECOVERED'
                                                    else 'FAIL')
                diagnostics['app_probe_state'] = ('PROOF_RESULT_RECORDED'
                                                  if checks['app_write_over_quota_denied'] != 'NOT_VERIFIED'
                                                  else 'UNRECOGNIZED_PROBE_OUTPUT')
            else:
                diagnostics['app_probe_state'] = 'PROCESS_EXITED_NONZERO'
            # Bounded, clean-VM-only outputs; no project files or secrets are present.
            diagnostics['app_probe_stdout_tail'] = value[-250:]
            diagnostics['app_probe_stderr_tail'] = p.stderr.decode(
                'utf-8', errors='replace')[-500:]
        except subprocess.TimeoutExpired:
            diagnostics['app_probe_state'] = 'PROCESS_TIMED_OUT'
        except OSError as exc:
            diagnostics['app_probe_state'] = 'PROCESS_LAUNCH_OSERROR'
            diagnostics['app_probe_oserror_type'] = type(exc).__name__
    print(json.dumps({'schema_version': '0.1', 'checks': checks, 'workspace_mib': mib,
                      'probe_diagnostics': diagnostics}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
