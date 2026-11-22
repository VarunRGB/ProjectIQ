"""D3 regression: approval, guest DB witness, redaction and host report gates.

All tests are offline; the local HTTP fixture is trusted. No MySQL/QEMU needed.
"""
from __future__ import annotations

from copy import deepcopy
import json
from http.server import ThreadingHTTPServer
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.tests.test_phase4cd2_auth import make_data, SRC, LocalAuthHandler
from projectiq_executioner.phase4.oracle_contracts import digest
from projectiq_executioner.phase4.guest_api import run_auth_flow
from projectiq_executioner.phase4.auth_report_contracts import validate_auth_report
from projectiq_executioner.phase4.db_postcondition_contracts import (
    PostconditionReviewError, compile_reviewed_postconditions,
    validate_reviewed_postcondition_bundle, validate_guest_postcondition_report)
from projectiq_executioner.phase4.guest_db_postconditions import (
    DbPostconditionError, validate_guest_postcondition_bundle,
    validate_postcondition_catalog, make_observer, build_guest_postcondition_report)
from projectiq_executioner.phase4.db_postcondition_vm_run import preflight, run


def prepare():
    plan, auth, oracles, candidate, cfg = make_data()
    catalog = {'schema_version': '0.1', 'source': dict(SRC), 'checks': [
        {'table': 'users', 'lookup_column': 'email',
         'expected_fields': {'name': 'ProjectIQ Test', 'role': 'user'}}]}
    identity = SRC['repository'] + '@' + SRC['commit_sha']
    cfg['approved_database_postconditions'] = [identity + '#' + digest(catalog)]
    return plan, auth, oracles, catalog, candidate, cfg


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.auth, self.oracles, self.post, self.candidate, self.config = prepare()

    def compile(self):
        return compile_reviewed_postconditions(self.plan, self.auth, self.oracles,
                                               self.post, self.candidate, self.config)

    def test_exact_approved_bundle(self):
        b = self.compile()
        self.assertEqual(b['postconditions']['adapter'], 'php_mysql_v1')
        self.assertEqual(validate_reviewed_postcondition_bundle(b, self.candidate, self.config),
                         {'auth': b['auth'], 'postconditions': b['postconditions']})

    def test_missing_independent_approval(self):
        self.config['approved_database_postconditions'] = []
        with self.assertRaises(PostconditionReviewError):
            self.compile()

    def test_unknown_or_wrong_snapshot_denied(self):
        self.candidate['source'] = {**SRC, 'commit_sha': 'b' * 40}
        with self.assertRaises(PostconditionReviewError):
            self.compile()

    def test_no_unreviewed_expected_fields(self):
        self.post['checks'][0]['expected_fields']['role'] = 'admin'
        self.config['approved_database_postconditions'] = [
            SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.post)]
        with self.assertRaises(PostconditionReviewError):
            self.compile()

    def test_non_mysql_adapter_denied(self):
        self.candidate['adapter'] = 'node_express_offline_v1'
        with self.assertRaises(PostconditionReviewError):
            self.compile()

    def test_bundle_tamper_not_approved_by_controller(self):
        bundle = self.compile()
        bundle['postconditions']['checks'][0]['lookup_column'] = 'username'
        with self.assertRaises(PostconditionReviewError):
            validate_reviewed_postcondition_bundle(bundle, self.candidate, self.config)

    def test_missing_review_proof_denied(self):
        b = self.compile()
        del b['_review']
        with self.assertRaises(PostconditionReviewError):
            validate_reviewed_postcondition_bundle(b, self.candidate, self.config)

    def test_injection_and_secrets_denied(self):
        for field, value in [('table', 'users;DELETE'),
                             ('lookup_column', 'password'),
                             ('lookup_column', 'email` OR 1=1'),
                             ('table', 'information_schema')]:
            with self.subTest(field=field, value=value):
                c = deepcopy(self.post)
                c['checks'][0][field] = value
                with self.assertRaises(DbPostconditionError):
                    validate_postcondition_catalog(c, SRC)
        self.post['checks'][0]['expected_fields']['password'] = 'secret'
        with self.assertRaises(DbPostconditionError):
            validate_postcondition_catalog(self.post, SRC)

    def test_duplicate_or_extra_keys_denied(self):
        self.post['checks'].append(deepcopy(self.post['checks'][0]))
        with self.assertRaises(DbPostconditionError):
            validate_postcondition_catalog(self.post, SRC)
        self.post['checks'].pop()
        self.post['sql'] = 'SELECT 1'
        with self.assertRaises(DbPostconditionError):
            validate_postcondition_catalog(self.post, SRC)

    def test_preflight_has_no_launch_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'preflight'
            report = preflight(self.plan, self.auth, self.oracles, self.post,
                               self.candidate, self.config, folder)
            self.assertEqual(report['status'], 'APPROVED_NOT_EXECUTED')
            self.assertEqual(list(folder.iterdir()), [folder/'d3_preflight.json'])
            with self.assertRaises(PostconditionReviewError):
                preflight(self.plan, self.auth, self.oracles, self.post,
                          self.candidate, self.config, folder)


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.auth, self.oracles, self.post, self.candidate, self.config = prepare()
        self.bundle = compile_reviewed_postconditions(
            self.plan, self.auth, self.oracles, self.post, self.candidate,
            self.config)['postconditions']
        self.email = 'piq-' + 'a'*24 + '@example.invalid'

    def observer(self, reply):
        def process(argv, *, input, timeout, env):
            self.assertEqual(argv[0:2], ['/usr/bin/php', '-r'])
            self.assertEqual(env['PROJECTIQ_DB_NAME'], 'projectiq_run')
            payload = json.loads(input)
            self.assertEqual(payload['email'], self.email)
            self.assertNotIn('password', str(payload).lower())
            class Completed:
                returncode = 0
                stdout = json.dumps(reply).encode('utf-8')
            return Completed()
        return process

    def test_before_after_persistence_pass(self):
        spec = self.bundle['checks'][0]
        responses = [
            [{'id': spec['id'], 'matches': 0, 'fields_match': False}],
            [{'id': spec['id'], 'matches': 1, 'fields_match': True}],
        ]
        def proc(*args, **kwargs):
            return self.observer(responses.pop(0))(*args, **kwargs)
        callback, states = make_observer(self.bundle, expected_source=SRC,
                                         expected_adapter='php_mysql_v1',
                                         env={'PROJECTIQ_DB_NAME': 'projectiq_run'},
                                         trusted_process=proc)
        callback('BEFORE', self.email)
        callback('AFTER', self.email)
        self.assertEqual(responses, [])
        auth_report = {'results': [{'step': 'REGISTRATION', 'result': 'PASS'}]}
        report = build_guest_postcondition_report(self.bundle, auth_report, states)
        self.assertEqual(report['summary']['passed'], 1)
        self.assertNotIn(self.email, json.dumps(report))
        validate_guest_postcondition_report(self.bundle, auth_report, report)

    def test_http_success_without_database_row_is_fail(self):
        spec = self.bundle['checks'][0]
        auth = {'results': [{'step': 'REGISTRATION', 'result': 'PASS'}]}
        states = {'BEFORE': [{'id': spec['id'], 'matches': 0, 'fields_match': False}],
                  'AFTER': [{'id': spec['id'], 'matches': 0, 'fields_match': False}]}
        report = build_guest_postcondition_report(self.bundle, auth, states)
        self.assertEqual(report['summary']['failed'], 1)
        validate_guest_postcondition_report(self.bundle, auth, report)

    def test_wrong_fields_or_preexisting_row_is_fail(self):
        spec = self.bundle['checks'][0]
        auth = {'results': [{'step': 'REGISTRATION', 'result': 'PASS'}]}
        for before, after, good in ((0, 1, False), (1, 1, True)):
            with self.subTest(before=before, after=after, good=good):
                states = {'BEFORE': [{'id': spec['id'], 'matches': before, 'fields_match': bool(before)}],
                          'AFTER': [{'id': spec['id'], 'matches': after, 'fields_match': good}]}
                report = build_guest_postcondition_report(self.bundle, auth, states)
                self.assertEqual(report['results'][0]['result'], 'FAIL')

    def test_registration_failure_is_inconclusive(self):
        auth = {'results': [{'step': 'REGISTRATION', 'result': 'FAIL'}]}
        report = build_guest_postcondition_report(self.bundle, auth, {})
        self.assertEqual(report['results'][0]['result'], 'INCONCLUSIVE')
        validate_guest_postcondition_report(self.bundle, auth, report)

    def test_malformed_probe_response_denied(self):
        callback, _ = make_observer(self.bundle, expected_source=SRC,
                                    expected_adapter='php_mysql_v1',
                                    env={'PROJECTIQ_DB_NAME': 'projectiq_run'},
                                    trusted_process=self.observer([{'id': 'bad', 'matches': 0, 'fields_match': False}]))
        with self.assertRaises(DbPostconditionError):
            callback('BEFORE', self.email)

    def test_db_scope_denied(self):
        with self.assertRaises(DbPostconditionError):
            make_observer(self.bundle, expected_source=SRC,
                          expected_adapter='php_mysql_v1',
                          env={'PROJECTIQ_DB_NAME': 'production'}, trusted_process=lambda **_: None)

    def test_report_cannot_claim_false_pass(self):
        spec = self.bundle['checks'][0]
        auth = {'results': [{'step': 'REGISTRATION', 'result': 'PASS'}]}
        report = build_guest_postcondition_report(self.bundle, auth, {
            'BEFORE': [{'id': spec['id'], 'matches': 0, 'fields_match': False}],
            'AFTER': [{'id': spec['id'], 'matches': 0, 'fields_match': False}]})
        report['results'][0]['result'] = 'PASS'
        with self.assertRaises(PostconditionReviewError):
            validate_guest_postcondition_report(self.bundle, auth, report)

    def test_leakage_field_denied(self):
        auth = {'results': [{'step': 'REGISTRATION', 'result': 'PASS'}]}
        report = build_guest_postcondition_report(self.bundle, auth, {})
        report['results'][0]['email'] = self.email
        with self.assertRaises(PostconditionReviewError):
            validate_guest_postcondition_report(self.bundle, auth, report)


class AuthObserverIntegrationTests(unittest.TestCase):
    """Exercise real D2 HTTP interpreter with D3's before/after callback."""

    def setUp(self):
        self.plan, self.auth, self.oracles, self.post, self.candidate, self.config = prepare()
        self.proof = compile_reviewed_postconditions(self.plan, self.auth, self.oracles,
                                                     self.post, self.candidate, self.config)
        LocalAuthHandler.user = None
        LocalAuthHandler.bad_cookie = False
        LocalAuthHandler.mismatch = False
        LocalAuthHandler.queries = []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), LocalAuthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.thread.join, 3)

    def test_persisted_registration_and_secrets_remain_guest_only(self):
        expected_id = self.proof['postconditions']['checks'][0]['id']
        observations = []
        def process(argv, *, input, timeout, env):
            doc = json.loads(input)
            observations.append(doc['email'])
            assert doc['email'].endswith('@example.invalid')
            assert doc['email'].startswith('piq-')
            # Simulated independent DB read; the local HTTP fixture stores user
            # state and we only expose bounded booleans to the D3 interpreter.
            inserted = LocalAuthHandler.user is not None
            if inserted:
                assert doc['email'] == LocalAuthHandler.user['email']
            payload = [{'id': expected_id, 'matches': 1 if inserted else 0,
                        'fields_match': inserted}]
            class Completed:
                returncode = 0
                stdout = json.dumps(payload).encode('utf-8')
            return Completed()
        observer, states = make_observer(
            self.proof['postconditions'], expected_source=SRC,
            expected_adapter='php_mysql_v1', env={'PROJECTIQ_DB_NAME': 'projectiq_run'},
            trusted_process=process)
        auth_report = run_auth_flow(
            self.proof['auth'], expected_source=SRC, expected_adapter='php_mysql_v1',
            port=self.server.server_port, postcondition_observer=observer)
        validate_auth_report(self.proof['auth'], auth_report)
        db_report = build_guest_postcondition_report(self.proof['postconditions'],
                                                     auth_report, states)
        validate_guest_postcondition_report(self.proof['postconditions'], auth_report, db_report)
        self.assertEqual(auth_report['summary']['passed'], 4)
        self.assertEqual(db_report['summary']['passed'], 1)
        self.assertEqual(observations, [LocalAuthHandler.user['email']]*2)
        self.assertNotIn(LocalAuthHandler.user['email'], json.dumps(auth_report) + json.dumps(db_report))
        self.assertNotIn(LocalAuthHandler.user['password'], json.dumps(auth_report) + json.dumps(db_report))

    def test_before_observation_failure_aborts_registration(self):
        def unavailable(stage, email):
            raise DbPostconditionError('DB_READ_PROBE_FAILED')
        with self.assertRaises(DbPostconditionError):
            run_auth_flow(self.proof['auth'], expected_source=SRC,
                          expected_adapter='php_mysql_v1', port=self.server.server_port,
                          postcondition_observer=unavailable)
        self.assertIsNone(LocalAuthHandler.user)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.auth, self.oracles, self.post, self.candidate, self.config = prepare()
        self.proof = compile_reviewed_postconditions(self.plan, self.auth, self.oracles,
                                                     self.post, self.candidate, self.config)

    def test_bad_review_denied_without_vm(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        bad = deepcopy(self.proof)
        bad['postconditions']['checks'][0]['table'] = 'unreviewed'
        with tempfile.TemporaryDirectory() as folder:
            r = execute_vm_candidate(self.candidate, self.config, Path(folder)/'vm.json',
                                     reviewed_fixture=True, postcondition_bundle=bad)
        self.assertEqual(r['status'], 'DENIED')
        self.assertEqual(r['execution'], 'NOT_PERFORMED')
        self.assertEqual(r['cleanup'], 'NOT_STARTED')
        self.assertEqual(r['reason'], 'PHASE4D3_POSTCONDITION_NOT_OPERATOR_APPROVED')

    def test_host_acceptance_requires_verified_cleanup(self):
        from projectiq_executioner.phase2.vm_worker import orchestrator
        auth_report = {'schema_version': '0.1'}  # never accepted without full evidence
        vm = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup': 'VM_STOP_FAILED', 'phase4d3_status': 'VALIDATED',
              'phase4d3_auth_report': auth_report}
        with tempfile.TemporaryDirectory() as folder, patch.object(
                orchestrator, 'execute_vm_candidate', return_value=vm):
            report = run(plan=self.plan, auth=self.auth, oracles=self.oracles,
                         postconditions=self.post, candidate=self.candidate,
                         config=self.config, output_dir=Path(folder)/'run')
        self.assertEqual(report['status'], 'CLEANUP_FAILED')

    def test_host_rejects_forged_guest_report(self):
        from projectiq_executioner.phase2.vm_worker import orchestrator
        vm = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED', 'source': SRC,
              'adapter': 'php_mysql_v1', 'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
              'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'phase4d3_status': 'VALIDATED',
              'phase4d3_auth_report': {'token': 'forged'},
              'phase4d3_postcondition_report': {'status': 'COMPLETE'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(
                orchestrator, 'execute_vm_candidate', return_value=vm):
            report = run(plan=self.plan, auth=self.auth, oracles=self.oracles,
                         postconditions=self.post, candidate=self.candidate,
                         config=self.config, output_dir=Path(folder)/'run')
        self.assertEqual(report['status'], 'GUEST_RESULTS_NOT_VERIFIED')

    def test_multiple_modes_denied(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        with tempfile.TemporaryDirectory() as folder:
            r = execute_vm_candidate(self.candidate, self.config, Path(folder)/'vm.json',
                                     reviewed_fixture=True, postcondition_bundle=self.proof,
                                     auth_bundle={'unreviewed': True})
        self.assertEqual(r['reason'], 'PHASE4_MULTIPLE_BUNDLE_MODES_DENIED')


class TransportAllowlistTests(unittest.TestCase):
    def test_d3_helper_copy_allowed_but_arbitrary_filenames_denied(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from projectiq_executioner.phase2.vm_worker.qemu import SSHControl, WorkerFailure

        with tempfile.TemporaryDirectory() as folder:
            helper = Path(folder) / 'guest_db_postconditions.py'
            payload = b'# trusted test helper\n'
            helper.write_bytes(payload)
            fake = SimpleNamespace(ssh=Mock(return_value=SimpleNamespace(returncode=0)))

            SSHControl.copy(fake, helper, 'guest_db_postconditions.py')
            fake.ssh.assert_called_once_with(
                'dd', 'of=/home/projectiq/guest_db_postconditions.py',
                'status=none', timeout=60, input=payload)

            with self.assertRaisesRegex(WorkerFailure, 'Unapproved destination filename'):
                SSHControl.copy(fake, helper, 'unapproved_student_script.py')
            self.assertEqual(fake.ssh.call_count, 1)


if __name__ == '__main__':
    unittest.main()
