"""4D-C: reviewed DOM rendering with a real in-VM Chrome/Chromium."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from .browser_ui_contracts import compile_reviewed_browser, BrowserReviewError
from .guest_browser_ui import validate_browser_ui_report, BrowserUiError
from .oracle_stage import _load_strict_json

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_ATTEST = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def _save(path: Path, body: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(body, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')


def _fresh(out):
    if out.exists():
        raise BrowserReviewError('BROWSER_REQUIRES_FRESH_OUTPUT_DIRECTORY')


def preflight(*, plan, catalog, candidate, config, output_dir: Path):
    _fresh(output_dir)
    reviewed = compile_reviewed_browser(plan, catalog, candidate, config)
    b = reviewed['browser']
    report = {'schema_version': '0.1', 'phase': 4, 'stage': 'browser_ui_preflight',
              'source': b['source'], 'adapter': b['adapter'],
              'catalog_sha256': b['catalog_sha256'],
              'status': 'APPROVED_NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
              'browser_binary': b['browser_binary'],
              'browser_installed_in_guest': 'NOT_YET_VERIFIED',
              'security_attestation': _ATTEST,
              'checks': [{'tag': x['tag']} for x in b['checks']],
              'launch_authorization': 'SEPARATE_PHASE2_VM_GATES_REQUIRED'}
    _save(output_dir/'d4c_preflight.json', report)
    return report


def run(*, plan, catalog, candidate, config, output_dir: Path,
        work_root: Path | None = None):
    _fresh(output_dir)
    compiled = compile_reviewed_browser(plan, catalog, candidate, config)
    b = compiled['browser']
    report = {'schema_version': '0.1', 'phase': 4, 'stage': 'reviewed_browser_ui',
              'source': b['source'], 'adapter': b['adapter'],
              'catalog_sha256': b['catalog_sha256'],
              'status': 'BROWSER_NOT_VERIFIED', 'execution': 'NOT_PERFORMED',
              'cleanup': 'NOT_STARTED', 'security_attestation': _ATTEST,
              'issue_code': 'BROWSER_VM_NOT_VERIFIED', 'checks': None,
              'limitations': [
                  'Browser pilot verifies reviewed rendered DOM text. FullStack React async status is checked separately; PHP login checks do not prove authentication or test clicks/forms.',
                  'Browser must be installed in the separately sealed and SHA-pinned Ubuntu base.',
                  'Not attested for untrusted student execution; no app-wide UI coverage.'
              ], 'artifacts': {'review': 'd4c_review.json',
                              'vm': 'd4c_vm_execution.json'}}
    _save(output_dir/'d4c_review.json', {
        'schema_version': '0.1', 'stage': 'browser_ui_review',
        'source': b['source'], 'adapter': b['adapter'],
        'catalog_sha256': b['catalog_sha256'],
        'status': 'EXACT_OPERATOR_APPROVAL_MATCHED', 'execution': 'NOT_PERFORMED'})
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm = execute_vm_candidate(dict(candidate), dict(config),
                                  output_dir/'d4c_vm_execution.json',
                                  reviewed_fixture=True, work_root=work_root,
                                  browser_bundle=compiled)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        report.update(execution='NOT_VERIFIED', cleanup='NOT_VERIFIED',
                      issue_code='VM_WORKER_EXCEPTION')
    else:
        report['execution'] = vm.get('execution', 'NOT_VERIFIED')
        report['cleanup'] = vm.get('cleanup', 'NOT_VERIFIED')
        if vm.get('cleanup') != _CLEAN:
            report.update(status='CLEANUP_FAILED', issue_code='BROWSER_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED'
                or vm.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM'
                or vm.get('managed_runtime') != 'SIGNED_LEASED_WORKSPACE_REMOVED'
                or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
                or vm.get('security_attestation') != _ATTEST
                or vm.get('source') != candidate['source']
                or vm.get('adapter') != candidate['adapter']
                or vm.get('phase4dc_status') != 'VALIDATED'):
            report.update(status='BROWSER_VM_NOT_VERIFIED',
                          issue_code='BROWSER_GUEST_OR_VM_EVIDENCE_NOT_VERIFIED')
        else:
            try:
                validate_browser_ui_report(b, vm.get('phase4dc_browser_report'))
            except (BrowserUiError, ValueError, KeyError, TypeError):
                report.update(status='BROWSER_VM_NOT_VERIFIED',
                              issue_code='BROWSER_GUEST_EVIDENCE_INVALID')
            else:
                guest = vm['phase4dc_browser_report']
                report['checks'] = guest['checks']
                report['issue_code'] = guest['issue_code']
                report['status'] = {
                    'PASS': 'BROWSER_PASSED', 'FAIL': 'BROWSER_FAILED',
                    'INCONCLUSIVE': 'BROWSER_INCONCLUSIVE'}[guest['status']]
    _save(output_dir/'d4c_run.json', report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Reviewed headless browser DOM assertion inside QEMU')
    for name in ('plan','catalog','candidate','vm-config','out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--work-root', type=Path)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight-only', action='store_true')
    mode.add_argument('--confirm-reviewed-browser', action='store_true')
    args=p.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        kwargs=dict(plan=_load_strict_json(args.plan,2_000_000),
                    catalog=_load_strict_json(args.catalog,32_768),
                    candidate=_load_strict_json(args.candidate,200_000),
                    config=load_config(args.vm_config), output_dir=args.out)
        result=preflight(**kwargs) if args.preflight_only else run(**kwargs,work_root=args.work_root)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):
        print('Phase 4D-C rejected input, review approval or evidence',file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4D-C — Real Browser UI')
    print('  Status:    '+result['status'])
    print('  Execution: '+result['execution'])
    print('  JSON:      '+str(args.out/('d4c_preflight.json' if args.preflight_only else 'd4c_run.json')))
    return 0 if result['status'] in ('APPROVED_NOT_EXECUTED','BROWSER_PASSED') else 2


if __name__=='__main__':
    raise SystemExit(main())
