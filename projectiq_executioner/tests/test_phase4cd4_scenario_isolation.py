"""4C-D4 offline acceptance: approvals, real-VM evidence gates and fail-closed reset."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4.oracle_contracts import digest
from projectiq_executioner.phase4.scenario_isolation_contracts import (
    IsolationReviewError, compile_reviewed_isolation, validate_isolation_manifest)
from projectiq_executioner.phase4.scenario_isolation_vm_run import (
    _verify_vm_boundary, preflight, run)


SOURCE = {'kind': 'github', 'repository': 'VarunRGB/Mini-Task-Collaboration-App',
          'commit_sha': 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37'}
STRATEGY = 'FRESH_DISPOSABLE_QCOW2_VM_PER_SCENARIO'


def parameters():
    manifest = {'schema_version': '0.1', 'source': dict(SOURCE), 'strategy': STRATEGY,
                'scenarios': [{'id': 'registration-first',
                               'workflow': 'REVIEWED_D3_REGISTRATION_PERSISTENCE'},
                              {'id': 'registration-after-reset',
                               'workflow': 'REVIEWED_D3_REGISTRATION_PERSISTENCE'}]}
    identity = SOURCE['repository'] + '@' + SOURCE['commit_sha']
    return {'plan': {'source': dict(SOURCE)}, 'auth': {}, 'oracles': {},
            'postconditions': {}, 'candidate': {'source': dict(SOURCE),
                                               'adapter': 'php_mysql_v1'},
            'config': {'approved_scenario_isolation': [identity + '#' + digest(manifest)]},
            'manifest': manifest}


def d3_proof():
    return {'postconditions': {
        'postcondition_catalog_sha256': 'a' * 64,
        'checks': [{'id': 'PC-EXAMPLE', 'table': 'users', 'lookup_column': 'email',
                    'expected_fields': {'name': 'ProjectIQ Test', 'role': 'user'}}]}}


def d3_result():
    return {'source': dict(SOURCE), 'adapter': 'php_mysql_v1',
            'postcondition_catalog_sha256': 'a' * 64,
            'status': 'POSTCONDITIONS_PASSED',
            'execution': 'PERFORMED_IN_DISPOSABLE_VM',
            'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
            'session_cookie_observed': True,
            'auth_summary': {'total': 4, 'passed': 4, 'failed': 0, 'inconclusive': 0},
            'summary': {'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0},
            'results': [{'id': 'PC-EXAMPLE', 'result': 'PASS', 'before_matches': 0,
                         'after_matches': 1, 'expected_fields_match': True,
                         'issue_code': None}]}


def vm_result(lease):
    return {'source': dict(SOURCE), 'adapter': 'php_mysql_v1',
            'managed_run_id': lease,
            'managed_runtime': 'SIGNED_LEASED_WORKSPACE_REMOVED',
            'status': 'STARTUP_VERIFIED',
            'execution': 'PERFORMED_IN_DISPOSABLE_VM',
            'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
            'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
            'phase4d3_status': 'VALIDATED',
            'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'}


class IsolationContractTests(unittest.TestCase):
    def test_two_distinct_scenarios_accepted(self):
        p = parameters()
        self.assertEqual(validate_isolation_manifest(p['manifest'], SOURCE),
                         ['registration-first', 'registration-after-reset'])

    def test_rejects_duplicate_ids(self):
        p = parameters()
        p['manifest']['scenarios'][1]['id'] = 'registration-first'
        with self.assertRaises(IsolationReviewError):
            validate_isolation_manifest(p['manifest'], SOURCE)

    def test_rejects_extra_scenario(self):
        p = parameters()
        p['manifest']['scenarios'].append(p['manifest']['scenarios'][0].copy())
        with self.assertRaises(IsolationReviewError):
            validate_isolation_manifest(p['manifest'], SOURCE)

    def test_rejects_wrong_source_or_strategy(self):
        for key in ('source', 'strategy'):
            with self.subTest(key=key):
                p = parameters()
                p['manifest'][key] = 'incorrect'
                with self.assertRaises(IsolationReviewError):
                    validate_isolation_manifest(p['manifest'], SOURCE)

    def test_rejects_arbitrary_workflow_and_extra_fields(self):
        for mutation in ('workflow', 'extra'):
            with self.subTest(mutation=mutation):
                p = parameters()
                p['manifest']['scenarios'][0][mutation] = 'arbitrary-command'
                with self.assertRaises(IsolationReviewError):
                    validate_isolation_manifest(p['manifest'], SOURCE)

    def test_rejects_paths_and_control_characters_in_ids(self):
        for scenario_id in ('../run', 'a/b', 'a\\b', 'A', 'a\n', 'a' * 41):
            with self.subTest(id=scenario_id):
                p = parameters()
                p['manifest']['scenarios'][0]['id'] = scenario_id
                with self.assertRaises(IsolationReviewError):
                    validate_isolation_manifest(p['manifest'], SOURCE)

    @patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
           return_value=d3_proof())
    def test_exact_separate_approval_required(self, _):
        p = parameters()
        self.assertEqual(compile_reviewed_isolation(**p)['scenario_ids'],
                         ['registration-first', 'registration-after-reset'])
        p['config']['approved_scenario_isolation'] = []
        with self.assertRaises(IsolationReviewError):
            compile_reviewed_isolation(**p)

    @patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
           return_value=d3_proof())
    def test_mutated_manifest_invalidates_approval(self, _):
        p = parameters()
        p['manifest']['scenarios'][1]['id'] = 'different'
        with self.assertRaises(IsolationReviewError):
            compile_reviewed_isolation(**p)

    @patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
           return_value=d3_proof())
    def test_requires_positive_email_lookup(self, _):
        p = parameters()
        bad = d3_proof()
        bad['postconditions']['checks'][0]['lookup_column'] = 'name'
        with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                   return_value=bad):
            with self.assertRaises(IsolationReviewError):
                compile_reviewed_isolation(**p)

    @patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
           return_value=d3_proof())
    def test_preflight_never_calls_vm(self, _):
        p = parameters()
        with TemporaryDirectory() as root:
            out = Path(root) / 'fresh'
            with patch('projectiq_executioner.phase4.db_postcondition_vm_run.run') as mock_d3:
                report = preflight(**p, output_dir=out)
                mock_d3.assert_not_called()
            self.assertEqual(report['status'], 'APPROVED_NOT_EXECUTED')
            self.assertEqual(report['scenario_count'], 2)
            self.assertTrue((out / 'd4_preflight.json').is_file())
            with self.assertRaises(IsolationReviewError):
                preflight(**p, output_dir=out)


class VmBoundaryTests(unittest.TestCase):
    def test_valid_report_and_unique_signed_lease(self):
        self.assertEqual(_verify_vm_boundary(d3_result(), vm_result('run-01'), set()),
                         ('run-01', None))

    def test_detects_lease_reuse(self):
        self.assertEqual(_verify_vm_boundary(d3_result(), vm_result('run-01'), {'run-01'}),
                         (None, 'VM_LEASE_IDENTITY_NOT_UNIQUE'))

    def test_detects_missing_cleanup(self):
        for part in ('d3', 'vm'):
            with self.subTest(part=part):
                d3, vm = d3_result(), vm_result('run-01')
                if part == 'd3':
                    d3['cleanup'] = 'CLEANUP_FAILED'
                else:
                    vm['managed_runtime'] = 'NOT_VERIFIED'
                self.assertIsNotNone(_verify_vm_boundary(d3, vm, set())[1])

    def test_detects_missing_or_forged_database_observation(self):
        for field, value in (('before_matches', 1), ('after_matches', 0),
                             ('expected_fields_match', False)):
            with self.subTest(field=field):
                d3 = d3_result()
                d3['results'][0][field] = value
                self.assertEqual(_verify_vm_boundary(d3, vm_result('run-01'), set())[1],
                                 'REGISTRATION_BASELINE_NOT_VERIFIED')

    def test_detects_wrong_backend_or_source(self):
        for field, value in (('backend', 'NOT_QEMU'), ('source', {'kind': 'other'}),
                             ('security_attestation', 'ATTESTED')):
            with self.subTest(field=field):
                vm = vm_result('run-01')
                vm[field] = value
                self.assertIsNotNone(_verify_vm_boundary(d3_result(), vm, set())[1])


class D4ExecutionTests(unittest.TestCase):
    def _execute(self, leases, change=None):
        p = parameters()
        calls = []

        def fake_run(**kwargs):
            idx = len(calls)
            calls.append(kwargs)
            destination = kwargs['output_dir']
            destination.mkdir(parents=True, exist_ok=False)
            vm = vm_result(leases[idx])
            report = d3_result()
            if change:
                change(idx, report, vm)
            (destination / 'd3_vm_execution.json').write_text(json.dumps(vm), 'utf-8')
            (destination / 'd3_run.json').write_text(json.dumps(report), 'utf-8')
            return report

        with TemporaryDirectory() as folder:
            out = Path(folder) / 'd4run'
            with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                       return_value=d3_proof()), patch(
                       'projectiq_executioner.phase4.db_postcondition_vm_run.run',
                       side_effect=fake_run):
                report = run(**p, output_dir=out)
            evidence = json.loads((out / 'd4_run.json').read_text('utf-8'))
            self.assertEqual(evidence, report)
            return report, calls

    def test_two_distinct_vm_runs_complete(self):
        report, calls = self._execute(['run-01', 'run-02'])
        self.assertEqual(report['status'], 'SCENARIO_ISOLATION_VERIFIED')
        self.assertEqual(report['execution'], 'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS')
        self.assertEqual(report['summary'], {'total': 2, 'passed': 2, 'failed': 0,
                                             'not_executed': 0})
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]['output_dir'], calls[1]['output_dir'])
        self.assertTrue(all(x['status'] == 'RESET_VERIFIED' for x in report['scenarios']))
        self.assertNotIn('run-01', json.dumps(report))  # Don't publish lease IDs.

    def test_wrong_catalog_binding_stops_before_second(self):
        def wrong_catalog(i, d3, vm):
            d3['postcondition_catalog_sha256'] = 'b' * 64
        report, calls = self._execute(['run-01'], wrong_catalog)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['issue_code'], 'SCENARIO_REVIEW_BINDING_MISMATCH')

    def test_duplicate_lease_fails_closed(self):
        report, calls = self._execute(['same-run', 'same-run'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(report['status'], 'ISOLATION_NOT_VERIFIED')
        self.assertEqual(report['summary']['passed'], 1)
        self.assertEqual(report['summary']['failed'], 1)
        self.assertEqual(report['issue_code'], 'VM_LEASE_IDENTITY_NOT_UNIQUE')

    def test_failed_first_scenario_never_launches_second(self):
        def wrong_rows(i, d3, vm):
            d3['results'][0]['after_matches'] = 0
        report, calls = self._execute(['run-01'], wrong_rows)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['summary'], {'total': 2, 'passed': 0, 'failed': 1,
                                             'not_executed': 1})
        self.assertEqual(report['issue_code'], 'REGISTRATION_BASELINE_NOT_VERIFIED')

    def test_failed_cleanup_never_launches_second(self):
        def cleanup_failure(i, d3, vm):
            d3['cleanup'] = 'CLEANUP_FAILED'
        report, calls = self._execute(['run-01'], cleanup_failure)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['issue_code'], 'D3_SCENARIO_NOT_VERIFIED')


if __name__ == '__main__':
    unittest.main()
