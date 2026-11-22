"""4D-C: swallowed browser-launch exceptions become bounded, safe evidence."""
from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from projectiq_executioner.phase4.guest_browser_ui import (
    _launch_exception_issue, run_browser_ui, validate_browser_ui_report,
)

SRC = {'kind': 'github', 'repository': 'Example/FullStack', 'commit_sha': 'a' * 40}
BUNDLE = {
    'schema_version': '0.1', 'stage': 'guest_browser_ui',
    'source': SRC, 'adapter': 'node_fullstack_offline_v1',
    'catalog_sha256': 'b' * 64, 'page_path': '/index.html',
    'browser_binary': '/usr/bin/google-chrome-stable',
    'checks': [{'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'}],
}
# Guest bundles intentionally do not have a stage field.
BUNDLE.pop('stage')


class LaunchExceptionEvidence(unittest.TestCase):
    def run_failing_launch(self, exception):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            home = workspace / 'home'
            home.mkdir()
            original_file = Path.is_file
            original_resolve = Path.resolve

            def is_file(path):
                return path.as_posix() == '/usr/bin/google-chrome-stable' or original_file(path)

            def resolve(path, *args, **kwargs):
                if path.as_posix() == '/usr/bin/google-chrome-stable':
                    return PurePosixPath('/opt/google/chrome/chrome')
                return original_resolve(path, *args, **kwargs)

            snapshots = [{name: {'max': 128 if name == 'evaluation' else 'max',
                                 'current': 12, 'peak': 24, 'events': 0,
                                 'events_local': None}
                          for name in ('user_slice','user_manager','projectiq','evaluation')}
                         for _ in range(2)]
            snapshots[1]['evaluation']['peak'] = 128
            calls = []

            def raise_failure(command, **kwargs):
                calls.append((command, kwargs))
                raise exception

            with patch.object(Path, 'is_file', is_file), \
                 patch.object(Path, 'resolve', resolve), \
                 patch('projectiq_executioner.phase4.guest_browser_ui.os.access', return_value=True), \
                 patch('projectiq_executioner.phase4.guest_browser_ui._pid_snapshot', side_effect=snapshots), \
                 patch('projectiq_executioner.phase4.guest_browser_ui._pid_limit_events', return_value=0), \
                 patch.dict(os.environ, {'PROJECTIQ_QUOTA_HOME': str(home)}):
                report = run_browser_ui(BUNDLE, expected_source=SRC,
                                        expected_adapter='node_fullstack_offline_v1',
                                        workspace=workspace, trusted_process=raise_failure)
            validate_browser_ui_report(BUNDLE, report)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0][1]['project_code'])
            self.assertTrue(calls[0][1]['browser_profile'])
            self.assertEqual(calls[0][1]['timeout'], 55)
            self.assertNotIn('--no-sandbox', calls[0][0])
            self.assertEqual(report['status'], 'INCONCLUSIVE')
            self.assertIsNone(report['checks'][0]['matched'])
            self.assertEqual(report['diagnostics']['cgroups']['evaluation']['after']['max'],128)
            self.assertEqual(report['diagnostics']['cgroups']['evaluation']['after']['peak'],128)
            self.assertIsNone(report['diagnostics']['exit_code'])
            self.assertEqual(report['diagnostics']['stderr_flags'], [])
            return report

    def test_timeout_becomes_specific_issue_with_kernel_snapshot(self):
        r = self.run_failing_launch(RuntimeError('APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT'))
        self.assertEqual(r['issue_code'], 'BROWSER_COMMAND_TIMEOUT')

    def test_known_profile_rejection_is_distinct(self):
        r = self.run_failing_launch(RuntimeError('BROWSER_PROFILE_REQUIRES_REVIEWED_CHROME_COMMAND'))
        self.assertEqual(r['issue_code'], 'BROWSER_LAUNCH_POLICY_REJECTED')

    def test_eagain_is_not_confused_with_timeout(self):
        r = self.run_failing_launch(OSError(errno.EAGAIN, 'secret=CANARY'))
        self.assertEqual(r['issue_code'], 'BROWSER_LAUNCH_EAGAIN')
        self.assertNotIn('CANARY', repr(r))

    def test_file_and_permission_failures_are_safely_separated(self):
        self.assertEqual(_launch_exception_issue(FileNotFoundError(errno.ENOENT, 'private')),
                         'BROWSER_LAUNCH_BINARY_UNAVAILABLE')
        self.assertEqual(_launch_exception_issue(PermissionError(errno.EACCES, 'private')),
                         'BROWSER_LAUNCH_PERMISSION_DENIED')

    def test_unknown_internal_exception_never_leaks_secret(self):
        r = self.run_failing_launch(RuntimeError('secrets-from-student=CANARY'))
        self.assertEqual(r['issue_code'], 'BROWSER_PROCESS_UNAVAILABLE')
        self.assertNotIn('CANARY', repr(r))
        self.assertEqual(_launch_exception_issue(ValueError('secret=CANARY')),
                         'BROWSER_PROCESS_UNAVAILABLE')

    def test_launch_exceptions_are_not_reported_as_pass(self):
        report = self.run_failing_launch(RuntimeError('APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT'))
        report['status'] = 'PASS'
        with self.assertRaises(Exception):
            validate_browser_ui_report(BUNDLE, report)


if __name__ == '__main__':
    unittest.main()
