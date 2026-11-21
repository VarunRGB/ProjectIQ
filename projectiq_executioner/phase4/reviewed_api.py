"""Compile operator-reviewed 4C-A oracles to a bounded trusted guest API bundle.

Approval is an explicit *policy entry* in operator-owned VM config bound to
source identity and exact canonical catalog digest, not merely a CLI flag.
Phase 2 still independently authorizes the SHA and enforces VM isolation.
"""
from __future__ import annotations

from typing import Any, Mapping

from .guest_api import MAX_TESTS, validate_api_bundle
from .oracle_contracts import OracleContractError, digest
from .oracle_stage import stage_oracles


class ReviewedApiError(ValueError):
    """Oracle catalog is not approved, bound, or supported."""


def compile_reviewed_api_bundle(plan: Mapping[str, Any], catalog: Mapping[str, Any],
                                candidate: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    """No network, IO, or student execution. Reject all unreviewed/fixture cases."""
    try:
        staged = stage_oracles(plan, catalog, candidate=candidate, vm_config=config)
    except (OracleContractError, ValueError, TypeError, KeyError) as exc:
        raise ReviewedApiError('INVALID_ORACLE_STAGING') from exc
    if staged['status'] != 'STAGED_FOR_REVIEW':
        raise ReviewedApiError('PINNED_SOURCE_NOT_APPROVED')
    items = staged['scenarios']
    if not 1 <= len(items) <= MAX_TESTS:
        raise ReviewedApiError('API_SCENARIO_LIMIT')
    approved = config.get('approved_oracle_catalogs')
    if not isinstance(approved, list) or len(approved) > 256 or any(
            not isinstance(x, str) or len(x) > 260 for x in approved):
        raise ReviewedApiError('MISSING_OR_INVALID_ORACLE_APPROVAL_LIST')
    source = plan['source']
    identity = source['repository'] + '@' + source['commit_sha']
    required = identity + '#' + digest(catalog)
    if required not in approved:
        raise ReviewedApiError('ORACLE_CATALOG_NOT_OPERATOR_APPROVED')
    tests = []
    for item in items:
        if item['fixture_refs']:
            raise ReviewedApiError('RUNTIME_FIXTURE_REFERENCES_REQUIRE_4CD')
        req = item['request']
        json_body, form_body = req['json_body'], req.get('form_body')
        kind = 'JSON' if json_body is not None else 'FORM' if form_body is not None else 'NONE'
        tests.append({
            'id': item['id'], 'phase3_test_id': item['phase3_test_id'],
            'method': req['method'], 'path': req['path'], 'body_kind': kind,
            'body': json_body if kind == 'JSON' else form_body if kind == 'FORM' else None,
            'assertions': item['assertions'],
        })
    bundle = {'schema_version': '0.1', 'source': dict(source),
              'adapter': candidate['adapter'], 'tests': tests}
    try:
        validate_api_bundle(bundle, expected_source=source,
                            expected_adapter=candidate['adapter'])
    except (ValueError, TypeError, KeyError) as exc:
        raise ReviewedApiError('API_GUEST_BUNDLE_REJECTED') from exc
    return bundle
