"""9B-B local trusted functional run. Single component, single reviewed VM.
Saved plans are never authority. Recompile against the actual dispatch checkout.
"""
import re
from pathlib import Path
from ..phase8a.planner import digest,canonical
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c_final.dispatch import write
from ..phase8d import dispatch as runtime
from ..phase8f.demo import temporary_approval
from ..phase8f.profile import load_profile,NAME
from ..phase8e.campaign import assert_no_qemu
from ..phase2.vm_worker.orchestrator import load_config
from ..orchestration.contracts import validate_request,validate_source,source_matches_url
from ..phase9a import planner
from .compiler import read_object,verify_checksum,_compile_rederived,require,SHA
from .http_runner import validate_suite,validate_results


def suite_from(compiled,component):
    rows=[g for g in compiled['components'] if g['component_id']==component['id']]
    require(len(rows)==1,'FUNCTIONAL_COMPONENT_MISMATCH');g=rows[0]
    require(g['root']==component['root'] and g['adapter']==component['adapter'] and g['guest_service_port']==component['service_port'],'FUNCTIONAL_COMPONENT_MISMATCH')
    return validate_suite({'compiled_sha256':compiled['compiled_sha256'],'tests':[{k:t[k] for k in ('case_id','method','path','assertions')} for t in g['tests']]})


def rederive_suite(saved,source,root,plan,component,out,*,adaptive=False):
    verify_checksum(saved,'compiled_sha256')
    require(saved.get('source')==source and saved.get('runtime_plan_sha256')==plan['plan_sha256'],'FUNCTIONAL_PLAN_REDERIVATION_MISMATCH')
    dest=runtime.fresh(out/'functional_rederived')
    manifest=planner.adapters.inspect_source(root,source,dest/'phase1')
    phase3=planner.generate_test_plan(manifest,None,source_root=root,source_verified=True)
    proposal=planner.build_plan(root,source,manifest,plan,phase3)
    compiled=_compile_rederived(proposal,plan)
    require(canonical(saved)==canonical(compiled),'FUNCTIONAL_PLAN_REDERIVATION_MISMATCH')
    suite=suite_from(compiled,component)
    if adaptive:
        from .adaptive_compiler import compile_workflow
        suite['workflow']=compile_workflow(root,proposal,component);validate_suite(suite)
        write(dest/'adaptive_contract.json',suite['workflow'])
    write(dest/'functional_plan.json',proposal);write(dest/'phase3_test_plan.json',phase3);write(dest/'compiled_plan.json',compiled)
    return suite


def collect_evidence(dest,dispatch,compiled,*,adaptive=False):
    """Validate saved links, cleanup and functional rows before promoting results."""
    from ..phase8d.worker import validate_receipt
    from ..phase8e.audit import plan_read
    plan=plan_read(dest,compiled['source']);source=compiled['source']
    saved=read_object(dest/'dispatch_result.json');verify_checksum(saved,'result_sha256')
    require(saved==dispatch and saved.get('source')==source and saved.get('plan_sha256')==plan['plan_sha256']==compiled['runtime_plan_sha256'] and saved.get('exact_sha_approved') is True,'FUNCTIONAL_DISPATCH_UNVERIFIED')
    require(saved.get('status')=='REVIEWED_FUNCTIONAL_EVIDENCE_COMPLETE' and saved.get('vm_start_attempts')==1 and saved.get('automatic_untrusted_execution') is False and saved.get('runtime_permission') is False,'FUNCTIONAL_DISPATCH_UNVERIFIED')
    refs=saved['components'];require(len(refs)==1,'FUNCTIONAL_COMPONENT_MISMATCH');ref=refs[0]
    row=next(c for c in plan['components'] if c['id']==ref['id']);suite=suite_from(compiled,row)
    if adaptive:
        suite['workflow']=read_object(dest/'functional_rederived'/'adaptive_contract.json')
        require(saved.get('adaptive_contract_sha256')==digest(suite['workflow']),'ADAPTIVE_CONTRACT_MISMATCH');validate_suite(suite)
    else:require('adaptive_contract_sha256' not in saved,'UNEXPECTED_ADAPTIVE_CONTRACT')
    require(ref['report']==row['id']+'/runtime_result.json','FUNCTIONAL_RECEIPT_MISMATCH')
    w=read_object(dest/row['id']/'runtime_result.json');verify_checksum(w,'result_sha256')
    require(w['result_sha256']==ref['result_sha256'] and w['source']==source and w['adapter']==row['adapter'] and w['component_id']==row['id'] and w['status']==ref['status']=='REVIEWED_HTTP_READINESS_VERIFIED' and w['cleanup']==ref['cleanup']==CLEAN and w['vm_start_attempts']==1 and w['network']=='QEMU_RESTRICT_ON' and w['runtime_permission'] is False and w['security_attestation']=='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS' and not w.get('error_code'),'FUNCTIONAL_RECEIPT_MISMATCH')
    guard=w['storage_watchdog'];require(guard['tripped'] is False and guard['reason'] is None and guard['samples']>0 and 0<=guard['peak_logical_file_bytes']<guard['budget_bytes'],'FUNCTIONAL_STORAGE_UNVERIFIED')
    ev=w['evidence'];dep=row['dependency']['kind'];bundle=ev['dependency_sha256']
    if dep!='NONE':
        name='manifest-inputs.zip' if dep=='NPM' else 'python-wheelhouse.zip'
        require(checked_file(dest/row['id']/name,145_000_000)['sha256']==bundle,'FUNCTIONAL_DEPENDENCY_MISMATCH')
    else:require(bundle is None,'FUNCTIONAL_DEPENDENCY_MISMATCH')
    req={'source':source,'component_id':row['id'],'adapter':row['adapter'],'snapshot_sha256':saved['snapshot']['sha256'],'plan_sha256':plan['plan_sha256'],'dependency_kind':dep,'bundle_sha256':bundle,'probe_path':row['probe_path'],'service_port':row['service_port'],'functional_suite':suite}
    require(validate_receipt(ev,req),'FUNCTIONAL_GUEST_RECEIPT_UNVERIFIED')
    functional=validate_results(ev['functional'],suite);count=sum(r['request_attempted'] for r in functional['cases'])
    require(count==w['functional_tests_executed']==saved['functional_tests_executed'],'FUNCTIONAL_COUNT_MISMATCH')
    return {'component_id':row['id'],'adapter':row['adapter'],'service_port':row['service_port'],'runtime_report':row['id']+'/runtime_result.json','runtime_sha256':w['result_sha256'],'dispatch_sha256':saved['result_sha256'],'cleanup':CLEAN,'http_readiness':ev['http'],'cases':functional['cases'],**({'workflow':functional['workflow'],'adaptive_contract_sha256':saved['adaptive_contract_sha256']} if adaptive else {})}


def run_functional(url,ref,compiled_path,out,*,trust_demo_source=False,vm_config,profile=None,component=None,require_bounded_volume=False,adaptive=False,confirm_disposable_state=False):
    require(type(adaptive) is bool and (not adaptive or confirm_disposable_state is True),'EXPLICIT_DISPOSABLE_STATE_CONFIRMATION_REQUIRED')
    require(trust_demo_source is True,'EXPLICIT_DEMO_TRUST_REQUIRED')
    require(isinstance(ref,str) and SHA.fullmatch(ref),'PHASE9B_EXACT_REF_REQUIRED')
    request=validate_request({'submission_id':'PHASE9BB','repository_url':url,'ref':ref.lower(),'mode':'STATIC','max_vm_launches':0})
    compiled=read_object(compiled_path);verify_checksum(compiled,'compiled_sha256');source=validate_source(compiled['source'])
    require(source_matches_url(source,request['repository_url']) and source['commit_sha']==ref.lower(),'PHASE9B_SOURCE_MISMATCH')
    require(compiled.get('schema_version')=='9BA.1' and compiled.get('runtime_permission') is False and compiled.get('automatic_untrusted_execution') is False,'FUNCTIONAL_CONTRACT_INVALID')
    groups=compiled['components'];require(type(groups) is list and 1<=len(groups)<=64,'FUNCTIONAL_COMPONENT_MISMATCH')
    groups=[g for g in groups if component is None or component==g['component_id'] or component==g['root']]
    require(len(groups)==1,'FUNCTIONAL_COMPONENT_MISMATCH');g=groups[0]
    # Actual eligibility, policy and plan rederivation happen in dispatcher before any execution.
    vm_config=Path(vm_config).absolute();out=runtime.fresh(out);before=None;temp_path=None;pending=None
    result={'schema_version':'9BB.1','mode':'OPERATOR_TRUSTED_LOCAL_DEMO','source':source,'compiled_sha256':compiled['compiled_sha256'],'status':'INCOMPLETE','stage':'PREFLIGHT','automatic_untrusted_execution':False,'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','grade_credit':None,'vm_start_attempts':0,'vm_attempt_count_uncertain':False,'functional_tests_executed':0,'functional_count_uncertain':False,'temporary_approval_cleanup':'NOT_CREATED','original_config_unchanged':None,'evidence':None,'summary':None,'limitations':['Single selected component only; no mutations or dynamic binding. Implementation-derived tests characterize observed behavior, not independent correctness.','Saved-report consistency is not security attestation. Local operator trust only.']}
    if adaptive:
        result['schema_version']='9BC.1';result['workflow_requests_attempted']=0;result['workflow_status']=None;result['workflow_count_uncertain']=False
        result['limitations']=['One narrow EJS in-memory CRUD characterization workflow, after independent GETs. State confirmation is operator supplied; not independent proof of isolation from external effects. No DB witness, auth or browser checks.','Saved-report consistency is not security attestation. Local operator trust only.']
    try:
        before=checked_file(vm_config,100_000);load_config(vm_config);require(checked_file(vm_config,100_000)==before,'CONFIG_CHANGED')
        explicit_profile=profile is not None
        profile=Path(profile).absolute() if explicit_profile else vm_config.parent/NAME
        assets={};pr=None
        if explicit_profile or profile.exists() or profile.is_symlink():assets,pr=load_profile(profile)
        assert_no_qemu()
        with temporary_approval(vm_config,before,source) as (temp,receipt):
            temp_path=temp;result['temporary_approval_cleanup']='PENDING';result['stage']='REVIEWED_DISPATCH'
            require(checked_file(temp,100_000)==receipt and checked_file(vm_config,100_000)==before,'CONFIG_CHANGED')
            if pr:require(checked_file(profile,16_384)==pr,'DEMO_ASSET_PROFILE_CHANGED')
            if adaptive:result['workflow_count_uncertain']=True
            result['vm_attempt_count_uncertain']=True;result['functional_count_uncertain']=True
            dest=out/'dispatch'
            d=runtime.dispatch(request['repository_url'],source['commit_sha'],dest,run_reviewed=True,fetch=True,vm_config=temp,component=g['component_id'],max_vms=1,require_bounded_volume=require_bounded_volume,functional_plan=compiled,**({'adaptive':True,'confirm_disposable_state':True} if adaptive else {}),**assets)
            require(type(d.get('vm_start_attempts')) is int and 0<=d['vm_start_attempts']<=1,'FUNCTIONAL_DISPATCH_UNVERIFIED')
            result['vm_start_attempts']=d['vm_start_attempts'];result['vm_attempt_count_uncertain']=False
            if d['vm_start_attempts']==0:
                result['functional_count_uncertain']=False
                if adaptive:result['workflow_count_uncertain']=False
            if d.get('status')!='REVIEWED_FUNCTIONAL_EVIDENCE_COMPLETE':
                result['dispatch_error_code']=d.get('error_code');raise ValueError('FUNCTIONAL_DISPATCH_UNVERIFIED')
            pending=collect_evidence(dest,d,compiled,**({'adaptive':True} if adaptive else {}))
            assert_no_qemu();require(checked_file(temp,100_000)==receipt and checked_file(vm_config,100_000)==before,'CONFIG_CHANGED')
        result['stage']='COMPLETE'
    except (Exception,KeyboardInterrupt) as exc:
        code=str(exc);result['error_code']=code if re.fullmatch('[A-Z][A-Z0-9_]{1,100}',code) else type(exc).__name__
    finally:
        if temp_path is not None:result['temporary_approval_cleanup']='REMOVED' if not temp_path.parent.exists() else 'UNVERIFIED'
        if before is not None:
            try:result['original_config_unchanged']=checked_file(vm_config,100_000)==before
            except Exception:result['original_config_unchanged']=False
        if result['temporary_approval_cleanup']=='UNVERIFIED' or result['original_config_unchanged'] is False:result['error_code']='FUNCTIONAL_FINAL_INTEGRITY_UNVERIFIED'
        if pending is not None and not result.get('error_code') and result['temporary_approval_cleanup']=='REMOVED' and result['original_config_unchanged'] is True:
            rows=pending['cases'];counts={k:sum(r['status']==k for r in rows) for k in ('PASS','FAIL','INCONCLUSIVE','NOT_RUN')}
            result.update(status='FUNCTIONAL_EVIDENCE_COMPLETE',evidence=pending,summary=counts,functional_count_uncertain=False,functional_tests_executed=sum(r['request_attempted'] for r in rows),coverage={'compiled_selected':len(rows),'compiled_other_components':compiled['summary']['compiled_tests']-len(rows),'deferred_cases':compiled['summary']['deferred_cases'],'deferred_workflows':compiled['summary']['deferred_workflows']})
            if adaptive:
                wf=pending['workflow'];result.update(workflow_requests_attempted=wf['requests_attempted'],workflow_status=wf['status'],workflow_count_uncertain=False)
                result['coverage']['adaptive_workflows_selected']=1;result['coverage']['adaptive_workflows_observed']=int(wf['requests_attempted']>0);result['coverage']['deferred_workflows']-=1
        result['result_sha256']=digest(result);write(out/'functional_result.json',result)
    return result
