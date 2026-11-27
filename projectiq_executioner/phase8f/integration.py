"""Local operator-only bridge; distinct from the public submission API.
Static reports and smoke evidence are separate and never converted to test credit.
"""
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require
from ..phase8c_final.dispatch import write
from ..phase8d.dispatch import fresh
from ..orchestration.contracts import validate_request, OrchestrationError
from ..orchestration import evaluate
from .demo import run_demo


def evaluate_trusted_demo(url, ref, out, *, submission_id='PHASE8F-DEMO', trust_demo_source=False, vm_config, **options):
    require(trust_demo_source is True,'EXPLICIT_DEMO_TRUST_REQUIRED')
    request={'submission_id':submission_id,'repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0,'run_readiness':'NO'}
    validate_request(request)  # Validate raw request without forwarding normalized resume keys.
    out=fresh(out)
    result={'schema_version':'8F-D.integrated.1','submission_id':submission_id,'mode':'OPERATOR_TRUSTED_LOCAL_DEMO','status':'INCOMPLETE','source':None,'automatic_untrusted_execution':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','functional_tests_executed':0,'vm_start_attempts':0,'vm_attempt_count_uncertain':False,'static':None,'smoke':None,'limitations':['Static findings and live smoke are separate evidence; no functional correctness or grade inferred.','Local operator trust only; not a qualified untrusted worker.']}
    try:
        result['vm_attempt_count_uncertain']=True
        demo=run_demo(url,ref,out/'demo',vm_config=vm_config,trust_demo_source=True,**options)
        dp=out/'demo'/'demo_result.json';receipt=checked_file(dp,8_000_000)
        require(load_object(dp.read_text('utf-8'))==demo and demo['result_sha256']==digest({k:v for k,v in demo.items() if k!='result_sha256'}),'DEMO_REPORT_MISMATCH')
        result.update(source=demo.get('source'),vm_start_attempts=demo['vm_start_attempts'],vm_attempt_count_uncertain=demo['vm_attempt_count_uncertain'])
        result['smoke']={'report':'demo/demo_result.json','sha256':receipt['sha256'],'status':demo['status'],'components':demo['components'],'original_config_unchanged':demo['original_config_unchanged'],'temporary_approval_cleanup':demo['temporary_approval_cleanup'],'error_code':demo.get('error_code')}
        if not result['source']:
            result['status']='SOURCE_UNAVAILABLE';return result
        # Pin static analysis to exactly the commit observed by demo, even if branch moved.
        request['ref']=result['source']['commit_sha']
        result['static']=run_static(request,result['source'],out)
        result['status']='DEMO_AND_STATIC_COMPLETE' if demo['status']=='DEMO_REVIEWED_SMOKE_COMPLETE' and result['static']['status'] in ('COMPLETED','COMPLETED_WITH_LIMITATIONS') else 'COMPLETED_WITH_LIMITATIONS'
    except (Exception,KeyboardInterrupt) as exc:
        result['error_code']=integration_error(exc)
    finally:
        result['result_sha256']=digest(result);write(out/'integrated_result.json',result)
    return result


def integration_error(exc):
    # Only fixed contract codes; never emit arbitrary exception text.
    known={'DEMO_REPORT_MISMATCH','STATIC_REPORT_IDENTITY_OR_MODE_MISMATCH','UNEXPECTED_RESUME_FIELDS','INVALID_REQUEST_FIELDS','INVALID_REF','INVALID_GITHUB_URL','OUTPUT_ALREADY_EXISTS'}
    return str(exc) if isinstance(exc,(ValueError,OrchestrationError)) and str(exc) in known else 'INTEGRATION_STAGE_FAILED_OR_INTERRUPTED'


def run_static(request,source,out):
    static=evaluate(request,output_dir=out/'static')
    sp=out/'static'/'executioner_result.json';sr=checked_file(sp,64_000_000)
    require(load_object(sp.read_text('utf-8'))==static and checked_file(sp,64_000_000)==sr and static.get('source')==source and static.get('effective_mode')=='STATIC' and static.get('vm_attempts_scheduled')==0,'STATIC_REPORT_IDENTITY_OR_MODE_MISMATCH')
    return {'report':'static/executioner_result.json','sha256':sr['sha256'],'status':static['status']}
