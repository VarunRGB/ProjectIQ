"""Fail-closed Phase 3 -> Phase 4A handoff for reviewed, SHA-pinned VMs.

Pure-data preparation only: no VM, network, processes, imports from a student
checkout, dependency preparation or automatic operator approval.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from ..phase3.contracts import TestPlanContractError, validate_test_plan
from .guest_http import validate_guest_bundle, MAX_TESTS

SCHEMA_VERSION = '0.1'
APPROVED_CANDIDATE = 'CANDIDATE_NEEDS_VM_VERIFICATION'
ALLOWED_ADAPTERS = frozenset((
    'static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1',
    'node_express_offline_v1', 'node_fullstack_offline_v1', 'python_web_offline_v1',
))


class Phase4HandoffError(ValueError):
    """Untrusted input cannot produce a valid Phase 4 handoff."""


def load_bounded_json(path: str | Path, max_bytes: int) -> dict:
    file = Path(path)
    if not file.is_file() or file.stat().st_size > max_bytes:
        raise Phase4HandoffError('INPUT_NOT_FOUND_OR_TOO_LARGE')
    try:
        value = json.loads(file.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise Phase4HandoffError('INVALID_JSON') from exc
    if not isinstance(value, dict):
        raise Phase4HandoffError('EXPECTED_JSON_OBJECT')
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                           allow_nan=False)
    return sha256(canonical.encode('utf-8')).hexdigest()


def _new_result(source: Mapping[str, Any], plan_hash: str) -> dict:
    return {
        'schema_version': SCHEMA_VERSION, 'phase': 4,
        'stage': 'trusted_vm_test_handoff', 'status': 'BLOCKED',
        'source': dict(source), 'adapter': None, 'phase3_plan_sha256': plan_hash,
        'phase2_candidate_sha256': None,
        'execution': 'NOT_PERFORMED',
        'security_attestation': 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
        'test_bundle': None, 'selected_test_ids': [], 'excluded': [],
        'summary': {'selected': 0, 'excluded': 0, 'needs_oracle': 0,
                    'unsupported': 0, 'capacity_limited': 0},
        'issues': [],
        'limitations': [
            'This handoff does not execute a student application or launch a VM.',
            'An operator-approved SHA does not establish arbitrary-untrusted security attestation.',
            'The VM must independently verify immutable source, offline assets, guest enforcement and cleanup.',
            'Guest tests must run while the server is alive, before VM teardown.',
        ],
    }


def prepare_handoff(plan: Mapping[str, Any], candidate: Mapping[str, Any] | None = None,
                    vm_config: Mapping[str, Any] | None = None) -> dict:
    """Compile a bounded guest test bundle, never a VM launch authorization.

    All source/authentication prerequisites must be independently rechecked by
    Phase 2 on VM launch; data from this handoff alone cannot authorize execution.
    """
    try:
        validate_test_plan(plan)
    except (TestPlanContractError, TypeError, ValueError) as exc:
        raise Phase4HandoffError('INVALID_PHASE3_TEST_PLAN') from exc

    source = plan['source']
    try:
        result = _new_result(source, _canonical_hash(plan))
    except (TypeError, ValueError) as exc:
        raise Phase4HandoffError('NON_CANONICAL_INPUT') from exc

    ready = [c for c in plan['test_cases'] if c['status'] == 'READY_FOR_PHASE4']
    unresolved = [c for c in plan['test_cases'] if c['status'] != 'READY_FOR_PHASE4']
    for c in unresolved:
        result['excluded'].append({
            'test_id': c['id'],
            'reason': ('NEEDS_ORACLE' if c['status'] == 'NEEDS_ORACLE' else 'UNSUPPORTED_BY_RUNNER'),
        })
    result['summary']['needs_oracle'] = sum(c['status'] == 'NEEDS_ORACLE' for c in unresolved)
    result['summary']['unsupported'] = sum(c['status'] == 'UNSUPPORTED_BY_RUNNER' for c in unresolved)

    def stop(code: str) -> dict:
        result['issues'] = [{'code': code, 'stage': 'handoff'}]
        for c in ready:
            result['excluded'].append({'test_id': c['id'], 'reason': code})
        result['summary']['excluded'] = len(result['excluded'])
        return result

    if plan['status'] == 'INPUT_ERROR':
        return stop('PHASE3_INPUT_ERROR')
    # A plan with no executable assertions is a valid Phase 3 discovery result.
    # It does not need an adapter, VM config or operator approval to be counted.
    if not ready:
        result['status'] = 'NO_RUNNABLE_TESTS'
        result['summary']['excluded'] = len(result['excluded'])
        return result
    if source.get('kind') != 'github':
        return stop('PINNED_GITHUB_SOURCE_REQUIRED')
    if not isinstance(candidate, Mapping):
        return stop('PHASE2_CANDIDATE_REQUIRED')
    if candidate.get('source') != source:
        return stop('SOURCE_IDENTITY_MISMATCH')
    adapter = candidate.get('adapter')
    if (candidate.get('schema_version') != '0.3'
            or candidate.get('stage') != 'vm_execution_candidate'
            or candidate.get('worker_provider') != 'qemu_qcow2_v1'
            or candidate.get('launch_authorization') != 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES'
            or candidate.get('status') != APPROVED_CANDIDATE or adapter not in ALLOWED_ADAPTERS
            or plan['execution_context'].get('adapter') != adapter
            or plan['execution_context'].get('phase2_candidate_status') != APPROVED_CANDIDATE):
        return stop('SUPPORTED_PHASE2_ADAPTER_REQUIRED')
    result['adapter'] = adapter
    result['phase2_candidate_sha256'] = _canonical_hash(candidate)
    # Bind each readiness test to the Phase 2 startup probe. This prevents a
    # rewritten Phase 3 file from extending the startup oracle to another path.
    if any(c['oracle']['basis'] == 'PHASE2_ADAPTER_READINESS_CONTRACT'
           and c['target']['path'] != candidate.get('probe_path') for c in ready):
        return stop('READINESS_PROBE_MISMATCH')
    if not isinstance(vm_config, Mapping):
        return stop('OPERATOR_VM_CONFIG_REQUIRED')
    approved = vm_config.get('approved_snapshots')
    identity = source['repository'] + '@' + source['commit_sha']
    if not isinstance(approved, list) or identity not in approved:
        return stop('PINNED_SHA_NOT_OPERATOR_APPROVED')
    # The operator policy is *only* checked, never updated by Phase 4.
    # Other Phase 2 security gates still run independently in its VM worker.

    ready.sort(key=lambda c: (c['category'] != 'HTTP_READINESS', c['id']))
    selected, capped = ready[:MAX_TESTS], ready[MAX_TESTS:]
    result['selected_test_ids'] = [c['id'] for c in selected]
    for c in capped:
        result['excluded'].append({'test_id': c['id'], 'reason': 'RUNNER_TEST_LIMIT'})
    result['summary']['capacity_limited'] = len(capped)
    result['summary']['selected'] = len(selected)
    result['summary']['excluded'] = len(result['excluded'])
    bundle = {
        'schema_version': SCHEMA_VERSION,
        'source': dict(source), 'adapter': adapter,
        'tests': [
            {'id': c['id'], 'method': 'GET', 'path': c['target']['path'],
             'assertions': [{'kind': a['kind'], 'expected': a['expected']}
                            for a in c['assertions']]}
            for c in selected
        ],
    }
    try:
        validate_guest_bundle(bundle, expected_source=source, expected_adapter=adapter)
    except ValueError as exc:
        return stop('GUEST_TEST_BUNDLE_REJECTED')
    result['test_bundle'] = bundle
    result['status'] = 'STAGED_NOT_EXECUTED'
    if capped:
        result['issues'].append({'code': 'RUNNER_TEST_LIMIT', 'stage': 'handoff'})
    return result


def save_handoff(report: Mapping[str, Any], destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve evidence: a fresh path is required for every invocation.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write('\n')
    return path
