"""D5 review, source isolation, real report binding and fail-closed orchestration."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4.oracle_contracts import digest
from projectiq_executioner.phase4.cross_repository_contracts import (
    CrossRepositoryReviewError, compile_reviewed_suite, validate_manifest,
    load_case_documents,
)
from projectiq_executioner.phase4.cross_repository_vm_run import (
    _verify_case, _vm_boundary, preflight, run,
)

A = {'kind': 'github', 'repository': 'Reviewed/Database', 'commit_sha': 'a' * 40}
B = {'kind': 'github', 'repository': 'Reviewed/ApiApp', 'commit_sha': 'b' * 40}
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def d3_proof():
    return {'auth': {'schema_version': '0.1'},
            'postconditions': {'postcondition_catalog_sha256': 'c' * 64,
                               'checks': [{'table': 'users', 'lookup_column': 'email'}]}}


def api_proof(json_assertion=True):
    return {'tests': [{'id': 'OC-' + 'A' * 16,
                       'assertions': [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}] +
                           ([{'kind': 'JSON_BODY_SUBSET_EQUALS', 'expected': {'healthy': True}}]
                            if json_assertion else [])}]}


def sample_documents(source, adapter, d3):
    value = {'plan': {'source': dict(source), 'test_cases': []},
             'candidate': {'source': dict(source), 'adapter': adapter},
             'oracles': {'schema_version': '0.1', 'source': dict(source), 'cases': []}}
    if d3:
        value.update(auth={'registration': {}}, postconditions={'checks': []})
    return value


def vm_source(source, adapter, ident):
    return {'status': 'STARTUP_VERIFIED', 'source': dict(source), 'adapter': adapter,
            'execution': 'PERFORMED_IN_DISPOSABLE_VM', 'cleanup': CLEAN,
            'managed_runtime': 'SIGNED_LEASED_WORKSPACE_REMOVED',
            'backend': 'QEMU_DISPOSABLE_QCOW2_VM', 'security_attestation': ATTESTATION,
            'managed_run_id': ident}


def d3_result():
    return {'source': dict(A), 'adapter': 'php_mysql_v1',
            'status': 'POSTCONDITIONS_PASSED',
            'execution': 'PERFORMED_IN_DISPOSABLE_VM', 'cleanup': CLEAN,
            'postcondition_catalog_sha256': 'c' * 64,
            'session_cookie_observed': True,
            'auth_summary': {'total': 4, 'passed': 4, 'failed': 0, 'inconclusive': 0},
            'summary': {'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0},
            'results': [{'result': 'PASS', 'before_matches': 0,
                         'after_matches': 1, 'expected_fields_match': True}]}


def api_result(oracles):
    report = {'source': dict(B), 'adapter': 'node_fullstack_offline_v1',
              'status': 'TESTS_PASSED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup': CLEAN, 'oracle_catalog_sha256': digest(oracles),
              'summary': {'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0},
              'results': [{'id': 'OC-' + 'A' * 16, 'result': 'PASS'}]}
    return report


class Prepared(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        db = sample_documents(A, 'php_mysql_v1', True)
        api = sample_documents(B, 'node_fullstack_offline_v1', False)
        self.manifest = {
            'schema_version': '0.1', 'strategy': 'SEQUENTIAL_DISTINCT_DISPOSABLE_VMS',
            'cases': []}
        for label, workflow, source, adapter, doc in (
                ('registration', 'D3_REGISTRATION', A, 'php_mysql_v1', db),
                ('api-check', 'REVIEWED_JSON_API', B, 'node_fullstack_offline_v1', api)):
            folder = self.inputs / label
            folder.mkdir()
            names = {}
            for key, value in doc.items():
                dest = folder / f'{key}.json'
                dest.write_text(json.dumps(value), encoding='utf-8')
                names[key] = f'{label}/{key}.json'
            self.manifest['cases'].append({
                'id': label, 'workflow': workflow, 'source': dict(source),
                'adapter': adapter, 'files': names, 'input_sha256': digest(doc)})
        self.config = {'approved_snapshots': [
            A['repository'] + '@' + A['commit_sha'],
            B['repository'] + '@' + B['commit_sha']],
            'approved_cross_repository_suites': []}
        self.approve()

    def approve(self):
        self.config['approved_cross_repository_suites'] = [
            'cross-repo#' + digest(self.manifest)]

    def compile(self):
        with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                   return_value=d3_proof()), patch(
                   'projectiq_executioner.phase4.reviewed_api.compile_reviewed_api_bundle',
                   return_value=api_proof()):
            return compile_reviewed_suite(self.manifest, self.config, self.inputs)


class ManifestTests(Prepared):
    def test_valid_exact_source_distinct_adapter_and_input_digest(self):
        self.assertEqual([c['id'] for c in validate_manifest(self.manifest)],
                         ['registration', 'api-check'])
        self.assertEqual([x['test_count'] for x in self.compile()], [1, 1])

    def test_reject_unapproved_suite_and_missing_second_snapshot(self):
        self.config['approved_cross_repository_suites'] = []
        with self.assertRaisesRegex(CrossRepositoryReviewError, 'D5_SUITE_NOT'):
            self.compile()
        self.approve()
        self.config['approved_snapshots'].pop()
        with self.assertRaisesRegex(CrossRepositoryReviewError, 'D5_PINNED_REPOSITORY'):
            self.compile()

    def test_reject_same_repo_even_different_sha(self):
        self.manifest['cases'][1]['source']['repository'] = A['repository']
        with self.assertRaises(CrossRepositoryReviewError):
            validate_manifest(self.manifest)

    def test_reject_duplicate_ids_and_same_workflow(self):
        changes = [('id', 'registration'), ('workflow', 'D3_REGISTRATION')]
        for field, value in changes:
            with self.subTest(field=field):
                bad = deepcopy(self.manifest)
                bad['cases'][1][field] = value
                with self.assertRaises(CrossRepositoryReviewError):
                    validate_manifest(bad)

    def test_reject_non_json_asserters(self):
        with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                   return_value=d3_proof()), patch(
                   'projectiq_executioner.phase4.reviewed_api.compile_reviewed_api_bundle',
                   return_value=api_proof(False)):
            with self.assertRaisesRegex(CrossRepositoryReviewError, 'D5_CASE_ORACLE'):
                compile_reviewed_suite(self.manifest, self.config, self.inputs)

    def test_reject_mutated_oracle_after_suite_approval(self):
        oracle_file = self.inputs / 'api-check/oracles.json'
        body = json.loads(oracle_file.read_text())
        body['extra'] = 'changed'
        oracle_file.write_text(json.dumps(body))
        with self.assertRaisesRegex(CrossRepositoryReviewError, 'D5_INPUT_CONTENT_DIGEST_MISMATCH'):
            self.compile()

    def test_reject_path_traversal_absolute_and_windows_drive(self):
        for path in ('../secret.json', '/etc/passwd', 'C:/private.json',
                     'registration/../../private.json', 'registration\\plan.json'):
            with self.subTest(path=path):
                bad = deepcopy(self.manifest['cases'][0])
                bad['files']['plan'] = path
                with self.assertRaises(CrossRepositoryReviewError):
                    load_case_documents(bad, self.inputs)

    def test_reject_symlink_inputs(self):
        real = self.inputs / 'registration/plan.json'
        other = self.root / 'elsewhere.json'
        other.write_text(real.read_text())
        real.unlink()
        try:
            real.symlink_to(other)
        except OSError:
            self.skipTest('Symlink permission unavailable')
        with self.assertRaises(CrossRepositoryReviewError):
            self.compile()

    def test_reject_wrong_source_and_adapter_even_with_rehashed_documents(self):
        p = self.inputs / 'api-check/plan.json'
        body = json.loads(p.read_text())
        body['source']['commit_sha'] = 'f' * 40
        p.write_text(json.dumps(body))
        docs = sample_documents(B, 'node_fullstack_offline_v1', False)
        docs['plan'] = body
        self.manifest['cases'][1]['input_sha256'] = digest(docs)
        self.approve()
        with self.assertRaisesRegex(CrossRepositoryReviewError, 'D5_SOURCE_OR_ADAPTER'):
            self.compile()

    def test_preflight_no_vm_and_exclusive_output(self):
        with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                   return_value=d3_proof()), patch(
                   'projectiq_executioner.phase4.reviewed_api.compile_reviewed_api_bundle',
                   return_value=api_proof()), patch(
                   'projectiq_executioner.phase4.db_postcondition_vm_run.run') as no_vm:
            report = preflight(manifest=self.manifest, config=self.config,
                               inputs_root=self.inputs, output_dir=self.root / 'preflight')
            no_vm.assert_not_called()
        self.assertEqual(report['status'], 'APPROVED_NOT_EXECUTED')
        self.assertEqual(report['case_count'], 2)
        with self.assertRaises(CrossRepositoryReviewError):
            preflight(manifest=self.manifest, config=self.config,
                      inputs_root=self.inputs, output_dir=self.root / 'preflight')


class StageTests(Prepared):
    def test_offline_prepare_uses_exact_approved_sources_and_fresh_output(self):
        from projectiq_executioner.phase4.cross_repository_prepare import prepare
        db = {key: self.inputs / value for key, value in self.manifest['cases'][0]['files'].items()}
        api = {key: self.inputs / value for key, value in self.manifest['cases'][1]['files'].items()}
        staged = self.root / 'prepared'
        manifest = prepare(db_files=db, api_files=api, out=staged)
        self.assertEqual(validate_manifest(manifest), manifest['cases'])
        self.assertEqual(manifest['cases'][0]['input_sha256'], self.manifest['cases'][0]['input_sha256'])
        self.assertEqual(manifest['cases'][1]['input_sha256'], self.manifest['cases'][1]['input_sha256'])
        self.assertTrue((staged / 'd5_suite.json').is_file())
        self.assertTrue((staged / 'inputs/json-api/oracles.json').is_file())
        with self.assertRaises(CrossRepositoryReviewError):
            prepare(db_files=db, api_files=api, out=staged)

    def test_prepare_rejects_duplicate_or_unsupported_adapters_without_artifacts(self):
        from projectiq_executioner.phase4.cross_repository_prepare import prepare
        db = {key: self.inputs / value for key, value in self.manifest['cases'][0]['files'].items()}
        api = {key: self.inputs / value for key, value in self.manifest['cases'][1]['files'].items()}
        candidate = api['candidate']
        data = json.loads(candidate.read_text())
        data['adapter'] = 'static_html_v1'
        candidate.write_text(json.dumps(data))
        dest = self.root / 'invalid'
        with self.assertRaises(CrossRepositoryReviewError):
            prepare(db_files=db, api_files=api, out=dest)
        self.assertFalse(dest.exists())


class EvidenceTests(Prepared):
    def test_vm_rejects_cleanup_and_duplicate_id(self):
        vm = vm_source(A, 'php_mysql_v1', 'piq-d5c3-11111111111111111111')
        self.assertEqual(_vm_boundary(vm, self.manifest['cases'][0], set()),
                         ('piq-d5c3-11111111111111111111', None))
        self.assertIsNotNone(_vm_boundary(vm, self.manifest['cases'][0], {'piq-d5c3-11111111111111111111'})[1])
        vm['cleanup'] = 'NOT_VERIFIED'
        self.assertEqual(_vm_boundary(vm, self.manifest['cases'][0], set())[1],
                         'D5_VM_OR_CLEANUP_NOT_VERIFIED')

    def test_d3_requires_real_row_evidence(self):
        item = self.compile()[0]
        vm = vm_source(A, 'php_mysql_v1', 'piq-d5c3-11111111111111111111')
        report = d3_result()
        vm.update(phase4d3_status='VALIDATED',
                  phase4d3_auth_report={'summary': report['auth_summary']},
                  phase4d3_postcondition_report={'summary': deepcopy(report['summary']),
                                                 'results': deepcopy(report['results'])})
        with patch('projectiq_executioner.phase4.auth_report_contracts.validate_auth_report'), patch(
                   'projectiq_executioner.phase4.db_postcondition_contracts.validate_guest_postcondition_report'):
            self.assertEqual(_verify_case(d3_result(), vm, item, set()), ('piq-d5c3-11111111111111111111', None))
            bad = d3_result()
            bad['results'][0]['after_matches'] = 0
            self.assertEqual(_verify_case(bad, vm, item, set())[1],
                             'D5_DATABASE_GUEST_REPORT_MISMATCH')

    def _execute(self, changes=None):
        calls = []
        docs_api = sample_documents(B, 'node_fullstack_offline_v1', False)
        def fake_db(**kw):
            calls.append('database')
            vm = vm_source(A, 'php_mysql_v1', 'piq-d5c3-11111111111111111111')
            report = d3_result()
            vm.update(phase4d3_status='VALIDATED',
                      phase4d3_auth_report={'summary': report['auth_summary']},
                      phase4d3_postcondition_report={'summary': deepcopy(report['summary']),
                                                     'results': deepcopy(report['results'])})
            if changes:
                changes('database', report, vm)
            folder = kw['output_dir']
            folder.mkdir(parents=True, exist_ok=False)
            (folder / 'd3_vm_execution.json').write_text(json.dumps(vm))
            (folder / 'd3_run.json').write_text(json.dumps(report))
            return report
        def fake_api(**kw):
            calls.append('api')
            report = api_result(kw['catalog'])
            vm = vm_source(B, 'node_fullstack_offline_v1', 'piq-d5c3-22222222222222222222')
            vm.update(phase4c_status='VALIDATED',
                      phase4c_test_results={'summary': report['summary'],
                                            'results': report['results']})
            if changes:
                changes('api', report, vm)
            folder = kw['output_dir']
            folder.mkdir(parents=True, exist_ok=False)
            (folder / 'api_vm_execution.json').write_text(json.dumps(vm))
            (folder / 'api_run.json').write_text(json.dumps(report))
            return report
        with patch('projectiq_executioner.phase4.db_postcondition_contracts.compile_reviewed_postconditions',
                   return_value=d3_proof()), patch(
                   'projectiq_executioner.phase4.reviewed_api.compile_reviewed_api_bundle',
                   return_value=api_proof()), patch(
                   'projectiq_executioner.phase4.db_postcondition_vm_run.run',
                   side_effect=fake_db), patch(
                   'projectiq_executioner.phase4.api_vm_run.run_reviewed_api',
                   side_effect=fake_api), patch(
                   'projectiq_executioner.phase4.auth_report_contracts.validate_auth_report'), patch(
                   'projectiq_executioner.phase4.db_postcondition_contracts.validate_guest_postcondition_report'), patch(
                   'projectiq_executioner.phase4.api_report_contracts.validate_api_report'):
            report = run(manifest=self.manifest, config=self.config,
                         inputs_root=self.inputs, output_dir=self.root / 'run')
        return report, calls

    def test_two_distinct_repo_adapters_and_leases_verified(self):
        result, calls = self._execute()
        self.assertEqual(result['status'], 'CROSS_REPOSITORY_VERIFIED')
        self.assertEqual(calls, ['database', 'api'])
        self.assertEqual(result['summary'], {'total': 2, 'passed': 2,
                                             'failed': 0, 'not_executed': 0})
        self.assertEqual(result['execution'], 'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS')
        self.assertEqual(result['cases'][1]['status'], 'VERIFIED')
        self.assertNotIn('piq-d5c3-11111111111111111111', json.dumps(result))
        self.assertNotIn('piq-d5c3-22222222222222222222', json.dumps(result))

    def test_failed_first_db_evidence_prevents_second_vm(self):
        def change(role, report, vm):
            if role == 'database':
                report['results'][0]['after_matches'] = 0
        result, calls = self._execute(change)
        self.assertEqual(calls, ['database'])
        self.assertEqual(result['summary']['not_executed'], 1)
        self.assertEqual(result['issue_code'], 'D5_DATABASE_GUEST_REPORT_MISMATCH')

    def test_failed_first_cleanup_prevents_second_vm(self):
        def change(role, report, vm):
            if role == 'database':
                vm['cleanup'] = 'NOT_VERIFIED'
        result, calls = self._execute(change)
        self.assertEqual(calls, ['database'])
        self.assertEqual(result['issue_code'], 'D5_VM_OR_CLEANUP_NOT_VERIFIED')

    def test_api_functional_failure_distinct_from_infrastructure_failure(self):
        def change(role, report, vm):
            if role == 'api':
                report['status'] = 'TESTS_FAILED'
                report['summary'] = {'total': 1, 'passed': 0, 'failed': 1, 'inconclusive': 0}
                report['results'] = [{'id': 'OC-' + 'A' * 16, 'result': 'FAIL'}]
                vm['phase4c_test_results'] = {'summary': report['summary'],
                                               'results': report['results']}
        result, calls = self._execute(change)
        self.assertEqual(calls, ['database', 'api'])
        self.assertEqual(result['status'], 'FUNCTIONAL_FAILURE_DETECTED')
        self.assertEqual(result['cases'][1]['status'], 'FUNCTIONAL_FAILURE')

    def test_api_oracle_binding_mismatch_is_not_pass(self):
        def change(role, report, vm):
            if role == 'api':
                report['oracle_catalog_sha256'] = '0' * 64
        result, calls = self._execute(change)
        self.assertNotEqual(result['status'], 'CROSS_REPOSITORY_VERIFIED')
        self.assertEqual(result['issue_code'], 'D5_API_ORACLE_REPORT_MISMATCH')


if __name__ == '__main__':
    unittest.main()
