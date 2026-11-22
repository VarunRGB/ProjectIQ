"""4C-D1 operator-reviewed, source-bound database fixture contracts.

Staging is offline. Approval is independent of the pinned-source approval;
execution uses the existing disposable VM and its scoped MySQL credentials.
"""
from __future__ import annotations

from typing import Any, Mapping

from .guest_db_fixture import DbFixtureError, validate_fixture_bundle
from .oracle_contracts import digest
from ..phase3.contracts import validate_test_plan, TestPlanContractError


class FixtureReviewError(ValueError):
    """Database fixture is unsupported, unreviewed or incorrectly bound."""


def _fail(code: str) -> None:
    raise FixtureReviewError(code)


def compile_reviewed_db_fixture(plan: Mapping[str, Any], manifest: Mapping[str, Any],
                                candidate: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    """Return data-only fixture bundle; never execute SQL or start a VM."""
    try:
        validate_test_plan(plan)
    except (TestPlanContractError, KeyError, TypeError, ValueError) as exc:
        raise FixtureReviewError('INVALID_PHASE3_PLAN') from exc
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'source', 'tables'}
            or manifest.get('schema_version') != '0.1'
            or manifest.get('source') != plan.get('source')):
        _fail('INVALID_FIXTURE_MANIFEST_OR_SOURCE')
    if (not isinstance(candidate, Mapping) or candidate.get('source') != plan['source']
            or candidate.get('schema_version') != '0.3'
            or candidate.get('stage') != 'vm_execution_candidate'
            or candidate.get('status') != 'CANDIDATE_NEEDS_VM_VERIFICATION'
            or candidate.get('worker_provider') != 'qemu_qcow2_v1'
            or candidate.get('adapter') != 'php_mysql_v1'
            or not isinstance(candidate.get('schema_path'), str)):
        _fail('UNSUPPORTED_OR_UNAPPROVED_FIXTURE_ADAPTER')
    if (plan.get('execution_context', {}).get('adapter') not in (None, 'php_mysql_v1')
            or plan['source'].get('kind') != 'github'):
        _fail('FIXTURE_PLAN_ADAPTER_OR_SOURCE_MISMATCH')
    if not isinstance(config, Mapping):
        _fail('INVALID_FIXTURE_POLICY')
    identity = plan['source']['repository'] + '@' + plan['source']['commit_sha']
    approved_snapshots = config.get('approved_snapshots')
    if (not isinstance(approved_snapshots, list) or identity not in approved_snapshots):
        _fail('PINNED_SOURCE_NOT_OPERATOR_APPROVED')
    digest_value = digest(manifest)
    approval = identity + '#' + digest_value
    allowed = config.get('approved_database_fixtures')
    if (not isinstance(allowed, list) or len(allowed) > 256
            or any(not isinstance(x, str) or len(x) > 260 for x in allowed)
            or approval not in allowed):
        _fail('DATABASE_FIXTURE_NOT_OPERATOR_APPROVED')
    bundle = {'schema_version': '0.1', 'source': dict(plan['source']),
              'adapter': 'php_mysql_v1', 'fixture_catalog_sha256': digest_value,
              'tables': manifest['tables']}
    try:
        validate_fixture_bundle(bundle, expected_source=plan['source'],
                                expected_adapter='php_mysql_v1')
    except (DbFixtureError, TypeError, KeyError, ValueError) as exc:
        raise FixtureReviewError('INVALID_FIXTURE_ROWS') from exc
    return bundle


def fixture_counts(bundle: Mapping[str, Any]) -> dict:
    return validate_fixture_bundle(bundle, expected_source=bundle['source'],
                                   expected_adapter='php_mysql_v1')


def validate_fixture_report(bundle: Mapping[str, Any], report: Any) -> None:
    """Reject missing/forged guest results; never treat readiness as seed evidence."""
    counts = fixture_counts(bundle)
    if (not isinstance(report, dict) or set(report) != {
            'schema_version', 'status', 'fixture_catalog_sha256',
            'table_count', 'seeded_rows', 'database_scope'
        } or report['schema_version'] != '0.1'
            or report['status'] != 'FIXTURES_SEEDED'
            or report['fixture_catalog_sha256'] != bundle['fixture_catalog_sha256']
            or report['database_scope'] != 'DISPOSABLE_GUEST_DATABASE_ONLY'
            or type(report['table_count']) is not int
            or report['table_count'] != counts['table_count']
            or type(report['seeded_rows']) is not int
            or report['seeded_rows'] != counts['row_count']):
        _fail('GUEST_DATABASE_SEED_EVIDENCE_INVALID')
