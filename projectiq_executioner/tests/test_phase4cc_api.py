"""4C-C: operator review, guest API sandbox and report validation (no QEMU)."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.tests.test_phase4c_oracle_contracts import (
    CANDIDATE, APPROVAL, plan, catalog, make_case,
)
from projectiq_executioner.phase4.oracle_contracts import digest, validate_oracle_catalog, OracleContractError
from projectiq_executioner.phase4.reviewed_api import compile_reviewed_api_bundle, ReviewedApiError
from projectiq_executioner.phase4.guest_api import (
    validate_api_bundle, GuestApiError, run_api_tests, _json_subset,
)
from projectiq_executioner.phase4.api_report_contracts import validate_api_report, ApiReportError
from projectiq_executioner.phase4.api_vm_run import run_reviewed_api, CLEAN


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/tasks':
            self.reply(200, {'ok': True, 'count': 2, 'extra': 'kept'})
        elif self.path == '/no-content':
            self.send_response(204)
            self.end_headers()
        elif self.path == '/bad-json':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{not json')
        elif self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://example.com/')
            self.end_headers()
        elif self.path == '/huge':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'x' * (1_048_576 + 8))
        else:
            self.reply(404, {'error': 'not found'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length)
        if self.path == '/api/tasks':
            self.reply(201, {'created': True, 'input': json.loads(data)})
        elif self.path == '/api/login':
            self.reply(200, {'ok': True, 'bodytype': self.headers.get('Content-Type'),
                             'body': data.decode('utf-8')})
        else:
            self.reply(404, {'error': 'not found'})

    def reply(self, code, body):
        raw = json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


class ApiFixture(unittest.TestCase):
    def setUp(self):
        self.p = plan()
        self.cat = catalog(self.p, make_case(self.p, body={'title': 'safe'}, subset={'created': True}))
        self.cfg = {**APPROVAL,
                    'approved_oracle_catalogs': [
                        self.p['source']['repository'] + '@' + self.p['source']['commit_sha']
                        + '#' + digest(self.cat)]}

    def bundle(self):
        return compile_reviewed_api_bundle(self.p, self.cat, CANDIDATE, self.cfg)

    def blocked(self, code, cfg=None):
        with self.assertRaisesRegex(ReviewedApiError, '^' + code + '$'):
            compile_reviewed_api_bundle(self.p, self.cat, CANDIDATE, self.cfg if cfg is None else cfg)


class ApprovalTests(ApiFixture):
    def test_compiles_only_exact_approved_catalog(self):
        b = self.bundle()
        self.assertEqual(b['tests'][0]['method'], 'POST')
        self.assertEqual(b['tests'][0]['body_kind'], 'JSON')
        self.assertEqual(b['tests'][0]['body'], {'title': 'safe'})
        self.assertEqual([x['kind'] for x in b['tests'][0]['assertions']],
                         ['HTTP_STATUS_EQUALS', 'JSON_BODY_SUBSET_EQUALS'])

    def test_rejects_no_oracle_approval(self):
        self.blocked('MISSING_OR_INVALID_ORACLE_APPROVAL_LIST', APPROVAL)

    def test_rejects_wrong_digest(self):
        self.blocked('ORACLE_CATALOG_NOT_OPERATOR_APPROVED',
                     {**APPROVAL, 'approved_oracle_catalogs': ['Fixture/Oracle@' + 'a'*40 + '#' + 'b'*64]})

    def test_rejects_unapproved_sha(self):
        self.blocked('PINNED_SOURCE_NOT_APPROVED',
                     {**self.cfg, 'approved_snapshots': []})

    def test_rejects_candidate_sha_mismatch(self):
        wrong = {**CANDIDATE, 'source': {**CANDIDATE['source'], 'commit_sha': 'b'*40}}
        with self.assertRaisesRegex(ReviewedApiError, 'INVALID_ORACLE_STAGING'):
            compile_reviewed_api_bundle(self.p, self.cat, wrong, self.cfg)

    def test_rejects_changed_inputs_until_reapproved(self):
        self.cat['cases'][0]['request']['json_body']['title'] = 'new'
        self.blocked('ORACLE_CATALOG_NOT_OPERATOR_APPROVED')

    def test_rejects_changed_expected_response_until_reapproved(self):
        self.cat['cases'][0]['expected']['status'] = 422
        self.blocked('ORACLE_CATALOG_NOT_OPERATOR_APPROVED')

    def test_rejects_symbolic_fixture_without_secret_provisioning(self):
        self.cat['cases'][0]['request']['json_body']['password'] = {'$fixture_ref': 'account_password'}
        self.cfg['approved_oracle_catalogs'] = [self.p['source']['repository'] + '@'
             + self.p['source']['commit_sha'] + '#' + digest(self.cat)]
        self.blocked('RUNTIME_FIXTURE_REFERENCES_REQUIRE_4CD')

    def test_rejects_extra_approval_policy_bloat(self):
        self.blocked('MISSING_OR_INVALID_ORACLE_APPROVAL_LIST',
                     {**APPROVAL, 'approved_oracle_catalogs': ['X'] * 257})

    def test_form_body_is_reviewable(self):
        request = self.cat['cases'][0]['request']
        request['json_body'] = None
        request['form_body'] = {'name': 'Demo', 'role': 'guest'}
        self.cfg['approved_oracle_catalogs'] = [self.p['source']['repository'] + '@'
             + self.p['source']['commit_sha'] + '#' + digest(self.cat)]
        b = self.bundle()
        self.assertEqual(b['tests'][0]['body_kind'], 'FORM')
        self.assertEqual(b['tests'][0]['body']['name'], 'Demo')

    def test_form_and_json_cannot_both_be_sent(self):
        self.cat['cases'][0]['request']['form_body'] = {'name': 'Demo'}
        with self.assertRaises(OracleContractError):
            validate_oracle_catalog(self.cat, self.p)

    def test_form_sensitive_value_requires_fixture(self):
        self.cat['cases'][0]['request']['form_body'] = {'password': 'secret'}
        self.cat['cases'][0]['request']['json_body'] = None
        with self.assertRaises(OracleContractError):
            validate_oracle_catalog(self.cat, self.p)

    def test_form_sensitive_fixture_staged_but_not_executable(self):
        self.cat['cases'][0]['request']['form_body'] = {'password': {'$fixture_ref': 'p'}}
        self.cat['cases'][0]['request']['json_body'] = None
        self.cfg['approved_oracle_catalogs'] = [self.p['source']['repository'] + '@'
             + self.p['source']['commit_sha'] + '#' + digest(self.cat)]
        self.blocked('RUNTIME_FIXTURE_REFERENCES_REQUIRE_4CD')

    def test_prior_json_only_schema_unchanged(self):
        self.assertNotIn('form_body', self.cat['cases'][0]['request'])
        self.assertIsNone(validate_oracle_catalog(self.cat, self.p)[0]['request']['form_body'])


class BundleTests(ApiFixture):
    def reject(self, mutate):
        b = self.bundle()
        mutate(b)
        with self.assertRaises(GuestApiError):
            validate_api_bundle(b)

    def test_reject_unknown_extra_instruction(self):
        self.reject(lambda b: b['tests'][0].update({'command': 'bash'}))

    def test_reject_host_override(self):
        self.reject(lambda b: b['tests'][0].update({'url': 'https://bad.test'}))

    def test_reject_method_override(self):
        self.reject(lambda b: b['tests'][0].update({'method': 'CONNECT'}))

    def test_reject_duplicate_ids(self):
        def dup(b): b['tests'].append(deepcopy(b['tests'][0]))
        self.reject(dup)

    def test_reject_missing_status_assertion(self):
        self.reject(lambda b: b['tests'][0].update({'assertions': [b['tests'][0]['assertions'][1]]}))

    def test_reject_open_redirect(self):
        self.reject(lambda b: b['tests'][0].update({'path': '//evil/'}))

    def test_reject_route_traversal(self):
        self.reject(lambda b: b['tests'][0].update({'path': '/../secret'}))

    def test_reject_percent_path(self):
        self.reject(lambda b: b['tests'][0].update({'path': '/%2e%2e/secret'}))

    def test_reject_arbitrary_headers(self):
        self.reject(lambda b: b['tests'][0].update({'headers': {'Authorization': 'x'}}))

    def test_reject_password_literals_even_direct_guest_bundle(self):
        self.reject(lambda b: b['tests'][0].update({'body': {'password': 'xyz'}}))

    def test_reject_credentials_in_form(self):
        def alter(b):
            b['tests'][0].update({'body_kind': 'FORM', 'body': {'password': 'abc'}})
        self.reject(alter)

    def test_reject_mismatched_source(self):
        b = self.bundle()
        with self.assertRaises(GuestApiError):
            validate_api_bundle(b, expected_source={**b['source'], 'commit_sha': 'b'*40})

    def test_reject_mismatched_adapter(self):
        b = self.bundle()
        with self.assertRaises(GuestApiError):
            validate_api_bundle(b, expected_adapter='node_express_offline_v1')

    def test_reject_body_on_get(self):
        self.reject(lambda b: b['tests'][0].update({'method': 'GET'}))

    def test_no_response_substring_infer(self):
        self.reject(lambda b: b['tests'][0]['assertions'].append({'kind': 'TEXT_CONTAINS', 'expected': 'hi'}))

    def test_strict_json_subset(self):
        self.assertTrue(_json_subset({'a': {'b': 1}, 'more': 2}, {'a': {'b': 1}}))
        self.assertFalse(_json_subset({'a': True}, {'a': 1}))
        self.assertFalse(_json_subset({'a': [1, 2, 3]}, {'a': [1, 2]}))


class RunAgainstTrustedHTTPFixture(ApiFixture):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def run_bundle(self, bundle):
        return run_api_tests(bundle, expected_source=bundle['source'],
                             expected_adapter=bundle['adapter'], port=self.server.server_port)

    def test_json_post_observed_response(self):
        b = self.bundle()
        result = self.run_bundle(b)
        self.assertEqual(result['summary']['passed'], 1)
        self.assertEqual(result['results'][0]['http_status'], 201)
        validate_api_report(b, result)

    def test_expected_mismatch_is_functional_fail(self):
        b = self.bundle()
        b['tests'][0]['assertions'][0]['expected'] = 422
        result = self.run_bundle(b)
        self.assertEqual(result['summary']['failed'], 1)
        validate_api_report(b, result)

    def test_form_post_encoded_and_compared(self):
        b = self.bundle()
        b['tests'][0]['method'] = 'POST'
        b['tests'][0]['path'] = '/api/login'
        b['tests'][0]['body_kind'] = 'FORM'
        b['tests'][0]['body'] = {'email': 'dummy@example.test', 'name': 'guest'}
        b['tests'][0]['assertions'] = [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
            {'kind': 'JSON_BODY_SUBSET_EQUALS', 'expected': {
                'bodytype': 'application/x-www-form-urlencoded',
                'body': 'email=dummy%40example.test&name=guest',
            }},
        ]
        result = self.run_bundle(b)
        self.assertEqual(result['summary']['passed'], 1)
        validate_api_report(b, result)

    def test_response_bad_json_fails_subset(self):
        b = self.bundle()
        b['tests'][0].update({'method': 'GET', 'path': '/bad-json', 'body_kind': 'NONE', 'body': None})
        b['tests'][0]['assertions'][0]['expected'] = 200
        result = self.run_bundle(b)
        self.assertEqual(result['summary']['failed'], 1)
        validate_api_report(b, result)

    def test_redirect_not_followed(self):
        b = self.bundle()
        b['tests'][0].update({'method': 'GET', 'path': '/redirect', 'body_kind': 'NONE', 'body': None})
        result = self.run_bundle(b)
        self.assertEqual(result['results'][0]['http_status'], 302)
        self.assertEqual(result['summary']['failed'], 1)

    def test_body_limit_is_inconclusive(self):
        b = self.bundle()
        b['tests'][0].update({'method': 'GET', 'path': '/huge', 'body_kind': 'NONE', 'body': None})
        result = self.run_bundle(b)
        self.assertEqual(result['summary']['inconclusive'], 1)
        validate_api_report(b, result)

    def test_connect_refused_inconclusive(self):
        b = self.bundle()
        result = run_api_tests(b, expected_source=b['source'],
                               expected_adapter=b['adapter'], port=1, timeout_s=0.1)
        self.assertEqual(result['summary']['inconclusive'], 1)
        validate_api_report(b, result)

    def test_no_body_text_leaks_to_result(self):
        b = self.bundle()
        result = self.run_bundle(b)
        self.assertNotIn('safe', json.dumps(result))


class ForgedReportTests(RunAgainstTrustedHTTPFixture):
    def test_reject_forged_status(self):
        b = self.bundle(); r = self.run_bundle(b)
        r['results'][0]['http_status'] = 200
        with self.assertRaises(ApiReportError): validate_api_report(b, r)

    def test_reject_duplicated_results(self):
        b = self.bundle(); r = self.run_bundle(b)
        r['results'].append(deepcopy(r['results'][0]))
        with self.assertRaises(ApiReportError): validate_api_report(b, r)

    def test_reject_forged_verdict(self):
        b = self.bundle(); r = self.run_bundle(b)
        r['results'][0]['result'] = 'FAIL'
        with self.assertRaises(ApiReportError): validate_api_report(b, r)

    def test_reject_fake_attestation(self):
        b = self.bundle(); r = self.run_bundle(b)
        r['runtime_attestation'] = 'ATTESTED_FOR_UNTRUSTED_STUDENTS'
        with self.assertRaises(ApiReportError): validate_api_report(b, r)


class OrchestrationTests(ApiFixture):
    def call(self, *, mode='PASS'):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'results'
            def vm(candidate, config, path, *, reviewed_fixture, work_root, api_bundle):
                self.assertTrue(reviewed_fixture)
                self.assertEqual(api_bundle['source'], CANDIDATE['source'])
                report = run_api_tests(api_bundle, expected_source=CANDIDATE['source'],
                                       expected_adapter=CANDIDATE['adapter'], port=1, timeout_s=0.1)
                if mode != 'INCONCLUSIVE':
                    report['results'][0].update({'http_status': 201, 'body_sha256': sha256(b'ok').hexdigest(),
                                                 'issue_code': None, 'result': 'PASS',
                                                 'assertions': [{'kind': 'HTTP_STATUS_EQUALS', 'result': 'PASS'},
                                                                {'kind': 'JSON_BODY_SUBSET_EQUALS', 'result': 'PASS'}]})
                    report['status'] = 'COMPLETE'
                    report['summary'] = {'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0}
                if mode == 'FAIL':
                    report['results'][0]['http_status'] = 422
                    report['results'][0]['result'] = 'FAIL'
                    report['results'][0]['assertions'][0]['result'] = 'FAIL'
                    report['summary'] = {'total': 1, 'passed': 0, 'failed': 1, 'inconclusive': 0}
                result = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
                          'cleanup': CLEAN, 'source': dict(CANDIDATE['source']),
                          'adapter': CANDIDATE['adapter'], 'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
                          'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
                          'phase4c_status': 'VALIDATED', 'phase4c_test_results': report}
                if mode == 'CLEANUP_FAILED': result['cleanup'] = 'CLEANUP_FAILED'
                if mode == 'BAD_GUEST': del result['phase4c_test_results']
                path.write_text(json.dumps(result), 'utf-8')
                return result
            module = types.ModuleType('projectiq_executioner.phase2.vm_worker.orchestrator')
            module.execute_vm_candidate = vm
            with patch.dict(sys.modules, {module.__name__: module}):
                r = run_reviewed_api(plan=self.p, catalog=self.cat, candidate=CANDIDATE,
                                     config=self.cfg, output_dir=output)
            self.assertTrue((output/'api_review.json').exists())
            self.assertTrue((output/'api_run.json').exists())
            self.assertNotIn('safe', (output/'api_review.json').read_text('utf-8'))
            return r

    def test_success(self):
        self.assertEqual(self.call()['status'], 'TESTS_PASSED')

    def test_failure(self):
        self.assertEqual(self.call(mode='FAIL')['status'], 'TESTS_FAILED')

    def test_inconclusive(self):
        self.assertEqual(self.call(mode='INCONCLUSIVE')['status'], 'TESTS_INCONCLUSIVE')

    def test_cleanup_failure_overrides(self):
        self.assertEqual(self.call(mode='CLEANUP_FAILED')['status'], 'CLEANUP_FAILED')

    def test_missing_guest_report_fails_closed(self):
        self.assertEqual(self.call(mode='BAD_GUEST')['status'], 'GUEST_RESULTS_NOT_VERIFIED')

    def test_unapproved_catalog_never_invokes_vm(self):
        with tempfile.TemporaryDirectory() as temp:
            def fail(*a, **kw):
                raise AssertionError('VM started for unapproved catalog')
            module = types.ModuleType('projectiq_executioner.phase2.vm_worker.orchestrator')
            module.execute_vm_candidate = fail
            with patch.dict(sys.modules, {module.__name__: module}):
                with self.assertRaises(ReviewedApiError):
                    run_reviewed_api(plan=self.p, catalog=self.cat, candidate=CANDIDATE,
                                     config=APPROVAL, output_dir=Path(temp))


class Phase2IntegrationWiringTests(unittest.TestCase):
    """Audits changed controller integration points without launching a VM."""
    def test_guest_dispatch_precedes_server_shutdown(self):
        source = (Path(__file__).parents[1] / 'phase2' / 'vm_worker' / 'guest_agent.py').read_text('utf-8')
        if 'def run_fullstack(' not in source:
            self.skipTest('Full Phase 2 guest source not available in test workspace')
        start = source.index('def run(request_path:')
        normal = source[start:source.index('def run_fullstack(', start)]
        self.assertLess(normal.index('_run_phase4c_on_live_app(req, port)'), normal.index('server.terminate()'))
        fullstack = source[source.index('def run_fullstack('):source.index('def main()', source.index('def run_fullstack('))]
        self.assertLess(fullstack.index('_run_phase4c_on_live_app(req, port)'), fullstack.index('_d4_stop_process(proc)'))

    def test_host_bundle_before_vm_boot_and_independent_of_4b(self):
        source = (Path(__file__).parents[1] / 'phase2' / 'vm_worker' / 'orchestrator.py').read_text('utf-8')
        self.assertIn('test_bundle: dict | None = None, api_bundle: dict | None = None', source)
        if 'verify_host_toolchain(config)' not in source:
            self.skipTest('Full Phase 2 orchestrator source not available in test workspace')
        self.assertLess(source.index('validate_api_bundle(api_bundle'), source.index('verify_host_toolchain(config)'))
        self.assertIn("result['phase4c_status'] = 'VALIDATED'", source)
        self.assertIn("result['phase4_status'] = 'VALIDATED'", source)

    def test_guest_api_copy_still_uses_strict_ssh_allowlist(self):
        qemu = Path(__file__).parents[1] / 'phase2' / 'vm_worker' / 'qemu.py'
        if not qemu.exists():
            self.skipTest('Full Phase 2 QEMU source not mounted in isolated test workspace')
        source = qemu.read_text('utf-8')
        self.assertIn("'guest_api.py'", source)
        self.assertIn("raise WorkerFailure('Unapproved destination filename')", source)
        self.assertIn("'guest_http.py'", source)



class CliPreflightTests(ApiFixture):
    def setup_files(self, root):
        root = Path(root)
        files = {'plan': self.p, 'oracles': self.cat, 'candidate': CANDIDATE}
        for name, value in files.items():
            (root / (name + '.json')).write_text(json.dumps(value), 'utf-8')
        (root / 'config.json').write_text(json.dumps(self.cfg), 'utf-8')

    def execute(self, root, *extra):
        from projectiq_executioner.phase4.api_vm_run import main
        root = Path(root)
        module = types.ModuleType('projectiq_executioner.phase2.vm_worker.orchestrator')
        module.load_config = lambda path: json.loads(Path(path).read_text('utf-8'))
        def no_vm(*args, **kwargs):
            raise AssertionError('Preflight launched a VM')
        module.execute_vm_candidate = no_vm
        args = ['--plan', str(root / 'plan.json'), '--oracles', str(root / 'oracles.json'),
                '--candidate', str(root / 'candidate.json'), '--vm-config', str(root / 'config.json'),
                '--out', str(root / 'preflight'), *extra]
        with patch.dict(sys.modules, {module.__name__: module}):
            return main(args)

    def test_preflight_never_starts_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_files(tmp)
            self.assertEqual(self.execute(tmp, '--preflight-only'), 0)
            report = json.loads((Path(tmp) / 'preflight' / 'api_preflight.json').read_text('utf-8'))
            self.assertEqual(report['status'], 'APPROVED_NOT_EXECUTED')
            self.assertEqual(len(report['tests']), 1)
            self.assertNotIn('title', json.dumps(report))

    def test_preflight_requires_oracle_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_files(tmp)
            cfg = {**APPROVAL, 'approved_oracle_catalogs': []}
            (Path(tmp) / 'config.json').write_text(json.dumps(cfg), 'utf-8')
            self.assertEqual(self.execute(tmp, '--preflight-only'), 2)
            self.assertFalse((Path(tmp) / 'preflight' / 'api_preflight.json').exists())

    def test_preflight_cannot_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_files(tmp)
            self.assertEqual(self.execute(tmp, '--preflight-only'), 0)
            with self.assertRaises(SystemExit):
                self.execute(tmp, '--preflight-only')

    def test_preflight_and_vm_mode_cannot_be_combined(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_files(tmp)
            with self.assertRaises(SystemExit):
                self.execute(tmp, '--preflight-only', '--confirm-reviewed-api-execution')


if __name__ == '__main__':
    unittest.main()
