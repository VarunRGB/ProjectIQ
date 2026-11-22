"""4D-B reviewed authenticated CRUD in one disposable QEMU Linux VM.

Authentication is existing approved D2 flow; CRUD is independent exact-digest
approval. Guest-collected read-only DB evidence is required, not just HTTP 200.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .crud_workflow_contracts import CrudReviewError, compile_reviewed_crud
from .guest_crud import CrudError, validate_crud_report
from .auth_report_contracts import AuthReportError, validate_auth_report
from .oracle_stage import _load_strict_json

_CLEAN='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
_ATTEST='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


def _fresh(out:Path):
    if out.exists():raise CrudReviewError('CRUD_REQUIRES_NEW_OUTPUT_DIRECTORY')


def _write(path:Path,record:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        json.dump(record,f,indent=2,sort_keys=True,allow_nan=False)
        f.write('\n')


def preflight(*,plan,auth_catalog,oracle_catalog,crud_catalog,candidate,config,output_dir:Path):
    _fresh(output_dir)
    compiled=compile_reviewed_crud(plan,auth_catalog,oracle_catalog,crud_catalog,candidate,config)
    b=compiled['crud']
    result={'schema_version':'0.1','phase':4,'stage':'reviewed_crud_preflight',
            'source':b['source'],'adapter':b['adapter'],
            'workflow_catalog_sha256':b['workflow_catalog_sha256'],
            'auth_catalog_sha256':b['auth_catalog_sha256'],
            'oracle_catalog_sha256':b['oracle_catalog_sha256'],
            'status':'APPROVED_NOT_EXECUTED','execution':'NOT_PERFORMED',
            'security_attestation':_ATTEST,
            'steps':[{'role':s['role'],'method':s['method'],'path':s['path']} for s in b['steps']],
            'launch_authorization':'SEPARATE_PHASE2_VM_GATES_REQUIRED'}
    _write(output_dir/'d4b_preflight.json',result)
    return result


def run(*,plan,auth_catalog,oracle_catalog,crud_catalog,candidate,config,
        output_dir:Path,work_root:Path|None=None):
    _fresh(output_dir)
    compiled=compile_reviewed_crud(plan,auth_catalog,oracle_catalog,crud_catalog,candidate,config)
    b=compiled['crud']
    report={'schema_version':'0.1','phase':4,'stage':'reviewed_authenticated_crud',
            'source':b['source'],'adapter':b['adapter'],
            'workflow_catalog_sha256':b['workflow_catalog_sha256'],
            'auth_catalog_sha256':b['auth_catalog_sha256'],
            'oracle_catalog_sha256':b['oracle_catalog_sha256'],
            'status':'CRUD_NOT_VERIFIED','execution':'NOT_PERFORMED','cleanup':'NOT_STARTED',
            'security_attestation':_ATTEST,'issue_code':'CRUD_VM_NOT_VERIFIED',
            'auth_summary':None,'observations':None,
            'limitations':['One reviewed PHP/MySQL task CRUD pilot only.',
                           'Not arbitrary untrusted student execution or browser coverage.',
                           'DB witness observes guest-scoped read-only MySQL, not independent VM attestation.'],
            'artifacts':{'review':'d4b_review.json','vm':'d4b_vm_execution.json'}}
    _write(output_dir/'d4b_review.json',{
        'schema_version':'0.1','phase':4,'stage':'reviewed_crud_approval',
        'source':b['source'],'adapter':b['adapter'],
        'workflow_catalog_sha256':b['workflow_catalog_sha256'],
        'status':'EXACT_OPERATOR_APPROVAL_MATCHED','execution':'NOT_PERFORMED'})
    from ..phase2.vm_worker.orchestrator import execute_vm_candidate
    try:
        vm=execute_vm_candidate(dict(candidate),dict(config),output_dir/'d4b_vm_execution.json',
                                reviewed_fixture=True,work_root=work_root,crud_bundle=compiled)
    except (OSError,ValueError,TypeError,RuntimeError,KeyError):
        report.update(status='CRUD_VM_NOT_VERIFIED',execution='NOT_VERIFIED',
                      cleanup='NOT_VERIFIED',issue_code='CRUD_VM_EXCEPTION')
    else:
        report['execution']=vm.get('execution','NOT_VERIFIED')
        report['cleanup']=vm.get('cleanup','NOT_VERIFIED')
        if report['cleanup']!=_CLEAN:
            report.update(status='CLEANUP_FAILED',issue_code='CRUD_CLEANUP_NOT_VERIFIED')
        elif (vm.get('status')!='STARTUP_VERIFIED'
                or vm.get('execution')!='PERFORMED_IN_DISPOSABLE_VM'
                or vm.get('backend')!='QEMU_DISPOSABLE_QCOW2_VM'
                or vm.get('managed_runtime')!='SIGNED_LEASED_WORKSPACE_REMOVED'
                or vm.get('security_attestation')!=_ATTEST
                or vm.get('source')!=candidate['source']
                or vm.get('adapter')!=candidate['adapter']
                or vm.get('phase4db_status')!='VALIDATED'):
            report.update(status='CRUD_VM_NOT_VERIFIED',issue_code='CRUD_VM_OR_GUEST_INVALID')
        else:
            try:
                validate_auth_report(compiled['auth'],vm.get('phase4db_auth_report'))
                validate_crud_report(b,vm.get('phase4db_crud_report'))
            except (AuthReportError,CrudError,KeyError,ValueError,TypeError):
                report.update(status='CRUD_VM_NOT_VERIFIED',issue_code='CRUD_REPORT_INVALID')
            else:
                a=vm['phase4db_auth_report'];c=vm['phase4db_crud_report']
                report['auth_summary']=a['summary'];report['observations']=c['observations']
                if c['status']=='PASS' and a['summary']=={'total':4,'passed':4,'failed':0,'inconclusive':0} and a['session_cookie_observed'] is True:
                    report.update(status='CRUD_PASSED',issue_code=None)
                elif c['status']=='FAIL':
                    report.update(status='CRUD_FAILED',issue_code=c['issue_code'])
                else:
                    report.update(status='CRUD_INCONCLUSIVE',issue_code=c['issue_code'])
    _write(output_dir/'d4b_run.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='4D-B reviewed authenticated CRUD VM pilot')
    for key in ('plan','auth','oracles','crud','candidate','vm-config','out'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--work-root',type=Path)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight-only',action='store_true')
    modes.add_argument('--confirm-reviewed-crud',action='store_true')
    a=p.parse_args(argv)
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        params={'plan':_load_strict_json(a.plan,2_000_000),
                'auth_catalog':_load_strict_json(a.auth,64_000),
                'oracle_catalog':_load_strict_json(a.oracles,256_000),
                'crud_catalog':_load_strict_json(a.crud,32_768),
                'candidate':_load_strict_json(a.candidate,200_000),
                'config':load_config(a.vm_config),'output_dir':a.out}
        result=preflight(**params) if a.preflight_only else run(**params,work_root=a.work_root)
    except (OSError,ValueError,TypeError,RuntimeError,KeyError):
        print('D4B rejected: source/approval/contract/output invalid',file=sys.stderr)
        return 2
    print('ProjectIQ 4D-B — '+result['status'])
    print('  Execution: '+result['execution'])
    if not a.preflight_only:print('  Cleanup: '+result['cleanup'])
    print('  JSON: '+str(a.out/('d4b_preflight.json' if a.preflight_only else 'd4b_run.json')))
    return 0 if result['status'] in ('APPROVED_NOT_EXECUTED','CRUD_PASSED') else 2

if __name__=='__main__':raise SystemExit(main())
