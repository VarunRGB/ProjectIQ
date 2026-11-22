"""4C-D4: reviewed, bounded VM-per-scenario reset plan for the PHP/MySQL pilot.

No SQL, student code or host-side credentials are accepted here. Each scenario
runs the already reviewed D3 workflow in a *new disposable VM*, rather than
attempting unsafe in-place database/session cleanup inside a live guest.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .oracle_contracts import digest

_ID = re.compile(r'[a-z][a-z0-9_-]{0,39}\Z', re.ASCII)
_STRATEGY = 'FRESH_DISPOSABLE_QCOW2_VM_PER_SCENARIO'
_WORKFLOW = 'REVIEWED_D3_REGISTRATION_PERSISTENCE'
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


class IsolationReviewError(ValueError):
    """Invalid, unreviewed or unsupported isolation scenario plan."""


def _deny(code: str) -> None:
    raise IsolationReviewError(code)


def validate_isolation_manifest(manifest: Any, source: Mapping[str, Any]) -> list[str]:
    if (not isinstance(manifest, dict)
            or set(manifest) != {'schema_version', 'source', 'strategy', 'scenarios'}
            or manifest.get('schema_version') != '0.1'
            or manifest.get('source') != dict(source)
            or manifest.get('strategy') != _STRATEGY):
        _deny('INVALID_D4_MANIFEST_OR_SOURCE')
    scenarios = manifest.get('scenarios')
    # Two distinct scenario IDs are enough to demonstrate the first reset.
    # No unbounded loop of QEMU runs from operator-supplied JSON.
    if not isinstance(scenarios, list) or len(scenarios) != 2:
        _deny('D4_PILOT_REQUIRES_EXACTLY_TWO_SCENARIOS')
    ids = []
    for item in scenarios:
        if (not isinstance(item, dict) or set(item) != {'id', 'workflow'}
                or not isinstance(item['id'], str) or not _ID.fullmatch(item['id'])
                or item['workflow'] != _WORKFLOW):
            _deny('INVALID_D4_SCENARIO')
        ids.append(item['id'])
    if ids[0] == ids[1]:
        _deny('DUPLICATE_D4_SCENARIO_ID')
    return ids


def compile_reviewed_isolation(plan: Mapping[str, Any], auth: Mapping[str, Any],
                               oracles: Mapping[str, Any], postconditions: Mapping[str, Any],
                               candidate: Mapping[str, Any], config: Mapping[str, Any],
                               manifest: Mapping[str, Any]) -> dict:
    """All D2/D3 and D4 approvals checked *before* the first VM launch."""
    from .db_postcondition_contracts import (PostconditionReviewError,
                                               compile_reviewed_postconditions)
    try:
        d3 = compile_reviewed_postconditions(plan, auth, oracles, postconditions,
                                            candidate, config)
    except (PostconditionReviewError, KeyError, TypeError, ValueError) as exc:
        raise IsolationReviewError('D3_OPERATOR_REVIEW_REQUIRED') from exc
    if (not isinstance(candidate, Mapping) or candidate.get('adapter') != 'php_mysql_v1'
            or candidate.get('source') != plan.get('source')
            or plan['source'].get('kind') != 'github'):
        _deny('D4_UNSUPPORTED_SOURCE_OR_ADAPTER')
    ids = validate_isolation_manifest(manifest, plan['source'])
    if not isinstance(config, Mapping):
        _deny('INVALID_D4_CONFIG')
    identity = plan['source']['repository'] + '@' + plan['source']['commit_sha']
    allowed = config.get('approved_scenario_isolation')
    approval = identity + '#' + digest(manifest)
    if (not isinstance(allowed, list) or len(allowed) > 256
            or any(not isinstance(value, str) or len(value) > 260 for value in allowed)
            or approval not in allowed):
        _deny('D4_ISOLATION_MANIFEST_NOT_OPERATOR_APPROVED')
    checks = d3['postconditions']['checks']
    if len(checks) != 1 or checks[0].get('table') != 'users':
        _deny('D4_ONLY_D3_REGISTRATION_PERSISTENCE_SUPPORTED')
    if checks[0].get('lookup_column') != 'email':
        _deny('D4_REQUIRES_POSITIVE_EMAIL_POSTCONDITION')
    return {'scenario_ids': ids, 'manifest_sha256': digest(manifest),
            'postcondition_catalog_sha256': d3['postconditions']['postcondition_catalog_sha256'],
            'source': dict(plan['source']), 'adapter': candidate['adapter'],
            'security_attestation': _ATTESTATION}
