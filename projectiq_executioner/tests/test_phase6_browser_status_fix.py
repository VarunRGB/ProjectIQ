"""Phase 6 v0.1.1 regression coverage for historical browser status spelling.

Only normalizes existing evidence. No student application or VM execution.
"""
import unittest

from projectiq_executioner.phase6.contracts import EvidenceError
from projectiq_executioner.phase6.evidence import NO_RUN_STATES, STAGES, normalize_run
from projectiq_executioner.phase6.pipeline import build

SOURCE = {
    'kind': 'github', 'repository': 'example/browser-fixture',
    'commit_sha': 'a' * 40,
}
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def browser_run(status, *, execution='NOT_PERFORMED', cleanup='NOT_STARTED'):
    return {
        'schema_version': '0.1', 'phase': 4,
        'stage': 'reviewed_browser_ui', 'source': SOURCE,
        'adapter': 'node_fullstack_offline_v1',
        'status': status, 'execution': execution,
        'cleanup': cleanup, 'security_attestation': ATTESTATION,
        'checks': [],
    }


def vm_report(*, execution='PERFORMED_IN_DISPOSABLE_VM', cleanup=CLEAN):
    return {
        'source': SOURCE, 'adapter': 'node_fullstack_offline_v1',
        'status': 'STARTUP_VERIFIED', 'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
        'execution': execution, 'cleanup': cleanup,
        'security_attestation': ATTESTATION,
        'phase4dc_status': 'VALIDATED',
    }


class BrowserHistoricalStatusCompat(unittest.TestCase):
    def _assert_nonexecution(self, status, *, vm=None, execution='NOT_PERFORMED', cleanup='NOT_STARTED'):
        r = browser_run(status, execution=execution, cleanup=cleanup)
        out = build(run_inputs=[(r, 'RUN001', vm)])
        self.assertEqual(out['summary']['runs'], 1)
        self.assertEqual(out['status'], 'PARTIAL')
        self.assertEqual(out['summary']['vm_verified_runs'], 0)
        self.assertEqual(out['summary']['passes'], 0)
        self.assertEqual(out['summary']['fails'], 0)
        self.assertEqual(out['summary']['tests_observed'], 0)
        self.assertEqual(out['root_cause_hypotheses'], [])
        self.assertIn('VM_EXECUTION_UNVERIFIED', {d['code'] for d in out['diagnoses']})
        self.assertNotIn('BROWSER_CHECK_FAILURE', {d['code'] for d in out['diagnoses']})
        self.assertTrue(all(d['student_defect'] == 'NOT_ESTABLISHED' for d in out['diagnoses']))
        self.assertEqual(out['execution'], 'NOT_PERFORMED')
        self.assertEqual(out['vm_launches'], 0)

    def test_new_spelling_in_both_closed_status_sets(self):
        self.assertIn('BROWSER_VM_NOT_VERIFIED', STAGES['reviewed_browser_ui'])
        self.assertIn('BROWSER_VM_NOT_VERIFIED', NO_RUN_STATES)

    def test_historical_spelling_without_vm_is_partial(self):
        self._assert_nonexecution('BROWSER_VM_NOT_VERIFIED')

    def test_legacy_spelling_preserved_without_vm(self):
        self._assert_nonexecution('BROWSER_NOT_VERIFIED')

    def test_historical_spelling_with_nonverified_vm_is_partial(self):
        vm = vm_report(execution='NOT_PERFORMED', cleanup='NOT_STARTED')
        self._assert_nonexecution('BROWSER_VM_NOT_VERIFIED', vm=vm)

    def test_historical_spelling_cannot_gain_verified_status_from_valid_vm(self):
        self._assert_nonexecution(
            'BROWSER_VM_NOT_VERIFIED', vm=vm_report(),
            execution='PERFORMED_IN_DISPOSABLE_VM', cleanup=CLEAN,
        )

    def test_legacy_spelling_cannot_gain_verified_status_from_valid_vm(self):
        self._assert_nonexecution(
            'BROWSER_NOT_VERIFIED', vm=vm_report(),
            execution='PERFORMED_IN_DISPOSABLE_VM', cleanup=CLEAN,
        )

    def test_historical_spelling_bad_cleanup_remains_security_hold(self):
        r = browser_run('BROWSER_VM_NOT_VERIFIED', cleanup='NOT_VERIFIED')
        out = build(run_inputs=[(r, 'RUN001', None)])
        self.assertEqual(out['status'], 'SECURITY_HOLD')
        self.assertIn('VM_CLEANUP_UNVERIFIED', {d['code'] for d in out['diagnoses']})
        self.assertFalse(any(d['student_defect'] != 'NOT_ESTABLISHED' for d in out['diagnoses']))

    def test_unknown_browser_status_still_rejected(self):
        with self.assertRaises(EvidenceError) as cm:
            normalize_run(browser_run('BROWSER_VM_FAKE_VERIFIED'), label='RUN001')
        self.assertEqual(cm.exception.code, 'INVALID_RUN_STATUS_FOR_STAGE')

    def test_historical_spelling_not_allowed_for_login_stage(self):
        r = browser_run('BROWSER_VM_NOT_VERIFIED')
        r['stage'] = 'reviewed_browser_login'
        with self.assertRaises(EvidenceError) as cm:
            normalize_run(r, label='RUN001')
        self.assertEqual(cm.exception.code, 'INVALID_RUN_STATUS_FOR_STAGE')

    def test_actual_browser_pass_still_works(self):
        r = browser_run('BROWSER_PASSED', execution='PERFORMED_IN_DISPOSABLE_VM', cleanup=CLEAN)
        r['checks'] = [{'id':'login_visible','matched':True}]
        out = build(run_inputs=[(r,'RUN001',vm_report())])
        self.assertEqual(out['status'], 'DIAGNOSED')
        self.assertEqual(out['summary']['passes'],1)
        self.assertEqual(out['summary']['vm_verified_runs'],1)
        self.assertEqual(out['summary']['diagnoses'],0)

if __name__ == '__main__':
    unittest.main()
