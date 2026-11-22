"""Trusted guest-only 4D-A fixed-form, dependent JSON API workflow.

A single reviewed synthetic row is discovered by its unique literal title. Its
integer id is captured from a live GET response, submitted in a subsequent POST,
and independently checked absent by a final GET. No scripts, dynamic URL
construction, cookies, external egress, credentials, or raw response reporting.
"""
from __future__ import annotations

from http.client import HTTPConnection, HTTPException
import json
import re
from typing import Any, Mapping

MAX_RESPONSE = 1_048_576
_SAFE_PATH = re.compile(r'/[A-Za-z0-9_./-]{1,180}\Z', re.ASCII)
_SAFE_FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,39}\Z', re.ASCII)
_SOURCE_SHA = re.compile(r'[a-f0-9]{40}\Z', re.ASCII)
_DIGEST = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_TEST_ID = re.compile(r'TC-[A-F0-9]{16}\Z', re.ASCII)
_SECRET = re.compile(r'(password|passwd|secret|token|authorization|cookie|credential|api[_-]?key)', re.I)
_ALLOWED = {'GET': {'DISCOVER', 'VERIFY_ABSENT'}, 'POST': {'DELETE_CAPTURED'}}


class DependentWorkflowError(ValueError):
    """Invalid data-only guest instructions or observation envelope."""


def _deny(code: str) -> None:
    raise DependentWorkflowError(code)


def validate_guest_workflow_bundle(bundle: Any, *, expected_source=None,
                                   expected_adapter='php_mysql_v1') -> None:
    if (not isinstance(bundle, dict) or set(bundle) != {
            'schema_version', 'source', 'adapter', 'workflow_catalog_sha256',
            'fixture_catalog_sha256', 'selector', 'capture_field', 'body_field',
            'seed_id', 'steps', 'expected_delete'}
            or bundle['schema_version'] != '0.1' or bundle['adapter'] != expected_adapter
            or bundle['adapter'] != 'php_mysql_v1'):
        _deny('INVALID_DEPENDENT_WORKFLOW_BUNDLE')
    source = bundle['source']
    if (not isinstance(source, dict) or set(source) != {'kind', 'repository', 'commit_sha'}
            or source.get('kind') != 'github'
            or not isinstance(source.get('repository'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', source['repository'])
            or not isinstance(source.get('commit_sha'), str)
            or not _SOURCE_SHA.fullmatch(source['commit_sha'])
            or (expected_source is not None and source != expected_source)
            or any(not isinstance(bundle[k], str) or not _DIGEST.fullmatch(bundle[k])
                   for k in ('workflow_catalog_sha256', 'fixture_catalog_sha256'))):
        _deny('INVALID_DEPENDENT_WORKFLOW_SOURCE')
    selector = bundle['selector']
    if (not isinstance(selector, dict) or set(selector) != {'field', 'value'}
            or not isinstance(selector.get('field'), str)
            or not _SAFE_FIELD.fullmatch(selector['field'])
            or _SECRET.search(selector['field'])
            or not isinstance(selector.get('value'), str)
            or not 1 <= len(selector['value']) <= 100
            or any(ord(c) < 32 for c in selector['value'])
            or bundle['capture_field'] != 'id' or bundle['body_field'] != 'id'
            or type(bundle['seed_id']) is not int or not 1 <= bundle['seed_id'] <= 10**9):
        _deny('INVALID_DEPENDENT_WORKFLOW_CAPTURE')
    steps = bundle['steps']
    if not isinstance(steps, list) or len(steps) != 3:
        _deny('DEPENDENT_WORKFLOW_REQUIRES_THREE_STEPS')
    roles = ('DISCOVER', 'DELETE_CAPTURED', 'VERIFY_ABSENT')
    ids = set()
    for step, role in zip(steps, roles):
        if (not isinstance(step, dict) or set(step) != {
                'role', 'test_id', 'method', 'path'} or step.get('role') != role
                or step.get('method') not in _ALLOWED or role not in _ALLOWED[step['method']]
                or not isinstance(step.get('test_id'), str)
                or not _TEST_ID.fullmatch(step['test_id'])
                or not isinstance(step.get('path'), str)
                or not _SAFE_PATH.fullmatch(step['path'])
                or step['path'].startswith('//')
                or '..' in step['path'].split('/') or step['test_id'] in ids):
            # The same discovered GET test ID may be used for both GET steps.
            if not (isinstance(step, dict) and role == 'VERIFY_ABSENT'
                    and step.get('test_id') == steps[0].get('test_id')
                    and isinstance(step.get('path'), str)
                    and _SAFE_PATH.fullmatch(step['path'])
                    and '..' not in step['path'].split('/')
                    and step.get('method') == 'GET' and step.get('role') == role
                    and set(step) == {'role', 'test_id', 'method', 'path'}):
                _deny('INVALID_DEPENDENT_WORKFLOW_STEP')
        ids.add(step['test_id'])
    if steps[0]['path'] != steps[2]['path'] or steps[0]['test_id'] != steps[2]['test_id']:
        _deny('DEPENDENT_WORKFLOW_VERIFY_MUST_REPEAT_DISCOVERY')
    expectation = bundle['expected_delete']
    if (not isinstance(expectation, dict) or set(expectation) != {'status', 'json_subset'}
            or type(expectation['status']) is not int or expectation['status'] != 200
            or expectation['json_subset'] != {'success': True}):
        _deny('INVALID_DEPENDENT_WORKFLOW_DELETE_EXPECTATION')


def _no_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _deny('WORKFLOW_DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def _json_http(method: str, path: str, body: dict | None, *, port: int) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body, separators=(',', ':')).encode('ascii')
    conn = HTTPConnection('127.0.0.1', port, timeout=8)
    try:
        conn.request(method, path, body=payload,
                     headers={'Content-Type': 'application/json'} if payload is not None else {})
        response = conn.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise DependentWorkflowError('WORKFLOW_RESPONSE_TOO_LARGE')
        if response.status != 200:
            return response.status, None
        return response.status, json.loads(raw.decode('utf-8'),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda _: (_deny('WORKFLOW_NONFINITE_JSON')))
    finally:
        conn.close()


def _normalize_id(value: Any) -> int | None:
    # PHP PDO may return a MySQL INTEGER as JSON number *or* a decimal string.
    # Accept only unambiguous canonical positive decimal IDs; send numeric JSON.
    if type(value) is int and 1 <= value <= 10**9:
        return value
    if (isinstance(value, str) and 1 <= len(value) <= 10
            and value.isascii() and value.isdecimal() and not value.startswith('0')):
        parsed = int(value)
        return parsed if 1 <= parsed <= 10**9 else None
    return None


def _select(rows: Any, field: str, value: str) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 1024:
        _deny('WORKFLOW_INVALID_ROW_LIST')
    if any(not isinstance(row, dict) or len(row) > 32 for row in rows):
        _deny('WORKFLOW_INVALID_ROW')
    return [row for row in rows if row.get(field) == value]


def run_dependent_workflow(bundle: dict, *, expected_source: dict,
                           expected_adapter: str, port: int = 8765,
                           http_client=None) -> dict:
    """Execute sequentially; stop on failure; never publish raw rows or IDs."""
    validate_guest_workflow_bundle(bundle, expected_source=expected_source,
                                   expected_adapter=expected_adapter)
    if type(port) is not int or not 1 <= port <= 65535:
        _deny('INVALID_GUEST_LOCAL_PORT')
    fetch = http_client or _json_http
    obs: dict = {
        'discovery_http': None, 'discovery_matches': None, 'captured_matches_seed': None,
        'mutation_http': None, 'mutation_subset_match': None,
        'verification_http': None, 'remaining_matches': None,
    }
    status = 'INCONCLUSIVE'
    reason = 'REQUEST_OR_JSON_UNAVAILABLE'
    field = bundle['selector']['field']
    value = bundle['selector']['value']
    try:
        get_path, post_path = bundle['steps'][0]['path'], bundle['steps'][1]['path']
        code, data = fetch('GET', get_path, None, port=port)
        obs['discovery_http'] = code
        if code != 200:
            reason = 'DISCOVERY_HTTP_STATUS_MISMATCH'
            status = 'FAIL'
        else:
            matches = _select(data, field, value)
            obs['discovery_matches'] = min(len(matches), 2)
            if len(matches) != 1:
                status, reason = 'FAIL', 'DISCOVERY_UNIQUE_ROW_NOT_FOUND'
            else:
                identity = _normalize_id(matches[0].get(bundle['capture_field']))
                same = identity == bundle['seed_id']
                obs['captured_matches_seed'] = same
                if not same:
                    status, reason = 'FAIL', 'CAPTURE_NOT_MATCHING_REVIEWED_FIXTURE'
                else:
                    code, data = fetch('POST', post_path, {'id': identity}, port=port)
                    obs['mutation_http'] = code
                    if code != 200:
                        status, reason = 'FAIL', 'DELETE_HTTP_STATUS_MISMATCH'
                    else:
                        matched = isinstance(data, dict) and data.get('success') is True
                        obs['mutation_subset_match'] = matched
                        if not matched:
                            status, reason = 'FAIL', 'DELETE_RESPONSE_MISMATCH'
                        else:
                            code, data = fetch('GET', get_path, None, port=port)
                            obs['verification_http'] = code
                            if code != 200:
                                status, reason = 'FAIL', 'VERIFY_HTTP_STATUS_MISMATCH'
                            else:
                                rows = _select(data, field, value)
                                obs['remaining_matches'] = min(len(rows), 2)
                                if any(_normalize_id(row.get('id')) == identity for row in rows):
                                    status, reason = 'FAIL', 'CAPTURED_ID_STILL_PRESENT'
                                elif rows:
                                    status, reason = 'FAIL', 'SELECTOR_REUSED_AFTER_DELETE'
                                else:
                                    status, reason = 'PASS', None
    except (OSError, TimeoutError, HTTPException, ValueError, TypeError, UnicodeError):
        status, reason = 'INCONCLUSIVE', 'REQUEST_OR_JSON_UNAVAILABLE'
    return {
        'schema_version': '0.1', 'stage': 'guest_dependent_api_workflow',
        'source': bundle['source'], 'adapter': bundle['adapter'],
        'workflow_catalog_sha256': bundle['workflow_catalog_sha256'],
        'fixture_catalog_sha256': bundle['fixture_catalog_sha256'],
        'execution': 'IN_GUEST_SEQUENTIAL_LOCALHOST_HTTP',
        'status': status, 'issue_code': reason, 'observations': obs,
    }


def validate_guest_workflow_report(bundle: dict, report: Any) -> None:
    validate_guest_workflow_bundle(bundle, expected_source=bundle['source'],
                                   expected_adapter=bundle['adapter'])
    if (not isinstance(report, dict) or set(report) != {
            'schema_version', 'stage', 'source', 'adapter',
            'workflow_catalog_sha256', 'fixture_catalog_sha256',
            'execution', 'status', 'issue_code', 'observations'}
            or report['schema_version'] != '0.1' or report['stage'] != 'guest_dependent_api_workflow'
            or report['source'] != bundle['source'] or report['adapter'] != bundle['adapter']
            or report['workflow_catalog_sha256'] != bundle['workflow_catalog_sha256']
            or report['fixture_catalog_sha256'] != bundle['fixture_catalog_sha256']
            or report['execution'] != 'IN_GUEST_SEQUENTIAL_LOCALHOST_HTTP'
            or report['status'] not in ('PASS', 'FAIL', 'INCONCLUSIVE')
            or (report['issue_code'] is not None and
                (not isinstance(report['issue_code'], str) or len(report['issue_code']) > 90))):
        _deny('INVALID_DEPENDENT_WORKFLOW_REPORT')
    obs = report['observations']
    if not isinstance(obs, dict) or set(obs) != {
        'discovery_http', 'discovery_matches', 'captured_matches_seed',
        'mutation_http', 'mutation_subset_match', 'verification_http', 'remaining_matches'}:
        _deny('INVALID_DEPENDENT_WORKFLOW_OBSERVATIONS')
    for key in ('discovery_http', 'mutation_http', 'verification_http'):
        if obs[key] is not None and (type(obs[key]) is not int or not 100 <= obs[key] <= 599):
            _deny('INVALID_DEPENDENT_WORKFLOW_HTTP_EVIDENCE')
    for key in ('discovery_matches', 'remaining_matches'):
        if obs[key] is not None and (type(obs[key]) is not int or obs[key] not in (0, 1, 2)):
            _deny('INVALID_DEPENDENT_WORKFLOW_COUNT')
    for key in ('captured_matches_seed', 'mutation_subset_match'):
        if obs[key] is not None and type(obs[key]) is not bool:
            _deny('INVALID_DEPENDENT_WORKFLOW_BOOLEAN')
    success = obs == {
        'discovery_http': 200, 'discovery_matches': 1, 'captured_matches_seed': True,
        'mutation_http': 200, 'mutation_subset_match': True,
        'verification_http': 200, 'remaining_matches': 0,
    }
    if (report['status'] == 'PASS') != success:
        _deny('DEPENDENT_WORKFLOW_RESULT_DISAGREES_WITH_EVIDENCE')
    if (report['status'] == 'PASS' and report['issue_code'] is not None
            or report['status'] != 'PASS' and report['issue_code'] is None):
        _deny('DEPENDENT_WORKFLOW_ISSUE_CODE_INVALID')
