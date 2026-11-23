"""4D-D1/D2: reviewed MiniCollabApp Chrome login/session pilot in disposable QEMU.

The preflight checks exact approvals and creates no VM. The confirmed run
uses the existing Phase 2 VM security boundaries, keeps report fields bounded,
and never exports raw credentials, cookies, or DOM.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .browser_login_contracts import (
    LoginReviewError, compile_reviewed_login, validate_login_report)
from .oracle_stage import _load_strict_json

_CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_EXEC = 'PERFORMED_IN_DISPOSABLE_VM'
_NOT_ATTESTED = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def _write(path:Path, data:dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        json.dump(data,f,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n')


def _fresh(directory:Path) -> None:
    if directory.exists():
        raise LoginReviewError('4DD_REQUIRES_FRESH_OUTPUT_DIRECTORY')


def preflight(*, plan, candidate, catalog, config, output_dir:Path) -> dict:
    _fresh(output_dir)
    login=compile_reviewed_login(plan,candidate,catalog,config)['login']
    report={'schema_version':'0.1','stage':'browser_login_preflight',
            'source':login['source'],'adapter':login['adapter'],
            'flow':login['flow'],'catalog_sha256':login['catalog_sha256'],
            'status':'APPROVED_NOT_EXECUTED','execution':'NOT_PERFORMED',
            'security_attestation':_NOT_ATTESTED,
            'checks':[{'id':key} for key in login['checks']],
            'launch_authorization':'SEPARATE_PHASE2_VM_GATES_REQUIRED'}
    _write(output_dir/'d4d_preflight.json',report)
    return report


def run(*, plan, candidate, catalog, config, output_dir:Path,
        work_root:Path|None=None) -> dict:
    _fresh(output_dir)
    proof=compile_reviewed_login(plan,candidate,catalog,config)
    login=proof['login']
    report={'schema_version':'0.1','stage':'reviewed_browser_login',
            'source':login['source'],'adapter':login['adapter'],
            'flow':login['flow'],'catalog_sha256':login['catalog_sha256'],
            'status':'LOGIN_VM_NOT_VERIFIED','execution':'NOT_PERFORMED',
            'cleanup':'NOT_STARTED','security_attestation':_NOT_ATTESTED,
            'issue_code':'LOGIN_VM_NOT_VERIFIED','checks':None,
            'artifacts':{'review':'d4d_review.json','vm':'d4d_vm_execution.json'},
            'limitations':['Exact reviewed MiniCollab login/session pilot only.',
                           'Does not attest the sandbox for arbitrary untrusted submissions.']}
    _write(output_dir/'d4d_review.json',{
        'schema_version':'0.1','stage':'browser_login_review',
        'source':login['source'],'adapter':login['adapter'],
        'flow':login['flow'],'catalog_sha256':login['catalog_sha256'],
        'status':'EXACT_OPERATOR_APPROVAL_MATCHED','execution':'NOT_PERFORMED'})
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm=execute_vm_candidate(dict(candidate),dict(config),
            output_dir/'d4d_vm_execution.json', reviewed_fixture=True,
            work_root=work_root,login_bundle=proof)
    except (OSError,RuntimeError,ValueError,TypeError,KeyError):
        report.update(status='LOGIN_VM_NOT_VERIFIED',
                      execution='NOT_VERIFIED',cleanup='NOT_VERIFIED',
                      issue_code='VM_WORKER_EXCEPTION')
    else:
        report['execution']=vm.get('execution','NOT_VERIFIED')
        report['cleanup']=vm.get('cleanup','NOT_VERIFIED')
        if vm.get('cleanup') != _CLEAN:
            report.update(status='CLEANUP_FAILED',issue_code='LOGIN_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status') != 'STARTUP_VERIFIED'
              or vm.get('execution') != _EXEC
              or vm.get('managed_runtime') != 'SIGNED_LEASED_WORKSPACE_REMOVED'
              or vm.get('backend') != 'QEMU_DISPOSABLE_QCOW2_VM'
              or vm.get('security_attestation') != _NOT_ATTESTED
              or vm.get('source') != candidate['source']
              or vm.get('adapter') != 'php_mysql_v1'
              or vm.get('phase4dd_status') != 'VALIDATED'):
            report.update(status='LOGIN_VM_NOT_VERIFIED',
                          issue_code='LOGIN_GUEST_OR_VM_EVIDENCE_NOT_VERIFIED')
        else:
            try:
                validate_login_report(login,vm.get('phase4dd_login_report'))
            except (LoginReviewError,ValueError,TypeError,KeyError):
                report.update(status='LOGIN_VM_NOT_VERIFIED',
                              issue_code='LOGIN_GUEST_EVIDENCE_INVALID')
            else:
                guest=vm['phase4dd_login_report']
                report['checks']=guest['checks']
                report['issue_code']=guest['issue_code']
                report['status']={'PASS':'LOGIN_PASSED','FAIL':'LOGIN_FAILED',
                                  'INCONCLUSIVE':'LOGIN_INCONCLUSIVE'}[guest['status']]
    _write(output_dir/'d4d_run.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='Reviewed real-browser login/session pilot in offline QEMU')
    for name in ('plan','candidate','catalog','vm-config','out'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--work-root',type=Path)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight-only',action='store_true')
    mode.add_argument('--confirm-reviewed-browser-login',action='store_true')
    args=p.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        inputs=dict(plan=_load_strict_json(args.plan,2_000_000),
                    candidate=_load_strict_json(args.candidate,200_000),
                    catalog=_load_strict_json(args.catalog,32768),
                    config=load_config(args.vm_config),output_dir=args.out)
        result=(preflight(**inputs) if args.preflight_only
                else run(**inputs,work_root=args.work_root))
    except (OSError,RuntimeError,ValueError,TypeError,KeyError):
        print('Phase 4D-D rejected review inputs/VM evidence',file=sys.stderr)
        return 2
    print('ProjectIQ Phase 4D-D — Browser Login/Session')
    print('  Status:    '+result['status'])
    print('  Execution: '+result['execution'])
    print('  JSON:      '+str(args.out/('d4d_preflight.json' if args.preflight_only else 'd4d_run.json')))
    return 0 if result['status'] in ('APPROVED_NOT_EXECUTED','LOGIN_PASSED') else 2


if __name__=='__main__':raise SystemExit(main())
