"""Controlled gate release must be atomic, controller owned and fail closed."""
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from projectiq_executioner.phase2.vm_worker import guest_agent as guest


class ReleaseFileTests(TestCase):
    def test_no_sigstop_remains_in_launcher(self):
        self.assertNotIn('SIGSTOP', guest.APP_LIMIT_LAUNCHER)
        self.assertIn('PROJECTIQ_ATTEST_GATE', guest.APP_LIMIT_LAUNCHER)
        self.assertIn('os.lstat(_gate)', guest.APP_LIMIT_LAUNCHER)

    def test_controller_atomic_release_and_preexisting_denial(self):
        if sys.platform != 'linux':
            self.skipTest('POSIX file ownership and mode checks only')
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o711)
            gate = parent / ('.projectiq-build-release-' + 'a'*32)
            old_mask = os.umask(0o077)
            try:
                guest._publish_build_release(gate)
            finally:
                os.umask(old_mask)
            self.assertEqual(gate.read_bytes(), b'GO\n')
            self.assertTrue(gate.stat().st_mode & stat.S_IROTH)
            self.assertEqual(gate.stat().st_uid, os.getuid())
            self.assertTrue(stat.S_ISREG(gate.stat().st_mode))
            self.assertFalse(gate.stat().st_mode & 0o022)
            with self.assertRaisesRegex(guest.GuestFailure, 'APP_CGROUP_BUILD_GATE_RELEASE_FAILED'):
                guest._publish_build_release(gate)

    def test_world_writable_gate_directory_refused(self):
        if sys.platform != 'linux':
            self.skipTest('POSIX directory mode enforcement only')
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o777)
            gate = parent / ('.projectiq-build-release-' + 'a'*32)
            with self.assertRaisesRegex(guest.GuestFailure, 'APP_CGROUP_BUILD_GATE_RELEASE_FAILED'):
                guest._publish_build_release(gate)
            self.assertFalse(gate.exists())
