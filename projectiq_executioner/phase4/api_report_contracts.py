"""Fail-closed validation of reviewed guest API outcomes (not VM attestation)."""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping

from .guest_api import validate_api_bundle

_SHA256 = re.compile(r'[a-f0-9]{64}\Z')
_RESULTS = {'test_id', 'result', 'http_status', 'body_sha256', 'assertions', 'issue_code'}
_LIMITATIONS = ['Operator-reviewed scenario expectations, not proven ground truth.',
                'No cookies, fixtures or external network requests.',
                'Runtime sandbox security is independently enforced by Phase 2.']


class ApiReportError(ValueError):
    """Inconsistent/forged guest API result."""


def validate_api_report(bundle: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    try:
        validate_api_bundle(bundle)
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiReportError('INVALID_API_BUNDLE') from exc
    if (not isinstance(report, Mapping) or set(report) != {
            'schema_version', 'phase', 'stage', 'execution', 'runtime_attestation',
            'source', 'adapter', 'results', 'summary', 'status', 'limitations'}
            or report.get('schema_version') != '0.1'
            or report.get('phase') != 4
            or report.get('stage') != 'reviewed_guest_api_tests'
            or report.get('execution') != 'LOOPBACK_API_REQUESTS_PERFORMED'
            or report.get('runtime_attestation') != 'NOT_EVALUATED_BY_API_INTERPRETER'
            or report.get('source') != bundle['source']
            or report.get('adapter') != bundle['adapter']
            or report.get('limitations') != _LIMITATIONS):
        raise ApiReportError('INVALID_API_REPORT_CONTEXT')
    results = report.get('results')
    if not isinstance(results, list) or len(results) != len(bundle['tests']):
        raise ApiReportError('MISSING_API_RESULTS')
    by_id = {x['id']: x for x in bundle['tests']}
    seen = set()
    for result in results:
        if not isinstance(result, Mapping) or set(result) != _RESULTS:
            raise ApiReportError('INVALID_API_RESULT')
        key = result.get('test_id')
        if not isinstance(key, str) or key not in by_id or key in seen:
            raise ApiReportError('UNKNOWN_OR_DUPLICATE_API_RESULT')
        seen.add(key)
        verdict, status, issue = result['result'], result['http_status'], result['issue_code']
        body_hash, checks = result['body_sha256'], result['assertions']
        if (verdict not in ('PASS', 'FAIL', 'INCONCLUSIVE')
                or status is not None and (type(status) is not int or not 100 <= status <= 599)
                or not isinstance(checks, list)):
            raise ApiReportError('BAD_API_RESULT_STATUS')
        if verdict == 'INCONCLUSIVE':
            if (issue not in ('LOOPBACK_API_UNAVAILABLE', 'API_BODY_LIMIT_EXCEEDED')
                    or checks or body_hash is not None):
                raise ApiReportError('INVALID_API_INCONCLUSIVE')
            continue
        if (issue is not None or status is None or not isinstance(body_hash, str)
                or not _SHA256.fullmatch(body_hash)):
            raise ApiReportError('MISSING_API_OBSERVATION')
        expected = by_id[key]['assertions']
        if len(checks) != len(expected):
            raise ApiReportError('INCOMPLETE_API_ASSERTIONS')
        for actual, spec in zip(checks, expected):
            if (not isinstance(actual, Mapping) or set(actual) != {'kind', 'result'}
                    or actual.get('kind') != spec['kind']
                    or actual.get('result') not in ('PASS', 'FAIL')):
                raise ApiReportError('BAD_API_ASSERTION')
            if spec['kind'] == 'HTTP_STATUS_EQUALS':
                if actual['result'] != ('PASS' if status == spec['expected'] else 'FAIL'):
                    raise ApiReportError('API_STATUS_CONTRADICTS_OBSERVATION')
        computed = 'PASS' if all(x['result'] == 'PASS' for x in checks) else 'FAIL'
        if verdict != computed:
            raise ApiReportError('API_VERDICT_CONTRADICTS_ASSERTIONS')
    counts = Counter(r['result'] for r in results)
    expected_summary = {'total': len(results), 'passed': counts['PASS'],
                        'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']}
    if (report.get('summary') != expected_summary
            or report.get('status') != ('INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE')):
        raise ApiReportError('INVALID_API_SUMMARY')
