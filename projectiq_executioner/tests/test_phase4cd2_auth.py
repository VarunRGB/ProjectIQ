"""D2 offline tests: reviewed registration/login, cookies and redacted results.

No QEMU, MySQL, external network or real credentials in this suite.
"""
from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.phase4.oracle_contracts import digest, validate_oracle_catalog
from projectiq_executioner.phase4.reviewed_auth import (
    AuthReviewError, compile_reviewed_auth_bundle, validate_reviewed_auth_bundle)
from projectiq_executioner.phase4.guest_api import (
    GuestApiError, validate_auth_guest_bundle, run_auth_flow)
from projectiq_executioner.phase4.auth_report_contracts import (
    AuthReportError, validate_auth_report)
from projectiq_executioner.phase4.auth_vm_run import preflight_auth

SRC = {'kind': 'github', 'repository': 'Fixture/AuthFlow', 'commit_sha': 'a' * 40}
CANDIDATE = {'schema_version': '0.3', 'stage': 'vm_execution_candidate',
             'worker_provider': 'qemu_qcow2_v1',
             'launch_authorization': 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES',
             'source': SRC, 'adapter': 'php_mysql_v1',
             'status': 'CANDIDATE_NEEDS_VM_VERIFICATION', 'probe_path': '/public/login.html',
             'schema_path': 'sql/app.sql'}


def make_data():
    raw = {'schema_version': '1.0', 'phase': 1, 'source': SRC,
           'routes': [{'path': path, 'method': 'POST', 'kind': 'declared_http_route', 'evidence': []}
                      for path in ('/register', '/login', '/protected')],
           'pages': ['public/login.html'], 'services': [], 'technologies': [], 'features': [],
           'proposed_test_targets': [], 'entrypoints': [], 'tests': [],
           'database_schema_files': [], 'inventory': {}}
    plan = generate_test_plan(raw, CANDIDATE)
    ids = {x['target']['path']: x['id'] for x in plan['test_cases']}
    def case(name, message):
        return {'name': name, 'test_id': ids['/protected'],
                'request': {'path_params': {}, 'query': {}, 'json_body': {}},
                'expected': {'status': 200, 'json_subset': {'error': message}}}
    oracles = {'schema_version': '0.1', 'source': SRC, 'cases': [
        case('No session rejected', 'Unauthorized'),
        case('Session accepts but ID required', 'ID required')]}
    by_message = {x['assertions'][-1]['expected']['error']: x['id']
                  for x in validate_oracle_catalog(oracles, plan)}
    auth = {'schema_version': '0.1', 'source': SRC,
            'registration': {'test_id': ids['/register'], 'body_kind': 'FORM',
                             'fields': {'email': {'$generated': 'email'},
                                        'password': {'$generated': 'password'},
                                        'name': {'$literal': 'ProjectIQ Test'},
                                        'role': {'$literal': 'user'}},
                             'expected': {'status': 302, 'json_subset': None}},
            'login': {'test_id': ids['/login'], 'body_kind': 'JSON',
                      'fields': {'email': {'$generated': 'email'},
                                 'password': {'$generated': 'password'}},
                      'expected': {'status': 200, 'json_subset': {'success': True}}},
            'unauthenticated_case_id': by_message['Unauthorized'],
            'authenticated_case_id': by_message['ID required']}
    identity = SRC['repository'] + '@' + SRC['commit_sha']
    config = {'approved_snapshots': [identity],
              'approved_oracle_catalogs': [identity + '#' + digest(oracles)],
              'approved_auth_flows': [identity + '#' + digest(auth)]}
    return plan, auth, oracles, deepcopy(CANDIDATE), config


class LocalAuthHandler(BaseHTTPRequestHandler):
    user = None
    bad_cookie = False
    mismatch = False
    queries = []

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/protected':
            self.__class__.queries.append((self.path, bool(self.headers.get('Cookie'))))
            message = 'ID required' if self.headers.get('Cookie') == 'sid=abc123' else 'Unauthorized'
            return self.reply(200, {'error': message})
        if self.path == '/register':
            fields = {k: v[0] for k, v in parse_qs(data.decode('utf-8')).items()}
            self.__class__.user = fields
            self.send_response(302)
            self.send_header('Location', '/login')
            self.end_headers()
            return
        if self.path == '/login':
            try:
                fields = json.loads(data)
            except ValueError:
                return self.reply(400, {'success': False})
            account = self.__class__.user
            valid = (account is not None and fields['email'] == account['email']
                     and fields['password'] == account['password'])
            result = self.reply(200, {'success': not self.__class__.mismatch if valid else False},
                                cookie=('sid=abc123; Path=/' if not self.__class__.bad_cookie
                                        else 'sid=abc123; Domain=external.invalid; Path=/'))
            return result
        return self.reply(404, {'error': 'missing'})

    def reply(self, status, body, *, cookie=None):
        payload = json.dumps(body).encode('utf-8')
        self.send_response(status)
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class AuthFixture(unittest.TestCase):
    def setUp(self):
        self.plan, self.auth, self.oracles, self.candidate, self.config = make_data()

    def compile(self):
        return compile_reviewed_auth_bundle(self.plan, self.auth, self.oracles,
                                            self.candidate, self.config)

    def rejected(self):
        with self.assertRaises((AuthReviewError, GuestApiError)):
            self.compile()


class AuthReviewTests(AuthFixture):
    def test_both_hashes_approved(self):
        bundle = self.compile()
        self.assertEqual(bundle['guest']['adapter'], 'php_mysql_v1')
        self.assertEqual(validate_reviewed_auth_bundle(bundle, self.candidate, self.config),
                         bundle['guest'])

    def test_unapproved_auth_hash_rejected(self):
        self.config['approved_auth_flows'].clear()
        self.rejected()

    def test_unapproved_oracle_hash_rejected(self):
        self.config['approved_oracle_catalogs'].clear()
        self.rejected()

    def test_tampered_auth_step_denied(self):
        self.auth['registration']['expected']['status'] = 201
        self.rejected()

    def test_tampered_protected_oracle_denied(self):
        self.oracles['cases'][1]['expected']['json_subset']['error'] = 'Other'
        self.rejected()

    def test_forged_guest_bundle_denied_by_controller(self):
        bundle = self.compile()
        bundle['guest']['login']['path'] = '/evil'
        with self.assertRaises(AuthReviewError):
            validate_reviewed_auth_bundle(bundle, self.candidate, self.config)

    def test_missing_review_proof_denied(self):
        with self.assertRaises(AuthReviewError):
            validate_reviewed_auth_bundle({'guest': self.compile()['guest']},
                                          self.candidate, self.config)

    def test_invalid_source_snapshot_denied(self):
        self.candidate['source'] = {**SRC, 'commit_sha': 'b'*40}
        self.rejected()

    def test_registration_cannot_be_get(self):
        self.auth['registration']['test_id'] = 'TC-FFFFFFFFFFFFFFFF'
        self.rejected()

    def test_password_must_be_guest_generated(self):
        self.auth['registration']['fields']['password'] = {'$literal': 'hunter2'}
        self.config['approved_auth_flows'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.auth)]
        self.rejected()

    def test_admin_role_denied(self):
        self.auth['registration']['fields']['role'] = {'$literal': 'admin'}
        self.config['approved_auth_flows'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.auth)]
        self.rejected()

    def test_external_url_and_commands_denied(self):
        for field in ('url', 'script', 'headers', 'sql'):
            with self.subTest(field=field):
                auth = deepcopy(self.auth)
                auth['login'][field] = 'http://external.invalid/'
                self.auth = auth
                self.config['approved_auth_flows'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(auth)]
                self.rejected()
                self.auth = deepcopy(make_data()[1])

    def test_protected_cases_must_share_request(self):
        self.oracles['cases'][1]['request']['json_body'] = {'id': 900}
        self.config['approved_oracle_catalogs'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.oracles)]
        self.rejected()

    def test_protected_case_must_not_have_mutating_payload(self):
        self.oracles['cases'][0]['request']['json_body'] = {'id': 900}
        self.oracles['cases'][1]['request']['json_body'] = {'id': 900}
        self.config['approved_oracle_catalogs'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.oracles)]
        self.rejected()

    def test_disallow_password_literal_in_expected_json(self):
        self.auth['login']['expected']['json_subset']['password'] = 'password'
        self.config['approved_auth_flows'] = [SRC['repository'] + '@' + SRC['commit_sha'] + '#' + digest(self.auth)]
        self.rejected()

    def test_guest_validation_rejects_direct_injection(self):
        guest = self.compile()['guest']
        guest['registration']['fields']['role'] = {'$literal': 'admin'}
        with self.assertRaises(GuestApiError):
            validate_auth_guest_bundle(guest)

    def test_preflight_does_not_run_vm(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            result = preflight_auth(self.plan, self.auth, self.oracles,
                                    self.candidate, self.config, path)
            self.assertEqual(result['status'], 'APPROVED_NOT_EXECUTED')
            self.assertEqual(result['execution'], 'NOT_PERFORMED')
            self.assertNotIn('password', (path/'auth_preflight.json').read_text().lower())
            self.assertEqual(list(path.iterdir()), [path / 'auth_preflight.json'])
            with self.assertRaises(AuthReviewError):
                preflight_auth(self.plan, self.auth, self.oracles,
                               self.candidate, self.config, path)


class AuthRuntimeTests(AuthFixture):
    def setUp(self):
        super().setUp()
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

    def run_guest(self):
        guest = self.compile()['guest']
        result = run_auth_flow(guest, expected_source=SRC,
                               expected_adapter='php_mysql_v1',
                               port=self.server.server_port)
        validate_auth_report(guest, result)
        return result

    def test_full_four_step_authentication(self):
        result = self.run_guest()
        self.assertEqual(result['summary'], {'total': 4, 'passed': 4,
                                              'failed': 0, 'inconclusive': 0})
        self.assertTrue(result['session_cookie_observed'])
        self.assertEqual(LocalAuthHandler.queries,
                         [('/protected', False), ('/protected', True)])
        self.assertNotIn(LocalAuthHandler.user['password'], json.dumps(result))
        self.assertNotIn(LocalAuthHandler.user['email'], json.dumps(result))
        self.assertNotIn('sid=abc123', json.dumps(result))

    def test_login_wrong_response_is_functional_failure(self):
        LocalAuthHandler.mismatch = True
        result = self.run_guest()
        self.assertEqual(result['results'][2]['result'], 'FAIL')
        self.assertEqual(result['results'][3]['result'], 'INCONCLUSIVE')
        self.assertEqual(result['summary']['failed'], 1)

    def test_untrusted_domain_cookie_denied(self):
        LocalAuthHandler.bad_cookie = True
        result = self.run_guest()
        self.assertEqual(result['results'][2]['issue_code'], 'SESSION_COOKIE_UNSAFE')
        self.assertFalse(result['session_cookie_observed'])
        self.assertEqual(result['results'][3]['result'], 'INCONCLUSIVE')

    def test_guest_report_tamper_denied(self):
        result = self.run_guest()
        result['results'][0]['http_status'] = 201
        with self.assertRaises(AuthReportError):
            validate_auth_report(self.compile()['guest'], result)

    def test_guest_report_secrets_denied(self):
        result = self.run_guest()
        result['results'][1]['password'] = 'leak'
        with self.assertRaises(AuthReportError):
            validate_auth_report(self.compile()['guest'], result)

    def test_bad_summary_denied(self):
        result = self.run_guest()
        result['summary']['passed'] = 100
        with self.assertRaises(AuthReportError):
            validate_auth_report(self.compile()['guest'], result)

    def test_wrong_cookie_claim_denied(self):
        result = self.run_guest()
        result['session_cookie_observed'] = False
        with self.assertRaises(AuthReportError):
            validate_auth_report(self.compile()['guest'], result)

    def test_source_mismatch_denied_by_guest(self):
        guest = self.compile()['guest']
        with self.assertRaises(GuestApiError):
            run_auth_flow(guest, expected_source={**SRC, 'commit_sha': 'b'*40},
                          expected_adapter='php_mysql_v1',
                          port=self.server.server_port)


class ControllerDenialTests(AuthFixture):
    """Runs only in the user's complete checkout; must stop BEFORE QEMU."""

    def controller(self):
        try:
            from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        except ImportError:
            self.skipTest('Full Phase 2 orchestrator not mounted in isolated workspace')
        return execute_vm_candidate

    def test_modified_approved_flow_denied_before_any_vm(self):
        execute = self.controller()
        forged = self.compile()
        forged['guest']['login']['path'] = '/unauthorized'
        with tempfile.TemporaryDirectory() as folder:
            report = execute(self.candidate, self.config, Path(folder) / 'vm.json',
                             reviewed_fixture=True, auth_bundle=forged)
        self.assertEqual(report['status'], 'DENIED')
        self.assertEqual(report['execution'], 'NOT_PERFORMED')
        self.assertEqual(report['reason'], 'PHASE4D2_AUTH_BUNDLE_NOT_OPERATOR_APPROVED')

    def test_multiple_guest_modes_denied_before_any_vm(self):
        execute = self.controller()
        with tempfile.TemporaryDirectory() as folder:
            report = execute(self.candidate, self.config, Path(folder) / 'vm.json',
                             reviewed_fixture=True, auth_bundle=self.compile(),
                             api_bundle={'unreviewed': True})
        self.assertEqual(report['status'], 'DENIED')
        self.assertEqual(report['reason'], 'PHASE4_MULTIPLE_BUNDLE_MODES_DENIED')
        self.assertEqual(report['cleanup'], 'NOT_STARTED')


class RunReportTests(AuthFixture):
    def setUp(self):
        super().setUp()
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

    def simulate_host_run(self, tamper=None):
        try:
            from projectiq_executioner.phase4.auth_vm_run import run_reviewed_auth
            from projectiq_executioner.phase2.vm_worker import orchestrator
        except ImportError:
            self.skipTest('Full Phase 2 orchestrator not mounted in isolated workspace')
        b = self.compile()['guest']
        guest_report = run_auth_flow(b, expected_source=SRC,
                                     expected_adapter='php_mysql_v1',
                                     port=self.server.server_port)
        if tamper:
            tamper(guest_report)
        vm = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
              'source': SRC, 'adapter': 'php_mysql_v1',
              'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
              'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'phase4d2_status': 'VALIDATED', 'phase4d2_auth_report': guest_report}
        with tempfile.TemporaryDirectory() as folder, patch.object(orchestrator,
                'execute_vm_candidate', return_value=vm):
            report = run_reviewed_auth(plan=self.plan, auth_catalog=self.auth,
                                       oracle_catalog=self.oracles, candidate=self.candidate,
                                       config=self.config, output_dir=Path(folder))
            content = (Path(folder) / 'auth_run.json').read_text()
            self.assertNotIn(LocalAuthHandler.user['password'], content)
            self.assertNotIn(LocalAuthHandler.user['email'], content)
            self.assertNotIn('sid=abc123', content)
            return report

    def test_real_guest_evidence_maps_to_pass(self):
        report = self.simulate_host_run()
        self.assertEqual(report['status'], 'TESTS_PASSED')
        self.assertEqual(report['summary']['passed'], 4)

    def test_forged_guest_evidence_not_passed(self):
        report = self.simulate_host_run(lambda r: r.update({'token': 'leak'}))
        self.assertEqual(report['status'], 'GUEST_RESULTS_NOT_VERIFIED')

    def test_vm_cleanup_failure_overrides_success(self):
        try:
            from projectiq_executioner.phase4.auth_vm_run import run_reviewed_auth
            from projectiq_executioner.phase2.vm_worker import orchestrator
        except ImportError:
            self.skipTest('Full Phase 2 orchestrator not mounted in isolated workspace')
        vm = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
              'cleanup': 'VM_STOP_FAILED'}
        with tempfile.TemporaryDirectory() as folder, patch.object(orchestrator,
                'execute_vm_candidate', return_value=vm):
            report = run_reviewed_auth(plan=self.plan, auth_catalog=self.auth,
                                       oracle_catalog=self.oracles, candidate=self.candidate,
                                       config=self.config, output_dir=Path(folder))
        self.assertEqual(report['status'], 'CLEANUP_FAILED')


if __name__ == '__main__':
    unittest.main()
