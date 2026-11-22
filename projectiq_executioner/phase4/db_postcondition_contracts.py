"""Phase 4C-D3: independent reviewed DB state expectations, bound to D2.

The caller supplies a review proof only; only canonical data-only commands
reach the reviewed disposable VM. All approvals must match exact source+SHA
and digest. D3 currently checks a newly registered row, not arbitrary CRUD.
"""
from __future__ import annotations

from typing import Any, Mapping

from .guest_db_postconditions import (DbPostconditionError,
    validate_postcondition_catalog, validate_guest_postcondition_bundle)
from .oracle_contracts import digest
from .reviewed_auth import AuthReviewError, compile_reviewed_auth_bundle


class PostconditionReviewError(ValueError):
    """Fail-closed D3 approval or contract error."""


def _deny(code: str) -> None:
    raise PostconditionReviewError(code)


def compile_reviewed_postconditions(plan: Mapping[str, Any], auth_catalog: Mapping[str, Any],
                                    oracle_catalog: Mapping[str, Any], post_catalog: Mapping[str, Any],
                                    candidate: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    """No IO, VM or SQL: approve a narrowly bounded persistence witness."""
    try:
        auth_proof = compile_reviewed_auth_bundle(plan, auth_catalog, oracle_catalog,
                                                  candidate, config)
    except (AuthReviewError, TypeError, KeyError, ValueError) as exc:
        raise PostconditionReviewError('D2_AUTH_APPROVAL_REQUIRED') from exc
    if candidate.get('adapter') != 'php_mysql_v1' or not isinstance(candidate.get('schema_path'), str):
        _deny('D3_REQUIRES_APPROVED_PHP_MYSQL_ADAPTER')
    source = plan['source']
    try:
        checks = validate_postcondition_catalog(post_catalog, source)
    except (DbPostconditionError, KeyError, TypeError, ValueError) as exc:
        raise PostconditionReviewError('INVALID_D3_POSTCONDITION_CATALOG') from exc
    # Every expected field must be a literal supplied by the approved registration
    # step. Otherwise it is a guessed expectation, not a grounded postcondition.
    registration = auth_catalog['registration']['fields']
    for check in checks:
        for field, value in check['expected_fields'].items():
            if registration.get(field) != {'$literal': value}:
                _deny('POSTCONDITION_NOT_GROUNDED_IN_REGISTRATION_INPUT')
    ident = source['repository'] + '@' + source['commit_sha']
    catalog_digest = digest(post_catalog)
    allowed = config.get('approved_database_postconditions')
    if (not isinstance(allowed, list) or len(allowed) > 256
            or any(not isinstance(value, str) or len(value) > 260 for value in allowed)
            or ident + '#' + catalog_digest not in allowed):
        _deny('D3_POSTCONDITION_DIGEST_NOT_OPERATOR_APPROVED')
    guest = {'schema_version': '0.1', 'source': dict(source), 'adapter': 'php_mysql_v1',
             'postcondition_catalog_sha256': catalog_digest,
             'auth_catalog_sha256': auth_proof['guest']['auth_catalog_sha256'],
             'oracle_catalog_sha256': auth_proof['guest']['oracle_catalog_sha256'],
             'checks': checks}
    validate_guest_postcondition_bundle(guest, expected_source=source,
                                        expected_adapter='php_mysql_v1')
    return {'auth': auth_proof['guest'], 'postconditions': guest,
            '_review': {'plan': dict(plan), 'auth_catalog': dict(auth_catalog),
                        'oracle_catalog': dict(oracle_catalog),
                        'postcondition_catalog': dict(post_catalog)}}


def validate_reviewed_postcondition_bundle(bundle: Any, candidate: Mapping[str, Any],
                                           config: Mapping[str, Any]) -> dict:
    """Second independent D3 authorization gate at the Phase 2 VM boundary."""
    if (not isinstance(bundle, dict) or set(bundle) != {'auth', 'postconditions', '_review'}
            or not isinstance(bundle['_review'], dict) or set(bundle['_review']) != {
                'plan', 'auth_catalog', 'oracle_catalog', 'postcondition_catalog'}):
        _deny('D3_REVIEW_PROOF_REQUIRED')
    proof = bundle['_review']
    rebuilt = compile_reviewed_postconditions(proof['plan'], proof['auth_catalog'],
                                               proof['oracle_catalog'], proof['postcondition_catalog'],
                                               candidate, config)
    if bundle != rebuilt:
        _deny('D3_BUNDLE_DOES_NOT_MATCH_OPERATOR_REVIEW')
    return {'auth': bundle['auth'], 'postconditions': bundle['postconditions']}


def validate_guest_postcondition_report(bundle: Mapping[str, Any], auth_report: Mapping[str, Any],
                                        report: Any) -> None:
    """Strict consistency checks; guest observations aren't independent DB attestation."""
    checks = validate_guest_postcondition_bundle(bundle, expected_source=bundle['source'],
                                                  expected_adapter=bundle['adapter'])
    if (not isinstance(report, dict) or set(report) != {
            'schema_version', 'phase', 'stage', 'source', 'adapter',
            'postcondition_catalog_sha256', 'execution', 'runtime_attestation',
            'results', 'summary', 'status'}
            or report['schema_version'] != '0.1' or type(report['phase']) is not int
            or report['phase'] != 4 or report['stage'] != 'guest_database_postconditions'
            or report['source'] != bundle['source'] or report['adapter'] != bundle['adapter']
            or report['postcondition_catalog_sha256'] != bundle['postcondition_catalog_sha256']
            or report['execution'] != 'READ_ONLY_GUEST_MYSQL_OBSERVATION'
            or report['runtime_attestation'] != 'NOT_EVALUATED_BY_DB_INTERPRETER'):
        _deny('INVALID_D3_GUEST_REPORT_ENVELOPE')
    values = report['results']
    if not isinstance(values, list) or len(values) != len(checks):
        _deny('D3_MISSING_OR_EXTRA_RESULTS')
    reg = next((item for item in auth_report['results'] if item['step'] == 'REGISTRATION'), None)
    counts = {'PASS': 0, 'FAIL': 0, 'INCONCLUSIVE': 0}
    for row, spec in zip(values, checks):
        if (not isinstance(row, dict) or set(row) != {
                'id', 'result', 'before_matches', 'after_matches',
                'expected_fields_match', 'issue_code'} or row['id'] != spec['id']
                or row['result'] not in counts):
            _deny('INVALID_D3_RESULT')
        b, a, fields = row['before_matches'], row['after_matches'], row['expected_fields_match']
        if (b is not None and (type(b) is not int or b not in (0, 1, 2))
                or a is not None and (type(a) is not int or a not in (0, 1, 2))
                or fields is not None and type(fields) is not bool
                or fields is True and a != 1):
            _deny('INVALID_D3_COUNT_EVIDENCE')
        if reg is None or reg['result'] != 'PASS' or b is None or a is None:
            expected = 'INCONCLUSIVE'
        else:
            expected = 'PASS' if b == 0 and a == 1 and fields is True else 'FAIL'
        issue = 'PREREQUISITE_NOT_SATISFIED' if expected == 'INCONCLUSIVE' else None
        if row['result'] != expected or row['issue_code'] != issue:
            _deny('D3_RESULT_EVIDENCE_DISAGREEMENT')
        counts[expected] += 1
    summary = report['summary']
    if (not isinstance(summary, dict)
            or set(summary) != {'total', 'passed', 'failed', 'inconclusive'}
            or any(type(value) is not int for value in summary.values())
            or summary != {'total': len(checks), 'passed': counts['PASS'],
                           'failed': counts['FAIL'], 'inconclusive': counts['INCONCLUSIVE']}
            or report['status'] != ('INCOMPLETE' if counts['INCONCLUSIVE'] else 'COMPLETE')):
        _deny('INVALID_D3_SUMMARY')
