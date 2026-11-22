"""4C-D5: cross-repository reviewed functional pilot in distinct disposable VMs.

This is a controller over existing D3 and 4C-C runners, not a new executor.
Both source approvals, catalogs, and a cross-repository suite digest must pass
preflight before the first VM. Each VM is destroyed before the next starts.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
from typing import Any

from .cross_repository_contracts import (
    CrossRepositoryReviewError, compile_reviewed_suite,
)
from .oracle_contracts import digest

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_REMOVED = 'SIGNED_LEASED_WORKSPACE_REMOVED'
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
_BACKEND = 'QEMU_DISPOSABLE_QCOW2_VM'
_LEASE = re.compile(r'piq-d5c3-[a-f0-9]{20}\Z', re.ASCII)


def _save(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(body, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def _fresh(path: Path) -> None:
    if path.exists():
        raise CrossRepositoryReviewError('D5_REQUIRES_FRESH_OUTPUT_DIRECTORY')


def _vm_boundary(vm: Any, case: dict, seen: set[str]) -> tuple[str | None, str | None]:
    """Independently validate the signed VM and its removed workspace."""
    if not isinstance(vm, dict):
        return None, 'D5_VM_REPORT_MISSING'
    if (vm.get('source') != case['source'] or vm.get('adapter') != case['adapter']
            or vm.get('status') != 'STARTUP_VERIFIED'
            or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
            or vm.get('cleanup') != _CLEAN
            or vm.get('managed_runtime') != _REMOVED
            or vm.get('backend') != _BACKEND
            or vm.get('security_attestation') != _ATTESTATION):
        return None, 'D5_VM_OR_CLEANUP_NOT_VERIFIED'
    ident = vm.get('managed_run_id')
    # D5c creates the unique cryptographic identifier inside the signed
    # runtime registry. Do not surface it in aggregate output.
    if (not isinstance(ident, str) or not _LEASE.fullmatch(ident)
            or ident in seen):
        return None, 'D5_VM_LEASE_NOT_UNIQUE_OR_INVALID'
    return ident, None


def _verify_case(report: Any, vm: Any, compiled: dict, seen: set[str]) -> tuple[str | None, str | None]:
    """Return verified case status and lease; classify real functional failures."""
    case = compiled['case']
    lease, issue = _vm_boundary(vm, case, seen)
    if issue:
        return None, issue
    if (not isinstance(report, dict)
            or report.get('source') != case['source']
            or report.get('adapter') != case['adapter']
            or report.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
            or report.get('cleanup') != _CLEAN):
        return None, 'D5_CASE_RESULT_NOT_VERIFIED'
    try:
        if case['workflow'] == 'D3_REGISTRATION':
            from .db_postcondition_contracts import validate_guest_postcondition_report
            from .auth_report_contracts import validate_auth_report
            proof = compiled['proof']
            # The D3 runner and VM boundary already validate guest evidence.
            # Bind the report to the exact operator-approved catalog again.
            if (vm.get('phase4d3_status') != 'VALIDATED'
                    or report.get('postcondition_catalog_sha256') !=
                    proof['postconditions']['postcondition_catalog_sha256']
                    or report.get('session_cookie_observed') is not True
                    or report.get('auth_summary') != {
                        'total': 4, 'passed': 4, 'failed': 0, 'inconclusive': 0}):
                return None, 'D5_DATABASE_REVIEW_OR_AUTH_NOT_VERIFIED'
            validate_auth_report(proof['auth'], vm.get('phase4d3_auth_report'))
            validate_guest_postcondition_report(
                proof['postconditions'], vm['phase4d3_auth_report'],
                vm.get('phase4d3_postcondition_report'))
            # The guest report is independently validated against exact reviewed
            # input, and the host aggregate must mirror that report exactly.
            observed = vm['phase4d3_postcondition_report']
            if (report.get('results') != observed['results']
                    or report.get('summary') != observed['summary']
                    or report.get('auth_summary') != vm['phase4d3_auth_report']['summary']):
                return None, 'D5_DATABASE_GUEST_REPORT_MISMATCH'
            checks = report.get('results')
            if (not isinstance(checks, list) or len(checks) != 1
                    or report.get('summary', {}).get('total') != 1
                    or type(report['summary'].get('passed')) is not int
                    or type(report['summary'].get('failed')) is not int
                    or report['summary'].get('inconclusive') != 0):
                return None, 'D5_DATABASE_RESULTS_NOT_VERIFIED'
            one = checks[0]
            if (report['status'] == 'POSTCONDITIONS_PASSED'
                    and report['summary']['passed'] == 1
                    and report['summary']['failed'] == 0
                    and one.get('result') == 'PASS'
                    and one.get('before_matches') == 0
                    and one.get('after_matches') == 1
                    and one.get('expected_fields_match') is True):
                return lease, None
            if (report['status'] == 'POSTCONDITIONS_FAILED'
                    and report['summary']['failed'] == 1
                    and report['summary']['passed'] == 0
                    and one.get('result') == 'FAIL'):
                return lease, 'D5_FUNCTIONAL_ASSERTION_FAILED'
            return None, 'D5_DATABASE_RESULTS_NOT_VERIFIED'
        else:
            from .api_report_contracts import validate_api_report
            bundle = compiled['proof']
            guest = vm.get('phase4c_test_results')
            if vm.get('phase4c_status') != 'VALIDATED':
                return None, 'D5_API_GUEST_NOT_VERIFIED'
            validate_api_report(bundle, guest)
            if (report.get('oracle_catalog_sha256') != digest(compiled['docs']['oracles'])
                    or report.get('summary') != guest.get('summary')
                    or report.get('results') != guest.get('results')):
                return None, 'D5_API_ORACLE_REPORT_MISMATCH'
            summary = guest['summary']
            if (summary['total'] != compiled['test_count']
                    or summary['inconclusive'] != 0):
                return None, 'D5_API_INCONCLUSIVE_OR_WRONG_COUNT'
            if report.get('status') == 'TESTS_PASSED' and summary['passed'] == summary['total']:
                return lease, None
            if report.get('status') == 'TESTS_FAILED' and summary['failed'] > 0:
                return lease, 'D5_FUNCTIONAL_ASSERTION_FAILED'
            return None, 'D5_API_RESULTS_NOT_VERIFIED'
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, 'D5_CASE_EVIDENCE_INVALID'


def preflight(*, manifest: dict, config: dict, inputs_root: Path,
              output_dir: Path) -> dict:
    _fresh(output_dir)
    compiled = compile_reviewed_suite(manifest, config, inputs_root)
    result = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'cross_repository_preflight',
        'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'suite_sha256': digest(manifest), 'case_count': len(compiled),
        'cases': [{'id': x['case']['id'], 'source': x['case']['source'],
                   'adapter': x['case']['adapter'], 'workflow': x['case']['workflow'],
                   'test_count': x['test_count']}
                  for x in compiled],
        'security_attestation': _ATTESTATION,
        'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
    }
    _save(output_dir / 'd5_preflight.json', result)
    return result


def run(*, manifest: dict, config: dict, inputs_root: Path,
        output_dir: Path, work_root: Path | None = None) -> dict:
    _fresh(output_dir)
    compiled = compile_reviewed_suite(manifest, config, inputs_root)
    result = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_cross_repository_functional_pilot',
        'suite_sha256': digest(manifest), 'status': 'CROSS_REPOSITORY_NOT_VERIFIED',
        'execution': 'NOT_PERFORMED', 'issue_code': 'CASE_EXECUTION_INCOMPLETE',
        'summary': {'total': len(compiled), 'passed': 0, 'failed': 0,
                    'not_executed': len(compiled)},
        'cases': [], 'security_attestation': _ATTESTATION,
        'limitations': [
            'Two reviewed pinned repositories only, not arbitrary untrusted student execution.',
            'Registration persistence covers one users row; JSON subset tests cover only approved API assertions.',
            'Distinct disposable VMs, not whole-application coverage or security attestation.',
        ],
    }
    _save(output_dir / 'd5_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'cross_repository_review',
        'suite_sha256': digest(manifest), 'status': 'EXACT_OPERATOR_APPROVAL_MATCHED',
        'execution': 'NOT_PERFORMED',
        'cases': [{'id': x['case']['id'], 'source': x['case']['source'],
                   'adapter': x['case']['adapter'], 'workflow': x['case']['workflow'],
                   'input_sha256': x['case']['input_sha256']}
                  for x in compiled],
    })
    leases: set[str] = set()
    for item in compiled:
        case = item['case']
        directory = output_dir / 'cases' / case['id']
        workflow = case['workflow']
        report_name = ('d3_run.json' if workflow == 'D3_REGISTRATION' else 'api_run.json')
        vm_name = ('d3_vm_execution.json' if workflow == 'D3_REGISTRATION' else 'api_vm_execution.json')
        row = {
            'id': case['id'], 'source': case['source'], 'adapter': case['adapter'],
            'workflow': workflow, 'status': 'NOT_VERIFIED', 'cleanup': 'NOT_VERIFIED',
            'issue_code': 'D5_CASE_NOT_VERIFIED', 'test_count': item['test_count'],
            'artifacts': {'report': f'cases/{case["id"]}/{report_name}',
                          'vm': f'cases/{case["id"]}/{vm_name}'},
        }
        try:
            docs = item['docs']
            if workflow == 'D3_REGISTRATION':
                from .db_postcondition_vm_run import run as existing_run
                report = existing_run(plan=docs['plan'], auth=docs['auth'],
                    oracles=docs['oracles'], postconditions=docs['postconditions'],
                    candidate=docs['candidate'], config=config,
                    output_dir=directory, work_root=work_root)
            else:
                from .api_vm_run import run_reviewed_api as existing_run
                report = existing_run(plan=docs['plan'], catalog=docs['oracles'],
                    candidate=docs['candidate'], config=config,
                    output_dir=directory, work_root=work_root)
            from .oracle_stage import _load_strict_json
            vm_file = directory / vm_name
            vm = _load_strict_json(vm_file, 200_000)
            lease, issue = _verify_case(report, vm, item, leases)
            row['cleanup'] = report.get('cleanup', 'NOT_VERIFIED')
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            lease, issue = None, 'D5_CASE_OR_VM_EVIDENCE_UNAVAILABLE'
        if lease is not None:
            leases.add(lease)
        if issue is None:
            row.update(status='VERIFIED', issue_code=None, cleanup=_CLEAN)
            result['summary']['passed'] += 1
        else:
            row.update(status=('FUNCTIONAL_FAILURE' if issue == 'D5_FUNCTIONAL_ASSERTION_FAILED'
                               else 'NOT_VERIFIED'), issue_code=issue)
            result['summary']['failed'] += 1
        result['summary']['not_executed'] -= 1
        result['cases'].append(row)
        result['execution'] = 'ATTEMPTED_SEQUENTIAL_DISPOSABLE_VMS'
        if issue:
            result['issue_code'] = issue
            break  # never launch a subsequent app after missing teardown/evidence
    if result['summary']['passed'] == len(compiled):
        result.update(status='CROSS_REPOSITORY_VERIFIED',
                      execution='PERFORMED_IN_DISTINCT_DISPOSABLE_VMS',
                      issue_code=None)
    elif result['issue_code'] == 'D5_FUNCTIONAL_ASSERTION_FAILED':
        result['status'] = 'FUNCTIONAL_FAILURE_DETECTED'
    _save(output_dir / 'd5_run.json', result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='D5 two-repository reviewed-VM functional pilot')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--inputs-root', type=Path, required=True)
    parser.add_argument('--vm-config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight-only', action='store_true')
    mode.add_argument('--confirm-reviewed-cross-repository', action='store_true')
    args = parser.parse_args(argv)
    try:
        from .oracle_stage import _load_strict_json
        from ..phase2.vm_worker.orchestrator import load_config
        manifest = _load_strict_json(args.manifest, 32_768)
        config = load_config(args.vm_config)
        if args.preflight_only:
            report = preflight(manifest=manifest, config=config,
                               inputs_root=args.inputs_root, output_dir=args.out)
        else:
            report = run(manifest=manifest, config=config,
                         inputs_root=args.inputs_root, output_dir=args.out,
                         work_root=args.work_root)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        print('Phase 4C-D5 rejected input or missing operator review', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4C-D5 — Cross-Repository Functional Pilot')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    print('  Cases:     ' + str(report.get('summary', {}).get('total', report.get('case_count'))))
    print('  JSON:      ' + str(args.out / ('d5_preflight.json' if args.preflight_only else 'd5_run.json')))
    return 0 if report['status'] in ('APPROVED_NOT_EXECUTED', 'CROSS_REPOSITORY_VERIFIED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
