"""Phase 4B: reviewed-SHA VM test orchestration.

Phase 3/4A never authorize launch. This module reuses the existing Phase 2C
worker and its immutable-source approval, offline-runtime, containment, watchdog
and cleanup checks. No student code or HTTP assertions run on the Windows host.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .handoff import (Phase4HandoffError, load_bounded_json,
                      prepare_handoff, save_handoff)
from .report_contracts import GuestReportError, validate_guest_report

CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


class Phase4RunError(ValueError):
    """Invalid operator input or output path. No student code was executed."""


def _persist(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write('\n')


def _empty(handoff: Mapping[str, Any]) -> dict:
    return {
        'schema_version': '0.1', 'phase': 4,
        'stage': 'reviewed_vm_functional_testing',
        'source': handoff['source'], 'adapter': handoff['adapter'],
        'status': 'BLOCKED', 'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED', 'security_attestation': ATTESTATION,
        'handoff_status': handoff['status'],
        'summary': {'total': 0, 'passed': 0, 'failed': 0, 'inconclusive': 0},
        'results': [], 'issue_code': None,
        'artifacts': {'handoff': 'phase4_handoff.json'},
        'limitations': [
            'Restricted to exact-SHA operator-approved, reviewed VM fixtures.',
            'HTTP tests cover only established status/body oracles, not general application correctness.',
            'No security attestation for arbitrary untrusted student submissions.',
        ],
    }


def run_reviewed_tests(*, plan: Mapping[str, Any], candidate: Mapping[str, Any],
                       config: Mapping[str, Any], output_dir: Path,
                       work_root: Path | None = None) -> dict:
    """Stage → independently approve in Phase 2 → launch → validate → cleanup.

    A FAIL assertion is a *functional* failure, not a VM launch failure.
    No stage here may turn a blocked/unsupported test into a runnable test.
    """
    out = Path(output_dir)
    # Preserve all previous evidence, including a previous partial attempt.
    if any((out / part).exists() for part in
           ('phase4_handoff.json', 'phase4_run.json', 'vm_execution.json')):
        raise Phase4RunError('PHASE4_OUTPUT_ALREADY_EXISTS')
    handoff = prepare_handoff(plan, candidate, config)
    report = _empty(handoff)
    save_handoff(handoff, out / 'phase4_handoff.json')
    if handoff['status'] != 'STAGED_NOT_EXECUTED':
        report['status'] = 'NO_RUNNABLE_TESTS' if handoff['status'] == 'NO_RUNNABLE_TESTS' else 'BLOCKED'
        report['issue_code'] = (None if report['status'] == 'NO_RUNNABLE_TESTS'
                                else (handoff['issues'][0]['code'] if handoff['issues'] else 'HANDOFF_DENIED'))
        _persist(out / 'phase4_run.json', report)
        return report

    # Imported lazily so offline contract tests do not require QEMU dependencies.
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate

    report['artifacts']['vm_execution'] = 'vm_execution.json'
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  out / 'vm_execution.json', reviewed_fixture=True,
                                  work_root=work_root, test_bundle=handoff['test_bundle'])
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        # Do not present a worker exception as a pass or a verified cleanup.
        report['status'] = 'VM_EXECUTION_NOT_VERIFIED'
        report['execution'] = 'NOT_VERIFIED'
        report['cleanup'] = 'NOT_VERIFIED'
        report['issue_code'] = 'VM_WORKER_EXCEPTION'
        _persist(out / 'phase4_run.json', report)
        return report
    report['execution'] = vm.get('execution', 'NOT_VERIFIED')
    report['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
    # Cleanup failure has precedence over all other outcomes.
    if vm.get('status') == 'DENIED' and vm.get('execution') == 'NOT_PERFORMED':
        report['status'] = 'VM_EXECUTION_NOT_VERIFIED'
        report['issue_code'] = 'VM_LAUNCH_DENIED'
    elif vm.get('cleanup') != CLEAN:
        report['status'] = 'CLEANUP_FAILED'
        report['issue_code'] = 'VM_CLEANUP_NOT_VERIFIED'
    elif (vm.get('status') != 'STARTUP_VERIFIED'
          or vm.get('source') != candidate.get('source')
          or vm.get('adapter') != candidate.get('adapter')
          or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
          or vm.get('security_attestation') != ATTESTATION
          or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'):
        report['status'] = 'VM_EXECUTION_NOT_VERIFIED'
        report['issue_code'] = 'VM_OR_GUEST_TESTS_NOT_VERIFIED'
    elif vm.get('phase4_status') != 'VALIDATED':
        report['status'] = 'GUEST_RESULTS_NOT_VERIFIED'
        report['issue_code'] = 'GUEST_REPORT_NOT_VERIFIED'
    else:
        guest_report = vm.get('phase4_test_results')
        try:
            validate_guest_report(handoff['test_bundle'], guest_report)
        except (GuestReportError, TypeError, ValueError):
            report['status'] = 'GUEST_RESULTS_NOT_VERIFIED'
            report['issue_code'] = 'GUEST_REPORT_NOT_VERIFIED'
        else:
            report['summary'] = guest_report['summary']
            report['results'] = guest_report['results']
            if guest_report['summary']['inconclusive']:
                report['status'] = 'TESTS_INCONCLUSIVE'
            elif guest_report['summary']['failed']:
                report['status'] = 'TESTS_FAILED'
            else:
                report['status'] = 'TESTS_PASSED'
    _persist(out / 'phase4_run.json', report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Phase 4B: run trusted HTTP test specifications in a reviewed disposable VM')
    parser.add_argument('--plan', type=Path, required=True, help='Phase 3 test_plan.json')
    parser.add_argument('--candidate', type=Path, required=True, help='Matching Phase 2C candidate')
    parser.add_argument('--vm-config', type=Path, required=True, help='Operator VM approval/configuration')
    parser.add_argument('--out', type=Path, required=True, help='New evidence directory')
    parser.add_argument('--work-root', type=Path, help='Managed VM workspace root (operator controlled)')
    args = parser.parse_args(argv)
    if any((args.out / x).exists() for x in
           ('phase4_handoff.json', 'vm_execution.json', 'phase4_run.json')):
        parser.error('Output directory already contains Phase 4B evidence')
    try:
        # Reuse Phase 2's configuration parser, which validates pinned image and approvals.
        from ..phase2.vm_worker.orchestrator import load_config
        plan = load_bounded_json(args.plan, 2_000_000)
        candidate = load_bounded_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        result = run_reviewed_tests(plan=plan, candidate=candidate, config=config,
                                    output_dir=args.out, work_root=args.work_root)
    except (OSError, ValueError, KeyError, RuntimeError):
        # Do not publish an exception containing host paths or repository data.
        error = _empty({'source': {'kind': 'unknown', 'repository': 'NOT_RESOLVED',
                                   'commit_sha': None},
                        'adapter': None, 'status': 'INPUT_ERROR'})
        error['status'] = 'INPUT_ERROR'
        error['issue_code'] = 'INVALID_PHASE4B_INPUT'
        try:
            _persist(args.out / 'phase4_run.json', error)
        except (OSError, ValueError):
            pass  # Preserve any existing evidence; never overwrite it.
        print('Phase 4B failed safely: INVALID_PHASE4B_INPUT', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4B — Reviewed VM Functional Tests')
    print('  Status:    ' + result['status'])
    print('  Execution: ' + result['execution'])
    print('  Cleanup:   ' + result['cleanup'])
    s = result['summary']
    print(f"  Tests:     {s['total']} (pass={s['passed']} fail={s['failed']} inconclusive={s['inconclusive']})")
    print('  JSON:      ' + str(args.out / 'phase4_run.json'))
    return 0 if result['status'] in ('TESTS_PASSED', 'NO_RUNNABLE_TESTS') else 2


if __name__ == '__main__':
    raise SystemExit(main())
