"""Phase 4A: no QEMU, no student code. Uses local trusted HTTP fixtures only."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.phase4.handoff import (
    Phase4HandoffError, load_bounded_json, prepare_handoff, save_handoff,
)
from projectiq_executioner.phase4.guest_http import (
    GuestTestError, MAX_TESTS, run_guest_tests, validate_guest_bundle,
)
from projectiq_executioner.phase4.__main__ import main
from projectiq_executioner.phase4.report_contracts import GuestReportError, validate_guest_report

SHA = 'a' * 40
SOURCE = {'kind': 'github', 'repository': 'Fixture/Phase4', 'commit_sha': SHA}
CANDIDATE = {'schema_version': '0.3', 'stage': 'vm_execution_candidate',
             'worker_provider': 'qemu_qcow2_v1',
             'launch_authorization': 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES',
             'source': SOURCE, 'adapter': 'static_html_v1',
             'status': 'CANDIDATE_NEEDS_VM_VERIFICATION', 'probe_path': '/index.html'}
CONFIG = {'approved_snapshots': ['Fixture/Phase4@' + SHA]}


def manifest(*, n_pages: int = 1):
    pages = ['index.html'] + [f'page{i}.html' for i in range(1, n_pages)]
    return {
        'schema_version': '1.0', 'phase': 1, 'source': SOURCE,
        'pages': pages, 'routes': [], 'services': [], 'technologies': [],
        'features': [], 'proposed_test_targets': [], 'entrypoints': [],
        'tests': [], 'database_schema_files': [], 'inventory': {},
    }


def make_plan(n_pages=1):
    return generate_test_plan(manifest(n_pages=n_pages), CANDIDATE)


class HandoffTests(unittest.TestCase):
    def test_approved_handoff_never_executes(self):
        plan = make_plan()
        handoff = prepare_handoff(plan, CANDIDATE, CONFIG)
        self.assertEqual(handoff['status'], 'STAGED_NOT_EXECUTED')
        self.assertEqual(handoff['execution'], 'NOT_PERFORMED')
        self.assertEqual(handoff['summary']['selected'], 1)
        self.assertEqual(handoff['test_bundle']['tests'][0]['path'], '/index.html')
        self.assertEqual(handoff['source'], SOURCE)
        self.assertNotIn('base_image', json.dumps(handoff))

    def test_unapproved_snapshot_does_not_become_runnable(self):
        report = prepare_handoff(make_plan(), CANDIDATE, {'approved_snapshots': []})
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['issues'][0]['code'], 'PINNED_SHA_NOT_OPERATOR_APPROVED')
        self.assertEqual(report['summary']['selected'], 0)
        self.assertEqual(report['summary']['excluded'], 1)
        self.assertIsNone(report['test_bundle'])

    def test_missing_config_fails_closed(self):
        self.assertEqual(prepare_handoff(make_plan(), CANDIDATE)['issues'][0]['code'],
                         'OPERATOR_VM_CONFIG_REQUIRED')

    def test_wrong_source_fails_closed(self):
        candidate = deepcopy(CANDIDATE)
        candidate['source']['commit_sha'] = 'b' * 40
        report = prepare_handoff(make_plan(), candidate, CONFIG)
        self.assertEqual(report['issues'][0]['code'], 'SOURCE_IDENTITY_MISMATCH')

    def test_forged_candidate_without_phase2_provenance_is_blocked(self):
        candidate = {'source': SOURCE, 'adapter': 'static_html_v1',
                     'status': 'CANDIDATE_NEEDS_VM_VERIFICATION', 'probe_path': '/index.html'}
        report = prepare_handoff(make_plan(), candidate, CONFIG)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['issues'][0]['code'], 'SUPPORTED_PHASE2_ADAPTER_REQUIRED')

    def test_wrong_adapter_fails_closed(self):
        candidate = deepcopy(CANDIDATE)
        candidate['adapter'] = 'python_web_offline_v1'
        report = prepare_handoff(make_plan(), candidate, CONFIG)
        self.assertEqual(report['issues'][0]['code'], 'SUPPORTED_PHASE2_ADAPTER_REQUIRED')

    def test_wrong_readiness_probe_fails_closed(self):
        candidate = deepcopy(CANDIDATE)
        candidate['probe_path'] = '/health'
        report = prepare_handoff(make_plan(), candidate, CONFIG)
        self.assertEqual(report['issues'][0]['code'], 'READINESS_PROBE_MISMATCH')

    def test_malformed_phase3_plan_rejected(self):
        plan = make_plan()
        plan['test_cases'][0]['assertions'][0]['expected'] = 201
        with self.assertRaisesRegex(Phase4HandoffError, 'INVALID_PHASE3_TEST_PLAN'):
            prepare_handoff(plan, CANDIDATE, CONFIG)

    def test_cap_and_stable_selection(self):
        plan = make_plan(MAX_TESTS + 4)
        handoff = prepare_handoff(plan, CANDIDATE, CONFIG)
        self.assertEqual(handoff['status'], 'STAGED_NOT_EXECUTED')
        self.assertEqual(len(handoff['test_bundle']['tests']), MAX_TESTS)
        self.assertEqual(handoff['summary']['capacity_limited'], 4)
        self.assertEqual(handoff['summary']['excluded'], 4)
        self.assertEqual(prepare_handoff(plan, CANDIDATE, CONFIG), handoff)

    def test_no_unresolved_candidate_becomes_executable(self):
        data = manifest()
        data['routes'] = [{'path': '/users', 'method': 'POST',
                           'kind': 'declared_http_route', 'evidence': []}]
        plan = generate_test_plan(data, CANDIDATE)
        report = prepare_handoff(plan, CANDIDATE, CONFIG)
        self.assertEqual(report['summary']['selected'], 1)
        self.assertEqual(report['summary']['needs_oracle'], 1)
        self.assertEqual(report['summary']['excluded'], 1)

    def test_no_runnable_test_returns_no_test_status(self):
        data = manifest()
        plan = generate_test_plan(data)  # no Phase 2 candidate: no runnable oracle
        # Build a matching candidate but Phase 3 explicitly doesn't claim it.
        report = prepare_handoff(plan, CANDIDATE, CONFIG)
        self.assertEqual(report['status'], 'NO_RUNNABLE_TESTS')
        self.assertEqual(report['summary']['selected'], 0)

    def test_write_once_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'plan.json').write_text(json.dumps(make_plan()), encoding='utf-8')
            (base/'candidate.json').write_text(json.dumps(CANDIDATE), encoding='utf-8')
            (base/'vm_config.json').write_text(json.dumps(CONFIG), encoding='utf-8')
            args = ['--plan', str(base/'plan.json'), '--candidate', str(base/'candidate.json'),
                    '--vm-config', str(base/'vm_config.json'), '--out', str(base/'out')]
            self.assertEqual(main(args), 0)
            report = json.loads((base/'out'/'phase4_handoff.json').read_text())
            self.assertEqual(report['status'], 'STAGED_NOT_EXECUTED')
            # Never overwrite old evidence, even if contents match.
            with self.assertRaises(SystemExit):
                main(args)

    def test_cli_invalid_plan_produces_fixed_schema_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'plan.json').write_text('{broken', encoding='utf-8')
            (base/'candidate.json').write_text(json.dumps(CANDIDATE), encoding='utf-8')
            (base/'vm_config.json').write_text(json.dumps(CONFIG), encoding='utf-8')
            args = ['--plan', str(base/'plan.json'), '--candidate', str(base/'candidate.json'),
                    '--vm-config', str(base/'vm_config.json'), '--out', str(base/'out')]
            self.assertEqual(main(args), 2)
            report = json.loads((base/'out'/'phase4_handoff.json').read_text())
            self.assertEqual(report['status'], 'INPUT_ERROR')
            self.assertEqual(report['execution'], 'NOT_PERFORMED')
            self.assertIsNone(report['test_bundle'])

    def test_bounded_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bad.json'
            path.write_text('[]')
            with self.assertRaises(Phase4HandoffError):
                load_bounded_json(path, 200)
            path.write_text('x' * 300)
            with self.assertRaises(Phase4HandoffError):
                load_bounded_json(path, 200)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/index.html':
            status, body = 200, b'<html>fixture</html>'
        elif self.path == '/not_found':
            status, body = 404, b'not found'
        elif self.path == '/redirect':
            status, body = 302, b'not following redirects'
        elif self.path == '/large':
            status, body = 200, b'z' * (1_048_576 + 20)
        else:
            status, body = 500, b'unknown'
        self.send_response(status)
        if status == 302:
            self.send_header('Location', 'http://example.com/')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except ConnectionResetError:
            pass

    def log_message(self, fmt, *args):
        pass


def bundle(*cases):
    tests = []
    for i, (path, assertions) in enumerate(cases):
        tests.append({'id': f'TC-{i+1:016X}', 'method': 'GET', 'path': path,
                      'assertions': assertions})
    return {'schema_version': '0.1', 'source': SOURCE, 'adapter': 'static_html_v1',
            'tests': tests}


class GuestHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def call(self, b):
        return run_guest_tests(b, expected_source=SOURCE, expected_adapter='static_html_v1',
                               port=self.httpd.server_port)

    def test_http_200_pass(self):
        r = self.call(bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}])))
        self.assertEqual(r['results'][0]['result'], 'PASS')
        self.assertEqual(r['summary']['passed'], 1)
        self.assertEqual(r['execution'], 'LOOPBACK_HTTP_REQUESTS_PERFORMED')
        self.assertEqual(r['runtime_attestation'], 'NOT_EVALUATED_BY_HTTP_TEST_INTERPRETER')

    def test_status_mismatch_fails(self):
        r = self.call(bundle(('/not_found', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}])))
        self.assertEqual(r['results'][0]['result'], 'FAIL')
        self.assertEqual(r['results'][0]['http_status'], 404)

    def test_content_hash_passes_without_returning_body(self):
        digest = sha256(b'<html>fixture</html>').hexdigest()
        r = self.call(bundle(('/index.html', [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
            {'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': digest},
        ])))
        self.assertEqual(r['results'][0]['result'], 'PASS')
        self.assertNotIn('fixture', json.dumps(r))

    def test_content_hash_mismatch_fails(self):
        r = self.call(bundle(('/index.html', [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
            {'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': 'f'*64},
        ])))
        self.assertEqual(r['summary']['failed'], 1)

    def test_redirect_not_followed(self):
        r = self.call(bundle(('/redirect', [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
        ])))
        self.assertEqual(r['results'][0]['result'], 'FAIL')
        self.assertEqual(r['results'][0]['http_status'], 302)

    def test_response_body_limit_is_inconclusive(self):
        r = self.call(bundle(('/large', [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
        ])))
        self.assertEqual(r['results'][0]['result'], 'INCONCLUSIVE')
        self.assertEqual(r['results'][0]['issue_code'], 'HTTP_BODY_LIMIT_EXCEEDED')

    def test_host_accepts_valid_guest_evidence(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        r = self.call(b)
        validate_guest_report(b, r)

    def test_host_rejects_spoofed_pass_and_missing_ids(self):
        b = bundle(('/not_found', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        r = self.call(b)
        r['results'][0]['result'] = 'PASS'
        r['results'][0]['assertions'][0]['result'] = 'PASS'
        r['summary'] = {'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0}
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)
        r = self.call(b)
        r['results'] = []
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)

    def test_host_rejects_incorrect_summary_and_malformed_data(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        r = self.call(b)
        r['summary']['passed'] = 999
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)
        r = self.call(b)
        r['results'][0]['raw_http_body'] = 'must-not-be-copied'
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)

    def test_reject_arbitrary_methods_urls_and_paths(self):
        for path in ('//evil', '/foo/../bar', '/foo%2e%2e', '/a?b=1', '/:id', 'http://example.org/',
                     '/x#frag', '/x\\y'):
            with self.subTest(path=path), self.assertRaises(GuestTestError):
                validate_guest_bundle(bundle((path, [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}])))
        bad = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        bad['tests'][0]['method'] = 'POST'
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(bad)

    def test_guest_body_hash_evidence_cannot_claim_false_pass(self):
        digest = sha256(b'<html>fixture</html>').hexdigest()
        b = bundle(('/index.html', [
            {'kind': 'HTTP_STATUS_EQUALS', 'expected': 200},
            {'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': digest},
        ]))
        r = self.call(b)
        validate_guest_report(b, r)
        r['results'][0]['body_sha256'] = 'f' * 64
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)

    def test_host_rejects_unexpected_guest_report_fields(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        r = self.call(b)
        r['server_output'] = 'untrusted information'
        with self.assertRaises(GuestReportError):
            validate_guest_report(b, r)

    def test_reject_unknown_fields_even_if_current_runner_ignores_them(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        b['tests'][0]['headers'] = {'Authorization': 'secret'}
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)
        del b['tests'][0]['headers']
        b['url'] = 'https://example.com/'
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)
        del b['url']
        b['tests'][0]['assertions'][0]['command'] = 'echo bad'
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)

    def test_reject_source_mismatch(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b, expected_source={'kind': 'github', 'repository': 'Other/App',
                                                     'commit_sha': SHA})

    def test_hash_oracle_requires_success_status_oracle(self):
        b = bundle(('/index.html', [
            {'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': 'f'*64},
        ]))
        with self.assertRaisesRegex(GuestTestError, 'BODY_HASH_REQUIRES'):
            validate_guest_bundle(b)

    def test_reject_duplicate_route_and_id(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]),
                   ('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 200}]))
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)

    def test_reject_invalid_values(self):
        b = bundle(('/index.html', [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 201}]))
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)
        b = bundle(('/index.html', [{'kind': 'PYTHON_SCRIPT', 'expected': 'rm -rf /'}]))
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)
        b = bundle(('/index.html', [{'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': 'bad'}]))
        with self.assertRaises(GuestTestError):
            validate_guest_bundle(b)


if __name__ == '__main__':
    unittest.main()
