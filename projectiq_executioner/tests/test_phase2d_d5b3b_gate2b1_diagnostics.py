"""Offline regression coverage for Gate 2B.1 launch diagnostics and release race."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_memory_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_memory_acceptance as host


class DiagnosticsTests(unittest.TestCase):
    def test_worker_marker_wait_covers_sequential_verification_window(self):
        # Each of two workers gets up to 16 s verification; a 12 s marker
        # timeout could kill worker 1 before the controller reaches worker 2.
        self.assertIn('for _ in range(600):', guest.WORKER)
        self.assertIn('time.sleep(.1)', guest.WORKER)
        self.assertIn('until = time.monotonic() + 16',
                      Path(guest.__file__).read_text(encoding='utf-8'))

    def test_failure_classifier_does_not_leak_raw_process_output(self):
        standard = {'wrapper_exit_code': -1, 'scope_seen': False,
                    'app_uid_seen': False, 'cgroup_seen': False, 'argv_seen': False}
        self.assertEqual(guest._launch_failure_reason(standard), 'SCOPE_NOT_OBSERVED')
        for key, value, expected in (
            ('scope_seen', True, 'APP_UID_NOT_OBSERVED'),
            ('app_uid_seen', True, 'CGROUP_MEMBERSHIP_NOT_OBSERVED'),
            ('cgroup_seen', True, 'EXACT_COMMAND_NOT_OBSERVED'),
            ('argv_seen', True, 'LAUNCH_VERIFICATION_TIMEOUT'),
        ):
            standard[key] = value
            self.assertEqual(guest._launch_failure_reason(standard), expected)
        for code in (111, 112, 113, 114, 115, 116):
            self.assertEqual(guest._launch_failure_reason({**standard,
                'wrapper_exit_code': code}), f'WORKER_EXIT_{code}')
        self.assertEqual(guest._launch_failure_reason({**standard,
            'wrapper_exit_code': 1}), 'WRAPPER_EXITED')
        self.assertEqual(guest._launch_failure_reason({**standard,
            'wrapper_exit_code': -9}), 'WRAPPER_SIGNALLED')

    def test_scope_snapshot_classifies_independent_kernel_observations(self):
        original_path = Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cg = root / 'sys'
            scope = cg / 'user.slice' / 'user-1000.slice' / 'user@1000.service' / 'projectiq.slice' / guest.SLICE / 'piq-mem-a.scope'
            scope.mkdir(parents=True)
            (scope / 'cgroup.procs').write_text('700\n', encoding='ascii')
            proc = root / 'proc' / '700'
            proc.mkdir(parents=True)
            (proc / 'status').write_text('Name:\tpython3\nUid:\t1001\t1001\t1001\t1001\n', encoding='utf-8')
            expected_group = '/' + str(scope.relative_to(cg)).replace('\\', '/')
            (proc / 'cgroup').write_text('0::' + expected_group + '\n', encoding='ascii')
            argv = ['/usr/bin/python3', '-I', '-c', 'trusted-worker', '/home/projectiq/.release']
            (proc / 'cmdline').write_bytes(b'\0'.join(x.encode() for x in argv) + b'\0')
            with patch.object(guest, 'CGROUP', cg), patch.object(guest, 'Path',
                    side_effect=lambda arg: proc.parent if arg == '/proc' else original_path(arg)):
                ok = guest._scope_snapshot(scope, 1001, argv)
                self.assertTrue(ok['scope_seen'])
                self.assertEqual(ok['max_cgroup_procs'], 1)
                self.assertTrue(ok['app_uid_seen'])
                self.assertTrue(ok['cgroup_seen'])
                self.assertTrue(ok['argv_seen'])
                self.assertFalse(guest._scope_snapshot(scope, 1001, argv + ['unexpected'])['argv_seen'])
                self.assertFalse(guest._scope_snapshot(scope, 1002, argv)['app_uid_seen'])
                (proc / 'cgroup').write_text('0::/outside.scope\n', encoding='ascii')
                self.assertFalse(guest._scope_snapshot(scope, 1001, argv)['cgroup_seen'])

    def test_host_parser_accepts_typed_diagnostics_and_rejects_forgeries(self):
        checks = {name: 'NOT_VERIFIED' for name in guest.CHECKS}
        checks.update(trusted_bootstraps_complete='PASS', evaluation_slice_kernel_policy='PASS',
                      guest_ram_headroom='PASS', worker_scopes_cleaned='PASS')
        worker = {'worker': 1, 'scope_seen': True, 'max_cgroup_procs': 2,
                  'app_uid_seen': True, 'cgroup_seen': False,
                  'argv_seen': False, 'wrapper_exit_code': 114, 'verified': False}
        diagnostics = {'probe_stage': 'WORKER_LAUNCH_FAILED',
                       'launch_failure_reason': 'WORKER_EXIT_114',
                       'worker_launch_observations': [worker],
                       'scope_cleanup_observed': True,
                       'cooperative_stop_markers_published': 1,
                       'scope_cleanup_observations': [{
                            'worker':1,'kill_returncode':-2,'stop_returncode':-2,
                            'scope_state':'ABSENT','remaining_tasks':0,
                            'wrapper_exit_code_after':114,
                            'stop_marker_published':True,
                            'kill_failure_reason':'NOT_ATTEMPTED'}]}
        doc = {'schema_version': '0.1', 'checks': checks, 'diagnostics': diagnostics}
        self.assertIsNotNone(host._parse_guest(json.dumps(doc).encode()))
        malformed = [
            {'worker_launch_observations': [dict(worker, argv_seen='yes')]},
            {'worker_launch_observations': [dict(worker, worker=2)]},
            {'worker_launch_observations': [dict(worker, verified=True)]},
            {'worker_launch_observations': [dict(worker, wrapper_exit_code=1000)]},
            {'worker_launch_observations': [dict(worker, extra='raw dump')]},
            {'launch_failure_reason': '../path'},
            {'probe_stage': 'SUCCESS_UNVERIFIED'},
        ]
        for change in malformed:
            with self.subTest(change=change):
                self.assertIsNone(host._parse_guest(json.dumps({**doc,
                    'diagnostics': {**diagnostics, **change}}).encode()))
        self.assertIsNone(host._parse_guest(b'X' * 3501))

    def test_launch_exit_preserves_evidence_and_never_marks_oom_pass(self):
        class FailedProcess:
            def poll(self): return 114
            def wait(self, timeout=None): return 114
            def kill(self): raise AssertionError('Already exited')

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'memory.events').write_text('max 0\noom 0\noom_kill 0\n')
            (root / 'memory.peak').write_text('1024\n')
            checks = {name: 'NOT_VERIFIED' for name in guest.CHECKS}
            diag = {}
            with patch.object(guest.subprocess, 'Popen', return_value=FailedProcess()), \
                 patch.object(guest, '_scope_snapshot', return_value={
                     'scope_seen': True, 'max_cgroup_procs': 2, 'app_uid_seen': True,
                     'cgroup_seen': True, 'argv_seen': False}), \
                 patch.object(guest, '_run', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(guest, '_cleanup_scoped_workers', return_value=True), \
                 patch.object(guest.secrets, 'token_hex', return_value='0123456789abcdef'), \
                 patch.object(guest, 'CONTROLLER_HOME', root):
                guest._test_memory(root, 1000, 1001, {}, diag, checks)
            self.assertEqual(diag['launch_failure_reason'], 'WORKER_EXIT_114')
            self.assertEqual(diag['probe_stage'], 'WORKER_LAUNCH_FAILED')
            self.assertEqual(diag['worker_launch_observations'][0]['wrapper_exit_code'], 114)
            self.assertEqual(checks['two_unprivileged_scoped_workers'], 'NOT_VERIFIED')
            self.assertEqual(checks['memory_events_cgroup_oom_kill'], 'NOT_VERIFIED')
            self.assertEqual(checks['worker_scopes_cleaned'], 'PASS')


if __name__ == '__main__':
    unittest.main()
