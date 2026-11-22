"""4C-D4: sequential, independently leased disposable VMs per reviewed scenario.

Reuses D3 unchanged. Each run destroys its entire VM overlay and workspace;
no operator SQL, unsafe application-level reset or host DB credentials.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .scenario_isolation_contracts import (
    IsolationReviewError, compile_reviewed_isolation,
)

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_REMOVED = 'SIGNED_LEASED_WORKSPACE_REMOVED'
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def _save_exclusive(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def _new_output(path: Path) -> None:
    if path.exists():
        raise IsolationReviewError('D4_REQUIRES_NEW_OUTPUT_DIRECTORY')


def preflight(plan, auth, oracles, postconditions, candidate, config,
              manifest, output_dir: Path) -> dict:
    _new_output(output_dir)
    approved = compile_reviewed_isolation(plan, auth, oracles, postconditions,
                                           candidate, config, manifest)
    report = {'schema_version': '0.1', 'phase': 4, 'stage': 'isolation_preflight',
              'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
              'source': approved['source'], 'adapter': approved['adapter'],
              'strategy': manifest['strategy'],
              'isolation_manifest_sha256': approved['manifest_sha256'],
              'postcondition_catalog_sha256': approved['postcondition_catalog_sha256'],
              'scenario_count': len(approved['scenario_ids']),
              'scenario_ids': approved['scenario_ids'],
              'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
              'security_attestation': _ATTESTATION}
    _save_exclusive(output_dir / 'd4_preflight.json', report)
    return report


def _verify_vm_boundary(d3: Any, vm: Any, prior_leases: set[str]) -> tuple[str | None, str | None]:
    """Validate full D3 result plus unique destroyed signed lease before reuse."""
    if (not isinstance(d3, dict) or not isinstance(vm, dict)
            or d3.get('status') != 'POSTCONDITIONS_PASSED'
            or d3.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
            or d3.get('cleanup') != _CLEAN
            or d3.get('session_cookie_observed') is not True
            or d3.get('auth_summary') != {
                'total': 4, 'passed': 4, 'failed': 0, 'inconclusive': 0}
            or d3.get('summary') != {
                'total': 1, 'passed': 1, 'failed': 0, 'inconclusive': 0}):
        return None, 'D3_SCENARIO_NOT_VERIFIED'
    results = d3.get('results')
    if (not isinstance(results, list) or len(results) != 1
            or not isinstance(results[0], dict)
            or results[0].get('result') != 'PASS'
            or type(results[0].get('before_matches')) is not int
            or results[0]['before_matches'] != 0
            or type(results[0].get('after_matches')) is not int
            or results[0]['after_matches'] != 1
            or results[0].get('expected_fields_match') is not True
            or results[0].get('issue_code') is not None):
        return None, 'REGISTRATION_BASELINE_NOT_VERIFIED'
    lease = vm.get('managed_run_id')
    if (not isinstance(lease, str) or not 1 <= len(lease) <= 128
            or lease in prior_leases or any(not (c.isalnum() or c in '-_') for c in lease)):
        return None, 'VM_LEASE_IDENTITY_NOT_UNIQUE'
    if (vm.get('status') != 'STARTUP_VERIFIED'
            or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
            or vm.get('cleanup') != _CLEAN
            or vm.get('managed_runtime') != _REMOVED
            or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
            or vm.get('security_attestation') != _ATTESTATION
            or vm.get('phase4d3_status') != 'VALIDATED'):
        return None, 'VM_LIFECYCLE_OR_CLEANUP_NOT_VERIFIED'
    if vm.get('source') != d3.get('source') or vm.get('adapter') != d3.get('adapter'):
        return None, 'SCENARIO_SOURCE_MISMATCH'
    return lease, None


def run(*, plan, auth, oracles, postconditions, candidate, config,
        manifest, output_dir: Path, work_root: Path | None = None) -> dict:
    _new_output(output_dir)
    approved = compile_reviewed_isolation(plan, auth, oracles, postconditions,
                                           candidate, config, manifest)
    # Lazy import allows offline contract tests to run without QEMU dependencies.
    from .db_postcondition_vm_run import run as run_d3
    result = {'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_vm_scenario_isolation',
              'source': approved['source'], 'adapter': approved['adapter'],
              'strategy': manifest['strategy'],
              'isolation_manifest_sha256': approved['manifest_sha256'],
              'postcondition_catalog_sha256': approved['postcondition_catalog_sha256'],
              'status': 'ISOLATION_NOT_VERIFIED', 'execution': 'NOT_PERFORMED',
              'security_attestation': _ATTESTATION,
              'issue_code': 'SCENARIO_EXECUTION_INCOMPLETE',
              'summary': {'total': len(approved['scenario_ids']), 'passed': 0,
                          'failed': 0, 'not_executed': len(approved['scenario_ids'])},
              'scenarios': [],
              'limitations': [
                  'Pilot: two reviewed D3 registration-persistence scenarios only.',
                  'Reset means an entirely new disposable VM and SQL import, not in-VM SQL rollback.',
                  'D3 baseline checks only a generated email; no full-table baseline equality claim.',
                  'No attestation for arbitrary untrusted student projects.',
              ]}
    seen: set[str] = set()
    _save_exclusive(output_dir / 'd4_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'isolation_manifest_review',
        'source': approved['source'], 'adapter': approved['adapter'],
        'isolation_manifest_sha256': approved['manifest_sha256'],
        'status': 'EXACT_OPERATOR_APPROVAL_MATCHED', 'execution': 'NOT_PERFORMED',
    })
    for sid in approved['scenario_ids']:
        directory = output_dir / 'scenarios' / sid
        entry = {'id': sid, 'status': 'NOT_VERIFIED', 'cleanup': 'NOT_VERIFIED',
                 'issue_code': 'SCENARIO_NOT_VERIFIED',
                 'artifacts': {'d3': f'scenarios/{sid}/d3_run.json',
                               'vm': f'scenarios/{sid}/d3_vm_execution.json'}}
        # D3 refuses an existing directory and runs the VM only after independent
        # source, digest and reviewed-fixture gates have been rechecked.
        try:
            d3 = run_d3(plan=plan, auth=auth, oracles=oracles,
                        postconditions=postconditions, candidate=candidate,
                        config=config, output_dir=directory, work_root=work_root)
            vm_path = directory / 'd3_vm_execution.json'
            if not vm_path.is_file() or vm_path.stat().st_size > 200_000:
                raise IsolationReviewError('D4_VM_EVIDENCE_MISSING_OR_OVERSIZED')
            from .oracle_stage import _load_strict_json
            vm = _load_strict_json(vm_path, 200_000)
            entry['cleanup'] = d3.get('cleanup', 'NOT_VERIFIED')
            if (d3.get('source') != approved['source']
                    or d3.get('adapter') != approved['adapter']
                    or d3.get('postcondition_catalog_sha256') != approved['postcondition_catalog_sha256']):
                lease, issue = None, 'SCENARIO_REVIEW_BINDING_MISMATCH'
            else:
                lease, issue = _verify_vm_boundary(d3, vm, seen)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            lease, issue = None, 'SCENARIO_OR_VM_REPORT_UNAVAILABLE'
        if issue is None and lease is not None:
            seen.add(lease)
            entry.update(status='RESET_VERIFIED', cleanup=_CLEAN, issue_code=None)
            result['summary']['passed'] += 1
        else:
            issue = issue or 'VM_LEASE_IDENTITY_MISSING'
            entry.update(status='ISOLATION_NOT_VERIFIED', issue_code=issue)
            result['summary']['failed'] += 1
        result['summary']['not_executed'] -= 1
        result['scenarios'].append(entry)
        result['execution'] = 'ATTEMPTED_SEQUENTIAL_DISPOSABLE_VMS'
        if issue is not None:
            # Fail closed: no subsequent VM starts after any missing cleanup or
            # evidence. Keep a durable report of completed and skipped scenarios.
            result['issue_code'] = issue
            break
    if result['summary']['passed'] == len(approved['scenario_ids']):
        result.update(status='SCENARIO_ISOLATION_VERIFIED',
                      execution='PERFORMED_IN_DISTINCT_DISPOSABLE_VMS',
                      issue_code=None)
    _save_exclusive(output_dir / 'd4_run.json', result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='4C-D4 reviewed VM-per-scenario reset')
    for name in ('plan', 'auth', 'oracles', 'postconditions', 'manifest',
                 'candidate', 'vm-config', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--work-root', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight-only', action='store_true')
    mode.add_argument('--confirm-reviewed-scenario-isolation', action='store_true')
    args = parser.parse_args(argv)
    try:
        from .oracle_stage import _load_strict_json
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        auth = _load_strict_json(args.auth, 64_000)
        oracles = _load_strict_json(args.oracles, 256_000)
        postconditions = _load_strict_json(args.postconditions, 32_768)
        manifest = _load_strict_json(args.manifest, 32_768)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        options = dict(plan=plan, auth=auth, oracles=oracles,
                       postconditions=postconditions, candidate=candidate,
                       config=config, manifest=manifest, output_dir=args.out)
        if args.preflight_only:
            report = preflight(**options)
        else:
            report = run(**options, work_root=args.work_root)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        print('Phase 4C-D4 rejected input or incomplete reviewed evidence', file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4C-D4 — VM Scenario Isolation')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    print('  Scenarios: ' + str(report.get('summary', {}).get('total', report.get('scenario_count'))))
    print('  JSON:      ' + str(args.out / ('d4_preflight.json' if args.preflight_only else 'd4_run.json')))
    return 0 if report['status'] in ('APPROVED_NOT_EXECUTED',
                                      'SCENARIO_ISOLATION_VERIFIED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
