"""Strict host-side validation of the Phase 4 guest HTTP results.

A well-formed guest result is evidence of HTTP checks only. VM lifecycle,
cleanup, execution provenance and security attestation belong to Phase 2.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping

from .guest_http import validate_guest_bundle

_RESULT_KEYS = {'test_id', 'result', 'http_status', 'body_sha256', 'assertions', 'issue_code'}
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_FAILURE_CODES = {'LOOPBACK_HTTP_UNAVAILABLE', 'HTTP_BODY_LIMIT_EXCEEDED'}


class GuestReportError(ValueError):
    """A guest test report is malformed or contradicts its request."""


def validate_guest_report(bundle: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    """Do not allow malformed/duplicated/miscounted results into the dashboard."""
    try:
        validate_guest_bundle(bundle)
    except ValueError as exc:
        raise GuestReportError('INVALID_GUEST_REQUEST') from exc
    expected_report_keys = {
        'schema_version', 'phase', 'stage', 'execution', 'runtime_attestation',
        'source', 'adapter', 'results', 'summary', 'status', 'limitations',
    }
    if (not isinstance(report, Mapping) or set(report) != expected_report_keys
            or any(report.get(k) != v for k, v in {
        'schema_version': '0.1', 'phase': 4,
        'stage': 'trusted_guest_http_tests',
        'execution': 'LOOPBACK_HTTP_REQUESTS_PERFORMED',
        'runtime_attestation': 'NOT_EVALUATED_BY_HTTP_TEST_INTERPRETER',
        'source': bundle['source'], 'adapter': bundle['adapter'],
    }.items())):
        raise GuestReportError('INVALID_GUEST_REPORT_CONTEXT')
    results = report.get('results')
    if not isinstance(results, list) or len(results) != len(bundle['tests']):
        raise GuestReportError('INCOMPLETE_GUEST_TEST_RESULT_SET')
    specs = {t['id']: t for t in bundle['tests']}
    seen: set[str] = set()
    for entry in results:
        if not isinstance(entry, Mapping) or set(entry) != _RESULT_KEYS:
            raise GuestReportError('INVALID_GUEST_TEST_RESULT')
        test_id = entry.get('test_id')
        if not isinstance(test_id, str) or test_id not in specs or test_id in seen:
            raise GuestReportError('UNKNOWN_OR_DUPLICATE_GUEST_TEST_ID')
        seen.add(test_id)
        state = entry.get('result')
        status = entry.get('http_status')
        issue = entry.get('issue_code')
        observed_body_hash = entry.get('body_sha256')
        assertions = entry.get('assertions')
        if state not in ('PASS', 'FAIL', 'INCONCLUSIVE'):
            raise GuestReportError('UNKNOWN_GUEST_TEST_STATE')
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise GuestReportError('INVALID_OBSERVED_HTTP_STATUS')
        if not isinstance(assertions, list):
            raise GuestReportError('INVALID_OBSERVED_ASSERTION_SET')
        if state == 'INCONCLUSIVE':
            if issue not in _FAILURE_CODES or assertions or observed_body_hash is not None:
                raise GuestReportError('INCONCLUSIVE_RESULT_MUST_HAVE_BOUNDED_REASON')
        else:
            if (issue is not None or status is None
                    or not isinstance(observed_body_hash, str)
                    or not _SHA256.fullmatch(observed_body_hash)):
                raise GuestReportError('DECISIVE_RESULT_MISSING_HTTP_EVIDENCE')
            expected_kinds = [a['kind'] for a in specs[test_id]['assertions']]
            actual_kinds = [a.get('kind') if isinstance(a, Mapping) else None for a in assertions]
            if actual_kinds != expected_kinds or any(
                not isinstance(a, Mapping) or set(a) != {'kind', 'result'}
                or a.get('result') not in ('PASS', 'FAIL') for a in assertions
            ):
                raise GuestReportError('OBSERVED_ASSERTIONS_DO_NOT_MATCH_REQUEST')
            computed = 'PASS' if all(a['result'] == 'PASS' for a in assertions) else 'FAIL'
            if computed != state:
                raise GuestReportError('TEST_VERDICT_CONTRADICTS_ASSERTIONS')
            # HTTP 200 is the only status oracle currently supported by Phase 3.
            # Avoid accepting a fabricated successful status assertion at 404.
            expected_hashes = {x['kind']: x['expected'] for x in specs[test_id]['assertions']}
            for a in assertions:
                if a['kind'] == 'HTTP_BODY_SHA256_EQUALS':
                    outcome = 'PASS' if observed_body_hash == expected_hashes[a['kind']] else 'FAIL'
                    if a['result'] != outcome:
                        raise GuestReportError('HTTP_BODY_HASH_ASSERTION_CONTRADICTS_OBSERVATION')
                if a['kind'] == 'HTTP_STATUS_EQUALS':
                    expected_outcome = 'PASS' if status == 200 else 'FAIL'
                    if a['result'] != expected_outcome:
                        raise GuestReportError('HTTP_STATUS_ASSERTION_CONTRADICTS_OBSERVATION')
    counts = Counter(r['result'] for r in results)
    summary = report.get('summary')
    if (not isinstance(summary, Mapping) or any(
            type(summary.get(k)) is not int for k in
            ('total', 'passed', 'failed', 'inconclusive')) or summary != {
        'total': len(results), 'passed': counts['PASS'],
        'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE'],
    }):
        raise GuestReportError('GUEST_RESULT_SUMMARY_MISMATCH')
    actual_state = 'INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE'
    if report.get('status') != actual_state:
        raise GuestReportError('GUEST_RESULT_STATUS_MISMATCH')
    if report.get('limitations') != [
        'HTTP status and content-byte assertions only; not business correctness.',
        'A successful assertion does not attest security of untrusted code.',
    ]:
        raise GuestReportError('GUEST_RESULT_LIMITATIONS_INVALID')
