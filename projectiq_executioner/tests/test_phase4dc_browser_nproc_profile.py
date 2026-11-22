"""Phase 4D-C: browser-only bounded RLIMIT_NPROC regression checks.

No VM, network, browser launch or student code is executed.
"""
from __future__ import annotations
import unittest
from unittest.mock import patch
from projectiq_executioner.phase2.vm_worker import guest_agent


class BrowserOnlyNprocPolicy(unittest.TestCase):
    def setUp(self):
        self.chrome = ['/usr/bin/google-chrome-stable', '--headless=new',
                       '--disable-gpu', '--dump-dom',
                       'http://127.0.0.1:8765/index.html']

    def test_only_reviewed_chrome_can_use_browser_profile(self):
        with patch.object(guest_agent, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest_agent.os, 'getuid', return_value=1001, create=True):
            good = guest_agent._project_command(self.chrome, browser_profile=True)
            self.assertIn('PROJECTIQ_BROWSER_PROFILE=1', good)
            self.assertIn('--slice=projectiq-eval.slice', good)
            for bad in (
                ['/usr/bin/python3', '--headless=new', '--dump-dom', self.chrome[-1]],
                self.chrome[:-1] + ['https://example.org'],
                self.chrome[:-1] + ['--no-sandbox', self.chrome[-1]],
            ):
                with self.subTest(bad=bad), self.assertRaises(guest_agent.GuestFailure):
                    guest_agent._project_command(bad, browser_profile=True)

    def test_browser_profile_requires_enforced_cgroup(self):
        with patch.object(guest_agent, '_APP_CGROUP_ACTIVE', False), \
             patch.object(guest_agent.os, 'getuid', return_value=1001, create=True):
            with self.assertRaises(guest_agent.GuestFailure):
                guest_agent._project_command(self.chrome, browser_profile=True)

    def test_unrelated_apps_cannot_get_browser_cap(self):
        with patch.object(guest_agent, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest_agent.os, 'getuid', return_value=1001, create=True):
            ordinary = guest_agent._project_command(['/usr/bin/node', 'app.js'])
            self.assertNotIn('PROJECTIQ_BROWSER_PROFILE=1', ordinary)

    def test_launcher_hard_caps_remain_bounded(self):
        launcher = guest_agent.APP_LIMIT_LAUNCHER
        self.assertIn('192 if _browser_requested else 96', launcher)
        self.assertIn('resource.RLIMIT_NOFILE, 512', launcher)
        self.assertIn('resource.RLIMIT_CORE, 0', launcher)
        self.assertIn("sys.exit(114)", launcher)
        self.assertIn("'/usr/bin/google-chrome-stable'", launcher)
        self.assertIn("x == '--no-sandbox'", launcher)
        self.assertIn('projectiq-eval.slice', launcher)
        self.assertEqual(guest_agent.APP_CGROUP_PIDS, 128)
        self.assertEqual(guest_agent.APP_CGROUP_MEMORY_BYTES, 2147483648)

    def test_browser_launcher_rejection_remains_sanitized(self):
        from projectiq_executioner.phase4.guest_browser_ui import _browser_exit_issue
        self.assertEqual(_browser_exit_issue(114, b'private argument'),
                         'BROWSER_APP_LAUNCHER_REJECTED')

if __name__ == '__main__':
    unittest.main()
