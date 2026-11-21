"""Phase 3E release gate: broad stack shapes, safety and offline replay.

Never downloads, imports student source, invokes a VM, or contacts an LLM.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase3.benchmark import (
    BenchmarkInputError, _checks_for_manifest, benchmark_specs,
    check_manifest, check_plan, discover_batch, render_markdown,
    main as benchmark_main,
)
from projectiq_executioner.phase3.contracts import TestPlanContractError, validate_test_plan
from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.tests.test_phase3_generator import manifest as old_manifest, candidate

SHA = 'a' * 40
SOURCE = {'kind': 'github', 'repository': 'Fixture/MultipleArchitectures', 'commit_sha': SHA}


def repo(*, routes=(), pages=(), tests=(), entrypoints=(), features=(), schemas=(), proposals=(), services=()):
    return {'schema_version': '1.0', 'phase': 1, 'source': dict(SOURCE),
            'routes': list(routes), 'pages': list(pages), 'tests': list(tests),
            'entrypoints': list(entrypoints), 'features': list(features),
            'database_schema_files': list(schemas), 'proposed_test_targets': list(proposals),
            'services': list(services), 'inventory': {}}


def cand(adapter: str, probe: str):
    return {'source': dict(SOURCE), 'status': 'CANDIDATE_NEEDS_VM_VERIFICATION',
            'adapter': adapter, 'probe_path': probe}


def route(method, path, kind='declared_http_route', evidence=None):
    return {'kind': kind, 'method': method, 'path': path,
            'evidence': [{'path': 'src/app.py', 'line': 3, 'reason': 'literal route'}] if evidence is None else evidence}


class BroadArchitectures(unittest.TestCase):
    """Positive/negative test-case discovery across *different*, not named, stacks."""

    def test_twelve_different_architecture_shapes(self):
        shapes = {
            'static': (repo(pages=['index.html','about.html']), cand('static_html_v1','/index.html'), 2),
            'python_flask_templates': (repo(routes=[route('GET', '/'),route('POST','/login')],
                pages=['templates/index.html'], services=[{'kind':'backend', 'technology':'Flask'},
                {'kind':'frontend','technology':'HTML'}]), cand('python_web_offline_v1','/'), 2),
            'python_fastapi_sqlite': (repo(routes=[route('GET','/health'),route('GET','/items/{item_id}')],
                schemas=['schema.sql']), cand('python_web_offline_v1','/openapi.json'), 2),
            'express_api': (repo(routes=[route('GET','/health'),route('POST','/users'),
                route('DELETE','/users/:id')], tests=[{'path':'tests/users.test.js','kind':'jest'}]),
                cand('node_express_offline_v1','/health'), 3),
            'vite_spa': (repo(pages=['index.html'], features=[{'name':'counter'}]),
                cand('node_vite_offline_v1','/index.html'), 1),
            'vite_express_fullstack': (repo(routes=[route('GET','/api/health')],
                pages=['frontend/index.html']), cand('node_fullstack_offline_v1','/api/health'), 1),
            'php_mysql': (repo(routes=[route('POST','auth/login.php','file_endpoint'),
                route('GET','logic/tasks.php','file_endpoint')], pages=['public/login.html'],
                schemas=['sql/schema.sql']), cand('php_mysql_v1','/public/login.html'), 3),
            'nextjs_external_api': (repo(routes=[route('GET','/products','client_http_call'),
                route('GET','/orders/:id','page_route')], pages=['src/app/page.tsx']), None, 2),
            'spring_java': (repo(routes=[route('GET','/api/people'),route('POST','/api/people')],
                entrypoints=[{'path':'src/main/java/Application.java','kind':'main'}]), None, 2),
            'graphql_grpc': (repo(routes=[route('UNKNOWN','query FindPeople','graphql_operation'),
                route('UNKNOWN','GetPerson','rpc_method')]), None, 2),
            'go_cli': (repo(entrypoints=[{'path':'cmd/demo/main.go','kind':'main'}]), None, 1),
            'rust_cli': (repo(entrypoints=[{'path':'src/main.rs','kind':'main'}]), None, 1),
        }
        for name, (m, c, expected) in shapes.items():
            with self.subTest(architecture=name):
                result = check_manifest(name, m, c)
                self.assertEqual(result['status'], 'PASS', result)
                self.assertTrue(result['deterministic_under_reordering'])
                self.assertEqual(result['summary']['total'], expected)
                self.assertEqual(result['route_coverage']['missing_http_operations'], 0)
                self.assertEqual(result['route_coverage']['missing_non_http_operations'], 0)

    def test_mutating_and_dynamic_routes_never_get_fabricated_oracles(self):
        m = repo(routes=[route('POST','/auth/login'), route('GET','/users/:id'),
                             route('GET','/shop/:slug','page_route'),
                             route('PUT','/cart/:id'),route('DELETE','/cart/:id')])
        p = generate_test_plan(m, cand('python_web_offline_v1','/health'))
        tests = [x for x in p['test_cases'] if x['category'] != 'HTTP_READINESS']
        self.assertEqual(len(tests), 5)
        self.assertTrue(all(x['status'] == 'NEEDS_ORACLE' and not x['assertions'] for x in tests))
        self.assertIsNone(validate_test_plan(p))

    def test_legacy_static_feature_proposals_are_inventory_only(self):
        m = repo(pages=['index.html'], features=[{'name':'responsive'}],
                 proposals=['Translate documented feature into observable assertions: responsive'])
        p = generate_test_plan(m, cand('static_html_v1','/index.html'))
        self.assertEqual(p['summary']['total'], 1)
        self.assertEqual(len(p['inventory']['features']), 1)
        self.assertEqual(p['inventory']['proposals'], [])

    def test_route_aliases_and_client_server_duplicates_preserve_unique_operations(self):
        m = repo(routes=[route('GET','/api/users'), route('GET','/api/users','client_http_call'),
                             route('POST','/api/users'),route('POST','/api/users','client_http_call')])
        p = generate_test_plan(m)
        self.assertEqual(p['summary']['total'], 2)
        self.assertEqual(_checks_for_manifest(m,p)['covered_http_operations'], 2)

    def test_unsafe_urls_do_not_become_executable_tests(self):
        m = repo(routes=[route('GET','https://prod.example/private','client_http_call'),
                             route('POST','../unsafe.php','file_endpoint'),
                             route('GET','/ok')])
        p = generate_test_plan(m)
        self.assertEqual(p['summary']['total'], 1)
        self.assertEqual(p['test_cases'][0]['target']['path'], '/ok')
        self.assertIn('UNSAFE_OR_UNRESOLVED_ROUTE_SKIPPED', [x['code'] for x in p['issues']])
        self.assertEqual(check_manifest('UnsafeRoutes', m)['status'], 'PASS')

    def test_readme_only_returns_inventory_not_pseudo_tests(self):
        m = repo(features=[{'name':'beautiful user interface','evidence':[]}],
                 proposals=['Write integration tests for login flow'])
        p = generate_test_plan(m)
        self.assertEqual(p['status'], 'DISCOVERY_ONLY')
        self.assertEqual(p['summary']['total'], 0)
        self.assertEqual(check_manifest('ReadmeOnly',m)['status'], 'PASS')

    def test_empty_unknown_language_has_stable_empty_json(self):
        p = generate_test_plan(repo())
        self.assertEqual(p['status'], 'NO_TEST_TARGETS')
        self.assertEqual(check_manifest('EmptyRepo',repo())['status'], 'PASS')

    def test_many_routes_truncation_is_explicit_and_not_full_coverage_claim(self):
        m = repo(routes=[route('GET',f'/r{i:04d}') for i in range(410)])
        result = check_manifest('LargeRepo', m)
        self.assertEqual(result['status'], 'LIMITED')
        self.assertEqual(result['summary']['total'], 400)
        self.assertTrue(result['route_coverage']['truncated'])
        self.assertEqual(result['route_coverage']['missing_http_operations'], 10)

    def test_existing_generated_plan_can_be_validated_without_manifest(self):
        p = generate_test_plan(repo(routes=[route('GET','/health')]))
        checked = check_plan('ExpressPlan',p)
        self.assertEqual(checked['status'], 'LIMITED')
        self.assertIsNone(checked['deterministic_under_reordering'])
        self.assertIsNone(checked['route_coverage'])


class Phase4ContractHardening(unittest.TestCase):
    def setUp(self):
        self.plan = generate_test_plan(repo(routes=[route('GET','/health')]),
                                       cand('node_express_offline_v1','/health'))

    def reject(self, modify):
        p = deepcopy(self.plan)
        modify(p)
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_valid_baseline(self):
        self.assertIsNone(validate_test_plan(self.plan))

    def test_unapproved_runtime_claim(self):
        self.reject(lambda p: p['execution_context'].update(operator_approval='APPROVED'))

    def test_false_student_execution(self):
        self.reject(lambda p: p['generator'].update(student_code_executed=True))

    def test_fake_runnable_with_unresolved_route(self):
        def attack(p):
            p['test_cases'][0]['target']['path'] = '/users/:id'
        self.reject(attack)

    def test_fake_runnable_mutation(self):
        self.reject(lambda p: p['test_cases'][0]['target'].update(method='POST'))

    def test_duplicate_assertion_kind(self):
        self.reject(lambda p: p['test_cases'][0]['assertions'].append(dict(p['test_cases'][0]['assertions'][0])))

    def test_forged_ready_without_matching_adapter(self):
        self.reject(lambda p: p['test_cases'][0].update(required_adapter='php_mysql_v1'))

    def test_missing_ready_evidence(self):
        self.reject(lambda p: p['test_cases'][0].update(evidence=[]))

    def test_fake_hash_without_verified_checkout(self):
        self.reject(lambda p: p['test_cases'][0]['assertions'].append({
            'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': sha256(b'student-file').hexdigest()}))

    def test_fake_readiness_basis(self):
        self.reject(lambda p: p['test_cases'][0]['oracle'].update(basis='USER_SUPPLIED_VERDICT'))

    def test_unsafe_absolute_http_path(self):
        self.reject(lambda p: p['test_cases'][0]['target'].update(path='//evil.com'))

    def test_unsafe_traversal_http_path(self):
        self.reject(lambda p: p['test_cases'][0]['target'].update(path='/a/../private'))

    def test_tampered_summary_counts(self):
        self.reject(lambda p: p['summary'].update(total=9000))

    def test_duplicate_test_id(self):
        def attack(p):
            p['test_cases'].append(deepcopy(p['test_cases'][0]))
            p['summary']['total'] += 1
            p['summary']['ready_for_phase4'] += 1
        self.reject(attack)

    def test_unknown_github_sha_cannot_claim_valid_plan(self):
        self.reject(lambda p: p['source'].update(commit_sha='main'))

    def test_unpinned_source_cannot_claim_pinned_content(self):
        p = generate_test_plan(repo(), cand('static_html_v1','/index.html'))
        p['test_cases'][0]['assertions'].append({
            'kind': 'HTTP_BODY_SHA256_EQUALS', 'expected': sha256(b'example').hexdigest()})
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_openapi_oracle_forged_from_no_source_evidence(self):
        p = generate_test_plan(repo(routes=[route('GET','/health')]), cand('python_web_offline_v1','/invalid'))
        p['test_cases'][0]['assertions'] = [{'kind':'HTTP_STATUS_EQUALS','expected':200}]
        p['test_cases'][0]['oracle'] = {'status':'GROUNDED','basis':'DECLARED_OPENAPI_GET_200'}
        p['test_cases'][0]['status'] = 'READY_FOR_PHASE4'
        p['test_cases'][0]['blockers'] = []
        p['summary']['needs_oracle'] = 0
        p['summary']['ready_for_phase4'] = 1
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_mutated_http_status_cannot_invent_expected_201(self):
        self.reject(lambda p: p['test_cases'][0]['assertions'][0].update(expected=201))

    def test_static_oracle_missing_page_inventory(self):
        p = generate_test_plan(repo(pages=['index.html']), cand('static_html_v1','/bad'))
        static = next(x for x in p['test_cases'] if x['oracle']['basis'] == 'STATIC_ADAPTER_RESOURCE_CONTRACT')
        static['target']['path'] = '/another.html'
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_unknown_test_category(self):
        self.reject(lambda p: p['test_cases'][0].update(category='SYSTEM_SHELL_COMMAND'))

    def test_local_source_cannot_claim_verified_snapshot(self):
        p = generate_test_plan(repo())
        p['source'] = {'kind': 'local', 'repository': 'local-app', 'commit_sha': None}
        p['execution_context']['source_snapshot_verified'] = True
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_openapi_claim_needs_matching_source_contract(self):
        p = generate_test_plan(repo(routes=[route('GET','/ping')]), cand('python_web_offline_v1','/not-ping'))
        test = next(x for x in p['test_cases'] if x['target']['path'] == '/ping')
        test['assertions'] = [{'kind':'HTTP_STATUS_EQUALS','expected':200}]
        test['oracle'] = {'status':'GROUNDED','basis':'DECLARED_OPENAPI_GET_200'}
        test['status'] = 'UNSUPPORTED_BY_RUNNER'
        test['blockers'] = ['SOURCE_SNAPSHOT_NOT_VERIFIED']
        p['summary']['needs_oracle'] = 0
        p['summary']['unsupported_by_runner'] = 1
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)

    def test_nonready_without_blocker(self):
        p = generate_test_plan(repo(routes=[route('GET','/nonready')]))
        p['test_cases'][0]['blockers'] = []
        with self.assertRaises(TestPlanContractError):
            validate_test_plan(p)


class ReplayBenchmarkTests(unittest.TestCase):
    def test_batch_discovery_and_override_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / 'batch'; folder = batch / 'FlaskTodo'; (folder/'phase1').mkdir(parents=True)
            (folder/'phase1'/'manifest.json').write_text(json.dumps(repo()),encoding='utf-8')
            (folder/'phase2c_candidate.json').write_text(json.dumps(cand('python_web_offline_v1','/')))
            overrides = root / 'override' / 'FlaskTodo'; overrides.mkdir(parents=True)
            (overrides/'phase2c_candidate.json').write_text(json.dumps(cand('python_web_offline_v1','/health')))
            specs = discover_batch(batch, root/'override')
            self.assertEqual(len(specs),1)
            self.assertEqual(specs[0][3],overrides/'phase2c_candidate.json')
            report=benchmark_specs(specs)
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['results'][0]['summary']['ready_for_phase4'],1)

    def test_batch_with_corrupt_input_preserves_other_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root/'good.json'; valid.write_text(json.dumps(repo(routes=[route('GET','/health')])))
            invalid = root/'bad.json'; invalid.write_text('{not-json',encoding='utf-8')
            result = benchmark_specs([('Good','manifest',valid,None),('Bad','manifest',invalid,None)])
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['summary']['passed'],1)
            self.assertEqual(result['summary']['failed'],1)
            self.assertEqual(result['summary']['total_scenarios'],1)
            self.assertEqual(result['results'][0]['issues'], ['INVALID_INPUT_OR_GENERATION'])

    def test_cannot_claim_bad_existing_plan_passed(self):
        p = generate_test_plan(repo(routes=[route('GET','/ping')]))
        p['test_cases'][0]['status'] = 'READY_FOR_PHASE4'
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'plan.json'; f.write_text(json.dumps(p))
            report = benchmark_specs([('BadPlan','plan',f,None)])
            self.assertEqual(report['status'],'FAIL')
            self.assertEqual(report['results'][0]['issues'],['INVALID_PLAN_CONTRACT'])

    def test_markdown_escapes_untrusted_case_name(self):
        with self.assertRaises(BenchmarkInputError):
            check_plan('unsafe|name', generate_test_plan(repo()))

    def test_benchmark_no_overwrite_and_cli_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            one=root/'one.json'; one.write_text(json.dumps(repo(routes=[route('GET','/users')])))
            out=root/'run'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark_main(['--manifest',str(one),'--out',str(out)]),0)
            report=json.loads((out/'benchmark.json').read_text())
            self.assertEqual(report['summary']['passed'],1)
            self.assertIn('Phase 3E', (out/'benchmark.md').read_text())
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                benchmark_main(['--manifest',str(one),'--out',str(out)])

    def test_input_error_envelope_is_not_release_pass(self):
        from projectiq_executioner.phase3.__main__ import _error_plan
        envelope = _error_plan('GITHUB_NETWORK_FAILURE')
        validate_test_plan(envelope)
        result = check_plan('FailedIngestion', envelope)
        self.assertEqual(result['status'], 'FAIL')

    def test_candidate_source_mismatch_reports_failure_without_crashing_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m = root/'manifest.json'; m.write_text(json.dumps(repo()), encoding='utf-8')
            forged = cand('static_html_v1','/index.html')
            forged['source']['commit_sha'] = 'b' * 40
            c = root/'candidate.json'; c.write_text(json.dumps(forged), encoding='utf-8')
            out = benchmark_specs([('Mismatch','manifest',m,c)])
            self.assertEqual(out['status'], 'FAIL')
            self.assertEqual(out['results'][0]['issues'], ['INVALID_INPUT_OR_GENERATION'])

    def test_no_valid_inputs_rejected(self):
        with self.assertRaises(BenchmarkInputError):
            benchmark_specs([])

    def test_duplicate_case_names_rejected(self):
        with self.assertRaises(BenchmarkInputError):
            benchmark_specs([('Same','plan',Path('a'),None),('Same','plan',Path('b'),None)])


if __name__ == '__main__':
    unittest.main()
