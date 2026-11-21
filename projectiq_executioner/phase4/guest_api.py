"""Trusted guest-only declarative API runner; never run student code on host.

Transport: localhost HTTP, no redirect following, no cookies, no arbitrary
headers, no credentials, bounded JSON/form data and bounded response reads.
Operator review and runtime isolation are independently enforced by the host.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import urlencode, parse_qsl

SCHEMA = '0.1'
MAX_TESTS = 8
MAX_REQUEST = 16_384
MAX_RESPONSE = 1_048_576
_STATUSES = frozenset({200, 201, 202, 204, 400, 401, 403, 404, 409, 422, 429})
_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE'})
_ADAPTERS = frozenset({'static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1',
                       'node_express_offline_v1', 'node_fullstack_offline_v1',
                       'python_web_offline_v1'})
_ID = re.compile(r'OC-[A-F0-9]{16}\Z')
_TEST_ID = re.compile(r'TC-[A-F0-9]{16}\Z')
_SHA = re.compile(r'[a-f0-9]{40}\Z')
_URL = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/%?\-]{0,510}\Z")
_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,63}\Z')
_SECRET = re.compile(r'(password|passwd|secret|token|authorization|cookie|credential|api[_-]?key)', re.I)


class GuestApiError(ValueError):
    """Rejected trusted-transport request or report contract."""


def _reject(code: str) -> None:
    raise GuestApiError(code)


def _canonical(obj: Any) -> bytes:
    try:
        return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                          allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError):
        _reject('INVALID_JSON')


def _validate_json(node: Any, *, depth: int = 0) -> None:
    if depth > 8:
        _reject('JSON_DEPTH_LIMIT')
    if isinstance(node, dict):
        if len(node) > 64:
            _reject('JSON_OBJECT_LIMIT')
        for key, value in node.items():
            if not isinstance(key, str) or not _KEY.fullmatch(key) or _SECRET.search(key):
                _reject('UNSAFE_JSON_FIELD')
            _validate_json(value, depth=depth+1)
    elif isinstance(node, list):
        if len(node) > 64:
            _reject('JSON_ARRAY_LIMIT')
        for item in node:
            _validate_json(item, depth=depth+1)
    elif node is None or type(node) in (str, int, float, bool):
        if isinstance(node, str) and len(node) > 1024:
            _reject('JSON_STRING_LIMIT')
        if type(node) in (int, float) and (abs(node) > 10**12 or not math.isfinite(node)):
            _reject('JSON_NUMERIC_LIMIT')
    else:
        _reject('INVALID_JSON_TYPE')


def validate_api_bundle(bundle: Mapping[str, Any], *, expected_source=None,
                        expected_adapter=None) -> None:
    if (not isinstance(bundle, Mapping) or set(bundle) != {'schema_version', 'source', 'adapter', 'tests'}
            or bundle.get('schema_version') != SCHEMA):
        _reject('INVALID_API_BUNDLE')
    src = bundle['source']
    if (not isinstance(src, Mapping) or set(src) != {'kind', 'repository', 'commit_sha'}
            or src.get('kind') != 'github'
            or not isinstance(src.get('repository'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', src['repository'])
            or not isinstance(src.get('commit_sha'), str) or not _SHA.fullmatch(src['commit_sha'])
            or bundle['adapter'] not in _ADAPTERS):
        _reject('INVALID_API_SOURCE')
    if expected_source is not None and src != expected_source:
        _reject('API_SOURCE_MISMATCH')
    if expected_adapter is not None and bundle['adapter'] != expected_adapter:
        _reject('API_ADAPTER_MISMATCH')
    tests = bundle['tests']
    if not isinstance(tests, list) or not 1 <= len(tests) <= MAX_TESTS:
        _reject('INVALID_API_TEST_COUNT')
    seen = set()
    for test in tests:
        if (not isinstance(test, Mapping)
                or set(test) != {'id', 'phase3_test_id', 'method', 'path', 'body_kind', 'body', 'assertions'}
                or not isinstance(test['id'], str) or not _ID.fullmatch(test['id'])
                or test['id'] in seen
                or not isinstance(test['phase3_test_id'], str)
                or not _TEST_ID.fullmatch(test['phase3_test_id'])
                or test['method'] not in _METHODS):
            _reject('INVALID_API_TEST')
        seen.add(test['id'])
        path = test['path']
        if (not isinstance(path, str) or not _URL.fullmatch(path)
                or path.startswith('//') or '\\' in path or '#' in path
                or any(seg in ('.', '..') for seg in path.split('?', 1)[0].split('/'))
                or '\r' in path or '\n' in path or '*' in path):
            _reject('UNSAFE_API_PATH')
        route, separator, query = path.partition('?')
        if '%' in route or (separator and ('?' in query or re.search(r'%(?![a-fA-F0-9]{2})', query))):
            _reject('UNSAFE_API_PATH')
        if separator:
            pairs = parse_qsl(query, keep_blank_values=True)
            if len(pairs) > 16 or any(not _KEY.fullmatch(k) or len(v) > 256
                                      for k, v in pairs):
                _reject('UNSAFE_API_QUERY')
        kind, body = test['body_kind'], test['body']
        if kind == 'NONE':
            if body is not None:
                _reject('UNEXPECTED_BODY')
        elif kind == 'JSON':
            if not isinstance(body, (dict, list)):
                _reject('INVALID_JSON_BODY')
            _validate_json(body)
            if len(_canonical(body)) > MAX_REQUEST:
                _reject('REQUEST_TOO_LARGE')
        elif kind == 'FORM':
            if not isinstance(body, dict) or len(body) > 32:
                _reject('INVALID_FORM_BODY')
            for k, v in body.items():
                if (not isinstance(k, str) or not _KEY.fullmatch(k) or _SECRET.search(k)
                        or not isinstance(v, str) or len(v) > 1024):
                    _reject('UNSAFE_FORM_FIELD')
            if len(urlencode(sorted(body.items())).encode('utf-8')) > MAX_REQUEST:
                _reject('FORM_TOO_LARGE')
        else:
            _reject('UNSUPPORTED_API_BODY_KIND')
        if test['method'] in ('GET', 'DELETE') and kind != 'NONE':
            _reject('REQUEST_BODY_NOT_ALLOWED')
        assertions = test['assertions']
        if not isinstance(assertions, list) or not 1 <= len(assertions) <= 2:
            _reject('INVALID_API_ASSERTION_COUNT')
        kinds = set()
        for a in assertions:
            if not isinstance(a, Mapping) or set(a) != {'kind', 'expected'} or a['kind'] in kinds:
                _reject('INVALID_API_ASSERTION')
            kinds.add(a['kind'])
            if a['kind'] == 'HTTP_STATUS_EQUALS':
                if type(a['expected']) is not int or a['expected'] not in _STATUSES:
                    _reject('INVALID_EXPECTED_STATUS')
            elif a['kind'] == 'JSON_BODY_SUBSET_EQUALS':
                if not isinstance(a['expected'], dict):
                    _reject('INVALID_EXPECTED_JSON')
                _validate_json(a['expected'])
                if len(_canonical(a['expected'])) > MAX_REQUEST:
                    _reject('EXPECTED_JSON_TOO_LARGE')
            else:
                _reject('UNSUPPORTED_API_ASSERTION')
        if 'HTTP_STATUS_EQUALS' not in kinds:
            _reject('API_STATUS_ASSERTION_REQUIRED')
        if ('JSON_BODY_SUBSET_EQUALS' in kinds and next(a['expected'] for a in assertions
                if a['kind'] == 'HTTP_STATUS_EQUALS') == 204):
            _reject('JSON_ASSERTION_ON_NO_CONTENT')


def _pairs(items):
    result = {}
    for k, v in items:
        if k in result:
            raise ValueError('duplicate JSON key')
        result[k] = v
    return result


def _json_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _json_subset(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _json_subset(a, e) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def run_api_tests(bundle: Mapping[str, Any], *, expected_source: Mapping[str, Any],
                  expected_adapter: str, port: int = 8765, timeout_s: float = 3.0) -> dict:
    validate_api_bundle(bundle, expected_source=expected_source,
                        expected_adapter=expected_adapter)
    if type(port) is not int or not 1 <= port <= 65535 or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 5:
        _reject('INVALID_API_TRANSPORT_LIMITS')
    results = []
    for test in bundle['tests']:
        headers = {'User-Agent': 'ProjectIQ-Trusted-API-Check'}
        kind = test['body_kind']
        if kind == 'JSON':
            body = _canonical(test['body'])
            headers['Content-Type'] = 'application/json'
        elif kind == 'FORM':
            body = urlencode(sorted(test['body'].items())).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            body = None
        conn = HTTPConnection('127.0.0.1', port, timeout=timeout_s)
        status, received, issue = None, b'', None
        try:
            conn.request(test['method'], test['path'], body=body, headers=headers)
            response = conn.getresponse()
            status = int(response.status)
            received = response.read(MAX_RESPONSE + 1)
            if len(received) > MAX_RESPONSE:
                issue = 'API_BODY_LIMIT_EXCEEDED'
        except (OSError, HTTPException, ValueError, TimeoutError):
            issue = 'LOOPBACK_API_UNAVAILABLE'
        finally:
            conn.close()
        checks = []
        if issue is None:
            for a in test['assertions']:
                if a['kind'] == 'HTTP_STATUS_EQUALS':
                    passed = status == a['expected']
                else:
                    try:
                        value = json.loads(received.decode('utf-8'), object_pairs_hook=_pairs,
                                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        passed = _json_subset(value, a['expected'])
                    except (ValueError, UnicodeError, RecursionError):
                        passed = False
                checks.append({'kind': a['kind'], 'result': 'PASS' if passed else 'FAIL'})
        result = ('INCONCLUSIVE' if issue else
                  'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL')
        results.append({'test_id': test['id'], 'result': result, 'http_status': status,
                        'body_sha256': sha256(received).hexdigest() if not issue else None,
                        'assertions': checks, 'issue_code': issue})
    counts = Counter(item['result'] for item in results)
    return {
        'schema_version': SCHEMA, 'phase': 4, 'stage': 'reviewed_guest_api_tests',
        'execution': 'LOOPBACK_API_REQUESTS_PERFORMED',
        'runtime_attestation': 'NOT_EVALUATED_BY_API_INTERPRETER',
        'source': dict(bundle['source']), 'adapter': bundle['adapter'],
        'results': results, 'summary': {'total': len(results), 'passed': counts['PASS'],
            'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']},
        'status': 'INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE',
        'limitations': ['Operator-reviewed scenario expectations, not proven ground truth.',
                        'No cookies, fixtures or external network requests.',
                        'Runtime sandbox security is independently enforced by Phase 2.'],
    }
