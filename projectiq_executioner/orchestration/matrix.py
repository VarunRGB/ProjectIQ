"""Conservative, evidence-linked final per-test-case outcome inventory.

The Phase 3 plan is a discovery/specification, not execution evidence.
Only a verified Phase 4 suite containing an explicit matching test_id may
supply an observed result for a planned case. Reviewed checks without such an
identity appear separately and never manufacture Phase 3 coverage.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

OUTCOMES = ('PASSED', 'FAILED', 'INCONCLUSIVE', 'BLOCKED', 'NOT_TESTED')
_MAX_CHECK_ROWS = 1000
_CHECK_RESULTS = {'PASS': 'PASSED', 'FAIL': 'FAILED', 'INCONCLUSIVE': 'INCONCLUSIVE',
                  'BLOCKED': 'BLOCKED', 'UNTESTED': 'NOT_TESTED',
                  'INVALID_TEST': 'INCONCLUSIVE'}


def _check_result(observation: Mapping[str, Any], suite: Mapping[str, Any]) -> tuple[str, str]:
    """Return a single check outcome or a deliberately non-decisive result."""
    if not suite.get('vm_verified') or suite.get('state') not in ('PASS', 'FAIL', 'INCONCLUSIVE'):
        return 'BLOCKED', 'EXECUTION_EVIDENCE_NOT_VERIFIED'
    raw = observation.get('result', observation.get('status'))
    outcome = _CHECK_RESULTS.get(raw) if type(raw) is str else None
    if outcome is None:
        return 'INCONCLUSIVE', 'CHECK_RESULT_NOT_RECOGNIZED'
    if outcome == 'PASSED' and suite.get('state') == 'FAIL':
        # Valid: other checks in the same verified suite may have failed.
        pass
    elif outcome == 'FAILED' and suite.get('state') == 'PASS':
        return 'INCONCLUSIVE', 'SUITE_AND_CHECK_RESULT_CONFLICT'
    if outcome == 'INCONCLUSIVE':
        return outcome, 'CHECK_INCONCLUSIVE'
    if outcome == 'NOT_TESTED':
        return outcome, 'CHECK_NOT_EXECUTED'
    if outcome == 'BLOCKED':
        return outcome, 'CHECK_BLOCKED'
    return outcome, 'VERIFIED_OBSERVATION'


def _evidence(suite: Mapping[str, Any], observation: Mapping[str, Any] | None = None) -> dict:
    """Bound metadata to approved, already-exported artifact names."""
    result = {'suite': suite.get('kind'), 'suite_state': suite.get('state'),
              'report_path': suite.get('report_path'),
              'vm_path': suite.get('vm_path'),
              'vm_verified': suite.get('vm_verified') is True,
              'reused': suite.get('reused_from_prior') is True}
    if suite.get('vm_preflight_reason'):
        result['vm_preflight_reason'] = suite['vm_preflight_reason']
    if observation is not None:
        for key in ('test_id', 'id', 'step', 'result', 'status', 'http_status', 'issue_code', 'reason_code'):
            if key in observation:
                result[key] = observation[key]
    return result


def _combine(observations: list[tuple[str, str, dict]]) -> tuple[str, str]:
    outcomes = {x[0] for x in observations}
    if len(outcomes) > 1:
        return 'INCONCLUSIVE', 'MULTIPLE_DIFFERENT_OBSERVED_OUTCOMES'
    if len(observations) > 1:
        return observations[0][0], 'MULTIPLE_VERIFIED_OBSERVATIONS'
    return observations[0][0], observations[0][1]


def build_test_case_matrix(plan: Mapping[str, Any] | None,
                           suites: list[dict] | None, *, effective_mode: str | None = None) -> dict:
    """Join only exact IDs; never infer app success from a suite or its startup."""
    cases = (plan or {}).get('cases') or []
    suites = suites or []
    known_ids = {c.get('id') for c in cases if type(c) is dict and type(c.get('id')) is str}
    linked: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    additional: list[dict] = []
    dropped = 0
    for suite in suites:
        if type(suite) is not dict:
            continue
        # Unverified results MUST NOT be used even if they contain PASS tokens.
        if suite.get('vm_verified') is not True:
            continue
        observations = suite.get('observations') or []
        if type(observations) is not list:
            continue
        for row in observations:
            if type(row) is not dict:
                continue
            outcome, reason = _check_result(row, suite)
            evidence = _evidence(suite, row)
            test_id = row.get('test_id')
            if type(test_id) is str and test_id in known_ids:
                linked[test_id].append((outcome, reason, evidence))
                continue
            if len(additional) == _MAX_CHECK_ROWS:
                dropped += 1
                continue
            ident = row.get('test_id', row.get('id', row.get('step')))
            # A reviewed check is not automatically a Phase 3 discovered case.
            additional.append({'suite': suite.get('kind'), 'check_id': ident,
                               'outcome': outcome, 'reason': reason,
                               'evidence': [evidence],
                               'scope': 'REVIEWED_CHECK_NOT_LINKED_TO_PHASE3_CASE'})
    discovered = []
    for case in cases:
        if type(case) is not dict:
            continue
        test_id = case.get('id')
        plan_status = case.get('status')
        matched = linked.get(test_id, [])
        if matched:
            outcome, reason = _combine(matched)
            proof = [item[2] for item in matched]
        elif plan_status == 'NEEDS_ORACLE':
            outcome, reason, proof = 'NOT_TESTED', 'REVIEWED_ORACLE_MISSING', []
        elif plan_status == 'UNSUPPORTED_BY_RUNNER':
            outcome, reason, proof = 'NOT_TESTED', 'UNSUPPORTED_TEST_TARGET', []
        elif plan_status == 'READY_FOR_PHASE4':
            readiness = [s for s in suites if s.get('kind') == 'readiness']
            # Only a selected readiness suite can block a specific readiness case.
            if case.get('category') == 'HTTP_READINESS' and readiness:
                selected = readiness[0]
                if not selected.get('vm_verified'):
                    outcome, reason = 'BLOCKED', 'READINESS_SUITE_NOT_VERIFIED'
                    proof = [_evidence(selected)]
                else:
                    outcome, reason, proof = 'NOT_TESTED', 'NO_MATCHING_TEST_OBSERVATION', []
            else:
                outcome, reason, proof = 'NOT_TESTED', 'NO_MATCHING_TEST_OBSERVATION', []
            if outcome == 'NOT_TESTED' and effective_mode == 'STATIC':
                reason = 'STATIC_MODE_NO_EXECUTION'
        else:
            outcome, reason, proof = 'NOT_TESTED', 'NO_VERIFIED_TEST_OBSERVATION', []
        discovered.append({
            'test_id': test_id, 'title': case.get('title'),
            'category': case.get('category'), 'target': case.get('target'),
            'planning_status': plan_status,
            'expected_assertions': case.get('assertions') or [],
            'outcome': outcome, 'reason': reason,
            'evidence': proof,
            'scope': 'PHASE3_DISCOVERED_CASE',
        })
    counts = Counter(x['outcome'] for x in discovered)
    review_counts = Counter(x['outcome'] for x in additional)
    return {
        'schema_version': '0.1',
        'discovered_cases': discovered,
        'reviewed_only_checks': additional,
        'counts': {k: counts[k] for k in OUTCOMES},
        'reviewed_check_counts': {k: review_counts[k] for k in OUTCOMES},
        'reviewed_checks_truncated': dropped,
        'limitations': [
            'A readiness PASS verifies only its explicit readiness assertion.',
            'NOT_TESTED is not a failing grade; BLOCKED is not a student defect.',
            'Reviewed checks are not linked to planned cases without exact matching test IDs.',
            'Only independently VM-verified, saved observations can establish an executed test outcome.',
            'An approved test oracle may still be incorrect; failed assertions do not establish student fault.',
        ],
    }
