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

# D2 is deliberately a separate interpreter mode. Ordinary 4C-C tests still
# have NO cookies or credentials, and their existing behavior is unchanged.
_AUTH_GUEST_KEYS = frozenset({'schema_version', 'source', 'adapter',
                              'auth_catalog_sha256', 'oracle_catalog_sha256',
                              'registration', 'login', 'unauthenticated', 'authenticated'})
_AUTH_STATUSES = frozenset({200, 201, 202, 204, 302, 303, 400, 401, 403, 404, 409, 422})
_COOKIE_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,63}\Z', re.ASCII)
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9_!~*'().%+\-:/=]{1,512}\Z", re.ASCII)


def _validate_auth_step(step, role):
    if (not isinstance(step, dict) or set(step) !=
            {'phase3_test_id', 'method', 'path', 'body_kind', 'fields', 'expected', 'role'}
            or step['role'] != role or not isinstance(step['phase3_test_id'], str)
            or not _TEST_ID.fullmatch(step['phase3_test_id'])
            or step['method'] != 'POST'
            or not isinstance(step['path'], str)
            or not _URL.fullmatch(step['path']) or step['path'].startswith('//')
            or '%' in step['path'] or '?' in step['path'] or '#' in step['path']
            or any(seg in ('.', '..') for seg in step['path'].split('/'))
            or step['body_kind'] not in ('JSON', 'FORM')):
        _reject('INVALID_AUTH_STEP_ROUTE')
    fields = step['fields']
    if (not isinstance(fields, dict) or not 2 <= len(fields) <= 4
            or not set(fields).issubset({'name', 'email', 'password', 'role'})
            or set(fields).isdisjoint({'email', 'password'})):
        _reject('INVALID_AUTH_FIELD_SET')
    if fields.get('email') != {'$generated': 'email'} or fields.get('password') != {'$generated': 'password'}:
        _reject('GUEST_GENERATED_CREDENTIALS_REQUIRED')
    if role == 'LOGIN' and set(fields) != {'email', 'password'}:
        _reject('LOGIN_MUST_USE_ONLY_GENERATED_CREDENTIALS')
    for key, binding in fields.items():
        if key in ('email', 'password'):
            continue
        if not isinstance(binding, dict) or set(binding) != {'$literal'}:
            _reject('UNSAFE_AUTH_LITERAL')
        value = binding['$literal']
        if (not isinstance(value, str) or not 1 <= len(value) <= 80
                or not re.fullmatch(r'[A-Za-z0-9 _-]{1,80}', value, re.ASCII)
                or (key == 'role' and value != 'user')):
            _reject('UNSAFE_AUTH_LITERAL')
    expected = step['expected']
    if (not isinstance(expected, dict) or set(expected) != {'status', 'json_subset'}
            or type(expected['status']) is not int
            or expected['status'] not in _AUTH_STATUSES):
        _reject('INVALID_AUTH_EXPECTATION')
    subset = expected['json_subset']
    if subset is not None:
        if not isinstance(subset, dict) or expected['status'] in (204, 302, 303):
            _reject('INVALID_AUTH_JSON_EXPECTATION')
        _validate_json(subset)
        if len(_canonical(subset)) > MAX_REQUEST:
            _reject('AUTH_EXPECTATION_TOO_LARGE')
    if role == 'LOGIN' and (not isinstance(subset, dict)
                             or subset.get('success') is not True):
        _reject('LOGIN_SUCCESS_ASSERTION_REQUIRED')


def validate_auth_guest_bundle(bundle, *, expected_source=None, expected_adapter=None):
    """Fail closed on the compact guest-only auth instructions."""
    if (not isinstance(bundle, dict) or set(bundle) != _AUTH_GUEST_KEYS
            or bundle.get('schema_version') != '0.1'
            or not isinstance(bundle.get('source'), dict)
            or not isinstance(bundle.get('adapter'), str)
            or bundle.get('adapter') not in {
                'php_mysql_v1', 'python_web_offline_v1',
                'node_express_offline_v1', 'node_fullstack_offline_v1'}
            or any(not isinstance(bundle.get(k), str) or
                   not re.fullmatch(r'[a-f0-9]{64}', bundle[k])
                   for k in ('auth_catalog_sha256', 'oracle_catalog_sha256'))):
        _reject('INVALID_AUTH_GUEST_BUNDLE')
    if expected_source is not None and bundle['source'] != expected_source:
        _reject('AUTH_GUEST_SOURCE_MISMATCH')
    if expected_adapter is not None and bundle['adapter'] != expected_adapter:
        _reject('AUTH_GUEST_ADAPTER_MISMATCH')
    _validate_auth_step(bundle['registration'], 'REGISTRATION')
    _validate_auth_step(bundle['login'], 'LOGIN')
    if bundle['registration']['phase3_test_id'] == bundle['login']['phase3_test_id']:
        _reject('AUTH_REGISTRATION_LOGIN_ROUTE_CONFLICT')
    validate_api_bundle({'schema_version': SCHEMA, 'source': bundle['source'],
                         'adapter': bundle['adapter'],
                         'tests': [bundle['unauthenticated'], bundle['authenticated']]},
                        expected_source=bundle['source'],
                        expected_adapter=bundle['adapter'])
    unauth, auth = bundle['unauthenticated'], bundle['authenticated']
    if (unauth['method'] != auth['method'] or unauth['path'] != auth['path']
            or unauth['body_kind'] != auth['body_kind'] or unauth['body'] != auth['body']
            or unauth['method'] not in ('GET', 'POST')
            or (unauth['method'] == 'POST' and not
                (unauth['body_kind'] == 'JSON' and unauth['body'] == {}))):
        _reject('AUTH_PROTECTED_REQUEST_MISMATCH')
    for item in (unauth, auth):
        subset = next((x['expected'] for x in item['assertions']
                       if x['kind'] == 'JSON_BODY_SUBSET_EQUALS'), None)
        if not isinstance(subset, dict) or not isinstance(subset.get('error'), str):
            _reject('AUTH_PROTECTED_ERROR_ASSERTION_REQUIRED')


def _auth_http(port: int, method: str, path: str, kind: str, body, cookie: str | None,
               timeout_s: float):
    """Only loopback HTTP. Never return request data in errors/evidence."""
    headers = {'User-Agent': 'ProjectIQ-Trusted-Auth-Check'}
    if kind == 'JSON':
        payload = _canonical(body)
        headers['Content-Type'] = 'application/json'
    elif kind == 'FORM':
        payload = urlencode(sorted(body.items())).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    else:
        payload = None
    if payload is not None and len(payload) > MAX_REQUEST:
        return None, b'', [], 'AUTH_BODY_LIMIT_EXCEEDED'
    if cookie is not None:
        headers['Cookie'] = cookie
    conn = HTTPConnection('127.0.0.1', port, timeout=timeout_s)
    try:
        conn.request(method, path, body=payload, headers=headers)
        reply = conn.getresponse()
        status = int(reply.status)
        headers_returned = reply.getheaders()
        raw = reply.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return status, b'', [], 'AUTH_BODY_LIMIT_EXCEEDED'
        return status, raw, headers_returned, None
    except (OSError, HTTPException, ValueError, TimeoutError):
        return None, b'', [], 'AUTH_LOOPBACK_UNAVAILABLE'
    finally:
        conn.close()


def _session_cookie(headers):
    """Accept only bounded host-only, non-Secure cookies for local HTTP.

    Never copy Domain cookies, redirects, unknown headers or raw cookie values
    to the host report. Path=/ is deliberately required for cross-route flow.
    """
    from http.cookies import SimpleCookie, CookieError
    collected = {}
    for key, value in headers:
        if key.lower() != 'set-cookie':
            continue
        if not isinstance(value, str) or len(value) > 512 or '\r' in value or '\n' in value:
            _reject('SESSION_COOKIE_UNSAFE')
        try:
            parsed = SimpleCookie()
            parsed.load(value)
        except CookieError:
            _reject('SESSION_COOKIE_UNSAFE')
        if len(parsed) != 1:
            _reject('SESSION_COOKIE_UNSAFE')
        morsel = next(iter(parsed.values()))
        if (morsel['domain'] or morsel['secure'] or morsel['path'] not in ('', '/')
                or not _COOKIE_NAME.fullmatch(morsel.key)
                or not _COOKIE_VALUE.fullmatch(morsel.value)):
            _reject('SESSION_COOKIE_UNSAFE')
        collected[morsel.key] = morsel.value
        if len(collected) > 8:
            _reject('SESSION_COOKIE_UNSAFE')
    cookie = '; '.join(f'{k}={v}' for k, v in sorted(collected.items()))
    if len(cookie) > 1024:
        _reject('SESSION_COOKIE_UNSAFE')
    return cookie or None


def _auth_checks(status, raw, assertions, *, has_session_cookie=None):
    checks = []
    for spec in assertions:
        if spec['kind'] == 'HTTP_STATUS_EQUALS':
            passed = status == spec['expected']
        elif spec['kind'] == 'SESSION_COOKIE_PRESENT':
            passed = has_session_cookie is True
        else:
            try:
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                passed = _json_subset(value, spec['expected'])
            except (ValueError, UnicodeError, RecursionError):
                passed = False
        checks.append({'kind': spec['kind'], 'result': 'PASS' if passed else 'FAIL'})
    return checks


def run_auth_flow(bundle, *, expected_source, expected_adapter,
                  port: int = 8765, timeout_s: float = 3.0,
                  postcondition_observer=None, authenticated_callback=None):
    """Fixed four-step authentication contrast; all secrets stay inside guest."""
    import secrets
    validate_auth_guest_bundle(bundle, expected_source=expected_source,
                               expected_adapter=expected_adapter)
    if postcondition_observer is not None and not callable(postcondition_observer):
        _reject('INVALID_POSTCONDITION_OBSERVER')
    if authenticated_callback is not None and not callable(authenticated_callback):
        _reject('INVALID_AUTHENTICATED_CALLBACK')
    if type(port) is not int or not 1 <= port <= 65535 or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 5:
        _reject('INVALID_AUTH_TRANSPORT_LIMITS')
    # Unique per-VM credentials, never emitted in reports or exceptions.
    generated = {'email': 'piq-' + secrets.token_hex(12) + '@example.invalid',
                 'password': secrets.token_urlsafe(24)}
    steps = [('UNAUTHENTICATED', bundle['unauthenticated']),
             ('REGISTRATION', bundle['registration']),
             ('LOGIN', bundle['login']),
             ('AUTHENTICATED', bundle['authenticated'])]
    results = []
    unauth_ok, registered, logged_in, session_cookie = False, False, False, None
    for name, spec in steps:
        if (name == 'REGISTRATION' and not unauth_ok or
                name == 'LOGIN' and not registered or
                name == 'AUTHENTICATED' and not logged_in):
            results.append({'step': name, 'result': 'INCONCLUSIVE', 'http_status': None,
                            'assertions': [], 'issue_code': 'PREREQUISITE_NOT_SATISFIED'})
            continue
        if name == 'REGISTRATION' and postcondition_observer is not None:
            postcondition_observer('BEFORE', generated['email'])
        if name in ('REGISTRATION', 'LOGIN'):
            kind = spec['body_kind']
            body = {k: (generated[v['$generated']] if '$generated' in v else v['$literal'])
                    for k, v in spec['fields'].items()}
            expected_assertions = [
                {'kind': 'HTTP_STATUS_EQUALS', 'expected': spec['expected']['status']}]
            if spec['expected']['json_subset'] is not None:
                expected_assertions.append({'kind': 'JSON_BODY_SUBSET_EQUALS',
                                            'expected': spec['expected']['json_subset']})
            if name == 'LOGIN':
                expected_assertions.append({'kind': 'SESSION_COOKIE_PRESENT', 'expected': True})
            method, path = spec['method'], spec['path']
        else:
            kind, body, expected_assertions = spec['body_kind'], spec['body'], spec['assertions']
            method, path = spec['method'], spec['path']
        status, received, headers, issue = _auth_http(port, method, path, kind, body,
                                                       session_cookie if name == 'AUTHENTICATED' else None,
                                                       timeout_s)
        if name == 'LOGIN' and issue is None:
            try:
                session_cookie = _session_cookie(headers)
            except GuestApiError:
                issue = 'SESSION_COOKIE_UNSAFE'
        checks = _auth_checks(status, received, expected_assertions,
                              has_session_cookie=session_cookie is not None) if issue is None else []
        outcome = ('INCONCLUSIVE' if issue else
                   'PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL')
        results.append({'step': name, 'result': outcome, 'http_status': status,
                        'assertions': checks, 'issue_code': issue})
        if name == 'UNAUTHENTICATED':
            unauth_ok = outcome == 'PASS'
        elif name == 'REGISTRATION':
            registered = outcome == 'PASS'
            if registered and postcondition_observer is not None:
                postcondition_observer('AFTER', generated['email'])
        elif name == 'LOGIN':
            logged_in = outcome == 'PASS' and session_cookie is not None
    if (authenticated_callback is not None and session_cookie is not None
            and len(results) == 4 and all(row['result'] == 'PASS' for row in results)):
        # Secret session and email stay inside the trusted Linux guest callback.
        authenticated_callback(session_cookie, generated['email'])
    counts = Counter(item['result'] for item in results)
    return {
        'schema_version': '0.1', 'phase': 4, 'stage': 'guest_reviewed_auth_flow',
        'source': dict(bundle['source']), 'adapter': bundle['adapter'],
        'auth_catalog_sha256': bundle['auth_catalog_sha256'],
        'oracle_catalog_sha256': bundle['oracle_catalog_sha256'],
        'execution': 'LOOPBACK_AUTH_REQUESTS_PERFORMED',
        'runtime_attestation': 'NOT_EVALUATED_BY_AUTH_INTERPRETER',
        'credential_origin': 'GENERATED_INSIDE_DISPOSABLE_VM',
        'session_cookie_observed': any(x['step'] == 'LOGIN' and x['issue_code'] is None
                                      and x['assertions'][-1] ==
                                      {'kind': 'SESSION_COOKIE_PRESENT', 'result': 'PASS'}
                                      for x in results if x['assertions']),
        'results': results,
        'summary': {'total': 4, 'passed': counts['PASS'],
                    'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']},
        'status': 'INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE',
    }
