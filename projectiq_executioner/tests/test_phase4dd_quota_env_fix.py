"""4D-D: guest trusted-HOME path handling; no Chrome or VM required."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from projectiq_executioner.phase4 import guest_browser_login_cdp as helper


class FakePath:
    """An in-memory trusted-workspace path; never reads or writes host files."""
    def __init__(self, value):
        self.value = str(value)
    def is_dir(self):
        return self.value.startswith('/srv/projectiq-workspace/')
    def resolve(self):
        return self
    def is_relative_to(self, parent):
        return self.value.startswith(str(parent).rstrip('/') + '/')
    def __truediv__(self, other):
        return type(self)(self.value.rstrip('/') + '/' + str(other))
    def __str__(self):
        return self.value


class TrustedHomeGate(unittest.TestCase):
    def run_main(self, environ, *, uid=1000):
        fake_pipe = types.ModuleType('guest_browser_cdp')
        fake_pipe._start_chrome = Mock(side_effect=RuntimeError('stub: Chrome must not start'))
        # All path and process side effects are mocked. No Chrome and no VM.
        with (patch.dict(os.environ, environ, clear=True),
              patch.object(helper.os, 'geteuid', return_value=uid, create=True),
              patch.object(helper.sys, 'platform', 'linux'),
              patch.object(helper.sys, 'argv', ['guest_browser_login_cdp.py']),
              patch.object(helper, 'Path', FakePath),
              patch.object(helper, '_load_trusted_cdp_module', return_value=fake_pipe)):
            result = helper.main()
        return result, fake_pipe._start_chrome

    def test_home_from_trusted_launcher_reaches_cdp(self):
        code, start = self.run_main({
            'HOME': '/srv/projectiq-workspace/checked-run',
            'PROJECTIQ_BROWSER_PROFILE': '1'})
        # 119 is our mocked runtime exception, proving we reached Chrome launch.
        self.assertEqual(code, 119)
        start.assert_called_once()

    def test_old_unforwarded_variable_not_required(self):
        code, start = self.run_main({
            'HOME': '/srv/projectiq-workspace/checked-run',
            'PROJECTIQ_QUOTA_HOME': '/tmp/incorrect-value',
            'PROJECTIQ_BROWSER_PROFILE': '1'})
        self.assertEqual(code, 119)
        start.assert_called_once()

    def test_missing_home_denied(self):
        code, start = self.run_main({'PROJECTIQ_BROWSER_PROFILE': '1'})
        self.assertEqual(code, 118)
        start.assert_not_called()

    def test_old_variable_cannot_substitute_for_home(self):
        code, start = self.run_main({
            'PROJECTIQ_QUOTA_HOME': '/srv/projectiq-workspace/checked-run',
            'PROJECTIQ_BROWSER_PROFILE': '1'})
        self.assertEqual(code, 118)
        start.assert_not_called()

    def test_outside_workspace_denied(self):
        code, start = self.run_main({
            'HOME': '/tmp/bad-path',
            'PROJECTIQ_BROWSER_PROFILE': '1'})
        self.assertEqual(code, 118)
        start.assert_not_called()

    def test_profile_still_required(self):
        code, start = self.run_main({'HOME': '/srv/projectiq-workspace/checked-run'})
        self.assertEqual(code, 118)
        start.assert_not_called()

    def test_root_still_denied(self):
        code, start = self.run_main({
            'HOME': '/srv/projectiq-workspace/checked-run',
            'PROJECTIQ_BROWSER_PROFILE': '1'}, uid=0)
        self.assertEqual(code, 118)
        start.assert_not_called()

    def test_no_cdp_profile_or_path_policy_changes(self):
        source = Path(helper.__file__).read_text('utf-8')
        self.assertIn("quota_text = os.environ.get('HOME', '')", source)
        self.assertIn("not quota.resolve().is_relative_to(Path('/srv/projectiq-workspace'))", source)
        self.assertIn("os.environ.get('PROJECTIQ_BROWSER_PROFILE') != '1'", source)
        self.assertIn("pipe._start_chrome(profile)", source)


if __name__ == '__main__':
    unittest.main()
