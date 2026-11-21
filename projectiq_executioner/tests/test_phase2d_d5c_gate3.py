"""D5c Gate 3: strict registry + negative security evidence tests (offline)."""
from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as registry
from projectiq_executioner.phase2.vm_worker import d5c_orphan_acceptance as acceptance


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = registry.initialize_root(Path(self.tmp.name) / 'managed')
        self.image = str(Path(self.tmp.name) / 'qemu-system-x86_64.exe')

    def test_preparing_run_can_be_recovered(self):
        folder = registry.create_run(self.root)
        self.assertEqual(registry._record(self.root, folder)['phase'], 'PREPARING')
        results = registry.recover_managed_root(self.root, self.image)
        self.assertEqual(results[0]['status'], 'RECOVERED')
        self.assertFalse(folder.exists())
        self.assertEqual(registry.recover_managed_root(self.root, self.image), [])

    def test_live_controller_lock_cannot_be_swept(self):
        folder = registry.create_run(self.root)
        @contextmanager
        def busy_lease(_):
            raise registry.RecoveryError('ACTIVE_OR_UNREADABLE_LEASE')
            yield  # valid context manager, never reached
        # Windows file locks can behave differently when both handles are
        # owned by one Python process. Cross-process locking is live-tested.
        with patch.object(registry, 'exclusive_lease', busy_lease):
            result = registry.recover_managed_root(self.root, self.image)
            self.assertEqual(result[0]['status'], 'SKIPPED_ACTIVE')
            self.assertTrue(folder.is_dir())
        self.assertEqual(registry.recover_managed_root(self.root, self.image)[0]['status'], 'RECOVERED')

    def test_malformed_manifest_rejected_without_changes(self):
        folder = registry.create_run(self.root)
        original = folder / registry.RECORD
        original.write_text('{"malicious": true}', encoding='utf-8')
        self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertEqual(original.read_text(), '{"malicious": true}')

    def test_valid_manifest_signature_cannot_be_forged(self):
        folder = registry.create_run(self.root)
        data = json.loads((folder / 'run.json').read_text())
        data['phase'] = 'STOPPED'
        (folder / 'run.json').write_text(json.dumps(data))
        self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue(folder.exists())

    def test_unknown_files_are_left_untouched(self):
        folder = registry.create_run(self.root)
        extra = folder/'not-owned.txt'
        extra.write_text('unrelated')
        self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertEqual(extra.read_text(), 'unrelated')

    def test_symlink_to_unrelated_data_is_not_followed(self):
        folder = registry.create_run(self.root)
        outside = Path(self.tmp.name)/'private.txt'
        outside.write_text('PRIVATE')
        link = folder / 'seed.iso'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink privilege unavailable on Windows')
        self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertEqual(outside.read_text(), 'PRIVATE')

    def test_launch_identity_gap_requires_manual_review(self):
        folder = registry.create_run(self.root)
        registry.update_run(self.root,folder,'LAUNCHING')
        self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
        self.assertTrue(folder.exists())

    def _launched(self):
        folder = registry.create_run(self.root)
        registry.update_run(self.root,folder,'LAUNCHING')
        registry.update_run(self.root,folder,'LAUNCHED',pid=456,born=987654321,image=self.image)
        return folder

    def test_active_qemu_refuses_cleanup_even_with_unlocked_lease(self):
        folder = self._launched()
        with patch.object(registry,'windows_process_state',return_value='ALIVE') as call:
            self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'], 'MANUAL_REVIEW')
            call.assert_called_once_with(456,987654321,self.image)
        self.assertTrue(folder.exists())

    def test_unknown_or_reused_pid_refuses_cleanup(self):
        folder = self._launched()
        with patch.object(registry,'windows_process_state',return_value='UNKNOWN'):
            self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'],'MANUAL_REVIEW')
        self.assertTrue(folder.exists())

    def test_dead_qemu_recovers_and_is_idempotent(self):
        folder = self._launched()
        registry.update_run(self.root,folder,'BOOTED',pid=456,born=987654321,image=self.image)
        for name in ('seed.iso','run.qcow2','workspace.raw','qemu.log'):
            (folder/name).write_bytes(b'123')
        with patch.object(registry,'windows_process_state',return_value='DEAD'):
            self.assertEqual(registry.recover_managed_root(self.root,self.image)[0]['status'],'RECOVERED')
            self.assertEqual(registry.recover_managed_root(self.root,self.image),[])
        self.assertFalse(folder.exists())

    def test_wrong_image_even_with_dead_pid_is_rejected(self):
        folder = self._launched()
        with patch.object(registry,'windows_process_state',return_value='DEAD') as f:
            result=registry.recover_managed_root(self.root,self.image+'-different')
            self.assertEqual(result[0]['status'],'MANUAL_REVIEW')
            f.assert_not_called()
        self.assertTrue(folder.exists())

    def test_invalid_state_transitions_rejected(self):
        folder=registry.create_run(self.root)
        for phase in ('LAUNCHED','BOOTED','STOPPED','SWEEPING'):
            with self.subTest(phase=phase), self.assertRaises(registry.RecoveryError):
                registry.update_run(self.root,folder,phase,pid=1,born=1,image=self.image)
        self.assertEqual(registry._record(self.root,folder)['phase'],'PREPARING')

    def test_unrelated_sibling_and_malformed_prefix_not_deleted(self):
        decoy=self.root/'piq-d5c3-not-hex'
        decoy.mkdir()
        (decoy/'file').write_text('unrelated')
        other=self.root/'ordinary'
        other.mkdir()
        results=registry.recover_managed_root(self.root,self.image)
        self.assertEqual(results[0]['status'],'MANUAL_REVIEW')
        self.assertEqual((decoy/'file').read_text(),'unrelated')
        self.assertTrue(other.exists())

    def test_rejects_root_key_as_symlink(self):
        alt=Path(self.tmp.name)/'other'
        alt.mkdir()
        link=alt/'key'
        try:
            link.symlink_to(self.root / registry.ROOT_KEY)
        except OSError:
            self.skipTest('Symlink privilege unavailable on Windows')
        with patch.object(registry,'ROOT_KEY','key'):
            with self.assertRaises(registry.RecoveryError):
                registry._root(alt)

    def test_missing_key_disables_recovery(self):
        folder=registry.create_run(self.root)
        (self.root/registry.ROOT_KEY).unlink()
        with self.assertRaises(FileNotFoundError):
            registry.recover_managed_root(self.root,self.image)
        self.assertTrue(folder.exists())


class AcceptanceContractTests(unittest.TestCase):
    def test_no_untrusted_claims_and_no_execution_by_default(self):
        b=acceptance._base()
        self.assertEqual(b['security_attestation'],'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertTrue(all(x=='NOT_VERIFIED' for x in b['checks'].values()))
        self.assertFalse(b['student_code_executed'])
        self.assertEqual(len(b['checks']),10)

    def test_uses_verified_windows_job_and_exact_live_handle(self):
        c=inspect.getsource(acceptance._child)
        p=inspect.getsource(acceptance.run)
        self.assertIn('ensure_controller_job()',c)
        self.assertIn('verify_child_in_job(worker.proc)',c)
        self.assertIn('windows_creation_time(worker.proc)',c)
        self.assertIn('exclusive_lease(run)',c)
        self.assertIn('_WindowsProcessHandle(',inspect.getsource(acceptance._await_boot))
        self.assertIn('first_handle.wait_exited(',p)
        self.assertIn('recover_managed_root(',p)

    def test_unknown_legacy_temp_roots_not_scanned(self):
        src=inspect.getsource(registry.recover_managed_root)
        self.assertIn('ID_PATTERN',inspect.getsource(registry._run))
        self.assertNotIn('os.walk',src)
        self.assertNotIn('kill(',src)
        self.assertNotIn('rmtree(',src)

    def test_static_run_does_not_claim_success_on_linux(self):
        if os.name != 'nt':
            r=acceptance.run(Path('unused.json'),execute=False)
            self.assertEqual(r['status'],'WINDOWS_RECOVERY_ACCEPTANCE_REQUIRES_WINDOWS')
        self.assertNotEqual(acceptance._base()['status'],acceptance.STATUS_PASS)


@unittest.skipUnless(os.name == 'nt', 'Requires Windows byte-range lock semantics')
class WindowsLeaseRegressionTests(unittest.TestCase):
    def test_cross_process_locked_lease_is_skipped_not_read_denied(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as td:
            root = registry.initialize_root(Path(td) / 'managed')
            run = registry.create_run(root)
            code = (
                'from pathlib import Path\n'
                'import sys, time\n'
                'from projectiq_executioner.phase2.vm_worker.d5c_orphan_registry import exclusive_lease\n'
                'with exclusive_lease(Path(sys.argv[1])):\n'
                '    print("LOCKED", flush=True)\n'
                '    time.sleep(9)\n'
            )
            proc = subprocess.Popen(
                [sys.executable, '-c', code, str(run)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                self.assertEqual(proc.stdout.readline().strip(), b'LOCKED')
                self.assertIsNone(proc.poll())
                outcome = registry.recover_managed_root(root, str(Path(td) / 'qemu.exe'))
                self.assertEqual(outcome[0]['status'], 'SKIPPED_ACTIVE', outcome)
                self.assertTrue(run.is_dir())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.communicate(timeout=12)
            self.assertEqual(registry.recover_managed_root(root, 'irrelevant')[0]['status'], 'RECOVERED')

    def test_failed_probe_preserves_workspace_and_original_reason(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as td, \
             patch.object(acceptance, 'load_config', return_value={}), \
             patch.object(acceptance, 'verify_host_toolchain', return_value={
                 'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(acceptance, '_expected_qemu', return_value='C:/qemu.exe'), \
             patch.object(acceptance, '_start_controller', return_value=MagicMock(**{
                 'poll.return_value': 1})), \
             patch.object(acceptance, '_await_boot', side_effect=registry.RecoveryError('INJECTED_FAILURE')):
            report = acceptance.run(Path('unused'), execute=True, acknowledge=True,
                                    work_root=Path(td))
            self.assertEqual(report['failure_code'], 'INJECTED_FAILURE')
            self.assertEqual(report['status'], 'CLEANUP_FAILED')
            self.assertEqual(report['cleanup'], 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')
            self.assertTrue(Path(report['manual_review_workspace']).is_dir())


if __name__ == '__main__':
    unittest.main()
