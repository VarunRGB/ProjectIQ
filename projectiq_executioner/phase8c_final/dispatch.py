"""One pinned-source dispatcher. Inert acquisition is automatic; VMs remain reviewed.
No authorization can be supplied in a saved report, repository, or CLI override.
"""
import json, re, shutil, subprocess, sys, time
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8a.scanner import inspect,_linked
from ..phase8b.planner import compile_dependencies,load_object
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c2.broker import compile_inputs,require
from .pypi import direct_pins,target,SAFE_ERRORS


def write(path,obj):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2,allow_nan=False);f.write('\n')


def plan_acquisition(root,source):
    plan,recipe=compile_dependencies(root,source=source);scan=inspect(root)
    require(digest(scan.digests)==recipe['snapshot_inspection']['inspected_content_sha256'] and digest(scan.paths)==recipe['snapshot_inspection']['inventory_sha256'] and sorted(scan.issues)==recipe['snapshot_inspection']['issues'],'SOURCE_CHANGED')
    rows=recipe['components']
    if not rows or len(rows)!=1 or rows[0]['blockers'] or recipe['snapshot_inspection']['issues']:
        return plan,recipe,{'kind':'BLOCKED','reason':'SINGLE_COMPONENT_SAFE_INPUT_REQUIRED'}
    c=rows[0];prefix='' if c['root']=='.' else c['root']+'/'
    if c['manager']=='npm':
        try:inputs=compile_inputs(load_object(scan.files[prefix+'package.json']),load_object(scan.files[prefix+'package-lock.json']))
        except (ValueError,TypeError,KeyError):return plan,recipe,{'kind':'BLOCKED','reason':'NPM_LOCK_INPUT_UNSUPPORTED'}
        return plan,recipe,{'kind':'NPM_LOCKED_ARCHIVES','inputs':inputs}
    if c['manager']=='pip':
        try:pins=direct_pins(scan.files[prefix+'requirements.txt'])
        except (ValueError,TypeError,KeyError):return plan,recipe,{'kind':'BLOCKED','reason':'PYTHON_EXACT_PIN_INPUT_UNSUPPORTED'}
        return plan,recipe,{'kind':'PYTHON_WHEEL_RESOLUTION','pins':pins}
    if c['status'] in {'NO_PACKAGE_PREPARATION_REQUIRED','BAKED_RUNTIME_PREREQUISITES_UNVERIFIED'}:
        return plan,recipe,{'kind':'NO_PACKAGE_JOB_REQUIRED'}
    return plan,recipe,{'kind':'BLOCKED','reason':'RESOLVER_NOT_IMPLEMENTED'}


def _download(cmd,out,bundle,staging,*,python_child=False):
    require(shutil.disk_usage(out).free>=500_000_000,'HOST_DISK_HEADROOM_INSUFFICIENT')
    try:
        try:cp=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=630,check=False)
        except subprocess.TimeoutExpired:
            if python_child:raise ValueError('PYPI_CHILD_TIMEOUT') from None
            raise
        if cp.returncode!=0:
            # Child may emit only a fixed bounded error code; no raw output.
            code='PYPI_CHILD_PROTOCOL_FAILED' if python_child else 'REGISTRY_ACQUISITION_FAILED'
            if len(cp.stdout)<=4096:
                try:
                    candidate=load_object(cp.stdout.decode('utf-8')).get('error_code')
                    if isinstance(candidate,str) and candidate in SAFE_ERRORS:code=candidate
                except (ValueError,TypeError,UnicodeError):pass
            raise ValueError(code)
        require(len(cp.stdout)<=4096,'REGISTRY_RESULT_LIMIT')
        try:return load_object(cp.stdout.decode('utf-8'))
        except (ValueError,UnicodeError):
            if python_child:raise ValueError('PYPI_CHILD_PROTOCOL_FAILED') from None
            raise
    except BaseException:
        bundle.unlink(missing_ok=True)
        if staging.exists() and not _linked(staging):shutil.rmtree(staging)
        raise


def _python_download(inputs,out,version):
    path=out/'python_input.json';write(path,{'pins':inputs['pins'],'target_python':version})
    bundle=out/'python-wheelhouse.zip';lock_path=out/'python-lock.json'
    stats=_download([sys.executable,'-m','projectiq_executioner.phase8c_final','_resolve','--input',str(path),'--out',str(out)],out,bundle,out/'.wheel-staging')
    lock=load_object(lock_path.read_text('utf-8'));require(lock['lock_sha256']==digest({k:v for k,v in lock.items() if k!='lock_sha256'}) and lock['direct_pins']==inputs['pins'] and lock['target_python']==version,'PYTHON_LOCK_MISMATCH')
    return bundle,lock,stats


def _npm_download(inputs,out):
    path=out/'npm-download-plan.json';write(path,inputs['inputs']);bundle=out/'manifest-inputs.zip'
    stats=_download([sys.executable,'-m','projectiq_executioner.phase8c2','_download','--input',str(path),'--out',str(bundle)],out,bundle,out/'.registry-staging')
    return bundle,stats


def source_approved(source,vm_config):
    if vm_config is None:return False
    from ..phase2.vm_worker.orchestrator import load_config
    return source['repository']+'@'+source['commit_sha'] in load_config(Path(vm_config))['approved_snapshots']


def readiness_pass(report):
    entries=[x for x in report.get('suites',[]) if x.get('kind')=='readiness']
    return (report.get('vm_attempts_scheduled')==1 and len(entries)==1 and entries[0].get('state')=='PASS' and entries[0].get('vm_verified') is True and entries[0].get('cleanup')==CLEAN and report.get('status') not in {'FAILED','SECURITY_HOLD','NEEDS_REVIEW'})


def choose_runtime(*,requested,approved,prior_cleanup,preparation_failed):
    if not requested:return 'STATIC_ONLY','RUNTIME_NOT_REQUESTED'
    if preparation_failed:return 'BLOCKED','PREPARATION_FAILED'
    if prior_cleanup not in {'NOT_STARTED',CLEAN}:return 'BLOCKED','PRIOR_VM_CLEANUP_UNVERIFIED'
    if not approved:return 'BLOCKED','DEDICATED_UNTRUSTED_WORKER_NOT_QUALIFIED'
    return 'REVIEWED','EXISTING_EXACT_SHA_GATE_REQUIRED'


def run(url,ref,out,*,fetch=False,assemble=False,run_reviewed=False,vm_config=None,node_tarball=None,node_sha256=None,pip_wheel=None,pip_sha256=None,python_version='3.12.3',require_bounded_volume=False,static=True,reuse_npm_bundle=None):
    from ..orchestration.adapters import pinned_checkout
    from ..orchestration.contracts import validate_request,source_matches_url
    target(python_version)
    req=validate_request({'submission_id':'PHASE8C-FINAL','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    require(not (fetch and reuse_npm_bundle is not None),'CHOOSE_FETCH_OR_REUSE')
    acquire=fetch or reuse_npm_bundle is not None
    require(not assemble or acquire and vm_config is not None,'ASSEMBLY_REQUIRES_EXPLICIT_FETCH_OR_REUSE_AND_CONFIG')
    require(not run_reviewed or vm_config is not None,'REVIEWED_RUNTIME_REQUIRES_CONFIG')
    require(not require_bounded_volume or assemble,'BOUNDED_VOLUME_REQUIRES_ASSEMBLY')
    out=Path(out).absolute();require(not out.exists() and not out.is_symlink() and not any(_linked(p) for p in out.parents if p.exists()),'OUTPUT_NOT_FRESH');out.mkdir(parents=True,exist_ok=False)
    result={'schema_version':'8C.final.1','status':'FAILED_OR_BLOCKED','stage':'SOURCE_PREFLIGHT','preparation':{'status':'NOT_STARTED','vm_start_attempts':0,'cleanup':'NOT_STARTED'},'runtime':{'status':'NOT_STARTED'},'automatic_runtime_authorized':False,'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','limitations':['Inert registry acquisition is not automatic untrusted execution.','Existing reviewed VM gate remains mandatory.','Independent dedicated-worker qualification and global host-write containment are outstanding.']}
    try:
        with pinned_checkout(req['repository_url'],req['ref']) as (source,root):
            require(source_matches_url(source,req['repository_url']) and re.fullmatch('[0-9a-f]{40}',source['commit_sha']) and (not ref or not re.fullmatch('[0-9a-fA-F]{40}',ref) or source['commit_sha']==ref.lower()),'SOURCE_IDENTITY_MISMATCH')
            plan,recipe,inputs=plan_acquisition(root,source)
        result.update(source=source,plan_sha256=plan['plan_sha256'],recipe_sha256=recipe['recipe_sha256'],acquisition_kind=inputs['kind'])
        write(out/'execution_plan.json',plan);write(out/'dependency_plan.json',recipe)
        approved=source_approved(source,vm_config);result['exact_sha_approved']=approved
        if vm_config is not None:
            # Operator-owned configuration only. Do not export private contents.
            result['vm_config_sha256']=checked_file(vm_config,100_000)['sha256']
        prep=result['preparation'];prep['status']='PLAN_READY' if inputs['kind']!='BLOCKED' else 'INPUT_BLOCKED';prep['input_reason']=inputs.get('reason')
        failed=False
        require(reuse_npm_bundle is None or inputs['kind']=='NPM_LOCKED_ARCHIVES','REUSE_REQUIRES_NPM_INPUT')
        if acquire and inputs['kind'] in {'NPM_LOCKED_ARCHIVES','PYTHON_WHEEL_RESOLUTION'}:
            result['stage']='INERT_DEPENDENCY_ACQUISITION'
            if assemble and not approved:
                prep['assembly_status']='BLOCKED_EXISTING_REVIEWED_GATE_REQUIRED'
            if inputs['kind']=='NPM_LOCKED_ARCHIVES':
                if reuse_npm_bundle is not None:
                    from .reuse import copy_verified_npm
                    bundle=out/'manifest-inputs.zip';write(out/'npm-download-plan.json',inputs['inputs']);stats=copy_verified_npm(reuse_npm_bundle,bundle,inputs['inputs'])
                else:bundle,stats=_npm_download(inputs,out)
                prep.update(status='NPM_ARCHIVES_ACQUIRED_NOT_INSTALLED',download=stats,download_artifact=checked_file(bundle,145_000_000))
                if assemble and approved:
                    from ..phase8c2.worker import assemble_reviewed
                    require(node_tarball is not None and node_sha256 is not None,'NODE_ASSET_REQUIRED')
                    result['stage']='REVIEWED_OFFLINE_ASSEMBLY';prep['status']='ASSEMBLY_IN_PROGRESS';prep['cleanup']='UNVERIFIED'
                    record=assemble_reviewed(source=source,bundle=bundle,out=out,vm_config=vm_config,node_tarball=node_tarball,node_sha256=node_sha256,storage_guard=True,require_bounded_volume=require_bounded_volume)
                    prep.update(status=record['status'],vm_start_attempts=record['vm_start_attempts'],cleanup=record['cleanup'],assembly=record);failed=record['status']!='OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED'
            else:
                bundle,lock,stats=_python_download(inputs,out,python_version);prep.update(status='PYTHON_WHEELS_ACQUIRED_NOT_VERIFIED_OFFLINE',download=stats,download_artifact=checked_file(bundle,120_000_000),lock_sha256=lock['lock_sha256'])
                if assemble and approved:
                    from .python_worker import assemble_reviewed
                    require(pip_wheel is not None and pip_sha256 is not None,'PIP_ASSET_REQUIRED')
                    result['stage']='REVIEWED_OFFLINE_ASSEMBLY';prep['status']='ASSEMBLY_IN_PROGRESS';prep['cleanup']='UNVERIFIED'
                    record=assemble_reviewed(source=source,lock=lock,wheelhouse=bundle,vm_config=vm_config,pip_wheel=pip_wheel,pip_sha256=pip_sha256,require_bounded_volume=require_bounded_volume)
                    prep.update(status=record['status'],vm_start_attempts=record['vm_start_attempts'],cleanup=record['cleanup'],assembly=record);failed=record['status']!='PYTHON_WHEELHOUSE_VERIFIED_REVIEWED_NOT_AUTHORIZED'
        elif acquire and inputs['kind']=='BLOCKED':failed=True
        if assemble and inputs['kind'] not in {'NPM_LOCKED_ARCHIVES','PYTHON_WHEEL_RESOLUTION'} and inputs['kind']!='NO_PACKAGE_JOB_REQUIRED':failed=True
        mode,reason=choose_runtime(requested=run_reviewed,approved=approved,prior_cleanup=prep['cleanup'],preparation_failed=failed or bool(assemble and not approved))
        result['runtime']={'status':'NOT_STARTED','route':mode,'reason':reason}
        # Use existing orchestrator as the execution authority. New preparation
        # candidates do NOT overwrite or automatically extend operator profiles.
        # Never fall back to reviewed runtime after preparation failure.
        if (mode=='REVIEWED' or static and mode=='STATIC_ONLY'):
            result['stage']='EXISTING_ORCHESTRATOR_DISPATCH'
            from ..orchestration.core import evaluate,OperatorContext
            if mode=='REVIEWED':require(checked_file(vm_config,100_000)['sha256']==result['vm_config_sha256'] and source_approved(source,vm_config),'OPERATOR_CONFIG_CHANGED')
            request={'submission_id':'PHASE8C-FINAL','repository_url':req['repository_url'],'ref':source['commit_sha'],'mode':mode if mode=='REVIEWED' else 'STATIC','run_readiness':'YES' if mode=='REVIEWED' else 'NO','max_vm_launches':1 if mode=='REVIEWED' else 0}
            if mode=='REVIEWED' and assemble and prep.get('assembly'):
                from .profiles import profile_from_current,transient_config
                key,identity,profile=profile_from_current(plan,recipe,prep,out,node_tarball=node_tarball,node_sha256=node_sha256,pip_wheel=pip_wheel,pip_sha256=pip_sha256)
                result['runtime']['profile_source']='CURRENT_VERIFIED_PREPARATION_EPHEMERAL_OPERATOR_CONFIG'
                with transient_config(vm_config,result['vm_config_sha256'],key,identity,profile) as cfg:
                    report=evaluate(request,output_dir=out/'executioner',operator=OperatorContext(vm_config=cfg))
            else:
                report=evaluate(request,output_dir=out/'executioner',operator=OperatorContext(vm_config=Path(vm_config)) if mode=='REVIEWED' else OperatorContext())
            require(report.get('source')==source,'ORCHESTRATION_SOURCE_MISMATCH')
            result['runtime'].update(status=report['status'],vm_attempts_scheduled=report.get('vm_attempts_scheduled'),report='executioner/executioner_result.json',report_sha256=checked_file(out/'executioner/executioner_result.json',30_000_000)['sha256'])
            if mode=='REVIEWED':result['runtime']['reviewed_readiness_verified']=readiness_pass(report)
        elif mode=='BLOCKED':result['runtime']['status']='BLOCKED'
        failed=failed or (run_reviewed and result['runtime'].get('reviewed_readiness_verified') is not True) or (assemble and not approved)
        result['status']='FAILED_OR_BLOCKED' if failed else 'COMPLETED_WITH_LIMITATIONS';result['stage']='COMPLETE'
    except (Exception,KeyboardInterrupt) as exc:
        known={'SOURCE_IDENTITY_MISMATCH','SOURCE_CHANGED','REGISTRY_ACQUISITION_FAILED','REGISTRY_RESULT_LIMIT','HOST_DISK_HEADROOM_INSUFFICIENT','PYTHON_LOCK_MISMATCH','NODE_ASSET_REQUIRED','PIP_ASSET_REQUIRED','ORCHESTRATION_SOURCE_MISMATCH','OPERATOR_CONFIG_CHANGED','EXISTING_OPERATOR_APPROVAL_REQUIRED','PIP_ASSET_MISMATCH','NODE_ASSET_MISMATCH','HOST_TOOLCHAIN_UNVERIFIED','MANAGED_ROOT_ACL_UNVERIFIED','CONFIG_CHANGED','LOCK_CHANGED','NPM_BUNDLE_MEMBER_SET_MISMATCH','NPM_BUNDLE_MEMBER_UNSAFE','NPM_BUNDLE_INTEGRITY_MISMATCH','NPM_BUNDLE_MANIFEST_MISMATCH','NPM_BUNDLE_CHANGED','NPM_BUNDLE_COPY_MISMATCH','NPM_BUNDLE_LIMIT','PROFILE_ADAPTER_UNSUPPORTED','PROFILE_ARTIFACT_MISMATCH','PREPARATION_NOT_VERIFIED'}|SAFE_ERRORS
        result['error_code']=str(exc) if type(exc) is ValueError and str(exc) in known else 'STAGE_FAILED_OR_INTERRUPTED'
    result['result_sha256']=digest(result);write(out/'dispatch_result.json',result);return result
