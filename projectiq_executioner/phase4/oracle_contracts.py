"""Phase 4C-A: bounded, declarative functional-oracle *design* contracts.

No student source is executed or imported. An oracle specification is not evidence
that its expectation is correct, not an approval, and not a 4B guest test bundle.
The 4C executor must independently revalidate and bind reviewed fixtures.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import urlencode

from ..phase3.contracts import TestPlanContractError, validate_test_plan

SCHEMA_VERSION = '0.1'
MAX_CASES = 64
MAX_JSON_BYTES = 16_384
MAX_QUERY_PARAMS = 16
MAX_BODY_DEPTH = 8
_ALLOWED_METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}
_ALLOWED_STATUS_CODES = {200, 201, 202, 204, 400, 401, 403, 404, 409, 422, 429}
_ALLOWED_CATEGORIES = {'HTTP_READINESS', 'HTTP_ROUTE', 'UI_PAGE'}
_SAFE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9 _.,:()/-]{0,99}$')
_SAFE_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
_SAFE_PARAM = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_ROUTE_PARAM = re.compile(r':([A-Za-z_][A-Za-z0-9_]*)')
_SAFE_FIXTURE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
_SAFE_PATH = re.compile(r'^/[A-Za-z0-9._~!$&\'()*+,;=:@/%?-]{0,510}$')
_SENSITIVE_KEY = re.compile(r'(password|passwd|secret|token|authorization|cookie|credential|api[_-]?key)', re.I)
_MAX_FIXTURE_REFS = 16


class OracleContractError(ValueError):
    """An operator-authored oracle file is malformed or not tied to discovery."""


def _fail(code: str) -> None:
    raise OracleContractError(code)


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise OracleContractError('NON_CANONICAL_JSON') from exc


def digest(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _walk_json(value: Any, *, depth: int, fixtures: set[str],
               allow_fixture: bool) -> None:
    """Check JSON types, shape and secrets, with finite traversal budgets."""
    if depth > MAX_BODY_DEPTH:
        _fail('JSON_NESTING_LIMIT')
    if isinstance(value, dict):
        if set(value) == {'$fixture_ref'}:
            if not allow_fixture:
                _fail('EXPECTED_OUTPUT_CANNOT_USE_FIXTURE_REFERENCE')
            ref = value['$fixture_ref']
            if not isinstance(ref, str) or not _SAFE_FIXTURE.fullmatch(ref):
                _fail('INVALID_FIXTURE_REFERENCE')
            fixtures.add(ref)
            if len(fixtures) > _MAX_FIXTURE_REFS:
                _fail('TOO_MANY_FIXTURE_REFERENCES')
            return
        if len(value) > 64:
            _fail('JSON_OBJECT_TOO_LARGE')
        for key, item in value.items():
            if not isinstance(key, str) or not _SAFE_KEY.fullmatch(key) or key == '$fixture_ref':
                _fail('INVALID_JSON_PROPERTY')
            if _SENSITIVE_KEY.search(key) and not (isinstance(item, dict)
                                                    and set(item) == {'$fixture_ref'}
                                                    and allow_fixture):
                _fail('SENSITIVE_LITERAL_NOT_ALLOWED')
            _walk_json(item, depth=depth+1, fixtures=fixtures,
                       allow_fixture=allow_fixture)
    elif isinstance(value, list):
        if len(value) > 64:
            _fail('JSON_ARRAY_TOO_LARGE')
        for item in value:
            _walk_json(item, depth=depth+1, fixtures=fixtures,
                       allow_fixture=allow_fixture)
    elif value is None or type(value) in (str, bool, int, float):
        if isinstance(value, str) and len(value) > 1024:
            _fail('JSON_STRING_TOO_LONG')
        if type(value) is int and abs(value) > 10**12:
            _fail('JSON_NUMBER_OUT_OF_RANGE')
        if type(value) is float and (not math.isfinite(value) or abs(value) > 10**12):
            _fail('JSON_NUMBER_OUT_OF_RANGE')
    else:
        _fail('INVALID_JSON_VALUE_TYPE')


def _validate_json(value: Any, *, allow_fixture: bool) -> list[str]:
    fixtures: set[str] = set()
    _walk_json(value, depth=0, fixtures=fixtures, allow_fixture=allow_fixture)
    if len(canonical_bytes(value)) > MAX_JSON_BYTES:
        _fail('JSON_BYTE_LIMIT')
    return sorted(fixtures)


def _resolved_target(path: str, method: str, request: Mapping[str, Any]) -> tuple[str, list[str]]:
    if not isinstance(request, Mapping) or set(request) != {'path_params', 'query', 'json_body'}:
        _fail('INVALID_REQUEST_SHAPE')
    params = request['path_params']
    query = request['query']
    body = request['json_body']
    if not isinstance(params, dict) or len(params) > 12:
        _fail('INVALID_ROUTE_PARAMETERS')
    route_names = _ROUTE_PARAM.findall(path)
    if len(set(route_names)) != len(route_names) or set(params) != set(route_names):
        _fail('ROUTE_PARAMETER_BINDING_MISMATCH')
    if any(not isinstance(k, str) or not _SAFE_KEY.fullmatch(k)
           or not isinstance(v, str) or not _SAFE_PARAM.fullmatch(v)
           for k, v in params.items()):
        _fail('UNSAFE_ROUTE_PARAMETER')
    replaced = _ROUTE_PARAM.sub(lambda match: params[match.group(1)], path)
    if ':' in replaced or '*' in replaced or '%' in replaced or not _SAFE_PATH.fullmatch(replaced):
        _fail('UNSAFE_HTTP_PATH')
    if (replaced.startswith('//') or '//' in replaced or
            any(part in ('.', '..') for part in replaced.split('/'))):
        _fail('UNSAFE_HTTP_PATH')
    if not isinstance(query, dict) or len(query) > MAX_QUERY_PARAMS:
        _fail('INVALID_QUERY_PARAMETERS')
    for key, value in query.items():
        if (not isinstance(key, str) or not _SAFE_KEY.fullmatch(key)
                or _SENSITIVE_KEY.search(key)
                or type(value) not in (str, int, bool)
                or (type(value) is int and abs(value) > 10**12)
                or (isinstance(value, str) and len(value) > 256)):
            _fail('UNSAFE_QUERY_VALUE')
    if query:
        # Sorting makes route encoding independent of source JSON key order.
        params_for_encoding = [(key, str(value).lower() if type(value) is bool else value)
                               for key, value in sorted(query.items())]
        replaced += '?' + urlencode(params_for_encoding, doseq=False)
    if len(replaced) > 512 or not _SAFE_PATH.fullmatch(replaced):
        _fail('UNSAFE_HTTP_PATH')
    if method in {'GET', 'DELETE'} and body is not None:
        _fail('REQUEST_BODY_NOT_ALLOWED_FOR_METHOD')
    fixture_refs = _validate_json(body, allow_fixture=True) if body is not None else []
    if body is not None and not isinstance(body, (dict, list)):
        _fail('BODY_MUST_BE_JSON_OBJECT_OR_ARRAY')
    return replaced, fixture_refs


def validate_oracle_catalog(catalog: Mapping[str, Any], phase3_plan: Mapping[str, Any]) -> list[dict]:
    """Bind *proposed* functional oracle scenarios to existing Phase 3 cases.

    A validated catalog is still UNREVIEWED and NOT EXECUTABLE. All source,
    method and route targets must have been independently discovered by Phase 3.
    """
    try:
        validate_test_plan(phase3_plan)
    except (TestPlanContractError, TypeError, ValueError) as exc:
        raise OracleContractError('INVALID_PHASE3_PLAN') from exc
    if (not isinstance(catalog, Mapping)
            or set(catalog) != {'schema_version', 'source', 'cases'}
            or catalog['schema_version'] != SCHEMA_VERSION):
        _fail('INVALID_ORACLE_CATALOG_SCHEMA')
    if catalog['source'] != phase3_plan['source']:
        _fail('ORACLE_SOURCE_MISMATCH')
    if len(canonical_bytes(catalog)) > 256_000:
        _fail('ORACLE_CATALOG_TOO_LARGE')
    cases = catalog['cases']
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        _fail('ORACLE_CASE_LIMIT')
    targets = {x['id']: x for x in phase3_plan['test_cases']}
    compiled = []
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {'name', 'test_id', 'request', 'expected'}:
            _fail('INVALID_ORACLE_CASE_SHAPE')
        name, target_id = case['name'], case['test_id']
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            _fail('INVALID_ORACLE_NAME')
        if not isinstance(target_id, str) or target_id not in targets:
            _fail('UNKNOWN_PHASE3_TEST_ID')
        target_case = targets[target_id]
        method = target_case['target']['method']
        path = target_case['target']['path']
        if (target_case['category'] not in _ALLOWED_CATEGORIES
                or target_case['status'] == 'UNSUPPORTED_BY_RUNNER'
                or method not in _ALLOWED_METHODS or not isinstance(path, str)):
            _fail('UNSUPPORTED_FUNCTIONAL_ORACLE_TARGET')
        request = case['request']
        url, fixture_refs = _resolved_target(path, method, request)
        expected = case['expected']
        if not isinstance(expected, Mapping) or set(expected) != {'status', 'json_subset'}:
            _fail('INVALID_EXPECTED_RESPONSE_SHAPE')
        status, subset = expected['status'], expected['json_subset']
        if type(status) is not int or status not in _ALLOWED_STATUS_CODES:
            _fail('EXPECTED_STATUS_NOT_SUPPORTED')
        if subset is not None:
            if status == 204 or not isinstance(subset, dict):
                _fail('UNSUPPORTED_EXPECTED_JSON_SUBSET')
            _validate_json(subset, allow_fixture=False)
        fp = digest({'name': name, 'test_id': target_id,
                     'request': request, 'expected': expected})
        identity = 'OC-' + fp[:16].upper()
        if identity in seen:
            _fail('DUPLICATE_ORACLE_CASE')
        seen.add(identity)
        assertions = [{'kind': 'HTTP_STATUS_EQUALS', 'expected': status}]
        if subset is not None:
            assertions.append({'kind': 'JSON_BODY_SUBSET_EQUALS', 'expected': subset})
        compiled.append({
            'id': identity, 'name': name, 'phase3_test_id': target_id,
            'request': {'method': method, 'path': url, 'json_body': request['json_body']},
            'assertions': assertions, 'fixture_refs': fixture_refs,
            'oracle_basis': 'OPERATOR_SUPPLIED_UNREVIEWED',
            'execution': 'NOT_PERFORMED',
            'blockers': ['ORACLE_REVIEW_REQUIRED', 'PHASE4C_EXECUTOR_NOT_IMPLEMENTED'] + (
                ['RUNTIME_FIXTURE_PROVISIONING_REQUIRED'] if fixture_refs else []),
        })
    return sorted(compiled, key=lambda x: (x['phase3_test_id'], x['id']))


def make_oracle_template(plan: Mapping[str, Any]) -> tuple[dict, dict]:
    """Empty operator-editable catalog and independently discovered target list."""
    try:
        validate_test_plan(plan)
    except (TestPlanContractError, TypeError, ValueError) as exc:
        raise OracleContractError('INVALID_PHASE3_PLAN') from exc
    catalog = {'schema_version': SCHEMA_VERSION, 'source': dict(plan['source']), 'cases': []}
    available = [
        {'test_id': c['id'], 'method': c['target']['method'],
         'path': c['target']['path'], 'status': c['status']}
        for c in plan['test_cases']
        if (c['category'] in _ALLOWED_CATEGORIES
            and c['status'] != 'UNSUPPORTED_BY_RUNNER'
            and c['target']['method'] in _ALLOWED_METHODS)
    ]
    targets = {'schema_version': SCHEMA_VERSION, 'source': dict(plan['source']),
               'eligible_targets': sorted(available, key=lambda x: (x['method'], x['path'], x['test_id'])),
               'execution': 'NOT_PERFORMED'}
    return catalog, targets
