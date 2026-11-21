"""Reviewed API oracle execution inside disposable Phase 2 VM only.

No oracle catalog can authorize a VM launch. This checks a second independent,
operator-owned exact-digest oracle approval and reuses Phase 2's SHA gates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .api_report_contracts import ApiReportError, validate_api_report
from .oracle_contracts import digest
from .reviewed_api import ReviewedApiError, compile_reviewed_api_bundle
from .oracle_stage import _load_strict_json

CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


class ApiRunError(ValueError):
    """Invalid evidence destination."""


def _persist(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def _report(source: dict, adapter: str | None, catalog_sha: str | None) -> dict:
    return {'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_vm_api_testing',
            'source': source, 'adapter': adapter, 'oracle_catalog_sha256': catalog_sha,
            'status': 'BLOCKED', 'execution': 'NOT_PERFORMED', 'cleanup': 'NOT_STARTED',
            'security_attestation': ATTESTATION,
            'summary': {'total': 0, 'passed': 0, 'failed': 0, 'inconclusive': 0},
            'results': [], 'issue_code': None,
            'artifacts': {},
            'limitations': ['Reviewed operator expectations may be incorrect.',
                            'Credentials, sessions, and database fixtures need separate 4C-D review.',
                            'Not a security attestation for arbitrary student submissions.']}


def run_reviewed_api(*, plan: Mapping[str, Any], catalog: Mapping[str, Any],
                     candidate: Mapping[str, Any], config: Mapping[str, Any],
                     output_dir: Path, work_root: Path | None = None) -> dict:
    """Validate inputs before any VM operation, then verify guest evidence/cleanup."""
    output = Path(output_dir)
    if any((output / name).exists() for name in ('api_review.json', 'api_vm_execution.json', 'api_run.json')):
        raise ApiRunError('API_OUTPUT_ALREADY_EXISTS')
    bundle = compile_reviewed_api_bundle(plan, catalog, candidate, config)
    report = _report(dict(plan['source']), candidate['adapter'], digest(catalog))
    report['artifacts'] = {'review': 'api_review.json', 'vm': 'api_vm_execution.json'}
    # No request values, PII or operator configuration are saved in this evidence.
    _persist(output / 'api_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_api_approval',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'phase3_plan_sha256': digest(plan), 'oracle_catalog_sha256': digest(catalog),
        'phase2_candidate_sha256': digest(candidate),
        'approved_test_ids': [x['id'] for x in bundle['tests']],
        'approval': 'MATCHED_OPERATOR_CONFIG_EXACT_CATALOG_DIGEST',
        'execution': 'NOT_PERFORMED', 'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
    })
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  output / 'api_vm_execution.json', reviewed_fixture=True,
                                  work_root=work_root, api_bundle=bundle)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        report.update(status='VM_EXECUTION_NOT_VERIFIED', execution='NOT_VERIFIED',
                      cleanup='NOT_VERIFIED', issue_code='VM_WORKER_EXCEPTION')
    else:
        report['execution'] = vm.get('execution', 'NOT_VERIFIED')
        report['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        if vm.get('cleanup') != CLEAN:
            report.update(status='CLEANUP_FAILED', issue_code='VM_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED'
              or vm.get('source') != candidate['source']
              or vm.get('adapter') != candidate['adapter']
              or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
              or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
              or vm.get('security_attestation') != ATTESTATION):
            report.update(status='VM_EXECUTION_NOT_VERIFIED', issue_code='VM_OR_GUEST_API_NOT_VERIFIED')
        elif vm.get('phase4c_status') != 'VALIDATED':
            report.update(status='GUEST_RESULTS_NOT_VERIFIED', issue_code='API_GUEST_REPORT_NOT_VERIFIED')
        else:
            guest = vm.get('phase4c_test_results')
            try:
                validate_api_report(bundle, guest)
            except (ApiReportError, TypeError, ValueError):
                report.update(status='GUEST_RESULTS_NOT_VERIFIED', issue_code='API_GUEST_REPORT_NOT_VERIFIED')
            else:
                report['summary'] = guest['summary']
                report['results'] = guest['results']
                report['status'] = ('TESTS_INCONCLUSIVE' if guest['summary']['inconclusive'] else
                                    'TESTS_FAILED' if guest['summary']['failed'] else 'TESTS_PASSED')
    _persist(output / 'api_run.json', report)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Phase 4C-C: reviewed functional API tests in approved VM')
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--oracles', required=True, type=Path)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--vm-config', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--work-root', type=Path)
    p.add_argument('--preflight-only', action='store_true',
                   help='Validate operator review and compile without launching a VM')
    p.add_argument('--confirm-reviewed-api-execution', action='store_true',
                   help='Explicitly permit operator-reviewed API operations in an approved VM')
    args = p.parse_args(argv)
    if not args.preflight_only and not args.confirm_reviewed_api_execution:
        p.error('Explicit --confirm-reviewed-api-execution required')
    if args.preflight_only and args.confirm_reviewed_api_execution:
        p.error('Choose preflight or VM execution, not both')
    if any((args.out / x).exists() for x in ('api_preflight.json', 'api_review.json', 'api_vm_execution.json', 'api_run.json')):
        p.error('Refusing to overwrite previous Phase 4C-C evidence')
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        catalog = _load_strict_json(args.oracles, 256_000)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        if args.preflight_only:
            bundle = compile_reviewed_api_bundle(plan, catalog, candidate, config)
            _persist(args.out / 'api_preflight.json', {
                'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_api_preflight',
                'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
                'source': dict(plan['source']), 'adapter': candidate['adapter'],
                'oracle_catalog_sha256': digest(catalog),
                'tests': [{'id': t['id'], 'method': t['method'], 'path': t['path'],
                           'body_kind': t['body_kind']} for t in bundle['tests']],
                'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
            })
            print('Phase 4C-C — Approved API preflight (NO VM)')
            print('  Status: APPROVED_NOT_EXECUTED')
            print('  Scenarios: ' + str(len(bundle['tests'])))
            print('  JSON: ' + str(args.out / 'api_preflight.json'))
            return 0
        report = run_reviewed_api(plan=plan, catalog=catalog, candidate=candidate,
                                  config=config, output_dir=args.out, work_root=args.work_root)
    except (ValueError, TypeError, OSError, KeyError, RuntimeError) as exc:
        # Fail before VM launch for invalid reviews; no exception strings disclosed.
        print('Phase 4C-C rejected input/review or failed before evidence completion', file=sys.stderr)
        return 2
    s = report['summary']
    print('Phase 4C-C — Reviewed VM API Tests')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    print('  Cleanup:   ' + report['cleanup'])
    print(f"  Tests:     {s['total']} (pass={s['passed']} fail={s['failed']} inconclusive={s['inconclusive']})")
    print('  JSON:      ' + str(args.out / 'api_run.json'))
    return 0 if report['status'] == 'TESTS_PASSED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
