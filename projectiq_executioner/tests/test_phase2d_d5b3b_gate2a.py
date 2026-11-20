"""D5/B3b Gate 2A offline tests; no VM or student code executed here."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import qemu, guest_agent as guest


class FakeISO:
    def __init__(self): self.parts = {}
    def new(self, **kwargs): pass
    def add_fp(self, fp, length, **kwargs): self.parts[kwargs['rr_name']] = fp.read()
    def write(self, path): Path(path).write_bytes(b'ISO')
    def close(self): pass


class Gate2ATests(unittest.TestCase):
    def test_seed_opt_in_and_mixed_php_allowed(self):
        iso = FakeISO()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {
                'pycdlib': types.SimpleNamespace(PyCdlib=lambda: iso)}):
            qemu.make_nocloud_iso(Path(tmp), 'ssh-ed25519 AAAATEST comment',
                                  'projectiq-b3b-gate2a', app_cgroup=True,
                                  mysql_bootstrap=True, workspace_disk_mib=96)
        data = iso.parts['user-data'].decode()
        self.assertIn('projectiq-eval-bootstrap', data)
        self.assertIn("permissions: '0700'", data)
        self.assertIn('ALL=(projectiq_run) NOPASSWD: ALL', data)
        self.assertLess(data.index('projectiq-workspace-bootstrap', data.index('runcmd:')),
                        data.index('projectiq-eval-bootstrap', data.index('runcmd:')))
        self.assertLess(data.index('projectiq-eval-bootstrap', data.index('runcmd:')),
                        data.index('projectiq-mysql-bootstrap', data.index('runcmd:')))
        for bad in (dict(app_cgroup=True),
                    dict(app_cgroup=True, workspace_disk_mib=96, template_setup=True),
                    dict(app_cgroup=True, workspace_disk_mib=96, cgroup_probe=True)):
            with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {
                    'pycdlib': types.SimpleNamespace(PyCdlib=FakeISO)}):
                with self.subTest(bad=bad), self.assertRaises(qemu.WorkerFailure):
                    qemu.make_nocloud_iso(Path(tmp), 'ssh-ed25519 AAAATEST comment',
                                          'projectiq-b3b-gate2a', **bad)

    def test_eval_policy_in_bootstrap_matches_guest(self):
        policy = qemu.APP_CGROUP_BOOTSTRAP_SCRIPT
        self.assertIn(f'MemoryMax={guest.APP_CGROUP_MEMORY_BYTES}', policy)
        self.assertIn(f'TasksMax={guest.APP_CGROUP_PIDS}', policy)
        self.assertIn(f'IOWriteBandwidthMax=/srv/projectiq-workspace {guest.APP_CGROUP_IO_BPS}', policy)
        self.assertIn('systemctl restart "user@${uid}.service"', policy)
        self.assertIn('Delegate=cpu cpuset io memory pids', policy)
        self.assertNotIn('NOPASSWD', policy)

    @patch.object(guest.os, 'major', return_value=8, create=True)
    @patch.object(guest.os, 'minor', return_value=1, create=True)
    def test_exact_kernel_files_are_required(self, *_device_helpers):
        # Linux-only os.major/os.minor do not exist on Windows. The mocked
        # device number still exercises the exact io.max matching predicate.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'sys'
            workspace = Path(td) / 'work'
            workspace.mkdir()
            parts = ['user.slice', 'user-1000.slice', 'user@1000.service',
                     'projectiq.slice', 'projectiq-eval.slice']
            node = root.joinpath(*parts)
            node.mkdir(parents=True)
            (node / 'memory.max').write_text(str(guest.APP_CGROUP_MEMORY_BYTES))
            (node / 'pids.max').write_text(str(guest.APP_CGROUP_PIDS))
            (node / 'io.max').write_text(f'8:1 wbps={guest.APP_CGROUP_IO_BPS}\n')
            group = '/' + '/'.join(parts)
            self.assertTrue(guest._evaluation_policy_matches(group, workspace, uid=1000, cgroup_root=root))
            self.assertFalse(guest._evaluation_policy_matches(group.replace('projectiq-eval', 'projectiq-probe'),
                                                              workspace, uid=1000, cgroup_root=root))
            (node / 'pids.max').write_text('max')
            self.assertFalse(guest._evaluation_policy_matches(group, workspace, uid=1000, cgroup_root=root))
            (node / 'pids.max').write_text(str(guest.APP_CGROUP_PIDS))
            (node / 'io.max').write_text('8:99 wbps=16777216\n')
            self.assertFalse(guest._evaluation_policy_matches(group, workspace, uid=1000, cgroup_root=root))

    def test_scoped_command_keeps_uid_drop_and_sanitized_environment(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.secrets, 'token_hex', return_value='aabbccdd00112233'), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True):
            cmd = guest._project_command(['/usr/bin/node', 'app.js'],
                {'NODE_OPTIONS':'--require /tmp/evil', 'PORT':'8765', 'PROJECTIQ_EVAL_REQUIRED':'0'})
        self.assertEqual(cmd[:3], ['/usr/bin/env', 'XDG_RUNTIME_DIR=/run/user/1000',
                                   'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus'])
        self.assertIn('/usr/bin/systemd-run', cmd)
        self.assertIn('--scope', cmd)
        self.assertIn('--slice=projectiq-eval.slice', cmd)
        self.assertIn('--unit=piq-app-aabbccdd00112233', cmd)
        self.assertEqual(cmd[cmd.index('/usr/bin/sudo'):cmd.index('/usr/bin/sudo')+5],
                         ['/usr/bin/sudo','-n','-u',guest.APP_USER,'--'])
        self.assertIn('PROJECTIQ_EVAL_REQUIRED=1', cmd)
        self.assertNotIn('PROJECTIQ_EVAL_REQUIRED=0', cmd)
        self.assertNotIn('NODE_OPTIONS=--require /tmp/evil', cmd)
        self.assertIn(guest.APP_LIMIT_LAUNCHER, cmd)
        self.assertEqual(cmd[-2:], ['/usr/bin/node', 'app.js'])
        self.assertIn('projectiq-eval.slice', guest.APP_LIMIT_LAUNCHER)
        self.assertIn('/proc/self/cgroup', guest.APP_LIMIT_LAUNCHER)

    def test_project_process_and_server_both_use_scoped_helper(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest.subprocess, 'run') as runner, \
             patch.object(guest.subprocess, 'Popen') as popen:
            runner.return_value = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            guest._trusted_process(['/usr/bin/python3','job.py'], project_code=True)
            guest._project_popen(['/usr/bin/php','-v'], cwd=Path('/tmp'), env={}, log=object())
        self.assertIn('--scope', runner.call_args.args[0])
        self.assertIn('--scope', popen.call_args.args[0])
        self.assertIn('start_new_session', popen.call_args.kwargs)
        with patch.object(guest, '_APP_CGROUP_ACTIVE', False):
            self.assertEqual(guest._project_command(['/usr/bin/node','--version'])[:5],
                             ['sudo','-n','-u',guest.APP_USER,'--'])

    def test_launcher_denies_unscoped_execution_without_activating_app(self):
        if sys.platform != 'linux':
            self.skipTest('Linux-only subprocess check')
        program = ['/usr/bin/python3', '-I', '-c', guest.APP_LIMIT_LAUNCHER,
                   '/usr/bin/python3', '-c', 'print("UNREACHABLE")']
        if os.geteuid() == 0:
            if not Path('/usr/sbin/runuser').exists():
                self.skipTest('Cannot drop root privilege on this host')
            program = ['/usr/sbin/runuser', '-u', 'nobody', '--', *program]
        env = dict(os.environ, PROJECTIQ_EVAL_REQUIRED='1')
        p = subprocess.run(program, capture_output=True, env=env, timeout=6, check=False)
        self.assertEqual(p.returncode, 112, p.stderr.decode('utf-8', 'replace'))
        self.assertNotIn(b'UNREACHABLE', p.stdout)


if __name__ == '__main__':
    unittest.main()
