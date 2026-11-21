"""Self-contained trusted guest-only HTTP assertion interpreter.

This file has no Phase 3 or host dependency so Phase 4B can copy it unchanged
into the disposable guest. Never execute it against student apps on the host.
No arbitrary Python, JS, shell, URLs, headers, redirects or HTTP methods run.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import re
from typing import Any, Mapping

MAX_TESTS = 16
MAX_BODY_BYTES = 1_048_576
_HTTP_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/-]{0,218}$")
_TEST_ID = re.compile(r'^TC-[A-F0-9]{16}$')
_SHA40 = re.compile(r'^[0-9a-f]{40}$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_ADAPTERS = frozenset((
    'static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1',
    'node_express_offline_v1', 'node_fullstack_offline_v1', 'python_web_offline_v1',
))


class GuestTestError(ValueError):
    """A test bundle is unsafe or malformed."""


def validate_guest_bundle(bundle: Mapping[str, Any], *,
                          expected_source: Mapping[str, Any] | None = None,
                          expected_adapter: str | None = None) -> None:
    """Independently validate guest input; do not rely on host-side validation."""
    if not isinstance(bundle, Mapping) or bundle.get('schema_version') != '0.1':
        raise GuestTestError('UNSUPPORTED_GUEST_BUNDLE')
    if set(bundle) != {'schema_version', 'source', 'adapter', 'tests'}:
        raise GuestTestError('GUEST_BUNDLE_EXTRA_FIELDS')
    src, adapter = bundle.get('source'), bundle.get('adapter')
    if (not isinstance(src, Mapping) or set(src) != {'kind', 'repository', 'commit_sha'}
            or src.get('kind') != 'github'
            or not isinstance(src.get('repository'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}', src['repository'])
            or not isinstance(src.get('commit_sha'), str) or not _SHA40.fullmatch(src['commit_sha'])
            or adapter not in _ADAPTERS):
        raise GuestTestError('INVALID_BUNDLE_SOURCE')
    if expected_source is not None and dict(src) != dict(expected_source):
        raise GuestTestError('GUEST_SOURCE_MISMATCH')
    if expected_adapter is not None and adapter != expected_adapter:
        raise GuestTestError('GUEST_ADAPTER_MISMATCH')
    tests = bundle.get('tests')
    if not isinstance(tests, list) or not 1 <= len(tests) <= MAX_TESTS:
        raise GuestTestError('GUEST_TEST_COUNT_INVALID')
    ids, routes = set(), set()
    for test in tests:
        if not isinstance(test, Mapping) or set(test) != {'id', 'method', 'path', 'assertions'}:
            raise GuestTestError('MALFORMED_GUEST_TEST')
        tid, method, path = test.get('id'), test.get('method'), test.get('path')
        if (not isinstance(tid, str) or not _TEST_ID.fullmatch(tid) or tid in ids
                or method != 'GET' or not isinstance(path, str)
                or not _HTTP_PATH.fullmatch(path) or '//' in path
                or any(p in ('.', '..') for p in path.split('/'))
                or path in routes or ':' in path or '%' in path or '*' in path):
            raise GuestTestError('UNSAFE_GUEST_TEST_TARGET')
        ids.add(tid)
        routes.add(path)
        assertions = test.get('assertions')
        if not isinstance(assertions, list) or not 1 <= len(assertions) <= 2:
            raise GuestTestError('GUEST_ASSERTION_COUNT_INVALID')
        kinds = set()
        for assertion in assertions:
            if not isinstance(assertion, Mapping) or set(assertion) != {'kind', 'expected'}:
                raise GuestTestError('MALFORMED_GUEST_ASSERTION')
            kind, expected = assertion.get('kind'), assertion.get('expected')
            if kind in kinds:
                raise GuestTestError('DUPLICATE_GUEST_ASSERTION')
            kinds.add(kind)
            if kind == 'HTTP_STATUS_EQUALS':
                if type(expected) is not int or expected != 200:
                    raise GuestTestError('UNSUPPORTED_EXPECTED_STATUS')
            elif kind == 'HTTP_BODY_SHA256_EQUALS':
                if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                    raise GuestTestError('INVALID_EXPECTED_BODY_HASH')
            else:
                raise GuestTestError('UNSUPPORTED_GUEST_ASSERTION')
        if 'HTTP_BODY_SHA256_EQUALS' in kinds and 'HTTP_STATUS_EQUALS' not in kinds:
            raise GuestTestError('BODY_HASH_REQUIRES_SUCCESS_STATUS_ORACLE')


def run_guest_tests(bundle: Mapping[str, Any], *, expected_source: Mapping[str, Any],
                    expected_adapter: str, port: int = 8765, timeout_s: float = 3.0) -> dict:
    """Execute fixed, bounded GETs against loopback only, while app remains live.

    This is intended for invocation by the existing trusted VM guest controller.
    It is not a host-side test launcher and cannot enable VM execution.
    """
    validate_guest_bundle(bundle, expected_source=expected_source,
                          expected_adapter=expected_adapter)
    if (type(port) is not int or not 1 <= port <= 65535
            or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 5):
        raise GuestTestError('INVALID_GUEST_HTTP_LIMITS')
    results = []
    for test in bundle['tests']:
        status = None
        body = b''
        issue = None
        conn = HTTPConnection('127.0.0.1', port, timeout=timeout_s)
        try:
            conn.request('GET', test['path'], headers={'User-Agent': 'ProjectIQ-Trusted-Guest-Check'})
            resp = conn.getresponse()
            status = int(resp.status)
            body = resp.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                issue = 'HTTP_BODY_LIMIT_EXCEEDED'
        except (OSError, TimeoutError, ValueError, HTTPException):
            issue = 'LOOPBACK_HTTP_UNAVAILABLE'
        finally:
            conn.close()
        assertions = []
        if issue:
            verdict = 'INCONCLUSIVE'
        else:
            for a in test['assertions']:
                actual = (status if a['kind'] == 'HTTP_STATUS_EQUALS'
                          else sha256(body).hexdigest())
                passed = actual == a['expected']
                assertions.append({'kind': a['kind'], 'result': 'PASS' if passed else 'FAIL'})
            verdict = ('PASS' if all(a['result'] == 'PASS' for a in assertions) else 'FAIL')
        results.append({
            'test_id': test['id'], 'result': verdict, 'http_status': status,
            'body_sha256': sha256(body).hexdigest() if issue is None else None,
            'assertions': assertions, 'issue_code': issue,
        })
    counts = Counter(x['result'] for x in results)
    return {
        'schema_version': '0.1', 'phase': 4, 'stage': 'trusted_guest_http_tests',
        'execution': 'LOOPBACK_HTTP_REQUESTS_PERFORMED',
        'status': 'COMPLETE' if not counts['INCONCLUSIVE'] else 'INCOMPLETE',
        'runtime_attestation': 'NOT_EVALUATED_BY_HTTP_TEST_INTERPRETER',
        'source': dict(bundle['source']), 'adapter': bundle['adapter'],
        'results': results,
        'summary': {'total': len(results), 'passed': counts['PASS'],
                    'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']},
        'limitations': ['HTTP status and content-byte assertions only; not business correctness.',
                        'A successful assertion does not attest security of untrusted code.'],
    }
