"""4C-D2: independently approved, source-bound authentication flow compilation.

No network, SQL, credential material, or student code is used on the host.
Guest identities are generated inside a disposable VM, never supplied here.
"""
from __future__ import annotations

from typing import Any, Mapping

from .guest_api import GuestApiError, validate_auth_guest_bundle
from .oracle_contracts import digest
from .reviewed_api import ReviewedApiError, compile_reviewed_api_bundle


class AuthReviewError(ValueError):
    """Reject unapproved, ungrounded or unsafe authentication workflows."""


def _deny(code: str) -> None:
    raise AuthReviewError(code)


def _approval(config: Mapping[str, Any], field: str, required: str) -> None:
    values = config.get(field)
    if (not isinstance(values, list) or len(values) > 256
            or any(not isinstance(v, str) or len(v) > 260 for v in values)
            or required not in values):
        _deny('MISSING_EXACT_' + field.upper() + '_APPROVAL')


def _review_step(step: Any, targets: dict, role: str) -> dict:
    if (not isinstance(step, dict) or set(step) !=
            {'test_id', 'body_kind', 'fields', 'expected'}):
        _deny('INVALID_AUTH_STEP')
    target = targets.get(step['test_id']) if isinstance(step.get('test_id'), str) else None
    if (not isinstance(target, dict) or target.get('category') not in
            ('HTTP_READINESS', 'HTTP_ROUTE', 'UI_PAGE')
            or target.get('status') == 'UNSUPPORTED_BY_RUNNER'):
        _deny('AUTH_TARGET_NOT_DISCOVERED')
    endpoint = target.get('target')
    if not isinstance(endpoint, dict) or endpoint.get('method') != 'POST':
        _deny('AUTH_TARGET_MUST_BE_POST')
    path = endpoint.get('path')
    if not isinstance(path, str) or not path.startswith('/') or '?' in path or ':' in path:
        _deny('UNSUPPORTED_AUTH_ROUTE')
    return {'phase3_test_id': step['test_id'], 'method': 'POST', 'path': path,
            'body_kind': step['body_kind'], 'fields': step['fields'],
            'expected': step['expected'], 'role': role}


def compile_reviewed_auth_bundle(plan: Mapping[str, Any], auth_catalog: Mapping[str, Any],
                                 oracle_catalog: Mapping[str, Any], candidate: Mapping[str, Any],
                                 config: Mapping[str, Any]) -> dict:
    """Compile an *exactly* operator-approved email/password session test.

    The review inputs travel only as a host-side proof and are stripped before
    constructing the VM request. Direct orchestrator callers must supply proof.
    """
    if (not isinstance(auth_catalog, dict) or set(auth_catalog) !=
            {'schema_version', 'source', 'registration', 'login',
             'unauthenticated_case_id', 'authenticated_case_id'}
            or auth_catalog.get('schema_version') != '0.1'
            or not isinstance(plan, Mapping) or auth_catalog.get('source') != plan.get('source')
            or not isinstance(candidate, Mapping) or candidate.get('source') != plan.get('source')
            or not isinstance(config, Mapping)):
        _deny('INVALID_AUTH_CATALOG_OR_SOURCE')
    try:
        approved_api = compile_reviewed_api_bundle(plan, oracle_catalog, candidate, config)
    except (ReviewedApiError, ValueError, TypeError, KeyError) as exc:
        raise AuthReviewError('REVIEWED_PROTECTED_ORACLES_REQUIRED') from exc
    source = plan['source']
    identity = source['repository'] + '@' + source['commit_sha']
    auth_digest = digest(auth_catalog)
    api_digest = digest(oracle_catalog)
    _approval(config, 'approved_auth_flows', identity + '#' + auth_digest)
    _approval(config, 'approved_oracle_catalogs', identity + '#' + api_digest)
    targets = {x['id']: x for x in plan['test_cases']}
    registration = _review_step(auth_catalog['registration'], targets, 'REGISTRATION')
    login = _review_step(auth_catalog['login'], targets, 'LOGIN')
    if registration['phase3_test_id'] == login['phase3_test_id']:
        _deny('REGISTRATION_AND_LOGIN_MUST_BE_DISTINCT')
    api_cases = {x['id']: x for x in approved_api['tests']}
    anon_id = auth_catalog['unauthenticated_case_id']
    auth_id = auth_catalog['authenticated_case_id']
    if (not isinstance(anon_id, str) or not isinstance(auth_id, str)
            or anon_id == auth_id or anon_id not in api_cases or auth_id not in api_cases):
        _deny('PROTECTED_CASES_NOT_IN_APPROVED_ORACLES')
    anon, authed = api_cases[anon_id], api_cases[auth_id]
    if (anon['phase3_test_id'] != authed['phase3_test_id']
            or anon['method'] != authed['method'] or anon['path'] != authed['path']
            or anon['body_kind'] != authed['body_kind'] or anon['body'] != authed['body']):
        _deny('PROTECTED_REQUESTS_MUST_MATCH')
    if (anon['method'] not in ('GET', 'POST')
            or (anon['method'] == 'POST' and not
                (anon['body_kind'] == 'JSON' and anon['body'] == {}))):
        _deny('PROTECTED_PROBE_MUST_BE_READONLY_OR_EMPTY_JSON')
    for item in (anon, authed):
        subset = next((x['expected'] for x in item['assertions']
                       if x['kind'] == 'JSON_BODY_SUBSET_EQUALS'), None)
        if (not isinstance(subset, dict) or not isinstance(subset.get('error'), str)
                or not 1 <= len(subset['error']) <= 160):
            _deny('PROTECTED_PROBE_MUST_ASSERT_REVIEWED_ERROR')
    if anon['assertions'] == authed['assertions']:
        _deny('AUTHENTICATION_DOES_NOT_CHANGE_EXPECTATION')
    guest = {'schema_version': '0.1', 'source': dict(source),
             'adapter': candidate['adapter'], 'auth_catalog_sha256': auth_digest,
             'oracle_catalog_sha256': api_digest, 'registration': registration,
             'login': login, 'unauthenticated': anon, 'authenticated': authed}
    try:
        validate_auth_guest_bundle(guest, expected_source=source,
                                   expected_adapter=candidate['adapter'])
    except (GuestApiError, ValueError, TypeError, KeyError) as exc:
        raise AuthReviewError('INVALID_GUEST_AUTH_BUNDLE') from exc
    return {'guest': guest, '_review': {'plan': plan, 'auth_catalog': auth_catalog,
                                       'oracle_catalog': oracle_catalog}}


def validate_reviewed_auth_bundle(bundle: Any, candidate: Mapping[str, Any],
                                  config: Mapping[str, Any]) -> dict:
    """Second authorization gate inside the VM controller, BEFORE launching."""
    if (not isinstance(bundle, dict) or set(bundle) != {'guest', '_review'}
            or not isinstance(bundle['_review'], dict)
            or set(bundle['_review']) != {'plan', 'auth_catalog', 'oracle_catalog'}):
        _deny('MISSING_AUTH_REVIEW_PROOF')
    proof = bundle['_review']
    rebuilt = compile_reviewed_auth_bundle(proof['plan'], proof['auth_catalog'],
                                           proof['oracle_catalog'], candidate, config)
    if rebuilt != bundle:
        _deny('AUTH_BUNDLE_DOES_NOT_MATCH_OPERATOR_REVIEW')
    return bundle['guest']
