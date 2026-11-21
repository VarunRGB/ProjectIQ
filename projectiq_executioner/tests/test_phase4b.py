"""Phase 4B host handoff/VM status acceptance; no real student code or QEMU."""
from __future__ import annotations

from copy import deepcopy
import ast
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4.vm_run import (CLEAN, Phase4RunError,
                                                   run_reviewed_tests)
from projectiq_executioner.phase4.handoff import prepare_handoff
from projectiq_executioner.phase4.guest_http import run_guest_tests
from projectiq_executioner.tests.test_phase4a import CANDIDATE, CONFIG, SOURCE, make_plan, manifest
from projectiq_executioner.phase3.generator import generate_test_plan

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def fake_report(bundle, *, outcome='PASS'):
    tests = []
    for spec in bundle['tests']:
        observed = 200 if outcome != 'FAIL' else 404
        body_hash = sha256(b'fixture').hexdigest()
        checks = [{'kind': item['kind'], 'result': (
            'PASS' if item['kind'] == 'HTTP_STATUS_EQUALS' and observed == 200 else
            'PASS' if item['kind'] == 'HTTP_BODY_SHA256_EQUALS' and body_hash == item['expected'] else
            'FAIL')}
            for item in spec['assertions']]
        tests.append({'test_id': spec['id'], 'result': 'PASS' if outcome == 'PASS' else 'FAIL',
                      'http_status': observed, 'body_sha256': body_hash,
                      'assertions': checks, 'issue_code': None})
    if outcome == 'INCONCLUSIVE':
        tests = [{**v, 'result': 'INCONCLUSIVE', 'assertions': [],
                  'body_sha256': None, 'issue_code': 'LOOPBACK_HTTP_UNAVAILABLE',
                  'http_status': None} for v in tests]
    return {
        'schema_version': '0.1', 'phase': 4, 'stage': 'trusted_guest_http_tests',
        'execution': 'LOOPBACK_HTTP_REQUESTS_PERFORMED',
        'runtime_attestation': 'NOT_EVALUATED_BY_HTTP_TEST_INTERPRETER',
        'source': dict(bundle['source']), 'adapter': bundle['adapter'],
        'results': tests, 'status': 'INCOMPLETE' if outcome == 'INCONCLUSIVE' else 'COMPLETE',
        'summary': {'total': len(tests), 'passed': len(tests) if outcome == 'PASS' else 0,
                    'failed': len(tests) if outcome == 'FAIL' else 0,
                    'inconclusive': len(tests) if outcome == 'INCONCLUSIVE' else 0},
        'limitations': ['HTTP status and content-byte assertions only; not business correctness.',
                        'A successful assertion does not attest security of untrusted code.'],
    }


def fake_vm(candidate, config, path, *, reviewed_fixture, work_root, test_bundle, outcome='PASS'):
    assert reviewed_fixture is True
    assert test_bundle['source'] == SOURCE
    result = {
        'source': dict(SOURCE), 'adapter': candidate['adapter'],
        'backend': 'QEMU_DISPOSABLE_QCOW2_VM', 'status': 'STARTUP_VERIFIED',
        'security_attestation': ATTESTATION,
        'execution': 'PERFORMED_IN_DISPOSABLE_VM', 'cleanup': CLEAN,
        'phase4_status': 'VALIDATED', 'phase4_test_results': fake_report(test_bundle, outcome=outcome),
    }
    path.write_text(json.dumps(result), encoding='utf-8')
    return result


def module_patch(fn):
    parent = types.ModuleType('projectiq_executioner.phase2')
    parent.__path__ = []
    worker = types.ModuleType('projectiq_executioner.phase2.vm_worker')
    worker.__path__ = []
    orchestrator = types.ModuleType('projectiq_executioner.phase2.vm_worker.orchestrator')
    orchestrator.execute_vm_candidate = fn
    return patch.dict(sys.modules, {
        parent.__name__: parent,
        worker.__name__: worker,
        orchestrator.__name__: orchestrator,
    })


class Phase4BOrchestrationTests(unittest.TestCase):
    def call(self, vm_fn=None, *, plan=None, config=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fn = vm_fn or fake_vm
            with module_patch(fn):
                result = run_reviewed_tests(
                    plan=plan if plan is not None else make_plan(),
                    candidate=CANDIDATE, config=config if config is not None else CONFIG,
                    output_dir=root / 'run')
            persisted = json.loads((root/'run'/'phase4_run.json').read_text('utf-8'))
            self.assertEqual(result, persisted)
            self.assertTrue((root/'run'/'phase4_handoff.json').exists())
            return result

    def test_success_status_from_valid_guest_evidence(self):
        result = self.call()
        self.assertEqual(result['status'], 'TESTS_PASSED')
        self.assertEqual(result['summary']['passed'], 1)
        self.assertEqual(result['execution'], 'PERFORMED_IN_DISPOSABLE_VM')
        self.assertEqual(result['cleanup'], CLEAN)
        self.assertEqual(result['results'][0]['result'], 'PASS')
        self.assertEqual(result['security_attestation'], ATTESTATION)

    def test_http_404_is_functional_failure_not_vm_failure(self):
        result = self.call(lambda *a, **kw: fake_vm(*a, **kw, outcome='FAIL'))
        self.assertEqual(result['status'], 'TESTS_FAILED')
        self.assertEqual(result['summary']['failed'], 1)
        self.assertEqual(result['cleanup'], CLEAN)

    def test_network_problem_not_called_failed_application(self):
        result = self.call(lambda *a, **kw: fake_vm(*a, **kw, outcome='INCONCLUSIVE'))
        self.assertEqual(result['status'], 'TESTS_INCONCLUSIVE')
        self.assertEqual(result['summary']['inconclusive'], 1)

    def test_vm_cleanup_failure_overrides_pass(self):
        def bad(*a, **kw):
            vm = fake_vm(*a, **kw)
            vm['cleanup'] = 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
            return vm
        result = self.call(bad)
        self.assertEqual(result['status'], 'CLEANUP_FAILED')
        self.assertFalse(result['results'])

    def test_denied_launch_not_misreported_as_cleanup_failed(self):
        def denied(*a, **kw):
            return {'status': 'DENIED', 'execution': 'NOT_PERFORMED', 'cleanup': 'NOT_STARTED'}
        result = self.call(denied)
        self.assertEqual(result['status'], 'VM_EXECUTION_NOT_VERIFIED')
        self.assertEqual(result['issue_code'], 'VM_LAUNCH_DENIED')

    def test_missing_guest_report_fails_closed(self):
        def missing(*a, **kw):
            vm = fake_vm(*a, **kw)
            del vm['phase4_test_results']
            return vm
        result = self.call(missing)
        self.assertEqual(result['status'], 'GUEST_RESULTS_NOT_VERIFIED')
        self.assertFalse(result['results'])

    def test_forged_guest_result_is_rejected(self):
        def forged(*a, **kw):
            vm = fake_vm(*a, **kw)
            vm['phase4_test_results']['results'][0]['http_status'] = 404
            return vm
        result = self.call(forged)
        self.assertEqual(result['status'], 'GUEST_RESULTS_NOT_VERIFIED')

    def test_wrong_sha_is_rejected_even_when_guest_claims_success(self):
        def wrong(*a, **kw):
            vm = fake_vm(*a, **kw)
            vm['source'] = {**SOURCE, 'commit_sha': 'b' * 40}
            return vm
        result = self.call(wrong)
        self.assertEqual(result['status'], 'VM_EXECUTION_NOT_VERIFIED')

    def test_missing_guest_attestation_rejected(self):
        def wrong(*a, **kw):
            vm = fake_vm(*a, **kw)
            vm['security_attestation'] = 'ATTESTED_FOR_UNTRUSTED_STUDENTS'
            return vm
        result = self.call(wrong)
        self.assertEqual(result['status'], 'VM_EXECUTION_NOT_VERIFIED')

    def test_no_operator_approval_means_no_vm_call(self):
        def forbidden(*a, **kw):
            self.fail('VM was launched without operator approval')
        result = self.call(forbidden, config={'approved_snapshots': []})
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['execution'], 'NOT_PERFORMED')

    def test_no_oracle_means_no_vm_call(self):
        def forbidden(*a, **kw):
            self.fail('VM was launched without an oracle')
        plan = generate_test_plan(manifest(), None)
        result = self.call(forbidden, plan=plan)
        self.assertEqual(result['status'], 'NO_RUNNABLE_TESTS')

    def test_output_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            out.mkdir()
            (out/'phase4_run.json').write_text('old evidence', encoding='utf-8')
            with self.assertRaisesRegex(Phase4RunError, 'PHASE4_OUTPUT_ALREADY_EXISTS'):
                run_reviewed_tests(plan=make_plan(), candidate=CANDIDATE, config=CONFIG,
                                   output_dir=out)
            self.assertEqual((out/'phase4_run.json').read_text('utf-8'), 'old evidence')

    def test_missing_guest_tests_cannot_inherit_startup_status(self):
        def startup_only(*a, **kw):
            vm = fake_vm(*a, **kw)
            vm['phase4_status'] = 'INVALID_GUEST_REPORT'
            return vm
        result = self.call(startup_only)
        self.assertEqual(result['status'], 'GUEST_RESULTS_NOT_VERIFIED')

    def test_vm_exception_does_not_fabricate_success(self):
        def problem(*a, **kw):
            raise OSError('broken VM')
        with tempfile.TemporaryDirectory() as tmp, module_patch(problem):
            result = run_reviewed_tests(plan=make_plan(), candidate=CANDIDATE,
                                        config=CONFIG, output_dir=Path(tmp)/'run')
            self.assertEqual(result['status'], 'VM_EXECUTION_NOT_VERIFIED')
            self.assertEqual(result['cleanup'], 'NOT_VERIFIED')
            self.assertEqual(result['issue_code'], 'VM_WORKER_EXCEPTION')
            self.assertTrue((Path(tmp)/'run'/'phase4_run.json').is_file())


class Phase2BoundaryTests(unittest.TestCase):
    """Require the real Phase 2 integration hooks on an installed ProjectIQ checkout."""

    @staticmethod
    def _phase2(path):
        root = Path(__file__).resolve().parents[1]
        file = root / 'phase2' / 'vm_worker' / path
        if not file.exists():
            raise unittest.SkipTest('Phase 2 source is not included in isolated patch validation')
        return file

    def test_trusted_guest_http_transfer_is_allowlisted_without_relaxing_ssh(self):
        # Phase 4B must copy the controller-owned interpreter into the VM.
        # This is an SSH transfer contract test; it launches no VM.
        self._phase2('qemu.py')
        from projectiq_executioner.phase2.vm_worker.qemu import SSHControl, WorkerFailure
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'guest_http.py'
            payload = b'# Trusted bounded guest HTTP interpreter\n'
            src.write_bytes(payload)
            control = SSHControl({}, root, 2222, root / 'key')
            with patch.object(control, 'ssh', return_value=types.SimpleNamespace(returncode=0)) as ssh:
                control.copy(src, 'guest_http.py')
                ssh.assert_called_once_with('dd', 'of=/home/projectiq/guest_http.py',
                                            'status=none', timeout=60, input=payload)
                for forbidden in ('arbitrary.py', '../guest_http.py', '/tmp/guest_http.py'):
                    with self.subTest(destination=forbidden):
                        with self.assertRaises(WorkerFailure):
                            control.copy(src, forbidden)
                self.assertEqual(ssh.call_count, 1)

    def test_guest_dispatch_before_teardown_in_both_guest_paths(self):
        source = self._phase2('guest_agent.py').read_text(encoding='utf-8')
        module = ast.parse(source)
        for name in ('run', 'run_fullstack'):
            function = next(node for node in module.body
                            if isinstance(node, ast.FunctionDef) and node.name == name)
            found = False
            for node in ast.walk(function):
                if not isinstance(node, ast.Try) or not node.finalbody:
                    continue
                in_live_body = any(isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == '_run_phase4_on_live_app'
                    for stmt in node.body for call in ast.walk(stmt))
                has_cleanup = any(isinstance(call, ast.Call) and (
                    isinstance(call.func, ast.Attribute) and call.func.attr in ('terminate', 'kill')
                    or isinstance(call.func, ast.Name) and call.func.id == '_d4_stop_process'
                    ) for stmt in node.finalbody for call in ast.walk(stmt))
                if in_live_body and has_cleanup:
                    found = True
            self.assertTrue(found, f'{name} must run Phase 4 before its VM app teardown')

    def test_host_rejects_malformed_bundle_before_vm_launch(self):
        self._phase2('orchestrator.py')
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'vm_result.json'
            result = execute_vm_candidate(
                deepcopy(CANDIDATE), dict(CONFIG), output, reviewed_fixture=True,
                test_bundle={'schema_version': '0.1'})
            self.assertEqual(result['status'], 'DENIED')
            self.assertEqual(result['reason'], 'PHASE4_TEST_BUNDLE_REJECTED')
            self.assertEqual(result['execution'], 'NOT_PERFORMED')
            self.assertEqual(json.loads(output.read_text('utf-8'))['reason'],
                             'PHASE4_TEST_BUNDLE_REJECTED')


if __name__ == '__main__':
    unittest.main()
