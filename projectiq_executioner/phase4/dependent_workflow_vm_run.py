"""4D-A approved three-step, capture-and-use dependent API workflow in QEMU.

No student code runs on Windows. No launch without independently approved D1
fixture and exact workflow catalog. Save redacted, bounded evidence exclusively.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .dependent_workflow_contracts import (
    DependentWorkflowReviewError, compile_reviewed_dependent_workflow,
)
from .db_fixture_contracts import validate_fixture_report, FixtureReviewError
from .guest_workflow import validate_guest_workflow_report, DependentWorkflowError
from .oracle_stage import _load_strict_json

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(body, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def _fresh(out: Path) -> None:
    if out.exists():
        raise DependentWorkflowReviewError('D4A_REQUIRES_FRESH_OUTPUT_DIRECTORY')


def preflight(*, plan, fixture_catalog, workflow_catalog, candidate,
              config, output_dir: Path) -> dict:
    _fresh(output_dir)
    compiled = compile_reviewed_dependent_workflow(
        plan, fixture_catalog, workflow_catalog, candidate, config)
    workflow = compiled['workflow']
    report = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'dependent_api_preflight',
        'source': workflow['source'], 'adapter': workflow['adapter'],
        'workflow_catalog_sha256': workflow['workflow_catalog_sha256'],
        'fixture_catalog_sha256': workflow['fixture_catalog_sha256'],
        'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'security_attestation': _ATTESTATION,
        'steps': [{'role': s['role'], 'method': s['method'], 'path': s['path']}
                  for s in workflow['steps']],
        'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
    }
    _write(output_dir / 'd4a_preflight.json', report)
    return report


def run(*, plan, fixture_catalog, workflow_catalog, candidate,
        config, output_dir: Path, work_root: Path | None = None) -> dict:
    _fresh(output_dir)
    compiled = compile_reviewed_dependent_workflow(
        plan, fixture_catalog, workflow_catalog, candidate, config)
    workflow = compiled['workflow']
    result = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_vm_dependent_api_workflow',
        'source': workflow['source'], 'adapter': workflow['adapter'],
        'workflow_catalog_sha256': workflow['workflow_catalog_sha256'],
        'fixture_catalog_sha256': workflow['fixture_catalog_sha256'],
        'status': 'WORKFLOW_NOT_VERIFIED', 'execution': 'NOT_PERFORMED',
        'cleanup': 'NOT_STARTED', 'security_attestation': _ATTESTATION,
        'issue_code': 'WORKFLOW_VM_NOT_VERIFIED', 'observations': None,
        'artifacts': {'review': 'd4a_review.json', 'vm': 'd4a_vm_execution.json'},
        'limitations': [
            'Pilot: one synthetic D1 task and three reviewed JSON API steps only.',
            'No full CRUD assertion or browser execution; data passing is an integer ID.',
            'Guest-generated observations are validated, not independently security-attested.',
            'Arbitrary untrusted student execution remains disabled.',
        ],
    }
    _write(output_dir / 'd4a_review.json', {
        'schema_version': '0.1', 'stage': 'dependent_api_review',
        'source': workflow['source'], 'adapter': workflow['adapter'],
        'workflow_catalog_sha256': workflow['workflow_catalog_sha256'],
        'fixture_catalog_sha256': workflow['fixture_catalog_sha256'],
        'status': 'EXACT_OPERATOR_APPROVAL_MATCHED', 'execution': 'NOT_PERFORMED',
    })
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  output_dir / 'd4a_vm_execution.json',
                                  reviewed_fixture=True, work_root=work_root,
                                  workflow_bundle=compiled)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        result.update(status='WORKFLOW_VM_NOT_VERIFIED', execution='NOT_VERIFIED',
                      cleanup='NOT_VERIFIED', issue_code='VM_WORKER_EXCEPTION')
    else:
        result['execution'] = vm.get('execution', 'NOT_VERIFIED')
        result['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        if vm.get('cleanup') != _CLEAN:
            result.update(status='CLEANUP_FAILED', issue_code='WORKFLOW_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED'
                or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
                or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
                or vm.get('source') != candidate['source']
                or vm.get('adapter') != candidate['adapter']
                or vm.get('managed_runtime') != 'SIGNED_LEASED_WORKSPACE_REMOVED'
                or vm.get('security_attestation') != _ATTESTATION
                or vm.get('phase4da_status') != 'VALIDATED'):
            result.update(status='WORKFLOW_VM_NOT_VERIFIED',
                          issue_code='WORKFLOW_VM_OR_GUEST_REPORT_NOT_VERIFIED')
        else:
            try:
                validate_fixture_report(compiled['fixture'], vm.get('phase4da_fixture_report'))
                validate_guest_workflow_report(workflow, vm.get('phase4da_workflow_report'))
            except (FixtureReviewError, DependentWorkflowError, TypeError, ValueError, KeyError):
                result.update(status='WORKFLOW_VM_NOT_VERIFIED',
                              issue_code='WORKFLOW_GUEST_EVIDENCE_NOT_VERIFIED')
            else:
                guest = vm['phase4da_workflow_report']
                result['observations'] = guest['observations']
                result['issue_code'] = guest['issue_code']
                result['status'] = {
                    'PASS': 'WORKFLOW_PASSED',
                    'FAIL': 'WORKFLOW_FAILED',
                    'INCONCLUSIVE': 'WORKFLOW_INCONCLUSIVE',
                }[guest['status']]
    _write(output_dir / 'd4a_run.json', result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='4D-A reviewed dependent API workflow in disposable VM')
    for part in ('plan', 'fixtures', 'workflow', 'candidate', 'vm-config', 'out'):
        parser.add_argument('--' + part, required=True, type=Path)
    parser.add_argument('--work-root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight-only', action='store_true')
    mode.add_argument('--confirm-reviewed-dependent-workflow', action='store_true')
    args = parser.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        fixtures = _load_strict_json(args.fixtures, 64_000)
        catalog = _load_strict_json(args.workflow, 32_768)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        kwargs = dict(plan=plan, fixture_catalog=fixtures, workflow_catalog=catalog,
                      candidate=candidate, config=config, output_dir=args.out)
        report = (preflight(**kwargs) if args.preflight_only
                  else run(**kwargs, work_root=args.work_root))
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        print('Phase 4D-A rejected input, missing review or invalid evidence', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4D-A — Dependent API Workflow')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    print('  JSON:      ' + str(args.out / ('d4a_preflight.json' if args.preflight_only else 'd4a_run.json')))
    return 0 if report['status'] in ('APPROVED_NOT_EXECUTED', 'WORKFLOW_PASSED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
