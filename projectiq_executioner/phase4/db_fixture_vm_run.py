"""4C-D1: separate approval and controlled MySQL seed in disposable VM.

No user SQL, external DB, host-side credentials, session secrets or test
scripts. Read-only preflight is the default; actual VM launch needs a flag.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .db_fixture_contracts import (FixtureReviewError, compile_reviewed_db_fixture,
                                   fixture_counts, validate_fixture_report)
from .oracle_stage import _load_strict_json
from .oracle_contracts import digest

CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
_FILES = ('db_fixture_preflight.json', 'db_fixture_review.json',
          'db_fixture_vm_execution.json', 'db_fixture_run.json')


def _persist(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
        f.write('\n')


def _prepare_output(path: Path) -> None:
    if any((path / name).exists() for name in _FILES):
        raise FixtureReviewError('REFUSING_TO_OVERWRITE_FIXTURE_EVIDENCE')


def preflight_db_fixture(plan: Mapping[str, Any], manifest: Mapping[str, Any],
                         candidate: Mapping[str, Any], config: Mapping[str, Any],
                         output_dir: Path) -> dict:
    _prepare_output(output_dir)
    bundle = compile_reviewed_db_fixture(plan, manifest, candidate, config)
    counts = fixture_counts(bundle)
    evidence = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_database_fixture_preflight',
        'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'fixture_catalog_sha256': digest(manifest),
        'table_count': counts['table_count'], 'row_count': counts['row_count'],
        'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED',
        'security_attestation': ATTESTATION,
    }
    _persist(output_dir / 'db_fixture_preflight.json', evidence)
    return evidence


def run_reviewed_db_fixture(*, plan: Mapping[str, Any], manifest: Mapping[str, Any],
                            candidate: Mapping[str, Any], config: Mapping[str, Any],
                            output_dir: Path, work_root: Path | None = None) -> dict:
    _prepare_output(output_dir)
    bundle = compile_reviewed_db_fixture(plan, manifest, candidate, config)
    counts = fixture_counts(bundle)
    _persist(output_dir / 'db_fixture_review.json', {
        'schema_version': '0.1', 'phase': 4, 'stage': 'database_fixture_approval',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'fixture_catalog_sha256': digest(manifest),
        'phase3_plan_sha256': digest(plan), 'phase2_candidate_sha256': digest(candidate),
        'approved_tables': counts['table_count'], 'approved_rows': counts['row_count'],
        'approval': 'MATCHED_EXACT_OPERATOR_FIXTURE_DIGEST',
        'execution': 'NOT_PERFORMED',
    })
    report = {
        'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_database_fixture_seeding',
        'source': dict(plan['source']), 'adapter': candidate['adapter'],
        'fixture_catalog_sha256': digest(manifest),
        'status': 'VM_EXECUTION_NOT_VERIFIED', 'execution': 'NOT_VERIFIED',
        'cleanup': 'NOT_VERIFIED', 'security_attestation': ATTESTATION,
        'table_count': 0, 'seeded_rows': 0, 'issue_code': 'VM_WORKER_EXCEPTION',
        'artifacts': {'review': 'db_fixture_review.json', 'vm': 'db_fixture_vm_execution.json'},
        'limitations': ['Only the approved PHP/MySQL adapter is supported.',
                        'Inserts occur only in a disposable VM; no persisted host database.',
                        'No authenticated workflows, session fixtures or row-level postconditions yet.',
                        'Not a security attestation for arbitrary student submissions.'],
    }
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  output_dir / 'db_fixture_vm_execution.json',
                                  reviewed_fixture=True, work_root=work_root,
                                  db_fixture_bundle=bundle)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
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
              or vm.get('phase4d1_status') != 'VALIDATED'):
            report.update(status='VM_EXECUTION_NOT_VERIFIED',
                          issue_code='VM_OR_GUEST_FIXTURE_NOT_VERIFIED')
        else:
            try:
                validate_fixture_report(bundle, vm.get('phase4d1_fixture_report'))
            except (FixtureReviewError, TypeError, ValueError):
                report.update(status='GUEST_RESULTS_NOT_VERIFIED',
                              issue_code='GUEST_FIXTURE_REPORT_NOT_VERIFIED')
            else:
                report.update(status='FIXTURES_SEEDED',
                              issue_code=None, table_count=counts['table_count'],
                              seeded_rows=counts['row_count'])
    _persist(output_dir / 'db_fixture_run.json', report)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Phase 4C-D1: approved database fixture seeding')
    for name in ('plan', 'fixtures', 'candidate', 'vm-config', 'out'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--work-root', type=Path)
    options = p.add_mutually_exclusive_group(required=True)
    options.add_argument('--preflight-only', action='store_true')
    options.add_argument('--confirm-reviewed-db-seeding', action='store_true')
    args = p.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        plan = _load_strict_json(args.plan, 2_000_000)
        manifest = _load_strict_json(args.fixtures, 64_000)
        candidate = _load_strict_json(args.candidate, 200_000)
        config = load_config(args.vm_config)
        if args.preflight_only:
            report = preflight_db_fixture(plan, manifest, candidate, config, args.out)
        else:
            report = run_reviewed_db_fixture(plan=plan, manifest=manifest, candidate=candidate,
                                             config=config, output_dir=args.out,
                                             work_root=args.work_root)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print('Phase 4C-D1 rejected fixture input or incomplete evidence', file=sys.stderr)
        return 2
    print('Phase 4C-D1 — Controlled database fixtures')
    print('  Status:    ' + report['status'])
    print('  Execution: ' + report['execution'])
    if not args.preflight_only:
        print('  Cleanup:   ' + report['cleanup'])
    print('  Tables:    ' + str(report['table_count']))
    print('  Rows:      ' + str(report.get('row_count', report.get('seeded_rows', 0))))
    print('  JSON:      ' + str(args.out / ('db_fixture_preflight.json' if args.preflight_only else 'db_fixture_run.json')))
    return 0 if report['status'] in ('APPROVED_NOT_EXECUTED', 'FIXTURES_SEEDED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
