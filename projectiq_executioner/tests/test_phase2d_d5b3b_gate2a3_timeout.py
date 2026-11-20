"""Gate 2A.3 hotfix: preserve fail-closed behavior and distinguish timeouts."""
from __future__ import annotations
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from projectiq_executioner.phase2.vm_worker import guest_agent as guest


class TimeoutDiagnosticTests(unittest.TestCase):
    def tearDown(self):
        guest._APP_BUILD_HANDOFFS.clear()

    def _base(self, child, *, run_result=None, run_side_effect=None):
        raise AssertionError('use mocks in each test')

    def test_gate_release_error_is_classified_and_stops_scope(self):
        child = SimpleNamespace(returncode=None, poll=lambda: None,
                                kill=lambda: None, communicate=lambda **kw: (b'', b''))
        marker = 'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef', marker]), \
             patch.object(guest, '_gated_build_pid', return_value=1234), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_publish_build_release', side_effect=guest.GuestFailure('APP_CGROUP_BUILD_GATE_RELEASE_FAILED')), \
             patch.object(guest, '_stop_build_scope') as stop:
            with self.assertRaisesRegex(guest.GuestFailure, '^APP_CGROUP_BUILD_GATE_RELEASE_FAILED$'):
                guest._audited_build_process(['/bin/true'], env={}, cwd=Path('/tmp'),
                                             input=None, timeout=20, kind='npm_ci')
        stop.assert_called_once()
        self.assertFalse(guest._APP_BUILD_HANDOFFS)

    def test_exec_timeout_is_classified_and_stops_scope(self):
        child = SimpleNamespace(returncode=None, poll=lambda: None,
                                kill=lambda: None,
                                communicate=lambda **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired('build',20)))
        marker = 'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef', marker]), \
             patch.object(guest, '_gated_build_pid', return_value=1234), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_publish_build_release'), \
             patch.object(guest, '_stop_build_scope') as stop:
            with self.assertRaisesRegex(guest.GuestFailure, '^APP_CGROUP_BUILD_EXEC_TIMEOUT$'):
                guest._audited_build_process(['/bin/true'], env={}, cwd=Path('/tmp'),
                                             input=None, timeout=20, kind='vite_build')
        stop.assert_called_once()
        self.assertFalse(guest._APP_BUILD_HANDOFFS)

    def test_other_trusted_timeout_keeps_distinct_code_in_evaluation(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.subprocess, 'run', side_effect=subprocess.TimeoutExpired('untrusted', 2)):
            with self.assertRaisesRegex(guest.GuestFailure, '^APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT$'):
                guest._trusted_process(['/usr/bin/python3','--version'], timeout=2)

    def test_preparation_vm_preserves_previous_error_contract(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', False), \
             patch.object(guest.subprocess, 'run', side_effect=subprocess.TimeoutExpired('tool', 2)):
            with self.assertRaisesRegex(guest.GuestFailure, '^Trusted guest tool missing or timed out$'):
                guest._trusted_process(['/usr/bin/python3','--version'], timeout=2)

    def test_failed_gate_release_does_not_mark_verified(self):
        child = SimpleNamespace(returncode=None, poll=lambda: None, kill=lambda: None,
                                communicate=lambda **kw: (b'', b''))
        marker = 'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef', marker]), \
             patch.object(guest, '_gated_build_pid', return_value=1234), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_publish_build_release', side_effect=OSError('denied')), \
             patch.object(guest, '_stop_build_scope'):
            with self.assertRaisesRegex(guest.GuestFailure, '^APP_CGROUP_BUILD_GATE_RELEASE_FAILED$'):
                guest._audited_build_process(['/bin/true'], env={}, cwd=None,
                                             input=None, timeout=20, kind='npm_ci')
        self.assertFalse(guest._APP_BUILD_HANDOFFS)

if __name__ == '__main__':
    unittest.main()
