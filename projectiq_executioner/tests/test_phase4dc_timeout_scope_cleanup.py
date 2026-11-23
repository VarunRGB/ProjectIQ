"""4D-C: browser timeout must stop its exact scoped descendants."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import guest_agent
from projectiq_executioner.phase4.guest_browser_ui import (
    _launch_exception_issue, _empty, _diagnostics, validate_browser_ui_report,
)

UNIT = 'piq-app-' + 'a' * 16
LAUNCH = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet',
          '--slice=projectiq-eval.slice', '--unit=' + UNIT,
          '/usr/bin/sudo', '-n', '-u', 'projectiq_run', '--', '/usr/bin/python3']
SRC = {'kind': 'github', 'repository': 'Example/FullStack', 'commit_sha': 'a' * 40}
BUNDLE = {
    'schema_version': '0.1', 'source': SRC, 'adapter': 'node_fullstack_offline_v1',
    'catalog_sha256': 'b' * 64, 'page_path': '/index.html',
    'browser_binary': '/usr/bin/google-chrome-stable',
    'checks': [{'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'}],
}

class BrowserScopeTimeoutTests(unittest.TestCase):
    def _failure(self, *, browser, output=None, stderr=None, launch=LAUNCH):
        err = subprocess.TimeoutExpired(launch, 55, output=output, stderr=stderr)
        with patch.object(guest_agent, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest_agent, '_project_command', return_value=launch), \
             patch.object(guest_agent.subprocess, 'run', side_effect=err), \
             patch.object(guest_agent, '_stop_build_scope') as stop, \
             patch.object(guest_agent.os, 'getuid', return_value=1001, create=True):
            with self.assertRaises(guest_agent.GuestFailure) as caught:
                guest_agent._trusted_process(['/usr/bin/google-chrome-stable', '--dump-dom'],
                    project_code=True, browser_profile=browser, timeout=55)
        return caught.exception, stop

    def test_timeout_stops_exact_browser_scope_and_preserves_bounded_stderr(self):
        err, stop = self._failure(browser=True, output=b'<html>ok</html>',
                                  stderr=b'PIQ4DC_LIMITS nproc=192/192 scope=4048 current=2\n'
                                         b'pthread_create EAGAIN' + b'x'*25000)
        stop.assert_called_once_with(UNIT, 1001)
        self.assertEqual(str(err), 'BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT')
        self.assertLessEqual(len(err.browser_stderr), 16384)
        self.assertNotIn(b'x'*20000, err.browser_stderr)
        self.assertEqual(_launch_exception_issue(err), 'BROWSER_COMMAND_TIMEOUT_WITH_OUTPUT')

    def test_no_stdout_remains_specific_timeout_and_stops_scope(self):
        err, stop = self._failure(browser=True, output=b'', stderr=None)
        stop.assert_called_once_with(UNIT, 1001)
        self.assertEqual(_launch_exception_issue(err), 'BROWSER_COMMAND_TIMEOUT')

    def test_unrelated_project_timeout_does_not_stop_browser_scope(self):
        err, stop = self._failure(browser=False, output=b'<html>ok</html>')
        stop.assert_not_called()
        self.assertEqual(str(err), 'APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT')

    def test_unexpected_scope_identifier_cannot_be_stopped(self):
        bad = [x for x in LAUNCH if not x.startswith('--unit=')] + ['--unit=other-scope']
        err, stop = self._failure(browser=True, output=b'', launch=bad)
        stop.assert_not_called()
        self.assertEqual(str(err), 'BROWSER_COMMAND_TIMEOUT')

    def test_timeout_stderr_is_sanitized_for_host_report(self):
        err, _ = self._failure(browser=True, output=b'<html>secret</html>',
                               stderr=b'PIQ4DC_LIMITS nproc=192/192 scope=4048 current=2\n'
                                      b'pthread_create: Resource temporarily unavailable'
                                      b'private=SECRET_123')
        sample = type('ProcessPreview', (), {'returncode': None,
                    'stderr': err.browser_stderr})()
        blank = {n: {'max': None, 'current': None, 'peak': None,
                     'events': None, 'events_local': None}
                 for n in ('user_slice', 'user_manager', 'projectiq', 'evaluation')}
        diag = _diagnostics(sample, blank, blank)
        self.assertEqual(diag['launcher']['nproc_hard'], 192)
        self.assertIn('PTHREAD_CREATE', diag['stderr_flags'])
        self.assertIn('EAGAIN', diag['stderr_flags'])
        r = _empty(BUNDLE, issue=_launch_exception_issue(err))
        r['diagnostics'] = diag
        validate_browser_ui_report(BUNDLE, r)
        self.assertNotIn('SECRET_123', repr(r))

    def test_sandbox_and_resource_ceilings_unchanged(self):
        from projectiq_executioner.phase2.vm_worker import qemu
        from projectiq_executioner.phase4 import guest_browser_ui
        self.assertEqual(qemu.APP_CGROUP_PIDS, 128)
        self.assertEqual(guest_agent.APP_CGROUP_PIDS, 128)
        self.assertEqual(guest_agent.APP_CGROUP_MEMORY_BYTES, 2147483648)
        self.assertIn('(512 if _browser_extended else 192) if _browser_requested else 96',
                      guest_agent.APP_LIMIT_LAUNCHER)
        self.assertIn('not _browser_requested or not _browser_valid',
                      guest_agent.APP_LIMIT_LAUNCHER)
        source = __import__('inspect').getsource(guest_browser_ui.run_browser_ui)
        self.assertNotIn('--no-sandbox', [
            part.strip(" '\"") for part in source.split(',')])

if __name__ == '__main__':
    unittest.main()
