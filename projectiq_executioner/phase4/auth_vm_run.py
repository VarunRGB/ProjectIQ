"""Phase 4C-D2: approved authentication contrast, never outside guest VM.

Requires separate exact approvals for the auth flow and both protected oracles.
The operator controls source, expectations and routes; password is generated in
VM and deliberately never part of a host-side artifact or VM configuration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .auth_report_contracts import AuthReportError, validate_auth_report
from .oracle_stage import _load_strict_json
from .oracle_contracts import digest
from .reviewed_auth import AuthReviewError, compile_reviewed_auth_bundle

CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
_FILES = ('auth_preflight.json', 'auth_review.json', 'auth_vm_execution.json', 'auth_run.json')


def _save(path: Path, data: Mapping[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def _no_overwrite(output: Path):
    if any((output / name).exists() for name in _FILES):
        raise AuthReviewError('AUTH_EVIDENCE_ALREADY_EXISTS')


def preflight_auth(plan, auth_catalog, oracle_catalog, candidate, config, output_dir: Path) -> dict:
    _no_overwrite(output_dir)
    bundle = compile_reviewed_auth_bundle(plan, auth_catalog, oracle_catalog, candidate, config)
    guest = bundle['guest']
    report = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'approved_authentication_preflight',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'auth_catalog_sha256': guest['auth_catalog_sha256'],
        'oracle_catalog_sha256': guest['oracle_catalog_sha256'],
        'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'steps': ['UNAUTHENTICATED', 'REGISTRATION', 'LOGIN', 'AUTHENTICATED'],
        'credential_origin': 'GENERATED_INSIDE_DISPOSABLE_VM_ON_EXECUTION',
        'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
        'security_attestation': ATTESTATION,
    }
    _save(output_dir / 'auth_preflight.json', report)
    return report


def run_reviewed_auth(*, plan, auth_catalog, oracle_catalog, candidate, config,
                      output_dir: Path, work_root: Path | None = None) -> dict:
    _no_overwrite(output_dir)
    bundle = compile_reviewed_auth_bundle(plan, auth_catalog, oracle_catalog, candidate, config)
    guest = bundle['guest']
    _save(output_dir / 'auth_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'approved_authentication_review',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'phase3_plan_sha256': digest(plan), 'phase2_candidate_sha256': digest(candidate),
        'auth_catalog_sha256': guest['auth_catalog_sha256'],
        'oracle_catalog_sha256': guest['oracle_catalog_sha256'],
        'approved_oracle_ids': [guest['unauthenticated']['id'], guest['authenticated']['id']],
        'status': 'EXACT_DIGEST_APPROVALS_MATCHED', 'execution': 'NOT_PERFORMED',
    })
    report = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_vm_authentication_testing',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'auth_catalog_sha256': guest['auth_catalog_sha256'],
        'oracle_catalog_sha256': guest['oracle_catalog_sha256'],
        'status': 'VM_EXECUTION_NOT_VERIFIED', 'execution': 'NOT_VERIFIED',
        'cleanup': 'NOT_VERIFIED', 'security_attestation': ATTESTATION,
        'issue_code': 'VM_WORKER_EXCEPTION',
        'summary': {'total': 0, 'passed': 0, 'failed': 0, 'inconclusive': 0},
        'results': [], 'session_cookie_observed': False,
        'artifacts': {'review': 'auth_review.json', 'vm': 'auth_vm_execution.json'},
        'limitations': ['Guest-generated throwaway identities only; no external identity provider.',
                        'Only exact reviewed registration/login and error-path probes.',
                        'No database postcondition or role authorization assertions yet.',
                        'Not an attestation for arbitrary untrusted student submissions.'],
    }
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  output_dir / 'auth_vm_execution.json',
                                  reviewed_fixture=True, work_root=work_root,
                                  auth_bundle=bundle)
    except (ValueError, OSError, KeyError, RuntimeError, TypeError):
        pass
    else:
        report['execution'] = vm.get('execution', 'NOT_VERIFIED')
        report['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        if report['cleanup'] != CLEAN:
            report.update(status='CLEANUP_FAILED', issue_code='VM_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED'
              or vm.get('source') != candidate['source']
              or vm.get('adapter') != candidate['adapter']
              or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
              or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
              or vm.get('security_attestation') != ATTESTATION
              or vm.get('phase4d2_status') != 'VALIDATED'):
            report.update(status='VM_EXECUTION_NOT_VERIFIED', issue_code='VM_AUTH_REPORT_NOT_VERIFIED')
        else:
            try:
                validate_auth_report(guest, vm.get('phase4d2_auth_report'))
            except (AuthReportError, TypeError, ValueError, KeyError):
                report.update(status='GUEST_RESULTS_NOT_VERIFIED',
                              issue_code='GUEST_AUTH_REPORT_NOT_VERIFIED')
            else:
                evidence = vm['phase4d2_auth_report']
                report['summary'] = evidence['summary']
                report['results'] = evidence['results']
                report['session_cookie_observed'] = evidence['session_cookie_observed']
                report['issue_code'] = None
                report['status'] = ('TESTS_INCONCLUSIVE' if evidence['summary']['inconclusive'] else
                                    'TESTS_FAILED' if evidence['summary']['failed'] else 'TESTS_PASSED')
    _save(output_dir / 'auth_run.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Phase 4C-D2 reviewed guest-only session authentication')
    for required in ('plan', 'auth', 'oracles', 'candidate', 'vm-config', 'out'):
        parser.add_argument('--' + required, type=Path, required=True)
    parser.add_argument('--work-root', type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--preflight-only', action='store_true')
    group.add_argument('--confirm-reviewed-auth-execution', action='store_true')
    args = parser.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        auth = _load_strict_json(args.auth, 64_000)
        oracles = _load_strict_json(args.oracles, 256_000)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        if args.preflight_only:
            output = preflight_auth(plan, auth, oracles, candidate, config, args.out)
        else:
            output = run_reviewed_auth(plan=plan, auth_catalog=auth,
                                       oracle_catalog=oracles, candidate=candidate,
                                       config=config, output_dir=args.out,
                                       work_root=args.work_root)
    except (ValueError, OSError, KeyError, RuntimeError, TypeError):
        print('Phase 4C-D2 rejected auth review or incomplete evidence', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4C-D2 — Reviewed VM Authentication')
    print('  Status:    ' + output['status'])
    print('  Execution: ' + output['execution'])
    if not args.preflight_only:
        print('  Cleanup:   ' + output['cleanup'])
        summary = output['summary']
        print('  Steps:     ' + str(summary['total']) + ' (pass=' + str(summary['passed']) +
              ' fail=' + str(summary['failed']) + ' inconclusive=' + str(summary['inconclusive']) + ')')
    print('  JSON:      ' + str(args.out / ('auth_preflight.json' if args.preflight_only else 'auth_run.json')))
    return 0 if output['status'] in ('APPROVED_NOT_EXECUTED', 'TESTS_PASSED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
