"""4C-D3 CLI: reviewed DB witness coupled to D2 session auth in one clean VM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .oracle_stage import _load_strict_json
from .oracle_contracts import digest
from .db_postcondition_contracts import (PostconditionReviewError,
    compile_reviewed_postconditions, validate_guest_postcondition_report)
from .auth_report_contracts import AuthReportError, validate_auth_report

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
_FILES = ('d3_preflight.json', 'd3_review.json', 'd3_vm_execution.json', 'd3_run.json')


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def _fresh(path: Path) -> None:
    if path.exists():
        raise PostconditionReviewError('D3_OUTPUT_DIRECTORY_MUST_BE_NEW')


def preflight(plan, auth, oracles, postconditions, candidate, config, output_dir: Path) -> dict:
    _fresh(output_dir)
    approved = compile_reviewed_postconditions(plan, auth, oracles, postconditions, candidate, config)
    witness = approved['postconditions']
    report = {'schema_version': '0.1', 'phase': 4, 'stage': 'database_postcondition_preflight',
              'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
              'source': dict(plan['source']), 'adapter': candidate['adapter'],
              'postcondition_catalog_sha256': witness['postcondition_catalog_sha256'],
              'check_count': len(witness['checks']),
              'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
              'security_attestation': _ATTESTATION}
    _write(output_dir / 'd3_preflight.json', report)
    return report


def run(*, plan, auth, oracles, postconditions, candidate, config,
        output_dir: Path, work_root: Path | None = None) -> dict:
    _fresh(output_dir)
    approved = compile_reviewed_postconditions(plan, auth, oracles, postconditions, candidate, config)
    witness = approved['postconditions']
    _write(output_dir / 'd3_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'database_postcondition_review',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'phase3_plan_sha256': digest(plan), 'phase2_candidate_sha256': digest(candidate),
        'postcondition_catalog_sha256': witness['postcondition_catalog_sha256'],
        'status': 'EXACT_DIGEST_APPROVAL_MATCHED', 'execution': 'NOT_PERFORMED'})
    result = {'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_vm_database_postconditions',
              'source': dict(plan['source']), 'adapter': candidate['adapter'],
              'postcondition_catalog_sha256': witness['postcondition_catalog_sha256'],
              'status': 'VM_EXECUTION_NOT_VERIFIED', 'execution': 'NOT_VERIFIED',
              'cleanup': 'NOT_VERIFIED', 'security_attestation': _ATTESTATION,
              'issue_code': 'VM_WORKER_EXCEPTION',
              'auth_summary': None, 'session_cookie_observed': False,
              'summary': {'total': 0, 'passed': 0, 'failed': 0, 'inconclusive': 0},
              'results': [],
              'artifacts': {'review': 'd3_review.json', 'vm': 'd3_vm_execution.json'},
              'limitations': ['D3 covers new registration row persistence for approved PHP/MySQL.',
                              'CRUD state changes and per-scenario resets remain future extensions.',
                              'Only exact reviewed SHA and postcondition digests are accepted.',
                              'Not attested for arbitrary untrusted student execution.']}
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config), output_dir / 'd3_vm_execution.json',
                                  reviewed_fixture=True, work_root=work_root,
                                  postcondition_bundle=approved)
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        pass
    else:
        result['execution'] = vm.get('execution', 'NOT_VERIFIED')
        result['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        if result['cleanup'] != _CLEAN:
            result.update(status='CLEANUP_FAILED', issue_code='VM_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED' or vm.get('source') != candidate['source']
              or vm.get('adapter') != candidate['adapter']
              or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
              or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
              or vm.get('security_attestation') != _ATTESTATION
              or vm.get('phase4d3_status') != 'VALIDATED'):
            result.update(status='VM_EXECUTION_NOT_VERIFIED',
                          issue_code='VM_POSTCONDITION_REPORT_NOT_VERIFIED')
        else:
            try:
                validate_auth_report(approved['auth'], vm.get('phase4d3_auth_report'))
                validate_guest_postcondition_report(witness, vm['phase4d3_auth_report'],
                                                   vm.get('phase4d3_postcondition_report'))
            except (AuthReportError, PostconditionReviewError, TypeError, KeyError, ValueError):
                result.update(status='GUEST_RESULTS_NOT_VERIFIED',
                              issue_code='D3_GUEST_REPORT_NOT_VERIFIED')
            else:
                ar = vm['phase4d3_auth_report']
                dbr = vm['phase4d3_postcondition_report']
                result.update(auth_summary=ar['summary'],
                              session_cookie_observed=ar['session_cookie_observed'],
                              results=dbr['results'], summary=dbr['summary'], issue_code=None)
                result['status'] = ('POSTCONDITIONS_INCONCLUSIVE'
                                    if dbr['summary']['inconclusive'] or ar['summary']['inconclusive']
                                    else 'POSTCONDITIONS_FAILED'
                                    if dbr['summary']['failed'] or ar['summary']['failed']
                                    else 'POSTCONDITIONS_PASSED')
    _write(output_dir / 'd3_run.json', result)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='4C-D3 reviewed guest-only database witness')
    for name in ('plan', 'auth', 'oracles', 'postconditions', 'candidate', 'vm-config', 'out'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--work-root', type=Path)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--preflight-only', action='store_true')
    group.add_argument('--confirm-reviewed-db-postconditions', action='store_true')
    args = p.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        auth = _load_strict_json(args.auth, 64_000)
        oracles = _load_strict_json(args.oracles, 256_000)
        postconditions = _load_strict_json(args.postconditions, 32_768)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        if args.preflight_only:
            report = preflight(plan, auth, oracles, postconditions, candidate, config, args.out)
        else:
            report = run(plan=plan, auth=auth, oracles=oracles,
                         postconditions=postconditions, candidate=candidate,
                         config=config, output_dir=args.out, work_root=args.work_root)
    except (OSError, TypeError, KeyError, RuntimeError, ValueError):
        print('Phase 4C-D3 rejected input or incomplete review evidence', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4C-D3 — Database Postconditions')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    if not args.preflight_only:
        print('  Cleanup:   ' + report['cleanup'])
        print('  DB checks: ' + str(report['summary']))
    print('  JSON:      ' + str(args.out / ('d3_preflight.json' if args.preflight_only else 'd3_run.json')))
    return 0 if report['status'] in ('APPROVED_NOT_EXECUTED', 'POSTCONDITIONS_PASSED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
