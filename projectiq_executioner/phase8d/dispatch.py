"""Fresh source -> per-component plan -> explicit reviewed offline execution.
No legacy gate removal; no authorization from saved reports or repository files.
"""
import json,re,sys
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8a.scanner import _linked
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8b.planner import load_object
from ..phase8c2.broker import require
from ..phase8c_final.dispatch import write,_download,_npm_download,source_approved
from ..phase8c_final.reuse import copy_verified_npm
from ..phase8c_final.pypi import SAFE_ERRORS
from .planner import compile_runtime
from .snapshot import make_snapshot
from .assets import validate_lock,copy_verified_python
from .toolchains import load_node_catalog


def fresh(out):
    out=Path(out).absolute();require(not out.exists() and not out.is_symlink() and not any(_linked(p) for p in out.parents if p.exists()),'OUTPUT_NOT_FRESH');out.mkdir(parents=True,exist_ok=False);return out


def select_components(plan,component=None,all_independent=False):
    rows=plan['components'];require(not (component and all_independent),'CHOOSE_COMPONENT_OR_ALL')
    if component:
        rows=[r for r in rows if r['id']==component or r['root']==component];require(len(rows)==1,'COMPONENT_SELECTION_NOT_UNIQUE')
    elif not all_independent:require(len(rows)==1,'EXPLICIT_MULTI_COMPONENT_SELECTION_REQUIRED')
    require(bool(rows),'NO_COMPONENT_SELECTED');return rows


def _python_fetch(requirements,out,version):
    path=out/'python_input.json';write(path,{'requirements':requirements,'target_python':version})
    bundle=out/'python-wheelhouse.zip'
    stats=_download([sys.executable,'-m','projectiq_executioner.phase8d','_resolve','--input',str(path),'--out',str(out)],out,bundle,out/'.wheel-staging',python_child=True)
    checked_file(out/'python-lock.json',1_000_000);lock=load_object((out/'python-lock.json').read_text('utf-8'));validate_lock(lock,requirements,version)
    return bundle,lock,stats


def plan_local(root,out,**options):
    root=Path(root).absolute();require(root.is_dir() and not any(_linked(p) for p in (root,*root.parents)),'SOURCE_LINKED');root=root.resolve();out=Path(out).absolute();require(not out.resolve().is_relative_to(root),'OUTPUT_INSIDE_SOURCE')
    base,plan,_=compile_runtime(root,**options);out=fresh(out);write(out/'execution_plan.json',base);write(out/'runtime_plan.json',plan);return plan


def dispatch(url,ref,out,*,run_reviewed=False,legacy_reviewed=False,fetch=False,reuse_npm_bundle=None,reuse_python_dir=None,vm_config=None,node_tarball=None,node_sha256=None,node_version='v24.22.3',node_catalog=None,pip_wheel=None,pip_sha256=None,python_version='3.12.3',allow_inferred=False,component=None,all_independent=False,max_vms=1,enable_optional_toolchains=False,require_bounded_volume=False,static_report=False):
    from ..orchestration.adapters import pinned_checkout
    from ..orchestration.contracts import validate_request,source_matches_url
    req=validate_request({'submission_id':'PHASE8D','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    require(type(max_vms) is int and 1<=max_vms<=4,'VM_BUDGET_INVALID');require(not run_reviewed or vm_config is not None,'REVIEWED_CONFIG_REQUIRED')
    require(not legacy_reviewed or run_reviewed and not any((fetch,reuse_npm_bundle,reuse_python_dir,component,all_independent,require_bounded_volume,enable_optional_toolchains,static_report)) and max_vms==1,'LEGACY_ROUTE_REQUIRES_EXISTING_ASSETS_AND_ONE_VM')
    require(not (node_catalog and (node_tarball or node_sha256)),'CHOOSE_NODE_ASSET_OR_CATALOG')
    assets=load_node_catalog(node_catalog) if node_catalog else {}
    require(not (fetch and (reuse_npm_bundle or reuse_python_dir)),'CHOOSE_FETCH_OR_REUSE')
    require(run_reviewed or not any((fetch,reuse_npm_bundle,reuse_python_dir,vm_config,component,all_independent,require_bounded_volume,enable_optional_toolchains)),'RUNTIME_FLAGS_REQUIRE_REVIEWED_RUN')
    out=fresh(out);result={'schema_version':'8D.dispatch.1','status':'FAILED_OR_BLOCKED','stage':'SOURCE_PREFLIGHT','runtime_permission':False,'automatic_untrusted_execution':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','vm_start_attempts':0,'functional_tests_executed':0,'components':[],'limitations':['Reviewed exact SHA execution only.','HTTP 2xx or CLI exit zero is readiness/smoke evidence, not correctness or rubric credit.','Composition dependencies and external services not automatically provisioned.','Global storage isolation and dedicated untrusted worker remain outstanding.']}
    snapshot=out/'snapshot.zip'
    try:
        with pinned_checkout(req['repository_url'],req['ref']) as (source,root):
            require(source_matches_url(source,req['repository_url']) and re.fullmatch('[0-9a-f]{40}',source['commit_sha']) and (not ref or not re.fullmatch('[0-9a-fA-F]{40}',ref) or source['commit_sha']==ref.lower()),'SOURCE_IDENTITY_MISMATCH')
            base,plan,inputs=compile_runtime(root,source=source,node_version=node_version,node_versions=list(assets) if assets else None,python_version=python_version,allow_inferred=allow_inferred)
            result.update(source=source,plan_sha256=plan['plan_sha256'],composition=plan['composition']);write(out/'execution_plan.json',base);write(out/'runtime_plan.json',plan)
            if run_reviewed:
                result['stage']='REVIEWED_ADMISSION';cfg=checked_file(vm_config,100_000);approved=source_approved(source,vm_config);result['exact_sha_approved']=approved
                require(approved,'EXISTING_OPERATOR_APPROVAL_REQUIRED');require(checked_file(vm_config,100_000)==cfg,'CONFIG_CHANGED')
                if not legacy_reviewed:
                    rows=select_components(plan,component,all_independent);require(len(rows)<=max_vms,'VM_BUDGET_EXCEEDED')
                    require(all(r['status']=='REVIEWED_CANDIDATE' and not r['blockers'] for r in rows),'SELECTED_COMPONENT_BLOCKED')
                    require(enable_optional_toolchains or not any(r['optional_toolchain'] for r in rows),'OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED')
                    # Reject before source transfer, package acquisition or any launch.
                    if any(r['adapter'].startswith('NODE_') for r in rows):
                        if not assets:
                            require(node_tarball is not None and Path(node_tarball).name==f'node-{node_version}-linux-x64.tar.xz' and checked_file(node_tarball,65_000_000)['sha256']==node_sha256,'NODE_ASSET_MISMATCH');assets[node_version]=(node_tarball,node_sha256)
                        require(all(r['toolchain'] in assets for r in rows if r['adapter'].startswith('NODE_')),'NODE_ASSET_MISMATCH')
                    if any(r['dependency']['kind']=='PYTHON' for r in rows):require(pip_wheel is not None and Path(pip_wheel).name=='pip-25.2-py3-none-any.whl' and checked_file(pip_wheel,5_000_000)['sha256']==pip_sha256,'PIP_ASSET_MISMATCH')
                    require(all(r['dependency']['kind']=='NONE' or fetch or (r['dependency']['kind']=='NPM' and reuse_npm_bundle is not None) or (r['dependency']['kind']=='PYTHON' and reuse_python_dir is not None) for r in rows),'EXPLICIT_FETCH_OR_REUSE_REQUIRED')
                    result['snapshot']=make_snapshot(root,snapshot,plan['snapshot_inspection'])
        if static_report:
            result['stage']='STATIC_ORCHESTRATOR';from ..orchestration.core import evaluate
            report=evaluate({'submission_id':'PHASE8D','repository_url':req['repository_url'],'ref':source['commit_sha'],'mode':'STATIC','run_readiness':'NO','max_vm_launches':0},output_dir=out/'executioner')
            require(report.get('source')==source and report.get('vm_attempts_scheduled')==0,'STATIC_ORCHESTRATION_UNVERIFIED');result['static_report']={'status':report['status'],'path':'executioner/executioner_result.json','sha256':checked_file(out/'executioner/executioner_result.json',30_000_000)['sha256']}
        if not run_reviewed:result.update(status='PLANNED_NOT_EXECUTED',stage='COMPLETE')
        elif legacy_reviewed:
            from ..orchestration.core import evaluate,OperatorContext
            from ..phase8c_final.dispatch import readiness_pass
            require(checked_file(vm_config,100_000)==cfg and source_approved(source,vm_config),'CONFIG_CHANGED')
            result['stage']='LEGACY_REVIEWED_ORCHESTRATOR';report=evaluate({'submission_id':'PHASE8D-LEGACY','repository_url':req['repository_url'],'ref':source['commit_sha'],'mode':'REVIEWED','run_readiness':'YES','max_vm_launches':1},output_dir=out/'executioner',operator=OperatorContext(vm_config=Path(vm_config)))
            require(report.get('source')==source,'SOURCE_IDENTITY_MISMATCH')
            result['vm_start_attempts']=None  # Legacy provides scheduled attempts, not launch observations.
            result['legacy_route']={'report':'executioner/executioner_result.json','report_sha256':checked_file(out/'executioner/executioner_result.json',30_000_000)['sha256'],'vm_attempts_scheduled':report.get('vm_attempts_scheduled'),'readiness_verified':readiness_pass(report),'new_storage_watchdog_applied':False}
            require(result['legacy_route']['readiness_verified'],'LEGACY_READINESS_UNVERIFIED')
            result.update(status='LEGACY_REVIEWED_READINESS_VERIFIED',stage='COMPLETE')
        else:
            from .worker import execute_reviewed
            for row in rows:
                # Sequential independent workers; stop at first failure, no retry.
                result['stage']='DEPENDENCY_ACQUISITION';dest=out/row['id'];dest.mkdir(exist_ok=False);bundle=lock=None;stats={'acquisition':'NOT_REQUIRED'}
                require(checked_file(vm_config,100_000)==cfg and source_approved(source,vm_config),'CONFIG_CHANGED')
                dep=row['dependency']['kind'];private=inputs[row['id']]
                if dep=='NPM':
                    if reuse_npm_bundle:
                        bundle=dest/'manifest-inputs.zip';write(dest/'npm-download-plan.json',private);stats=copy_verified_npm(reuse_npm_bundle,bundle,private)
                    else:bundle,stats=_npm_download({'inputs':private},dest)
                if dep=='PYTHON':
                    if reuse_python_dir:
                        bundle,lock,stats=copy_verified_python(reuse_python_dir,dest,private,python_version);write(dest/'python-lock.json',lock)
                    else:bundle,lock,stats=_python_fetch(private,dest,python_version)
                result['stage']='REVIEWED_COMPONENT_EXECUTION'
                # Worker receives only freshly constructed plan and verified artifacts.
                require(checked_file(snapshot,30_000_000)=={k:result['snapshot'][k] for k in ('sha256','bytes')},'SOURCE_CHANGED')
                selected_node,selected_node_sha=assets.get(row['toolchain'],(None,None))
                record=execute_reviewed(source=source,plan=plan,component=row,snapshot=snapshot,vm_config=vm_config,expected_config=cfg,bundle=bundle,lock=lock,node_tarball=selected_node,node_sha256=selected_node_sha,pip_wheel=pip_wheel,pip_sha256=pip_sha256,require_bounded_volume=require_bounded_volume)
                record['acquisition']=stats;record['result_sha256']=digest(record);write(dest/'runtime_result.json',record);result['components'].append({'id':row['id'],'root':row['root'],'adapter':row['adapter'],'status':record['status'],'cleanup':record['cleanup'],'report':row['id']+'/runtime_result.json','result_sha256':record['result_sha256']});result['vm_start_attempts']+=record['vm_start_attempts']
                require(record['status'] in {'REVIEWED_HTTP_READINESS_VERIFIED','REVIEWED_CLI_EXIT_ZERO_VERIFIED'} and record['cleanup']==CLEAN,'COMPONENT_RUNTIME_UNVERIFIED')
            result.update(status='REVIEWED_COMPONENT_SMOKE_COMPLETE',stage='COMPLETE')
    except (Exception,KeyboardInterrupt) as exc:
        known={'SOURCE_IDENTITY_MISMATCH','SOURCE_CHANGED','EXISTING_OPERATOR_APPROVAL_REQUIRED','CONFIG_CHANGED','COMPONENT_SELECTION_NOT_UNIQUE','CHOOSE_COMPONENT_OR_ALL','EXPLICIT_MULTI_COMPONENT_SELECTION_REQUIRED','NO_COMPONENT_SELECTED','VM_BUDGET_EXCEEDED','SELECTED_COMPONENT_BLOCKED','OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED','NODE_ASSET_MISMATCH','PIP_ASSET_MISMATCH','EXPLICIT_FETCH_OR_REUSE_REQUIRED','SOURCE_LINKED','SOURCE_MEMBER_LIMIT','SOURCE_TOTAL_LIMIT','SOURCE_ARCHIVE_LIMIT','STATIC_ORCHESTRATION_UNVERIFIED','COMPONENT_RUNTIME_UNVERIFIED','LEGACY_READINESS_UNVERIFIED','PYTHON_LOCK_MISMATCH','PYTHON_LOCK_REQUIREMENT_MISMATCH','PYTHON_WHEELHOUSE_MEMBER_MISMATCH','PYTHON_WHEELHOUSE_HASH_MISMATCH','PYTHON_WHEELHOUSE_CHANGED','NPM_BUNDLE_MEMBER_SET_MISMATCH','NPM_BUNDLE_MANIFEST_MISMATCH','NPM_BUNDLE_INTEGRITY_MISMATCH','REGISTRY_ACQUISITION_FAILED','HOST_TOOLCHAIN_UNVERIFIED','MANAGED_ROOT_ACL_UNVERIFIED'}|SAFE_ERRORS
        result['error_code']=str(exc) if type(exc) is ValueError and str(exc) in known else 'STAGE_FAILED_OR_INTERRUPTED'
    finally:
        # Never expose a submitted source archive as a report artifact.
        snapshot.unlink(missing_ok=True)
    result['result_sha256']=digest(result);write(out/'dispatch_result.json',result);return result
