"""Local operator-trusted demonstration only; not an untrusted admission path."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import re
import tempfile

from ..orchestration.adapters import pinned_checkout
from ..orchestration.contracts import validate_request, source_matches_url
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require
from ..phase8c_final.dispatch import write
from ..phase8d import dispatch as runtime
from ..phase8d.planner import compile_runtime, SUPPORTED
from ..phase8d.toolchains import load_node_catalog
from ..phase8e.audit import audit_runtime, dispatch_read
from ..phase8e.campaign import assert_no_qemu
from ..phase2.vm_worker.orchestrator import load_config


@contextmanager
def temporary_approval(path, before, source):
    """Private short-lived copy; never rewrite the operator's real config."""
    path = Path(path).absolute()
    require(checked_file(path, 100_000) == before, 'CONFIG_CHANGED')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == before['sha256'], 'CONFIG_CHANGED')
    config = load_object(raw.decode('utf-8-sig'))
    # Validate the original with the same loader used by the real worker.
    require(load_config(path) == config and checked_file(path, 100_000) == before,
            'CONFIG_CHANGED')
    identity = source['repository'] + '@' + source['commit_sha']
    config['approved_snapshots'] = [identity]
    # Keep paths interpreted under the same working directory as normal 8D.
    # Never place the temporary configuration in report/output directories.
    with tempfile.TemporaryDirectory(prefix='projectiq_8f_demo_') as td:
        temp = Path(td) / 'vm_config.json'
        write(temp, config)
        receipt = checked_file(temp, 100_000)
        require(load_config(temp)['approved_snapshots'] == [identity], 'DEMO_APPROVAL_INVALID')
        yield temp, receipt


def _check_assets(rows, *, assets, node_version, node_tarball, node_sha256, pip_wheel, pip_sha256):
    if any(r['adapter'].startswith('NODE_') for r in rows):
        if not assets:
            require(node_tarball is not None and Path(node_tarball).name == f'node-{node_version}-linux-x64.tar.xz'
                    and checked_file(node_tarball, 65_000_000)['sha256'] == node_sha256, 'NODE_ASSET_MISMATCH')
            assets = {node_version: (node_tarball, node_sha256)}
        require(all(r['toolchain'] in assets for r in rows if r['adapter'].startswith('NODE_')), 'NODE_ASSET_MISMATCH')
    if any(r['dependency']['kind'] == 'PYTHON' for r in rows):
        require(pip_wheel is not None and Path(pip_wheel).name == 'pip-25.2-py3-none-any.whl'
                and checked_file(pip_wheel, 5_000_000)['sha256'] == pip_sha256, 'PIP_ASSET_MISMATCH')


def run_demo(url, ref, out, *, trust_demo_source=False, vm_config, component=None,
             all_independent=False, max_vms=1, node_version='v24.22.3', node_catalog=None,
             node_tarball=None, node_sha256=None, pip_wheel=None, pip_sha256=None,
             python_version='3.12.3', allow_inferred=False, enable_optional_toolchains=False,
             require_bounded_volume=False):
    """Only a local operator's explicit trust opt-in can invoke this route.

    Existing 8D worker performs all execution; no host installation, no changed
    network, resource or dependency policies. Never expose as an untrusted API.
    """
    require(trust_demo_source is True, 'EXPLICIT_DEMO_TRUST_REQUIRED')
    require(type(max_vms) is int and 1 <= max_vms <= 4, 'VM_BUDGET_INVALID')
    require(not (node_catalog and (node_tarball or node_sha256)), 'CHOOSE_NODE_ASSET_OR_CATALOG')
    req = validate_request({'submission_id':'PHASE8F-DEMO','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    vm_config = Path(vm_config).absolute()
    out = runtime.fresh(out)
    result = {'schema_version':'8F-B.demo.1','mode':'OPERATOR_TRUSTED_LOCAL_DEMO',
              'status':'FAILED_OR_BLOCKED','stage':'CONFIG_PREFLIGHT',
              'operator_trust_asserted':True,'automatic_untrusted_execution':False,
              'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'vm_start_attempts':0,'vm_attempt_count_uncertain':False,
              'functional_tests_executed':0,'components':[], 'approval_scope':'SINGLE_EXACT_COMMIT',
              'temporary_approval_cleanup':'NOT_CREATED','original_config_unchanged':None,
              'limitations':['Trust is asserted by the local operator, not established by static analysis.',
                             'Reviewed worker protections retained; dedicated untrusted worker and global disk boundary not qualified.',
                             'Smoke readiness is not functional correctness; multi-component runs are independent.']}
    before = None
    temp_path = None
    try:
        before = checked_file(vm_config,100_000)
        load_config(vm_config)
        require(checked_file(vm_config,100_000)==before,'CONFIG_CHANGED')
        assets = load_node_catalog(node_catalog) if node_catalog else {}
        result['stage']='COMPATIBILITY_PREFLIGHT'
        opts = dict(node_version=node_version,python_version=python_version,allow_inferred=allow_inferred)
        with pinned_checkout(req['repository_url'], req['ref']) as (source, root):
            require(source_matches_url(source,req['repository_url']) and re.fullmatch('[0-9a-f]{40}',source['commit_sha'])
                    and (not ref or not re.fullmatch('[0-9a-fA-F]{40}',ref) or source['commit_sha']==ref.lower()),'SOURCE_IDENTITY_MISMATCH')
            base,plan,_ = compile_runtime(root,source=source,node_versions=list(assets) if assets else None,**opts)
            result.update(source=source,plan_sha256=plan['plan_sha256'])
            write(out/'execution_plan.json',base);write(out/'runtime_plan.json',plan)
        require(plan['source']==source and plan['plan_sha256']==digest({k:v for k,v in plan.items() if k!='plan_sha256'}),'PLAN_CHANGED_OR_BLOCKED')
        require(plan['snapshot_inspection']['issues']==[],'SOURCE_SCAN_INCOMPLETE')
        rows=runtime.select_components(plan,component,all_independent)
        result['selected_components']=[{'id':r['id'],'root':r['root'],'adapter':r['adapter'],'blockers':r['blockers']} for r in rows]
        require(len(rows)<=max_vms,'VM_BUDGET_EXCEEDED')
        require(all(r['status']=='REVIEWED_CANDIDATE' and r['permission'] is False and not r['blockers'] and r['adapter'] in SUPPORTED and r['dependency']['kind'] in {'NONE','NPM','PYTHON'} for r in rows),'SELECTED_COMPONENT_BLOCKED')
        require(enable_optional_toolchains or not any(r['optional_toolchain'] for r in rows),'OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED')
        result['stage']='ASSET_PREFLIGHT'
        _check_assets(rows,assets=assets,node_version=node_version,node_tarball=node_tarball,node_sha256=node_sha256,pip_wheel=pip_wheel,pip_sha256=pip_sha256)
        result['stage']='WORKER_IDLE_PREFLIGHT'
        assert_no_qemu()
        result['stage']='DEMO_APPROVAL'
        with temporary_approval(vm_config,before,source) as (temp,receipt):
            temp_path=temp
            result['temporary_approval_cleanup']='PENDING'
            for row in rows:
                require(checked_file(vm_config,100_000)==before and checked_file(temp,100_000)==receipt,'CONFIG_CHANGED')
                result['stage']='REVIEWED_DISPATCH'
                dest=out/row['id']
                # Any interruption or missing receipt after calling dispatch makes
                # the actual attempt count uncertain, never silently zero.
                result['vm_attempt_count_uncertain']=True
                d=runtime.dispatch(req['repository_url'],source['commit_sha'],dest,
                    run_reviewed=True,fetch=row['dependency']['kind']!='NONE',vm_config=temp,
                    component=row['id'],max_vms=1,node_catalog=node_catalog,node_tarball=node_tarball,
                    node_sha256=node_sha256,pip_wheel=pip_wheel,pip_sha256=pip_sha256,
                    enable_optional_toolchains=enable_optional_toolchains,require_bounded_volume=require_bounded_volume,**opts)
                saved_plan,saved=dispatch_read(dest,source)
                require(saved==d and saved_plan['plan_sha256']==plan['plan_sha256'],'DISPATCH_PLAN_OR_RECEIPT_MISMATCH')
                result['vm_start_attempts']+=saved['vm_start_attempts']
                result['vm_attempt_count_uncertain']=False
                record={'id':row['id'],'root':row['root'],'adapter':row['adapter'],'dispatch_status':saved['status'],
                        'dispatch_report':row['id']+'/dispatch_result.json','dispatch_sha256':saved['result_sha256']}
                result['components'].append(record)
                if saved.get('error_code'):record['error_code']=saved['error_code']
                require(saved['status']=='REVIEWED_COMPONENT_SMOKE_COMPLETE','DEMO_RUNTIME_NOT_VERIFIED')
                record['evidence']=audit_runtime(dest,source,row['root'],row['adapter'],require_artifacts=True)
                assert_no_qemu()
                require(checked_file(vm_config,100_000)==before and checked_file(temp,100_000)==receipt,'CONFIG_CHANGED')
        result.update(status='DEMO_REVIEWED_SMOKE_COMPLETE',stage='COMPLETE')
    except (Exception,KeyboardInterrupt) as exc:
        known={'CONFIG_CHANGED','SOURCE_IDENTITY_MISMATCH','PLAN_CHANGED_OR_BLOCKED','SOURCE_SCAN_INCOMPLETE','VM_BUDGET_EXCEEDED','SELECTED_COMPONENT_BLOCKED','OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED','NODE_ASSET_MISMATCH','PIP_ASSET_MISMATCH','DEMO_APPROVAL_INVALID','DISPATCH_PLAN_OR_RECEIPT_MISMATCH','DEMO_RUNTIME_NOT_VERIFIED','COMPONENT_SELECTION_NOT_UNIQUE','CHOOSE_COMPONENT_OR_ALL','EXPLICIT_MULTI_COMPONENT_SELECTION_REQUIRED','NO_COMPONENT_SELECTED','WINDOWS_REVIEWED_WORKER_REQUIRED','QEMU_PROCESS_PRESENT_OR_UNVERIFIED'}
        result['error_code']=str(exc) if type(exc) is ValueError and str(exc) in known else 'DEMO_STAGE_FAILED_OR_INTERRUPTED'
    finally:
        if temp_path is not None:
            result['temporary_approval_cleanup']='REMOVED' if not temp_path.parent.exists() else 'UNVERIFIED'
        if before is not None:
            try:result['original_config_unchanged']=checked_file(vm_config,100_000)==before
            except Exception:result['original_config_unchanged']=False
        if result['temporary_approval_cleanup']=='UNVERIFIED' or result['original_config_unchanged'] is False:
            result.update(status='FAILED_OR_BLOCKED',stage='FINAL_INTEGRITY',error_code='DEMO_FINAL_INTEGRITY_UNVERIFIED')
        result['result_sha256']=digest(result);write(out/'demo_result.json',result)
    return result
