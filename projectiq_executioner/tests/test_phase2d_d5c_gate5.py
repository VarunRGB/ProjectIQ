"""D5c Gate 5: real OS locks, atomic registration, authenticated sweep retries.

No QEMU or student project is started. Windows-specific acceptance is separate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as registry
from projectiq_executioner.phase2.vm_worker import d5c_runtime_workspace as runtime


class ManagedRootConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = registry.initialize_root(self.home / 'managed')
        self.image = str(self.home / 'trusted-qemu.exe')

    def test_root_lock_persists_and_legacy_root_is_upgraded(self):
        self.assertEqual((self.root / registry.ROOT_LEASE).read_bytes(), b'1')
        (self.root / registry.ROOT_LEASE).unlink()  # Simulate a prior Gate 4 root.
        folder = registry.create_run(self.root)
        self.assertEqual((self.root / registry.ROOT_LEASE).read_bytes(), b'1')
        self.assertEqual(registry.recover_managed_root(self.root, self.image)[0]['status'], 'RECOVERED')
        self.assertFalse(folder.exists())

    def test_cross_process_root_lock_refuses_concurrent_mutation(self):
        code = (
            'import sys\n'
            'from pathlib import Path\n'
            'from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as r\n'
            'try:\n'
            ' with r.managed_root_lease(Path(sys.argv[1]), timeout_seconds=0): pass\n'
            'except r.RecoveryError as exc:\n'
            ' print(str(exc))\n'
            ' sys.exit(8)\n'
        )
        with registry.managed_root_lease(self.root):
            run = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                 capture_output=True, text=True, timeout=9)
            self.assertEqual(run.returncode, 8, run.stderr)
            self.assertIn('ACTIVE_OR_UNREADABLE_LEASE', run.stdout)
            self.assertFalse(any(x.name.startswith(registry.PREFIX) for x in self.root.iterdir()))
        folder = registry.create_run(self.root)
        self.assertTrue(folder.is_dir())
        self.assertEqual(registry.recover_managed_root(self.root, self.image)[0]['status'], 'RECOVERED')

    def test_atomic_create_and_live_run_lease_prevents_other_sweeps(self):
        folder, lease = registry.create_leased_run(self.root)
        try:
            self.assertEqual(registry._record(self.root, folder)['phase'], 'PREPARING')
            result = registry.recover_managed_root(self.root, self.image)
            self.assertEqual(result[0]['status'], 'SKIPPED_ACTIVE', result)
            self.assertTrue(folder.exists())
        finally:
            lease.__exit__(None, None, None)
        self.assertEqual(registry.recover_managed_root(self.root, self.image)[0]['status'], 'RECOVERED')

    def test_root_lease_tamper_blocks_without_deleting(self):
        folder = registry.create_run(self.root)
        (self.root / registry.ROOT_LEASE).write_bytes(b'0')
        with self.assertRaisesRegex(registry.RecoveryError, 'INVALID_LEASE'):
            registry.recover_managed_root(self.root, self.image)
        self.assertTrue(folder.exists())

    def test_runtime_cleanup_keeps_root_owned_while_unlocking_run(self):
        with patch.object(runtime, '_windows_runtime_supported', return_value=True):
            run = runtime.ManagedRuntimeRun({'managed_run_root': str(self.root),
                                             'qemu_binary': self.image})
            folder = run.open()
        original = registry._recover_managed_root_locked
        def verify_owned(*args, **kwargs):
            # Another OS process must not acquire the root lease during the
            # actual per-run unlock + delete window.
            code = (
                'import sys\nfrom pathlib import Path\n'
                'from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as r\n'
                'try:\n with r.managed_root_lease(Path(sys.argv[1]), timeout_seconds=0): pass\n'
                'except r.RecoveryError: sys.exit(7)\n'
            )
            probe = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                   capture_output=True, timeout=9)
            self.assertEqual(probe.returncode, 7, probe.stderr)
            return original(*args, **kwargs)
        with patch.object(registry, '_recover_managed_root_locked', side_effect=verify_owned):
            run.close()
        self.assertTrue(run.cleanup_verified)
        self.assertFalse(folder.exists())

    def test_runtime_open_uses_atomic_registration(self):
        with patch.object(runtime, '_windows_runtime_supported', return_value=True):
            run = runtime.ManagedRuntimeRun({'managed_run_root': str(self.root),
                                             'qemu_binary': self.image})
            with patch.object(registry, 'create_run', side_effect=AssertionError('UNLEASED_RUNTIME')):
                folder = run.open()
                with run.hold():
                    self.assertEqual(registry._record(self.root, folder)['phase'], 'PREPARING')
            self.assertTrue(run.cleanup_verified)
            self.assertFalse(folder.exists())


class JournalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = registry.initialize_root(self.home / 'managed')
        self.image = str(self.home / 'trusted-qemu.exe')

    def _interrupt_at_final_rmdir(self, *, launched=False):
        folder = registry.create_run(self.root)
        if launched:
            registry.update_run(self.root, folder, 'LAUNCHING')
            registry.update_run(self.root, folder, 'LAUNCHED', pid=12345,
                                born=8877112233, image=self.image)
            registry.update_run(self.root, folder, 'STOPPED', pid=12345,
                                born=8877112233, image=self.image)
        (folder / 'snapshot.zip').write_bytes(b'SNAPSHOT')
        original = Path.rmdir
        def injected(path, *args, **kwargs):
            if path == folder:
                raise PermissionError('synthetic locked directory')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'rmdir', injected):
            with patch.object(registry, 'windows_process_state', return_value='DEAD'):
                result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue(folder.exists())
        self.assertFalse((folder / registry.RECORD).exists())
        self.assertFalse((folder / registry.LEASE).exists())
        self.assertTrue((self.root / registry.ROOT_SWEEP).is_file())
        return folder

    def test_signed_journal_recovers_after_run_identity_files_deleted(self):
        folder = self._interrupt_at_final_rmdir()
        result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'RECOVERED')
        self.assertFalse(folder.exists())
        self.assertFalse((self.root / registry.ROOT_SWEEP).exists())
        self.assertEqual(registry.recover_managed_root(self.root, self.image), [])

    def test_journal_rechecks_process_identity_before_retry(self):
        folder = self._interrupt_at_final_rmdir(launched=True)
        with patch.object(registry, 'windows_process_state', return_value='ALIVE'):
            result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue(folder.exists())
        with patch.object(registry, 'windows_process_state', return_value='DEAD'):
            self.assertEqual(registry.recover_managed_root(self.root, self.image)[0]['status'], 'RECOVERED')

    def test_journal_tamper_is_rejected_without_deletion(self):
        folder = self._interrupt_at_final_rmdir()
        path = self.root / registry.ROOT_SWEEP
        record = json.loads(path.read_text('ascii'))
        record['id'] = registry.PREFIX + '0' * 20
        path.write_text(json.dumps(record), encoding='ascii')
        sibling = self.home / 'unrelated'
        sibling.write_bytes(b'KEEP')
        result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue(folder.exists())
        self.assertEqual(sibling.read_bytes(), b'KEEP')

    def test_stale_uncommitted_journal_temp_is_safe_to_discard(self):
        folder = registry.create_run(self.root)
        (self.root / registry.ROOT_SWEEP_TEMP).write_bytes(b'TORN')
        result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'RECOVERED')
        self.assertFalse(folder.exists())
        self.assertFalse((self.root / registry.ROOT_SWEEP_TEMP).exists())

    def test_unknown_file_in_partial_journal_blocks_cleanup(self):
        folder = self._interrupt_at_final_rmdir()
        (folder / 'unapproved.log').write_bytes(b'KEEP')
        result = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(result[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue((folder / 'unapproved.log').exists())


if __name__ == '__main__':
    unittest.main()
