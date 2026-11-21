"""4C-A adversarial offline checks: no student app, no VM, no HTTP request."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.phase4.oracle_contracts import (
    OracleContractError, canonical_bytes, digest, make_oracle_template,
    validate_oracle_catalog,
)
from projectiq_executioner.phase4.oracle_stage import main, stage_oracles

SRC = {'kind': 'github', 'repository': 'Fixture/Oracle', 'commit_sha': 'a'*40}
CANDIDATE = {'schema_version': '0.3', 'stage': 'vm_execution_candidate',
             'worker_provider': 'qemu_qcow2_v1',
             'launch_authorization': 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES',
             'source': SRC, 'adapter': 'php_mysql_v1',
             'status': 'CANDIDATE_NEEDS_VM_VERIFICATION',
             'probe_path': '/public/login.html'}
APPROVAL = {'approved_snapshots': ['Fixture/Oracle@' + 'a'*40]}


def plan():
    raw = {
        'schema_version': '1.0', 'phase': 1, 'source': SRC,
        'routes': [
            {'path': '/api/tasks', 'method': 'POST', 'kind': 'declared_http_route', 'evidence': []},
            {'path': '/api/tasks/:id', 'method': 'GET', 'kind': 'declared_http_route', 'evidence': []},
            {'path': '/api/tasks', 'method': 'GET', 'kind': 'declared_http_route', 'evidence': []},
            {'path': '/api/tasks/:id', 'method': 'DELETE', 'kind': 'declared_http_route', 'evidence': []},
            {'path': '/api/login', 'method': 'POST', 'kind': 'declared_http_route', 'evidence': []},
        ],
        'pages': ['public/login.html'], 'services': [], 'technologies': [],
        'features': [], 'proposed_test_targets': [], 'entrypoints': [],
        'tests': [], 'database_schema_files': [], 'inventory': {},
    }
    return generate_test_plan(raw, CANDIDATE)


def make_case(p, method='POST', path='/api/tasks', *, name='Create task',
              body=None, params=None, query=None, status=201, subset=None):
    target = next(x for x in p['test_cases']
                  if x['target']['method'] == method and x['target']['path'] == path)
    return {'name': name, 'test_id': target['id'],
            'request': {'path_params': params if params is not None else {},
                        'query': query if query is not None else {}, 'json_body': body},
            'expected': {'status': status, 'json_subset': subset}}


def catalog(p, *cases):
    return {'schema_version': '0.1', 'source': deepcopy(p['source']), 'cases': list(cases)}


class OracleShapeTests(unittest.TestCase):
    def setUp(self):
        self.p = plan()
        self.case = make_case(self.p, body={'title': 'Safe fixture'},
                              subset={'created': True})
        self.data = catalog(self.p, self.case)

    def validate(self, obj=None):
        return validate_oracle_catalog(self.data if obj is None else obj, self.p)

    def bad(self, code, obj=None):
        with self.assertRaisesRegex(OracleContractError, '^' + code + '$'):
            self.validate(obj)

    def test_valid_oracle_yields_advisory_scenario_not_runnable_bundle(self):
        spec, = self.validate()
        self.assertEqual(spec['request']['method'], 'POST')
        self.assertEqual(spec['request']['path'], '/api/tasks')
        self.assertEqual(spec['assertions'][0], {'kind': 'HTTP_STATUS_EQUALS', 'expected': 201})
        self.assertEqual(spec['assertions'][1]['kind'], 'JSON_BODY_SUBSET_EQUALS')
        self.assertEqual(spec['oracle_basis'], 'OPERATOR_SUPPLIED_UNREVIEWED')
        self.assertEqual(spec['execution'], 'NOT_PERFORMED')
        self.assertIn('PHASE4C_EXECUTOR_NOT_IMPLEMENTED', spec['blockers'])
        self.assertNotIn('test_bundle', spec)

    def test_template_has_no_fabricated_oracles(self):
        editable, targets = make_oracle_template(self.p)
        self.assertEqual(editable['cases'], [])
        self.assertEqual(len(targets['eligible_targets']), 6)
        self.assertEqual(targets['execution'], 'NOT_PERFORMED')
        self.assertTrue(all('test_id' in x for x in targets['eligible_targets']))

    def test_duplicate_oracle_rejected(self):
        self.data['cases'].append(deepcopy(self.case))
        self.bad('DUPLICATE_ORACLE_CASE')

    def test_multiple_distinct_scenarios_same_discovered_route(self):
        self.data['cases'].append(make_case(self.p, name='Reject empty title',
                                           body={'title': ''}, status=422,
                                           subset={'error': 'Title required'}))
        result = self.validate()
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['id'], result[1]['id'])
        self.assertEqual({x['phase3_test_id'] for x in result}, {self.case['test_id']})

    def test_scenarios_are_order_independent(self):
        other = make_case(self.p, name='Get tasks', method='GET', path='/api/tasks',
                          status=200, subset={'ok': True})
        a = catalog(self.p, self.case, other)
        b = catalog(self.p, other, self.case)
        self.assertEqual(self.validate(a), self.validate(b))

    def test_catalog_schema_rejects_unknown_keys(self):
        self.data['arbitrary_commands'] = ['rm -rf /']
        self.bad('INVALID_ORACLE_CATALOG_SCHEMA')

    def test_reject_catalog_version(self):
        self.data['schema_version'] = '0.2'
        self.bad('INVALID_ORACLE_CATALOG_SCHEMA')

    def test_reject_stale_sha(self):
        self.data['source']['commit_sha'] = 'b'*40
        self.bad('ORACLE_SOURCE_MISMATCH')

    def test_reject_non_object_catalog(self):
        self.bad('INVALID_ORACLE_CATALOG_SCHEMA', [])

    def test_reject_nonlist_cases(self):
        self.data['cases'] = {}
        self.bad('ORACLE_CASE_LIMIT')

    def test_case_limit(self):
        self.data['cases'] = [deepcopy(self.case) for _ in range(65)]
        self.bad('ORACLE_CASE_LIMIT')

    def test_case_shape_rejects_command_fields(self):
        self.case['shell_command'] = 'echo hello'
        self.bad('INVALID_ORACLE_CASE_SHAPE')

    def test_name_must_be_bounded(self):
        self.case['name'] = 'N'*110
        self.bad('INVALID_ORACLE_NAME')

    def test_unknown_phase3_id_rejected(self):
        self.case['test_id'] = 'TC-FFFFFFFFFFFFFFFF'
        self.bad('UNKNOWN_PHASE3_TEST_ID')

    def test_invalid_phase3_plan_rejected(self):
        self.p['test_cases'][0]['id'] = 'test'
        self.bad('INVALID_PHASE3_PLAN')

    def test_request_cannot_choose_different_method_or_path(self):
        self.case['request']['method'] = 'DELETE'
        self.bad('INVALID_REQUEST_SHAPE')
        self.case['request'].pop('method')
        self.case['request']['url'] = 'http://127.0.0.1/admin'
        self.bad('INVALID_REQUEST_SHAPE')

    def test_expected_cannot_claim_arbitrary_shell_or_code(self):
        self.case['expected']['python'] = 'assert True'
        self.bad('INVALID_EXPECTED_RESPONSE_SHAPE')

    def test_expected_status_allowlist(self):
        for value in (302, 500, 999, '200', True, None):
            with self.subTest(value=value):
                self.case['expected']['status'] = value
                self.bad('EXPECTED_STATUS_NOT_SUPPORTED')

    def test_json_response_subset_requires_object(self):
        self.case['expected']['json_subset'] = [1, 2]
        self.bad('UNSUPPORTED_EXPECTED_JSON_SUBSET')
        self.case['expected']['json_subset'] = None
        self.assertEqual(len(self.validate()), 1)

    def test_http_204_prohibits_expected_json_body(self):
        self.case['expected']['status'] = 204
        self.bad('UNSUPPORTED_EXPECTED_JSON_SUBSET')
        self.case['expected']['json_subset'] = None
        self.assertEqual(self.validate()[0]['assertions'], [{'kind': 'HTTP_STATUS_EQUALS', 'expected': 204}])

    def test_get_rejects_body(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks', body={'x': 1})]
        self.bad('REQUEST_BODY_NOT_ALLOWED_FOR_METHOD')

    def test_delete_rejects_body_initial_contract(self):
        self.data['cases'] = [make_case(self.p, 'DELETE', '/api/tasks/:id',
                                        params={'id': '1'}, body={'x': 1})]
        self.bad('REQUEST_BODY_NOT_ALLOWED_FOR_METHOD')

    def test_valid_get_query_canonicalization(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks',
                                        query={'sort': 'new first', 'page': 2, 'active': True},
                                        status=200)]
        self.assertEqual(self.validate()[0]['request']['path'],
                         '/api/tasks?active=true&page=2&sort=new+first')

    def test_reject_arbitrary_query_keys_and_secrets(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks', query={'api_key': 'abc'})]
        self.bad('UNSAFE_QUERY_VALUE')
        self.data['cases'][0]['request']['query'] = {'bad key': 1}
        self.bad('UNSAFE_QUERY_VALUE')

    def test_reject_unsafe_query_types(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks', query={'page': [1]})]
        self.bad('UNSAFE_QUERY_VALUE')

    def test_safe_parameter_binding(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks/:id',
                                        params={'id': 'a123'}, status=200)]
        self.assertEqual(self.validate()[0]['request']['path'], '/api/tasks/a123')

    def test_unbound_route_parameter_rejected(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks/:id', status=200)]
        self.bad('ROUTE_PARAMETER_BINDING_MISMATCH')

    def test_extra_route_parameter_rejected(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks/:id',
                                        params={'id': '1', 'unused': '2'})]
        self.bad('ROUTE_PARAMETER_BINDING_MISMATCH')

    def test_path_traversal_and_encoded_path_rejected(self):
        self.data['cases'] = [make_case(self.p, 'GET', '/api/tasks/:id',
                                        params={'id': '../admin'})]
        self.bad('UNSAFE_ROUTE_PARAMETER')
        self.data['cases'][0]['request']['path_params']['id'] = '%2e%2e'
        self.bad('UNSAFE_ROUTE_PARAMETER')

    def test_valid_password_as_symbolic_reference_only(self):
        self.data['cases'] = [make_case(self.p, 'POST', '/api/login', name='Valid login',
                                        body={'username': 'testuser',
                                              'password': {'$fixture_ref': 'login_password'}},
                                        status=200)]
        compiled, = self.validate()
        self.assertEqual(compiled['fixture_refs'], ['login_password'])
        self.assertIn('RUNTIME_FIXTURE_PROVISIONING_REQUIRED', compiled['blockers'])
        self.assertNotIn('plaintext', json.dumps(compiled))

    def test_plaintext_password_rejected(self):
        self.data['cases'] = [make_case(self.p, 'POST', '/api/login',
                                        body={'username': 'test', 'password': 'plaintext'})]
        self.bad('SENSITIVE_LITERAL_NOT_ALLOWED')

    def test_unknown_fixture_reference_name_rejected(self):
        self.case['request']['json_body'] = {'password': {'$fixture_ref': '../steal'}}
        self.bad('INVALID_FIXTURE_REFERENCE')

    def test_expected_secret_literal_rejected(self):
        self.case['expected']['json_subset'] = {'token': 'abc'}
        self.bad('SENSITIVE_LITERAL_NOT_ALLOWED')

    def test_expected_fixture_reference_rejected(self):
        self.case['expected']['json_subset'] = {'item': {'$fixture_ref': 'secret'}}
        self.bad('EXPECTED_OUTPUT_CANNOT_USE_FIXTURE_REFERENCE')

    def test_nested_payload_depth_and_length_enforced(self):
        node = {}
        for i in range(10):
            node = {'child': node}
        self.case['request']['json_body'] = node
        self.bad('JSON_NESTING_LIMIT')
        self.case['request']['json_body'] = {'title': 'A'*1025}
        self.bad('JSON_STRING_TOO_LONG')

    def test_non_finite_and_unbounded_numeric_values_rejected(self):
        self.case['request']['json_body'] = {'number': float('nan')}
        self.bad('NON_CANONICAL_JSON')
        self.case['request']['json_body'] = {'number': 10**60}
        self.bad('JSON_NUMBER_OUT_OF_RANGE')

    def test_json_byte_limit(self):
        self.case['request']['json_body'] = {'items': ['a'*500 for _ in range(50)]}
        self.bad('JSON_BYTE_LIMIT')

    def test_stable_fingerprints_and_json_reordering(self):
        a = deepcopy(self.data)
        b = deepcopy(self.data)
        b['cases'][0]['request']['json_body'] = {'title': 'Safe fixture'}
        self.assertEqual(digest(a), digest(b))
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))

    def test_no_silent_http_request_execution(self):
        compiled, = self.validate()
        self.assertEqual(compiled['execution'], 'NOT_PERFORMED')
        self.assertFalse(any(key in compiled for key in ('result', 'passed', 'http_status')))


class OracleStageTests(unittest.TestCase):
    def setUp(self):
        self.p = plan()
        self.case = make_case(self.p, body={'title': 'Task'}, status=201)
        self.catalog = catalog(self.p, self.case)

    def test_reviewed_sha_stages_but_still_not_executable(self):
        report = stage_oracles(self.p, self.catalog, candidate=CANDIDATE, vm_config=APPROVAL)
        self.assertEqual(report['status'], 'STAGED_FOR_REVIEW')
        self.assertEqual(report['oracle_review'], 'NOT_PERFORMED')
        self.assertEqual(report['launch_authorization'], 'NOT_GRANTED')
        self.assertEqual(report['execution'], 'NOT_PERFORMED')
        self.assertEqual(report['summary']['total'], 1)
        self.assertNotIn('test_bundle', report)
        self.assertEqual(report['adapter'], 'php_mysql_v1')

    def test_no_candidate_yields_nonexecutable_review(self):
        report = stage_oracles(self.p, self.catalog)
        self.assertEqual(report['status'], 'REVIEW_REQUIRED')
        self.assertIsNone(report['adapter'])

    def test_no_approval_yields_review_required(self):
        report = stage_oracles(self.p, self.catalog, candidate=CANDIDATE)
        self.assertEqual(report['status'], 'REVIEW_REQUIRED')
        self.assertIn('PHASE2_ADAPTER_AND_APPROVAL_NOT_VERIFIED',
                      [x['code'] for x in report['issues']])

    def test_explicit_unapproved_sha_blocks_staging(self):
        report = stage_oracles(self.p, self.catalog, candidate=CANDIDATE,
                               vm_config={'approved_snapshots': []})
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['launch_authorization'], 'NOT_GRANTED')
        self.assertIn('PINNED_SHA_NOT_OPERATOR_APPROVED',
                      [x['code'] for x in report['issues']])

    def test_invalid_candidate_provenance_rejected(self):
        candidate = deepcopy(CANDIDATE)
        candidate['source']['commit_sha'] = 'b'*40
        with self.assertRaisesRegex(OracleContractError, 'PHASE2_SOURCE_MISMATCH'):
            stage_oracles(self.p, self.catalog, candidate=candidate, vm_config=APPROVAL)

    def test_forged_candidate_rejected(self):
        candidate = deepcopy(CANDIDATE)
        candidate.pop('worker_provider')
        with self.assertRaisesRegex(OracleContractError, 'INVALID_PHASE2_CANDIDATE'):
            stage_oracles(self.p, self.catalog, candidate=candidate, vm_config=APPROVAL)

    def test_mismatched_adapter_rejected(self):
        candidate = deepcopy(CANDIDATE)
        candidate['adapter'] = 'python_web_offline_v1'
        with self.assertRaisesRegex(OracleContractError, 'PHASE3_ADAPTER_MISMATCH'):
            stage_oracles(self.p, self.catalog, candidate=candidate, vm_config=APPROVAL)

    def test_empty_catalog_is_valid_template_not_claimed_coverage(self):
        report = stage_oracles(self.p, catalog(self.p), candidate=CANDIDATE, vm_config=APPROVAL)
        self.assertEqual(report['status'], 'NO_SCENARIOS')
        self.assertEqual(report['summary']['total'], 0)

    def test_staging_is_deterministic(self):
        a = stage_oracles(self.p, self.catalog, candidate=CANDIDATE, vm_config=APPROVAL)
        b = stage_oracles(self.p, deepcopy(self.catalog), candidate=CANDIDATE, vm_config=APPROVAL)
        self.assertEqual(a, b)

    def test_login_ref_does_not_provision_credentials(self):
        item = make_case(self.p, 'POST', '/api/login', name='Login fixture',
                         body={'password': {'$fixture_ref': 'test_password'}})
        report = stage_oracles(self.p, catalog(self.p, item), candidate=CANDIDATE,
                               vm_config=APPROVAL)
        self.assertEqual(report['summary']['requires_runtime_fixtures'], 1)
        self.assertEqual(report['scenarios'][0]['fixture_refs'], ['test_password'])
        self.assertEqual(report['execution'], 'NOT_PERFORMED')

    def test_cli_template_generation_and_existing_file_protection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'plan.json').write_text(json.dumps(self.p), encoding='utf-8')
            args = ['--plan', str(root/'plan.json'), '--template', '--out', str(root/'out')]
            self.assertEqual(main(args), 0)
            editable = json.loads((root/'out'/'oracle_template.json').read_text())
            eligible = json.loads((root/'out'/'eligible_targets.json').read_text())
            self.assertEqual(editable['cases'], [])
            self.assertEqual(len(eligible['eligible_targets']), 6)
            with self.assertRaises(SystemExit):
                main(args)

    def test_cli_stage_with_approved_fixture_and_no_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, value in (('plan.json', self.p), ('oracle.json', self.catalog),
                                ('candidate.json', CANDIDATE), ('config.json', APPROVAL)):
                (root/name).write_text(json.dumps(value), encoding='utf-8')
            args = ['--plan', str(root/'plan.json'), '--oracles', str(root/'oracle.json'),
                    '--candidate', str(root/'candidate.json'), '--vm-config', str(root/'config.json'),
                    '--out', str(root/'out')]
            self.assertEqual(main(args), 0)
            staged = json.loads((root/'out'/'oracle_review.json').read_text())
            self.assertEqual(staged['status'], 'STAGED_FOR_REVIEW')
            self.assertEqual(staged['execution'], 'NOT_PERFORMED')
            with self.assertRaises(SystemExit):
                main(args)

    def test_cli_invalid_json_never_creates_executable_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'plan.json').write_text(json.dumps(self.p), encoding='utf-8')
            (root/'oracle.json').write_text('{oops', encoding='utf-8')
            args = ['--plan', str(root/'plan.json'), '--oracles', str(root/'oracle.json'),
                    '--out', str(root/'out')]
            self.assertEqual(main(args), 2)
            self.assertFalse((root/'out'/'oracle_review.json').exists())

    def test_cli_rejects_vm_config_without_candidate(self):
        with self.assertRaises(SystemExit):
            main(['--plan', 'x', '--template', '--vm-config', 'cfg', '--out', 'out'])


class OracleCrossArchitectureTests(unittest.TestCase):
    def test_http_oracles_do_not_depend_on_framework_or_adapter(self):
        stacks = ('flask', 'fastapi', 'express', 'php_mysql', 'nextjs', 'spring',
                  'vite_express', 'go_http', 'rust_http', 'aspnet', 'django', 'laravel')
        for label in stacks:
            with self.subTest(stack=label):
                manifest = {
                    'schema_version': '1.0', 'phase': 1,
                    'source': {'kind': 'github', 'repository': f'Example/{label}',
                               'commit_sha': 'b'*40},
                    'routes': [{'path': '/api/items', 'method': 'POST',
                                'kind': 'declared_http_route', 'evidence': []}],
                    'pages': [], 'services': [], 'technologies': [], 'features': [],
                    'proposed_test_targets': [], 'entrypoints': [], 'tests': [],
                    'database_schema_files': [], 'inventory': {},
                }
                generated = generate_test_plan(manifest)
                targets = [x for x in generated['test_cases']
                           if x['target']['method'] == 'POST' and x['target']['path'] == '/api/items']
                self.assertEqual(len(targets), 1)
                c = make_case(generated, method='POST', path='/api/items',
                              body={'description': 'Example'}, status=201)
                compiled = validate_oracle_catalog(catalog(generated, c), generated)
                self.assertEqual(compiled[0]['request']['method'], 'POST')
                self.assertEqual(stage_oracles(generated, catalog(generated, c))['status'],
                                 'REVIEW_REQUIRED')

    def test_unsupported_protocol_remains_discovery_only(self):
        m = {'schema_version': '1.0', 'phase': 1, 'source': SRC,
             'routes': [{'path': 'Query.getItem', 'method': None,
                         'kind': 'graphql_operation', 'evidence': []}],
             'pages': [], 'services': [], 'technologies': [], 'features': [],
             'proposed_test_targets': [], 'entrypoints': [], 'tests': [],
             'database_schema_files': [], 'inventory': {}}
        p = generate_test_plan(m)
        _, targets = make_oracle_template(p)
        self.assertEqual(targets['eligible_targets'], [])
        self.assertEqual(stage_oracles(p, catalog(p))['status'], 'NO_SCENARIOS')

    def test_powershell_utf8_bom_catalog_is_supported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = plan()
            catalog_data = catalog(p, make_case(p, body={'title': 'Task'}, status=201))
            (root/'plan.json').write_text(json.dumps(p), encoding='utf-8')
            (root/'oracle.json').write_bytes(b'\xef\xbb\xbf' +
                                              json.dumps(catalog_data).encode('utf-8'))
            rc = main(['--plan', str(root/'plan.json'), '--oracles', str(root/'oracle.json'),
                       '--out', str(root/'out')])
            self.assertEqual(rc, 0)
            staged = json.loads((root/'out'/'oracle_review.json').read_text())
            self.assertEqual(staged['status'], 'REVIEW_REQUIRED')

    def test_input_loader_rejects_duplicate_json_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = plan()
            (root/'plan.json').write_text(json.dumps(p), encoding='utf-8')
            (root/'oracle.json').write_text('{"schema_version":"0.1","source":{},"cases":[],"cases":[]}',
                                            encoding='utf-8')
            rc = main(['--plan', str(root/'plan.json'), '--oracles', str(root/'oracle.json'),
                       '--out', str(root/'out')])
            self.assertEqual(rc, 2)
            self.assertFalse((root/'out'/'oracle_review.json').exists())

    def test_input_loader_rejects_nan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = plan()
            (root/'plan.json').write_text(json.dumps(p), encoding='utf-8')
            (root/'oracle.json').write_text('{"schema_version":"0.1","source":{},"cases":NaN}',
                                            encoding='utf-8')
            rc = main(['--plan', str(root/'plan.json'), '--oracles', str(root/'oracle.json'),
                       '--out', str(root/'out')])
            self.assertEqual(rc, 2)
            self.assertFalse((root/'out'/'oracle_review.json').exists())


if __name__ == '__main__':
    unittest.main()
