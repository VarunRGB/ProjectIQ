"""Offline reliability regression for memory pressure release accessibility.

Never boots a VM, creates memory pressure, or touches a real controller home.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_memory_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_memory_acceptance as host


class ReleaseVisibilityTests(unittest.TestCase):
    def test_home_permissions_mirror_guest_agent_and_are_traversal_only(self):
        if not hasattr(os, 'getuid'):
            self.skipTest('POSIX-only filesystem ownership')
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / 'projectiq'
            home.mkdir(mode=0o700)
            home.chmod(0o700)
            diag = {}
            with patch.object(guest, 'CONTROLLER_HOME', home):
                guest._prepare_controller_home(os.getuid(), diag)
            self.assertEqual(diag['controller_home_mode_before'], 0o700)
            self.assertEqual(diag['controller_home_mode_after'], 0o711)
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o711)
            self.assertFalse(stat.S_IMODE(home.stat().st_mode) & 0o066)

    def test_home_rejects_wrong_owner_and_symlink(self):
        if not hasattr(os, 'getuid'):
            self.skipTest('POSIX-only filesystem ownership')
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / 'projectiq'
            home.mkdir(mode=0o700)
            home.chmod(0o700)
            with patch.object(guest, 'CONTROLLER_HOME', home):
                with self.assertRaises(ValueError):
                    guest._prepare_controller_home(os.getuid() + 1, {})
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
                home.chmod(0o722)
                with self.assertRaises(ValueError):
                    guest._prepare_controller_home(os.getuid(), {})
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o722)
            link = Path(td) / 'home-link'
            link.symlink_to(home, target_is_directory=True)
            with patch.object(guest, 'CONTROLLER_HOME', link):
                with self.assertRaises(ValueError):
                    guest._prepare_controller_home(os.getuid(), {})

    def test_visibility_uses_exact_app_uid_fixed_test_command(self):
        with patch.object(guest, '_run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(guest._app_can_read_release(Path('/home/projectiq/.fixed'), {}))
            self.assertEqual(run.call_args.args[0],
                ['/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--',
                 '/usr/bin/test', '-r', '/home/projectiq/.fixed'])
            self.assertEqual(run.call_args.kwargs['timeout'], 6)
        with patch.object(guest, '_run', return_value=SimpleNamespace(returncode=1)):
            self.assertFalse(guest._app_can_read_release(Path('/x'), {}))

    def test_visibility_converts_windows_guest_path_to_posix(self):
        # The probe is imported for tests on Windows but dispatched to Linux.
        # A WindowsPath must not leak backslashes into the Linux sudo command.
        with patch.object(guest, '_run', return_value=SimpleNamespace(returncode=0)) as run:
            marker = PureWindowsPath(r'\home\projectiq\.fixed')
            self.assertTrue(guest._app_can_read_release(marker, {}))
            self.assertEqual(run.call_args.args[0][-1], '/home/projectiq/.fixed')

    def test_guest_worker_reports_permissions_distinctly(self):
        self.assertIn('except PermissionError: sys.exit(117)', guest.WORKER)
        self.assertIn('for _ in range(600)', guest.WORKER)
        self.assertIn('release_markers_visible_to_app',
                      Path(guest.__file__).read_text(encoding='utf-8'))

    def test_report_parser_rejects_forged_release_and_non_oom_outcome(self):
        checks = {c: 'PASS' for c in guest.CHECKS}
        worker = {'worker': 1, 'scope_seen': True, 'max_cgroup_procs': 2,
                  'app_uid_seen': True, 'cgroup_seen': True, 'argv_seen': True,
                  'wrapper_exit_code': -1, 'verified': True}
        base = {'schema_version': '0.1', 'checks': checks,
                'diagnostics': {
                    'verified_worker_count': 2,
                    'memory_max_events_delta': 2,
                    'memory_oom_kill_delta': 1,
                    'memory_peak_bytes': guest.MEMORY_LIMIT,
                    'scope_cleanup_observed': True,
                     'cooperative_stop_markers_published': 2,
                    'scope_cleanup_observations': [
                        {'worker': i, 'kill_returncode': 0, 'stop_returncode': 0,
                         'scope_state': 'ABSENT', 'remaining_tasks': 0,
                         'wrapper_exit_code_after': -9,
                          'stop_marker_published': True,
                          'kill_failure_reason': 'NONE'} for i in (1, 2)],
                    'controller_home_mode_after': 0o711,
                    'release_markers_visible_to_app': True,
                    'stress_monitor_outcome': 'KERNEL_OOM_EVENT',
                    'wrapper_exit_codes_before_cleanup': [-1, 137],
                    'worker_launch_observations': [worker, dict(worker, worker=2)]}}
        self.assertIsNotNone(host._parse_guest(json.dumps(base).encode()))
        for name, value in (
            ('release_markers_visible_to_app', False),
            ('release_markers_visible_to_app', 'True'),
            ('release_markers_visible_to_app', None),
            ('controller_home_mode_after', 0o700),
            ('stress_monitor_outcome', 'ALL_WRAPPERS_EXITED'),
            ('stress_monitor_outcome', 'OOM_KILLED_BUT_UNOBSERVED'),
            ('wrapper_exit_codes_before_cleanup', [-1]),
            ('wrapper_exit_codes_before_cleanup', [-1, 999]),
            ('wrapper_exit_codes_before_cleanup', [True, 0]),
            ('wrapper_exit_codes_before_cleanup', None),
        ):
            with self.subTest(name=name, value=value):
                rec = {**base, 'diagnostics': {**base['diagnostics'], name: value}}
                self.assertIsNone(host._parse_guest(json.dumps(rec).encode()))

    def test_no_memory_pass_when_readability_check_denies_release(self):
        if not hasattr(os, 'getuid'):
            self.skipTest('POSIX-only filesystem ownership')
        class FakeProcess:
            alive = True
            def poll(self): return None if self.alive else 0
            def kill(self): self.alive = False
            def wait(self, timeout=None): self.alive = False; return 0
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'memory.events').write_text('max 0\noom 0\noom_kill 0\n')
            (root / 'memory.peak').write_text('4096\n')
            checks = {c: 'NOT_VERIFIED' for c in guest.CHECKS}
            diag = {}
            seq = iter(['0123456789abcdef', 'fedcba9876543210'])
            snapshot = {'scope_seen': True, 'max_cgroup_procs': 2,
                        'app_uid_seen': True, 'cgroup_seen': True, 'argv_seen': True}
            with patch.object(guest, 'CONTROLLER_HOME', root), \
                 patch.object(guest.subprocess, 'Popen', side_effect=lambda *a, **k: FakeProcess()), \
                 patch.object(guest, '_scope_snapshot', return_value=snapshot), \
                 patch.object(guest, '_run', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(guest, '_app_can_read_release', return_value=False), \
                 patch.object(guest, '_cleanup_scoped_workers', return_value=True), \
                 patch.object(guest.secrets, 'token_hex', side_effect=lambda _: next(seq)):
                guest._test_memory(root, os.getuid(), os.getuid() + 1, {}, diag, checks)
            self.assertEqual(diag['probe_stage'], 'RELEASE_VISIBILITY_FAILED')
            self.assertIs(diag['release_markers_visible_to_app'], False)
            self.assertEqual(checks['two_unprivileged_scoped_workers'], 'PASS')
            self.assertEqual(checks['memory_events_cgroup_oom_kill'], 'NOT_VERIFIED')
            self.assertEqual(checks['memory_peak_near_policy_limit'], 'NOT_VERIFIED')
            self.assertEqual(checks['worker_scopes_cleaned'], 'PASS')
            self.assertFalse(list(root.glob('.projectiq-mem-release-*')))


if __name__ == '__main__':
    unittest.main()
