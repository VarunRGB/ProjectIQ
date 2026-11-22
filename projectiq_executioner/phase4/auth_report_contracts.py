"""Strict, redacted guest evidence contract for 4C-D2 session verification."""
from __future__ import annotations

from typing import Any, Mapping


class AuthReportError(ValueError):
    """Missing, forged, inconsistent or unsafe authentication test evidence."""


def validate_auth_report(guest: Mapping[str, Any], report: Any) -> None:
    if (not isinstance(report, dict) or set(report) != {
            'schema_version', 'phase', 'stage', 'source', 'adapter',
            'auth_catalog_sha256', 'oracle_catalog_sha256', 'execution',
            'runtime_attestation', 'credential_origin', 'session_cookie_observed',
            'results', 'summary', 'status'} or report['schema_version'] != '0.1'
            or type(report['phase']) is not int or report['phase'] != 4
            or report['stage'] != 'guest_reviewed_auth_flow'
            or report['source'] != guest['source'] or report['adapter'] != guest['adapter']
            or report['auth_catalog_sha256'] != guest['auth_catalog_sha256']
            or report['oracle_catalog_sha256'] != guest['oracle_catalog_sha256']
            or report['execution'] != 'LOOPBACK_AUTH_REQUESTS_PERFORMED'
            or report['runtime_attestation'] != 'NOT_EVALUATED_BY_AUTH_INTERPRETER'
            or report['credential_origin'] != 'GENERATED_INSIDE_DISPOSABLE_VM'
            or type(report['session_cookie_observed']) is not bool):
        raise AuthReportError('INVALID_AUTH_REPORT_ENVELOPE')
    result = report['results']
    if not isinstance(result, list) or len(result) != 4:
        raise AuthReportError('INCOMPLETE_AUTH_STEP_LIST')
    spec = [('UNAUTHENTICATED', guest['unauthenticated']),
            ('REGISTRATION', guest['registration']),
            ('LOGIN', guest['login']),
            ('AUTHENTICATED', guest['authenticated'])]
    counts = {'PASS': 0, 'FAIL': 0, 'INCONCLUSIVE': 0}
    cookie_result = None
    for entry, (step_name, expected) in zip(result, spec):
        if (not isinstance(entry, dict) or set(entry) != {
                'step', 'result', 'http_status', 'assertions', 'issue_code'}
                or entry['step'] != step_name or entry['result'] not in counts):
            raise AuthReportError('INVALID_AUTH_STEP_RESULT')
        counts[entry['result']] += 1
        issue = entry['issue_code']
        if issue is not None and issue not in {
                'AUTH_LOOPBACK_UNAVAILABLE', 'AUTH_BODY_LIMIT_EXCEEDED',
                'SESSION_COOKIE_UNSAFE', 'PREREQUISITE_NOT_SATISFIED'}:
            raise AuthReportError('INVALID_AUTH_ISSUE')
        status = entry['http_status']
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise AuthReportError('INVALID_AUTH_HTTP_STATUS')
        checks = entry['assertions']
        if not isinstance(checks, list):
            raise AuthReportError('INVALID_AUTH_ASSERTIONS')
        expected_kinds = ([x['kind'] for x in expected['assertions']]
                          if step_name in ('UNAUTHENTICATED', 'AUTHENTICATED') else
                          ['HTTP_STATUS_EQUALS'] + (['JSON_BODY_SUBSET_EQUALS']
                           if expected['expected']['json_subset'] is not None else []) +
                          (['SESSION_COOKIE_PRESENT'] if step_name == 'LOGIN' else []))
        if issue is None:
            if ([x.get('kind') for x in checks if isinstance(x, dict)] != expected_kinds
                    or len(checks) != len(expected_kinds)
                    or any(set(x) != {'kind', 'result'} or x['result'] not in ('PASS', 'FAIL') for x in checks)):
                raise AuthReportError('INVALID_AUTH_ASSERTION_KINDS')
            if entry['result'] != ('PASS' if all(x['result'] == 'PASS' for x in checks) else 'FAIL'):
                raise AuthReportError('AUTH_RESULT_ASSERTION_DISAGREEMENT')
            expected_status = (next(x['expected'] for x in expected['assertions']
                                    if x['kind'] == 'HTTP_STATUS_EQUALS')
                               if step_name in ('UNAUTHENTICATED', 'AUTHENTICATED')
                               else expected['expected']['status'])
            status_check = checks[0]['result']
            if (status_check == 'PASS') != (status == expected_status):
                raise AuthReportError('AUTH_STATUS_EVIDENCE_DISAGREEMENT')
            if step_name == 'LOGIN':
                cookie_result = checks[-1]['result'] == 'PASS'
        elif checks or entry['result'] != 'INCONCLUSIVE':
            raise AuthReportError('INCONCLUSIVE_STEP_MUST_HAVE_NO_ASSERTIONS')
    if report['session_cookie_observed'] != (cookie_result is True):
        raise AuthReportError('AUTH_COOKIE_EVIDENCE_INCONSISTENT')
    summary = report['summary']
    if (not isinstance(summary, dict) or set(summary) !=
            {'total', 'passed', 'failed', 'inconclusive'} or
            any(type(x) is not int for x in summary.values())
            or summary != {'total': 4, 'passed': counts['PASS'],
                           'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']}):
        raise AuthReportError('AUTH_SUMMARY_INVALID')
    if report['status'] != ('INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE'):
        raise AuthReportError('AUTH_REPORT_STATUS_INVALID')
