"""Targeted offline 4C-D1 fixture-safety and evidence tests.

No QEMU/MySQL is launched in this suite. Real-VM acceptance is separate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from projectiq_executioner.phase4.db_fixture_contracts import (
    FixtureReviewError, compile_reviewed_db_fixture, validate_fixture_report)
from projectiq_executioner.phase4.db_fixture_vm_run import (
    preflight_db_fixture, run_reviewed_db_fixture)
from projectiq_executioner.phase4.guest_db_fixture import (
    DbFixtureError, seed_mysql_fixture, validate_fixture_bundle)
from projectiq_executioner.phase4.oracle_contracts import digest


SOURCE = {'kind': 'github', 'repository': 'Example/SafePhp', 'commit_sha': 'a' * 40}


def _inputs():
    plan = {'source': dict(SOURCE), 'execution_context': {'adapter': 'php_mysql_v1'}}
    candidate = {'source': dict(SOURCE), 'schema_version': '0.3',
                 'stage': 'vm_execution_candidate', 'status': 'CANDIDATE_NEEDS_VM_VERIFICATION',
                 'worker_provider': 'qemu_qcow2_v1', 'adapter': 'php_mysql_v1',
                 'schema_path': 'sql/schema.sql'}
    manifest = {'schema_version': '0.1', 'source': dict(SOURCE), 'tables': [
        {'name': 'tasks', 'rows': [
            {'id': 901, 'title': 'ProjectIQ fixture task', 'user_id': 2,
             'priority': 'Low', 'status': 'Pending', 'deadline': '2027-03-15'}]}]}
    identity = SOURCE['repository'] + '@' + SOURCE['commit_sha']
    config = {'approved_snapshots': [identity],
              'approved_database_fixtures': [identity + '#' + digest(manifest)]}
    return plan, candidate, manifest, config


class FixtureSchemaTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.candidate, self.manifest, self.config = _inputs()
        self.bundle = {'schema_version': '0.1', 'source': dict(SOURCE),
                       'adapter': 'php_mysql_v1',
                       'fixture_catalog_sha256': digest(self.manifest),
                       'tables': deepcopy(self.manifest['tables'])}

    def valid(self, bundle=None):
        bundle = self.bundle if bundle is None else bundle
        return validate_fixture_bundle(bundle,
                                       expected_source=SOURCE, expected_adapter='php_mysql_v1')

    def test_valid_fixture_counts(self):
        self.assertEqual(self.valid(), {'table_count': 1, 'row_count': 1})

    def test_no_schema_or_sql_text_accepted(self):
        for addition in ('sql', 'statement', 'script', 'path', 'hooks', 'command', 'ddl'):
            with self.subTest(addition=addition):
                value = deepcopy(self.bundle)
                value[addition] = 'DROP TABLE users'
                with self.assertRaises(DbFixtureError):
                    self.valid(value)

    def test_source_mismatch_denied(self):
        value = deepcopy(self.bundle)
        value['source']['commit_sha'] = 'b' * 40
        with self.assertRaises(DbFixtureError):
            self.valid(value)

    def test_wrong_backend_denied(self):
        value = deepcopy(self.bundle)
        value['adapter'] = 'python_web_offline_v1'
        with self.assertRaises(DbFixtureError):
            self.valid(value)

    def test_invalid_catalog_digest_denied(self):
        value = deepcopy(self.bundle)
        value['fixture_catalog_sha256'] = 'a'
        with self.assertRaises(DbFixtureError):
            self.valid(value)

    def test_duplicate_casefolded_table_denied(self):
        value = deepcopy(self.bundle)
        value['tables'].append({'name': 'TASKS', 'rows': [{'id': 902}]})
        with self.assertRaises(DbFixtureError):
            self.valid(value)

    def test_sql_identifier_injection_denied(self):
        for name in ('tasks;DROP', 'other.tasks', 'a`b', 'a b', '', '__'*40):
            with self.subTest(name=name):
                value = deepcopy(self.bundle)
                value['tables'][0]['name'] = name
                with self.assertRaises(DbFixtureError):
                    self.valid(value)

    def test_column_identifier_injection_denied(self):
        for name in ('title`', 'a.b', 'x;delete', '$value'):
            with self.subTest(name=name):
                value = deepcopy(self.bundle)
                value['tables'][0]['rows'][0][name] = 'abc'
                with self.assertRaises(DbFixtureError):
                    self.valid(value)

    def test_credential_columns_denied(self):
        for col in ('password', 'hashed_password', 'api_key', 'access_token', 'auth_code', 'secret'):
            with self.subTest(col=col):
                value = deepcopy(self.bundle)
                value['tables'][0]['rows'][0][col] = 'secret'
                with self.assertRaises(DbFixtureError):
                    self.valid(value)

    def test_reject_nested_row_values(self):
        for value in ({'x': 1}, [1, 2], 3.0, 10**30):
            with self.subTest(value=value):
                candidate = deepcopy(self.bundle)
                candidate['tables'][0]['rows'][0]['id'] = value
                with self.assertRaises(DbFixtureError):
                    self.valid(candidate)

    def test_reject_control_characters(self):
        for value in ('hello\nworld', '\x00', '\x1b', 'a'*257):
            with self.subTest(value=value[:10]):
                candidate = deepcopy(self.bundle)
                candidate['tables'][0]['rows'][0]['title'] = value
                with self.assertRaises(DbFixtureError):
                    self.valid(candidate)

    def test_reject_zero_rows(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'][0]['rows'] = []
        with self.assertRaises(DbFixtureError):
            self.valid(candidate)

    def test_reject_row_budget(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'][0]['rows'] = [{'id': i} for i in range(25)]
        with self.assertRaises(DbFixtureError):
            self.valid(candidate)

    def test_reject_table_budget(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'] = [{'name': f'table_{i}', 'rows': [{'id': 1}]} for i in range(9)]
        with self.assertRaises(DbFixtureError):
            self.valid(candidate)

    def test_reject_reserved_table_name(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'][0]['name'] = 'mysql'
        with self.assertRaises(DbFixtureError):
            self.valid(candidate)

    def test_reject_extra_row_fields(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'][0]['rows'][0].update({f'col_{i}': i for i in range(15)})
        with self.assertRaises(DbFixtureError):
            self.valid(candidate)

    def test_null_bool_integer_and_unicode_allowed(self):
        candidate = deepcopy(self.bundle)
        candidate['tables'][0]['rows'][0] = {
            'id': 12, 'active': True, 'deleted_at': None, 'label': 'Test ç\u00e9'}
        candidate['fixture_catalog_sha256'] = digest({'schema_version':'0.1', 'source': candidate['source'], 'tables': candidate['tables']})
        self.assertEqual(self.valid(candidate)['row_count'], 1)


class OperatorApprovalTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.candidate, self.manifest, self.config = _inputs()
        self.patcher = patch('projectiq_executioner.phase4.db_fixture_contracts.validate_test_plan')
        self.mock_plan = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def compile(self):
        return compile_reviewed_db_fixture(self.plan, self.manifest,
                                           self.candidate, self.config)

    def test_approved_bundle(self):
        result = self.compile()
        self.assertEqual(result['fixture_catalog_sha256'], digest(self.manifest))
        self.assertEqual(result['tables'], self.manifest['tables'])

    def test_missing_snapshot_approval_denied(self):
        self.config['approved_snapshots'] = []
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_missing_fixture_approval_denied(self):
        self.config['approved_database_fixtures'] = []
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_manifest_edit_invalidates_approval(self):
        self.manifest['tables'][0]['rows'][0]['title'] = 'Different fixture'
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_candidate_source_mismatch_denied(self):
        self.candidate['source']['commit_sha'] = 'b'*40
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_plan_adapter_mismatch_denied(self):
        self.plan['execution_context']['adapter'] = 'node_express_offline_v1'
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_no_arbitrary_sql_in_manifest(self):
        self.manifest['tables'][0]['rows'][0]['sql'] = 'DROP TABLE users'
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_unsupported_database_adapter_denied(self):
        self.candidate['adapter'] = 'node_fullstack_offline_v1'
        with self.assertRaises(FixtureReviewError):
            self.compile()

    def test_guest_report_strict(self):
        b = self.compile()
        report = {'schema_version': '0.1', 'status': 'FIXTURES_SEEDED',
                  'fixture_catalog_sha256': b['fixture_catalog_sha256'],
                  'table_count': 1, 'seeded_rows': 1,
                  'database_scope': 'DISPOSABLE_GUEST_DATABASE_ONLY'}
        validate_fixture_report(b, report)
        for col, bad in [('status','STARTUP_VERIFIED'), ('seeded_rows',0),
                         ('seeded_rows', True), ('table_count', 0),
                         ('fixture_catalog_sha256','b'*64),
                         ('database_scope','HOST_DATABASE')]:
            with self.subTest(col=col):
                forged = dict(report)
                forged[col] = bad
                with self.assertRaises(FixtureReviewError):
                    validate_fixture_report(b, forged)

    def test_guest_report_extra_fields_denied(self):
        b = self.compile()
        report = {'schema_version': '0.1', 'status': 'FIXTURES_SEEDED',
                  'fixture_catalog_sha256': b['fixture_catalog_sha256'],
                  'table_count': 1, 'seeded_rows': 1,
                  'database_scope': 'DISPOSABLE_GUEST_DATABASE_ONLY', 'password': 'leak'}
        with self.assertRaises(FixtureReviewError):
            validate_fixture_report(b, report)


class GuestDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.candidate, self.manifest, self.config = _inputs()
        self.bundle = {'schema_version':'0.1','source':dict(SOURCE),
                       'adapter':'php_mysql_v1','fixture_catalog_sha256':digest(self.manifest),
                       'tables':deepcopy(self.manifest['tables'])}
        self.env = {'PROJECTIQ_DB_NAME':'projectiq_run','PROJECTIQ_DB_PASSWORD':'neverprinted'}

    def test_seed_passes_parameterized_rows_only(self):
        def runner(argv, *, input, env, timeout):
            self.assertEqual(argv[:2], ['/usr/bin/php', '-r'])
            self.assertIn('prepare($sql)', argv[2])
            self.assertIn('bindValue', argv[2])
            self.assertNotIn('ProjectIQ fixture task', argv[2])
            doc = json.loads(input)
            self.assertEqual(doc['tables'][0]['rows'][0]['id'], 901)
            self.assertEqual(timeout, 25)
            return types.SimpleNamespace(returncode=0, stdout=b'SEEDED_ROWS=1')
        report = seed_mysql_fixture(self.bundle, expected_source=SOURCE,
                                    expected_adapter='php_mysql_v1', env=self.env,
                                    trusted_process=runner)
        self.assertEqual(report['status'], 'FIXTURES_SEEDED')
        self.assertNotIn('neverprinted', str(report))

    def test_wrong_row_count_failed_closed(self):
        runner = Mock(return_value=types.SimpleNamespace(returncode=0, stdout=b'SEEDED_ROWS=0'))
        with self.assertRaises(DbFixtureError):
            seed_mysql_fixture(self.bundle, expected_source=SOURCE,
                               expected_adapter='php_mysql_v1', env=self.env,
                               trusted_process=runner)

    def test_failed_php_does_not_disclose_stderr(self):
        runner = Mock(return_value=types.SimpleNamespace(
            returncode=16, stdout=b'', stderr=b'secret database error'))
        with self.assertRaisesRegex(DbFixtureError, '^DATABASE_SEEDING_NOT_VERIFIED$'):
            seed_mysql_fixture(self.bundle, expected_source=SOURCE,
                               expected_adapter='php_mysql_v1', env=self.env,
                               trusted_process=runner)

    def test_wrong_db_scope_denied_before_any_execution(self):
        runner = Mock()
        self.env['PROJECTIQ_DB_NAME'] = 'production'
        with self.assertRaises(DbFixtureError):
            seed_mysql_fixture(self.bundle, expected_source=SOURCE,
                               expected_adapter='php_mysql_v1', env=self.env,
                               trusted_process=runner)
        runner.assert_not_called()

    def test_bad_fixture_denied_before_any_execution(self):
        runner = Mock()
        self.bundle['tables'][0]['rows'][0]['password'] = 'secret'
        with self.assertRaises(DbFixtureError):
            seed_mysql_fixture(self.bundle, expected_source=SOURCE,
                               expected_adapter='php_mysql_v1', env=self.env,
                               trusted_process=runner)
        runner.assert_not_called()


class HostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.candidate, self.manifest, self.config = _inputs()
        self.patcher = patch('projectiq_executioner.phase4.db_fixture_contracts.validate_test_plan')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_preflight_does_not_start_vm(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate') as worker:
                report = preflight_db_fixture(self.plan,self.manifest,self.candidate,self.config,Path(d))
                self.assertEqual(report['status'],'APPROVED_NOT_EXECUTED')
                self.assertEqual(report['execution'],'NOT_PERFORMED')
                worker.assert_not_called()
                self.assertNotIn('fixture task', (Path(d)/'db_fixture_preflight.json').read_text())

    def test_preflight_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            preflight_db_fixture(self.plan,self.manifest,self.candidate,self.config,Path(d))
            with self.assertRaises(FixtureReviewError):
                preflight_db_fixture(self.plan,self.manifest,self.candidate,self.config,Path(d))

    def test_full_vm_report_requires_verified_cleanup(self):
        b=compile_reviewed_db_fixture(self.plan,self.manifest,self.candidate,self.config)
        good={'source':dict(SOURCE),'adapter':'php_mysql_v1',
              'status':'STARTUP_VERIFIED','backend':'QEMU_DISPOSABLE_QCOW2_VM',
              'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'execution':'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
              'phase4d1_status':'VALIDATED','phase4d1_fixture_report':{
                  'schema_version':'0.1','status':'FIXTURES_SEEDED',
                  'fixture_catalog_sha256':b['fixture_catalog_sha256'],
                  'table_count':1,'seeded_rows':1,
                  'database_scope':'DISPOSABLE_GUEST_DATABASE_ONLY'}}
        import projectiq_executioner.phase2.vm_worker.orchestrator as orch
        for variant, expected in [({},'FIXTURES_SEEDED'),
                                  ({'cleanup':'NOT_VERIFIED'},'CLEANUP_FAILED'),
                                  ({'phase4d1_status':'INVALID_GUEST_REPORT'},'VM_EXECUTION_NOT_VERIFIED'),
                                  ({'phase4d1_fixture_report':None},'GUEST_RESULTS_NOT_VERIFIED')]:
            with self.subTest(variant=variant):
                vm=deepcopy(good)
                vm.update(variant)
                with tempfile.TemporaryDirectory() as d:
                    with patch.object(orch,'execute_vm_candidate',return_value=vm) as worker:
                        result=run_reviewed_db_fixture(plan=self.plan,manifest=self.manifest,
                            candidate=self.candidate,config=self.config,output_dir=Path(d))
                        self.assertEqual(result['status'],expected)
                        worker.assert_called_once()
                        self.assertEqual(worker.call_args.kwargs['db_fixture_bundle'],b)


class DirectControllerApprovalTests(unittest.TestCase):
    """No direct controller caller can bypass independent fixture approval."""

    def setUp(self):
        self.plan, self.candidate, self.manifest, self.config = _inputs()
        self.bundle = {'schema_version': '0.1', 'source': dict(SOURCE),
                       'adapter': 'php_mysql_v1',
                       'fixture_catalog_sha256': digest(self.manifest),
                       'tables': deepcopy(self.manifest['tables'])}

    def test_direct_vm_entry_rejects_unapproved_fixture_without_launch(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        self.config['approved_database_fixtures'] = []
        with tempfile.TemporaryDirectory() as d:
            report = execute_vm_candidate(self.candidate, self.config,
                                          Path(d) / 'vm.json', reviewed_fixture=True,
                                          db_fixture_bundle=self.bundle)
            self.assertEqual(report['status'], 'DENIED')
            self.assertEqual(report['execution'], 'NOT_PERFORMED')
            self.assertEqual(report['reason'], 'PHASE4D1_FIXTURE_NOT_OPERATOR_APPROVED')

    def test_direct_vm_entry_rejects_tampered_rows_without_launch(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        self.bundle['tables'][0]['rows'][0]['title'] = 'TAMPERED'
        with tempfile.TemporaryDirectory() as d:
            report = execute_vm_candidate(self.candidate, self.config,
                                          Path(d) / 'vm.json', reviewed_fixture=True,
                                          db_fixture_bundle=self.bundle)
            self.assertEqual(report['status'], 'DENIED')
            self.assertEqual(report['execution'], 'NOT_PERFORMED')
            self.assertEqual(report['reason'], 'PHASE4D1_DB_FIXTURE_BUNDLE_REJECTED')


if __name__=='__main__':
    unittest.main()
