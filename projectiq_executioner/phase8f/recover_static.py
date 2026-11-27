"""Static-only recovery from an operator-selected prior successful demo run.
Saved receipts establish consistency, never authorization. No VM dispatch.
"""
import argparse
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file
from ..phase8c2.broker import require
from ..phase8c_final.dispatch import write
from ..phase8d.dispatch import fresh
from ..phase8e.audit import read, audit_runtime, ID
from ..orchestration.contracts import validate_request, validate_source
from .integration import run_static, integration_error


def recover(prior,out):
    prior=Path(prior).absolute(); out=Path(out).absolute()
    require(not out.is_relative_to(prior),'RECOVERY_OUTPUT_INSIDE_PRIOR')
    ip=prior/'integrated_result.json'; old=read(ip,'result_sha256'); ir=checked_file(ip,4_000_000)
    require(old.get('schema_version')=='8F-D.integrated.1' and old.get('mode')=='OPERATOR_TRUSTED_LOCAL_DEMO' and old.get('static') is None,'RECOVERY_PRIOR_NOT_ELIGIBLE')
    source=validate_source(old['source']); dp=prior/'demo'/'demo_result.json';dr=checked_file(dp,4_000_000);demo=read(dp,'result_sha256');smoke=old['smoke']
    require(smoke.get('report')=='demo/demo_result.json' and smoke.get('sha256')==dr['sha256'] and smoke.get('components')==demo.get('components') and smoke.get('status')==demo.get('status')=='DEMO_REVIEWED_SMOKE_COMPLETE' and demo.get('source')==source and demo.get('operator_trust_asserted') is True and demo.get('automatic_untrusted_execution') is False and demo.get('original_config_unchanged') is True and demo.get('temporary_approval_cleanup')=='REMOVED' and demo.get('vm_attempt_count_uncertain') is False,'RECOVERY_DEMO_UNVERIFIED')
    rows=demo['components'];require(isinstance(rows,list) and 1<=len(rows)<=4 and demo['vm_start_attempts']==len(rows)==old['vm_start_attempts'],'RECOVERY_VM_COUNT_MISMATCH')
    evidence=[]
    for row in rows:
        cid=row['id'];require(isinstance(cid,str) and ID.fullmatch(cid) and row.get('dispatch_report')==cid+'/dispatch_result.json','RECOVERY_COMPONENT_INVALID')
        dest=prior/'demo'/cid
        d=read(dest/'dispatch_result.json','result_sha256');require(d['result_sha256']==row['dispatch_sha256'],'RECOVERY_DISPATCH_MISMATCH')
        ev=audit_runtime(dest,source,row['root'],row['adapter'],require_artifacts=True);require(ev==row['evidence'],'RECOVERY_EVIDENCE_MISMATCH');evidence.append(ev)
    require(len({r['id'] for r in rows})==len(rows),'RECOVERY_COMPONENT_DUPLICATE')
    request={'submission_id':old['submission_id'],'repository_url':'https://github.com/'+source['repository'],'ref':source['commit_sha'],'mode':'STATIC','run_readiness':'NO','max_vm_launches':0};validate_request(request)
    out=fresh(out);result={'schema_version':'8F-D.static-recovery.1','status':'INCOMPLETE','source':source,'mode':'OPERATOR_TRUSTED_LOCAL_DEMO','automatic_untrusted_execution':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','vm_start_attempts':0,'prior_vm_start_attempts':len(rows),'functional_tests_executed':0,'prior_integrated_report':str(ip),'prior_integrated_sha256':ir['sha256'],'prior_demo_sha256':dr['sha256'],'smoke_evidence':evidence,'static':None,'limitations':['Original runtime evidence reused; no new VM launched. Saved-report consistency is not security attestation.']}
    try:
        result['static']=run_static(request,source,out)
        require(checked_file(ip,4_000_000)==ir and checked_file(dp,4_000_000)==dr,'RECOVERY_PRIOR_CHANGED')
        result['status']='DEMO_AND_STATIC_COMPLETE' if result['static']['status'] in ('COMPLETED','COMPLETED_WITH_LIMITATIONS') else 'COMPLETED_WITH_LIMITATIONS'
    except (Exception,KeyboardInterrupt) as exc:result['error_code']=integration_error(exc)
    result['result_sha256']=digest(result);write(out/'recovery_result.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--from-run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    try:r=recover(a.from_run,a.out)
    except (Exception,KeyboardInterrupt) as exc:
        print('Recovery refused:',str(exc) if type(exc) is ValueError and str(exc).startswith('RECOVERY_') else type(exc).__name__);return 2
    print('Static recovery:',r['status']);print('New VM attempts: 0');print('Report:',a.out/'recovery_result.json');return 0 if r['status']=='DEMO_AND_STATIC_COMPLETE' else 2

if __name__=='__main__':raise SystemExit(main())
