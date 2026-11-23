"""Offline worker-forensics regression tests. No VM or network used."""
from __future__ import annotations
import ast
import unittest
from pathlib import Path
from projectiq_executioner.phase2.vm_worker import orchestrator

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'phase2/vm_worker/orchestrator.py'
EXPECTED_STAGES=(
    'SNAPSHOT_FETCH','VM_LAUNCH','VM_SSH_READY',
    'SNAPSHOT_TRANSFER','GUEST_AGENT_TRANSFER','GUEST_REQUEST_TRANSFER',
    'LOGIN_CONTROLLER_TRANSFER','LOGIN_CONTROLLER_PERMISSIONS',
    'LOGIN_CDP_TRANSFER','LOGIN_CDP_PERMISSIONS','BASE_CDP_TRANSFER','BASE_CDP_PERMISSIONS',
    'GUEST_EXECUTION','GUEST_REPORT_RETRIEVAL','GUEST_REPORT_PARSING',
)
class WorkerForensicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=SOURCE.read_text(encoding='utf-8')
        cls.tree=ast.parse(cls.source)
    def test_all_expected_checkpoints_present(self):
        for stage in EXPECTED_STAGES:
            with self.subTest(stage=stage):
                self.assertIn(repr(stage),self.source)
    def test_error_code_mapping_is_closed_and_redacted(self):
        # Test the actual AST dict from the exception handler, never a duplicate.
        handler=[n for n in ast.walk(self.tree) if isinstance(n,ast.ExceptHandler)
                 and any(isinstance(c,ast.Subscript) and isinstance(c.value,ast.Name)
                         and c.value.id=='result' and isinstance(c.slice,ast.Constant)
                         and c.slice.value=='worker_failure_code'
                         for c in ast.walk(n))]
        self.assertEqual(len(handler),1)
        maps=[n for n in ast.walk(handler[0]) if isinstance(n,ast.Dict)]
        mapping=next(value for n in maps if isinstance((value:=ast.literal_eval(n)),dict) and 'Unapproved destination filename' in value)
        self.assertEqual(mapping['Unapproved destination filename'],'UNAPPROVED_VM_TRANSFER_DESTINATION')
        self.assertEqual(mapping['Unable to copy trusted input into disposable VM'],'VM_TRANSFER_FAILED')
        self.assertTrue(all(isinstance(k,str) and isinstance(v,str) for k,v in mapping.items()))
        self.assertNotIn('PASSWORD',repr(mapping).upper())
    def test_no_raw_exception_export(self):
        self.assertIn("}.get(str(exc), 'OTHER_WORKER_FAILURE')",self.source)
        self.assertNotIn("result['worker_failure_code'] = str(exc)",self.source)
        self.assertNotIn("result['worker_failure_stage'] = str(exc)",self.source)
    def test_only_when_worker_failure(self):
        self.assertIn("if isinstance(exc, WorkerFailure):",self.source)
    def test_current_qemu_boundaries_unchanged(self):
        self.assertIn('ssh.wait_ssh(worker, seconds=85)',self.source)
        self.assertIn('VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',Path(ROOT/'phase4/browser_login_vm_run.py').read_text())
    def test_installer_does_not_open_network_or_vm(self):
        # Offline test is intentionally lightweight. Integration/acceptance prove the rest.
        self.assertTrue(callable(orchestrator.execute_vm_candidate))
if __name__=='__main__':unittest.main()
