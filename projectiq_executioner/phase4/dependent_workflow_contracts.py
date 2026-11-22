"""Phase 4D-A host-side review: grounded 3-step dependent API pilot.

Distinct exact operator approvals for source SHA, synthetic D1 fixture, and
workflow definition. The reviewed proof is recompiled by the VM boundary.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..phase3.contracts import validate_test_plan
from .db_fixture_contracts import (compile_reviewed_db_fixture, FixtureReviewError)
from .guest_workflow import (DependentWorkflowError, validate_guest_workflow_bundle)
from .oracle_contracts import digest


class DependentWorkflowReviewError(ValueError):
    """Invalid, ungrounded or unapproved Phase 4D-A workflow."""


def _deny(code: str) -> None:
    raise DependentWorkflowReviewError(code)


def compile_reviewed_dependent_workflow(plan: Mapping[str, Any],
                                        fixture_catalog: Mapping[str, Any],
                                        workflow_catalog: Mapping[str, Any],
                                        candidate: Mapping[str, Any],
                                        config: Mapping[str, Any]) -> dict:
    """Compile only reviewed data and Phase 3-discovered routes; no VM or SQL."""
    try:
        validate_test_plan(plan)
        fixture = compile_reviewed_db_fixture(plan, fixture_catalog, candidate, config)
    except (FixtureReviewError, KeyError, TypeError, ValueError) as exc:
        raise DependentWorkflowReviewError('D4A_REQUIRES_APPROVED_D1_FIXTURE') from exc
    source = plan['source']
    if (not isinstance(workflow_catalog, dict) or set(workflow_catalog) != {
            'schema_version', 'source', 'selector', 'capture_field', 'body_field',
            'steps', 'expected_delete'} or workflow_catalog['schema_version'] != '0.1'
            or workflow_catalog['source'] != source):
        _deny('INVALID_D4A_WORKFLOW_CATALOG')
    # Ensure the controlled row is not a special-cased value invented by the
    # runner. This can only use one existing D1-approved fixture row.
    tables = fixture['tables']
    if not isinstance(tables, list) or len(tables) != 1 or len(tables[0]['rows']) != 1:
        _deny('D4A_REQUIRES_ONE_SYNTHETIC_FIXTURE_ROW')
    seed = tables[0]['rows'][0]
    selector = workflow_catalog['selector']
    if (not isinstance(selector, dict) or set(selector) != {'field', 'value'}
            or not isinstance(selector.get('field'), str)
            or not isinstance(selector.get('value'), str)
            or seed.get(selector['field']) != selector['value']
            or type(seed.get('id')) is not int):
        _deny('D4A_SELECTOR_NOT_GROUNDED_IN_D1_FIXTURE')
    if (workflow_catalog.get('capture_field') != 'id'
            or workflow_catalog.get('body_field') != 'id'):
        _deny('D4A_ONLY_BOUNDED_INTEGER_ID_PASSING_SUPPORTED')
    raw_steps = workflow_catalog['steps']
    if not isinstance(raw_steps, list) or len(raw_steps) != 3:
        _deny('D4A_THREE_DEPENDENT_STEPS_REQUIRED')
    by_id = {item['id']: item for item in plan['test_cases']}
    steps = []
    for raw, role in zip(raw_steps, ('DISCOVER', 'DELETE_CAPTURED', 'VERIFY_ABSENT')):
        if (not isinstance(raw, dict) or set(raw) != {'role', 'test_id'}
                or raw['role'] != role or not isinstance(raw['test_id'], str)):
            _deny('D4A_INVALID_WORKFLOW_STEP')
        case = by_id.get(raw['test_id'])
        if (not isinstance(case, dict) or case.get('status') == 'UNSUPPORTED_BY_RUNNER'
                or case.get('category') not in ('HTTP_ROUTE', 'HTTP_READINESS', 'UI_PAGE')):
            _deny('D4A_TARGET_NOT_DISCOVERED_IN_PHASE3')
        target = case['target']
        method = 'POST' if role == 'DELETE_CAPTURED' else 'GET'
        if target.get('method') != method or not isinstance(target.get('path'), str):
            _deny('D4A_TARGET_METHOD_MISMATCH')
        steps.append({'role': role, 'test_id': raw['test_id'],
                      'method': method, 'path': target['path']})
    identity = source['repository'] + '@' + source['commit_sha']
    approved = config.get('approved_dependent_workflows')
    workflow_hash = digest(workflow_catalog)
    if (not isinstance(approved, list) or len(approved) > 256
            or any(not isinstance(x, str) or len(x) > 260 for x in approved)
            or identity + '#' + workflow_hash not in approved):
        _deny('D4A_WORKFLOW_DIGEST_NOT_OPERATOR_APPROVED')
    guest = {
        'schema_version': '0.1', 'source': dict(source),
        'adapter': candidate['adapter'],
        'workflow_catalog_sha256': workflow_hash,
        'fixture_catalog_sha256': fixture['fixture_catalog_sha256'],
        'selector': selector, 'capture_field': workflow_catalog['capture_field'],
        'body_field': workflow_catalog['body_field'], 'seed_id': seed['id'],
        'steps': steps, 'expected_delete': workflow_catalog['expected_delete'],
    }
    try:
        validate_guest_workflow_bundle(guest, expected_source=source,
                                       expected_adapter='php_mysql_v1')
    except (DependentWorkflowError, ValueError, KeyError, TypeError) as exc:
        raise DependentWorkflowReviewError('D4A_GUEST_BUNDLE_REJECTED') from exc
    return {
        'fixture': fixture, 'workflow': guest,
        '_review': {'plan': dict(plan), 'fixture_catalog': dict(fixture_catalog),
                    'workflow_catalog': dict(workflow_catalog)},
    }


def validate_reviewed_dependent_workflow(bundle: Any, candidate: Mapping[str, Any],
                                         config: Mapping[str, Any]) -> dict:
    """Independent exact recompile at QEMU boundary; no raw bundle trust."""
    if (not isinstance(bundle, dict) or set(bundle) != {'fixture', 'workflow', '_review'}
            or not isinstance(bundle['_review'], dict) or set(bundle['_review']) != {
                'plan', 'fixture_catalog', 'workflow_catalog'}):
        _deny('D4A_REVIEW_PROOF_REQUIRED')
    proof = bundle['_review']
    rebuilt = compile_reviewed_dependent_workflow(
        proof['plan'], proof['fixture_catalog'], proof['workflow_catalog'], candidate, config)
    if rebuilt != bundle:
        _deny('D4A_REVIEW_PROOF_AND_BUNDLE_DISAGREE')
    return {'fixture': bundle['fixture'], 'workflow': bundle['workflow']}
