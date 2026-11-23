"""Controlled, inert Phase 7 test corpus.

Labels are specified from the scenario design, NOT copied from Phase 6 outputs.
These are SYNTHETIC controls, not independent real-world reviewer findings.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from ..phase6.pipeline import build
from ..phase6.report import validate_report
from .contracts import DEFAULT_POLICY, BenchmarkError, require

NOT_ATTESTED='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
EXEC='PERFORMED_IN_DISPOSABLE_VM'
TEST='TC-AAAAAAAAAAAAAAAA'


def _source(repo,sha):
    return {'kind':'github','repository':repo,'commit_sha':sha}


def _vm(src):
    return {'source':src,'adapter':'php_mysql_v1','status':'STARTUP_VERIFIED',
            'execution':EXEC,'cleanup':CLEAN,'backend':'QEMU_DISPOSABLE_QCOW2_VM',
            'security_attestation':NOT_ATTESTED,'phase4c_status':'VALIDATED'}


def _run(src,outcome,http):
    return {'schema_version':'0.1','phase':4,'stage':'reviewed_vm_api_testing',
            'source':src,'adapter':'php_mysql_v1','status':{
                 'PASS':'TESTS_PASSED','FAIL':'TESTS_FAILED',
                 'INCONCLUSIVE':'TESTS_INCONCLUSIVE'}[outcome],
            'execution':EXEC,'cleanup':CLEAN,'security_attestation':NOT_ATTESTED,
            'results':[{'test_id':TEST,'result':outcome,'http_status':http}],
            'summary':{'total':1,'passed':int(outcome=='PASS'),'failed':int(outcome=='FAIL'),
                       'inconclusive':int(outcome=='INCONCLUSIVE')},
            'catalog_sha256':'b'*64,'issue_code':None}


def _truth(status,codes,events=None,comparisons=None):
    return {'reviewed':True,'reviewer_id':'CONTROLLED_SCENARIO',
            'reviewed_at':'2026-11-24','basis':'CONTROLLED_INJECTION',
            'expected_report_status':status,'expected_codes':codes,
            'expected_events':events or [],'expected_comparisons':comparisons or [],
            'maintainability_rating':None}


def _event(outcome='FAIL',http=None,run='RUN001',known=None):
    return {'run':run,'test_id':TEST,'outcome':outcome,'known_cause':known}


def make(out:Path):
    require(not out.exists() and not out.is_symlink() and not any(x.is_symlink() for x in out.parents),
            'OUTPUT_ALREADY_EXISTS_OR_UNSAFE')
    artifacts=[]
    for repo,sha,prefix in (('example/php-clean-controlled','a'*40,'PHP'),
                            ('example/node-clean-controlled','b'*40,'NODE')):
        src=_source(repo,sha);vm=_vm(src)
        scenarios=[
            ('CLEAN_200','CLEAN',build(run_inputs=[(_run(src,'PASS',200),'RUN001',vm)]),
             _truth('DIAGNOSED',[],[_event('PASS')])),
            ('DEFECT_500','APPLICATION_DEFECT',build(run_inputs=[(_run(src,'FAIL',500),'RUN001',vm)]),
             _truth('DIAGNOSED',['TEST_ASSERTION_MISMATCH'],[_event(known='APPLICATION_RUNTIME_ERROR')])),
            ('DEFECT_404','APPLICATION_DEFECT',build(run_inputs=[(_run(src,'FAIL',404),'RUN001',vm)]),
             _truth('DIAGNOSED',['TEST_ASSERTION_MISMATCH'],[_event(known='APPLICATION_ROUTE_MISSING')])),
            ('INCONCLUSIVE','INCONCLUSIVE',build(run_inputs=[(_run(src,'INCONCLUSIVE',503),'RUN001',vm)]),
             _truth('DIAGNOSED',['TEST_INCONCLUSIVE'],[_event('INCONCLUSIVE')])),
            ('NO_VM_DEFECT','APPLICATION_DEFECT',build(run_inputs=[(_run(src,'FAIL',502),'RUN001',None)]),
             _truth('PARTIAL',['TEST_ASSERTION_MISMATCH'],[_event(known='GATEWAY_OR_UPSTREAM_UNAVAILABLE')])),
        ]
        for label,cohort,report,truth in scenarios:
            artifacts.append((prefix+'_'+label,cohort,src,report,truth))
        blocked_vm=deepcopy(vm);blocked_vm['status']='LAUNCH_BLOCKED';blocked_vm['execution']='NOT_PERFORMED';blocked_vm['cleanup']='NOT_STARTED'
        blocked=build(vm_standalone=(blocked_vm,'VM001'))
        artifacts.append((prefix+'_VM_BLOCKED','INFRASTRUCTURE',src,blocked,
                          _truth('PARTIAL',['VM_LAUNCH_BLOCKED'])))
        insecure_vm=deepcopy(vm);insecure_vm['cleanup']='CLEANUP_UNVERIFIED'
        insecure=build(vm_standalone=(insecure_vm,'VM001'))
        artifacts.append((prefix+'_VM_CLEANUP','SECURITY',src,insecure,
                          _truth('SECURITY_HOLD',['VM_CLEANUP_UNVERIFIED','VM_EXECUTION_UNVERIFIED'])))
        history=build(run_inputs=[(_run(src,'PASS',200),'RUN001',vm),
                                  (_run(src,'FAIL',500),'RUN002',vm)])
        artifacts.append((prefix+'_HISTORY','APPLICATION_DEFECT',src,history,
                          _truth('DIAGNOSED',['TEST_ASSERTION_MISMATCH','RERUN_OUTCOME_INCONSISTENT'],
                                 [_event('PASS',run='RUN001'),_event(known='APPLICATION_RUNTIME_ERROR',run='RUN002')],
                                 [{'test_id':TEST,'status':'OUTCOME_INCONSISTENT_REVIEW_REQUIRED',
                                   'environment_equivalent':False}])))
    # Two deliberately unsupported non-execution report stages.
    for prefix,src in (('PHP',_source('example/php-clean-controlled','a'*40)),
                       ('NODE',_source('example/node-clean-controlled','b'*40))):
        unsupported={'schema_version':'0.1','phase':4,'stage':'reviewed_unrecognized_plugin',
                     'status':'FUTURE_STAGE_NOT_EXECUTED','source':src,'adapter':'php_mysql_v1',
                     'execution':'NOT_PERFORMED','cleanup':'NOT_STARTED',
                     'security_attestation':NOT_ATTESTED,'results':[]}
        report=build(run_inputs=[(unsupported,'RUN001',None)])
        artifacts.append((prefix+'_UNSUPPORTED','UNSUPPORTED',src,report,
                          _truth('PARTIAL',['UNSUPPORTED_STAGE'])))
    cases=[]
    out.mkdir(parents=True,exist_ok=False)
    for case_id,cohort,src,report,truth in artifacts:
        validate_report(report)
        payload=json.dumps(report,sort_keys=True,indent=2,ensure_ascii=False).encode('utf8')+b'\n'
        p=out/(case_id+'.json')
        with p.open('xb') as f:f.write(payload)
        cases.append({'id':case_id,'cohort':cohort,'source':src,
                      'phase6':{'path':p.name,'sha256':hashlib.sha256(payload).hexdigest()},
                      'ground_truth':truth})
    manifest={'schema_version':'0.1','stage':'phase7_benchmark_dataset',
              'benchmark_id':'PHASE7_SYNTHETIC_CONTROLS',
              'cases':cases,'policy':dict(DEFAULT_POLICY)}
    with (out/'benchmark_manifest.json').open('x',encoding='utf-8') as f:
        json.dump(manifest,f,indent=2,sort_keys=True)
        f.write('\n')
    return len(cases)


def main(argv=None):
    p=argparse.ArgumentParser(description='Generate SYNTHETIC Phase 7 benchmark controls; no VM execution')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    try:count=make(args.out)
    except (BenchmarkError,OSError,ValueError,TypeError,KeyError):
        print('Controlled corpus creation stopped safely.')
        return 2
    print(f'SYNTHETIC controls: {count} generated. No VM/student code executed.')
    print('Calibration remains LIMITED until independently reviewed Phase 5 ratings exist.')
    return 0

if __name__=='__main__':raise SystemExit(main())
