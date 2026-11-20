"""B3b Gate 2A.2: kernel-backed, trusted-controller live server evidence."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from projectiq_executioner.phase2.vm_worker import guest_agent as guest


class LivePlacementTests(unittest.TestCase):
    def _fixture(self, td, *, uid=1001, argv=None, pid_group=None, wrong_path=False):
        root = Path(td)
        cg = root / 'cgroup'
        ps = root / 'proc'
        unit = 'piq-app-0123456789abcdef'
        expected = ('/user.slice/user-1000.slice/user@1000.service/'
                    'projectiq.slice/projectiq-eval.slice/' + unit + '.scope')
        scope = cg.joinpath(*expected.strip('/').split('/'))
        scope.mkdir(parents=True)
        (scope / 'cgroup.procs').write_text('4321\n')
        task = ps / '4321'
        task.mkdir(parents=True)
        (task / 'status').write_text(f'Name:\tapp\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        (task / 'cgroup').write_text('0::' + (pid_group or expected) + '\n')
        cmd = argv if argv is not None else ['/usr/bin/node', '/app/server.js']
        (task / 'cmdline').write_bytes(b'\x00'.join(s.encode() for s in cmd) + b'\x00')
        return unit, cg, ps

    def test_real_uid_scope_and_exact_executed_program_required(self):
        with tempfile.TemporaryDirectory() as td:
            unit, cg, ps = self._fixture(td)
            cmd = ['/usr/bin/node', '/app/server.js']
            evidence = lambda: guest._server_pid_in_scope(
                unit, cmd, controller_uid=1000, app_uid=1001,
                cgroup_root=cg, proc_root=ps)
            self.assertTrue(evidence())
            self.assertFalse(guest._server_pid_in_scope(unit, ['/usr/bin/php'],
                controller_uid=1000, app_uid=1001, cgroup_root=cg, proc_root=ps))
            self.assertFalse(guest._server_pid_in_scope('piq-app-../etc', cmd,
                controller_uid=1000, app_uid=1001, cgroup_root=cg, proc_root=ps))
            (ps / '4321' / 'status').write_text('Uid:\t0\t0\t0\t0\n')
            self.assertFalse(evidence())

    def test_pid_in_sibling_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            unit, cg, ps = self._fixture(td, pid_group='/user.slice/other.scope')
            self.assertFalse(guest._server_pid_in_scope(unit,
                ['/usr/bin/node', '/app/server.js'], controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=ps))

    def test_absent_or_dead_process_never_supplies_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            unit, cg, ps = self._fixture(td)
            (ps / '4321' / 'cmdline').unlink()
            self.assertFalse(guest._server_pid_in_scope(unit,
                ['/usr/bin/node', '/app/server.js'], controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=ps))

    def test_trusted_launcher_records_only_successful_kernel_observation(self):
        guest._APP_SERVER_PLACEMENTS.clear()
        launch = ['/usr/bin/systemd-run','--unit=piq-app-0123456789abcdef']
        cmd = ['/usr/bin/node','/app/server.js']
        proc = SimpleNamespace(poll=lambda: None)
        with patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda n: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_server_pid_in_scope', return_value=True) as observe:
            guest._verify_live_server_process(launch, cmd, proc)
        self.assertEqual(len(guest._APP_SERVER_PLACEMENTS), 1)
        observe.assert_called_once()
        self.assertEqual(guest._APP_SERVER_PLACEMENTS[0], 'piq-app-0123456789abcdef')
        guest._APP_SERVER_PLACEMENTS.clear()

    def test_server_popen_is_audited_only_inside_evaluation_vm(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef']), \
             patch.object(guest, '_verify_live_server_process') as audit, \
             patch.object(guest.subprocess, 'Popen', return_value=SimpleNamespace()) as spawn:
            guest._project_popen(['/usr/bin/php'], cwd=Path('/tmp'), env={}, log=object())
        audit.assert_called_once()
        self.assertEqual(spawn.call_args.kwargs['stdin'], guest.subprocess.DEVNULL)

    def test_guest_does_not_claim_builds_or_database(self):
        self.assertIn("'builds_verified': False", Path(guest.__file__).read_text('utf-8'))
        self.assertIn("'database_service_verified': False", Path(guest.__file__).read_text('utf-8'))

if __name__ == '__main__':
    unittest.main()
