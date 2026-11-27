"""Independent saved-report consistency checks; never an authorization mechanism."""
from __future__ import annotations
import re
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8a import validate_plan
from ..phase8b.bridge import checked_file, CLEAN
from ..phase8b.planner import load_object
from ..phase8d.worker import validate_receipt, CLI

ATTESTATION='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
HEX=re.compile(r'[0-9a-f]{64}\Z')
ID=re.compile(r'component-[0-9a-f]{16}\Z')


def require(ok,code):
    if not ok:raise ValueError(code)


def read(path, field=None):
    receipt=checked_file(path,4_000_000)
    obj=load_object(Path(path).read_text('utf-8'))
    require(checked_file(path,4_000_000)==receipt,'REPORT_CHANGED')
    if field: require(obj.get(field)==digest({k:v for k,v in obj.items() if k!=field}),'REPORT_DIGEST_MISMATCH')
    return obj


def plan_read(path, source=None):
    base=read(Path(path)/'execution_plan.json','plan_sha256');plan=read(Path(path)/'runtime_plan.json','plan_sha256')
    validate_plan(base)
    require(base.get('execution_authorized') is False and base.get('vm_launches')==0 and base.get('commands_executed')==0 and base.get('functional_tests_executed')==0 and plan.get('snapshot_inspection')==base.get('snapshot_inspection'),'BASE_PLAN_PERMISSION_OR_SNAPSHOT_MISMATCH')
    require(plan.get('schema_version')=='8D.1' and plan.get('source')==base.get('source') and (source is None or plan['source']==source) and plan.get('phase8a_plan_sha256')==base['plan_sha256'],'PLAN_SOURCE_OR_LINK_MISMATCH')
    require(plan.get('runtime_permission') is False and plan.get('automatic_untrusted_execution') is False and type(plan.get('vm_launches')) is int and plan['vm_launches']==0,'PLAN_PERMISSION_MISMATCH')
    rows=plan.get('components');require(type(rows) is list and len(rows)<=64,'PLAN_COMPONENTS_INVALID')
    require(all(type(r) is dict and isinstance(r.get('id'),str) and ID.fullmatch(r['id']) and r.get('permission') is False and r.get('status') in {'BLOCKED','REVIEWED_CANDIDATE'} and type(r.get('blockers')) is list for r in rows),'PLAN_COMPONENT_INVALID')
    require(len({r['id'] for r in rows})==len(rows),'PLAN_COMPONENT_DUPLICATE')
    return plan


def dispatch_read(path,source):
    plan=plan_read(path,source);dispatch=read(Path(path)/'dispatch_result.json','result_sha256')
    require(dispatch.get('schema_version')=='8D.dispatch.1' and dispatch.get('source')==source and dispatch.get('plan_sha256')==plan['plan_sha256'],'DISPATCH_SOURCE_OR_LINK_MISMATCH')
    require(dispatch.get('runtime_permission') is False and dispatch.get('automatic_untrusted_execution') is False and dispatch.get('security_attestation')==ATTESTATION and type(dispatch.get('functional_tests_executed')) is int and dispatch['functional_tests_executed']==0,'DISPATCH_PERMISSION_MISMATCH')
    require(type(dispatch.get('vm_start_attempts')) is int and 0<=dispatch['vm_start_attempts']<=1,'VM_BUDGET_UNVERIFIED')
    return plan,dispatch


def audit_runtime(path,source,root,adapter,*,require_artifacts=False):
    """Validate an exact single-component live result. No trust in status alone."""
    path=Path(path);plan,d=dispatch_read(path,source)
    require(d.get('status')=='REVIEWED_COMPONENT_SMOKE_COMPLETE' and d.get('stage')=='COMPLETE' and d.get('exact_sha_approved') is True and d['vm_start_attempts']==1,'RUNTIME_DISPATCH_UNVERIFIED')
    rows=[r for r in plan['components'] if r.get('root')==root and r.get('adapter')==adapter]
    require(len(rows)==1 and rows[0]['status']=='REVIEWED_CANDIDATE' and not rows[0]['blockers'],'SELECTED_COMPONENT_MISMATCH');row=rows[0]
    refs=d.get('components');require(type(refs) is list and len(refs)==1,'RUNTIME_COMPONENT_COUNT_MISMATCH');ref=refs[0];component=row['id']
    require(ref.get('id')==component and ref.get('root')==root and ref.get('adapter')==adapter and ref.get('report')==component+'/runtime_result.json','RUNTIME_REFERENCE_MISMATCH')
    w=read(path/component/'runtime_result.json','result_sha256')
    require(w.get('result_sha256')==ref.get('result_sha256') and w.get('source')==source and w.get('component_id')==component and w.get('adapter')==adapter and w.get('status')==ref.get('status'),'RUNTIME_RECEIPT_LINK_MISMATCH')
    require(w.get('cleanup')==CLEAN and ref.get('cleanup')==CLEAN and type(w.get('vm_start_attempts')) is int and w['vm_start_attempts']==1 and w.get('runtime_permission') is False and w.get('security_attestation')==ATTESTATION and w.get('network')=='QEMU_RESTRICT_ON' and w.get('stage')=='COMPLETE' and type(w.get('functional_tests_executed')) is int and w['functional_tests_executed']==0 and not w.get('error_code'),'RUNTIME_BOUNDARY_UNVERIFIED')
    guard=w.get('storage_watchdog',{})
    require(guard.get('tripped') is False and guard.get('reason') is None and type(guard.get('samples')) is int and guard['samples']>=1 and type(guard.get('peak_logical_file_bytes')) is int and type(guard.get('budget_bytes')) is int and 0<=guard['peak_logical_file_bytes']<guard['budget_bytes'] and guard.get('global_disk_bound_verified') is False,'STORAGE_WATCHDOG_UNVERIFIED')
    ev=w.get('evidence',{});dep=row['dependency']['kind'];snapshot=d.get('snapshot',{}).get('sha256');bundle=ev.get('dependency_sha256')
    require(isinstance(snapshot,str) and HEX.fullmatch(snapshot) and (bundle is None if dep=='NONE' else isinstance(bundle,str) and HEX.fullmatch(bundle)),'ARTIFACT_DIGEST_INVALID')
    req={'source':source,'component_id':component,'adapter':adapter,'snapshot_sha256':snapshot,'plan_sha256':plan['plan_sha256'],'dependency_kind':dep,'bundle_sha256':bundle,'probe_path':row['probe_path'],'service_port':row['service_port']}
    require(validate_receipt(ev,req),'GUEST_EVIDENCE_UNVERIFIED')
    require(w['status']==('REVIEWED_CLI_EXIT_ZERO_VERIFIED' if adapter in CLI else 'REVIEWED_HTTP_READINESS_VERIFIED'),'READINESS_KIND_MISMATCH')
    if require_artifacts and dep!='NONE':
        name='manifest-inputs.zip' if dep=='NPM' else 'python-wheelhouse.zip'
        require(checked_file(path/component/name,145_000_000)['sha256']==bundle,'DEPENDENCY_ARTIFACT_CHANGED')
    return {'status':'REVIEWED_SMOKE_VERIFIED','adapter':adapter,'source':source,'root':root,'vm_attempts':1,'cleanup':CLEAN,'runtime_sha256':w['result_sha256'],'evidence_scope':'SAVED_REPORT_CONSISTENCY_NOT_ATTESTATION','http_status':ev.get('http',{}).get('http_status'),'cli_exit_code':ev.get('cli_exit_code'),'dependency_kind':dep,'peak_logical_file_bytes':guard['peak_logical_file_bytes'],'functional_tests_executed':0}


def classify_error(code):
    from ..phase8c_final.pypi import SAFE_ERRORS
    if isinstance(code,str) and code in SAFE_ERRORS:return 'DEPENDENCY'
    groups={
        'REVIEW_POLICY':{'EXISTING_OPERATOR_APPROVAL_REQUIRED','SELECTED_COMPONENT_BLOCKED','OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED','EXPLICIT_FETCH_OR_REUSE_REQUIRED'},
        'DEPENDENCY':{'NPM_OFFLINE_INSTALL_FAILED','PIP_OFFLINE_INSTALL_FAILED','PIP_OFFLINE_RESOLUTION_FAILED','PIP_RESOLUTION_MISMATCH','REGISTRY_ACQUISITION_FAILED','PYPI_RESOLUTION_OR_FETCH_FAILED'},
        'TOOLCHAIN':{'TOOLCHAIN_UNAVAILABLE','NODE_ASSET_MISMATCH','PIP_ASSET_MISMATCH','NODE_RUNTIME_MISMATCH','PYTHON_TARGET_MISMATCH','NPM_RUNTIME_VERSION_MISMATCH'},
        'BUILD':{'BUILD_FAILED','BUILD_OUTPUT_MISSING'},
        'STARTUP_OR_SMOKE':{'CLI_FAILED','HTTP_READINESS_UNVERIFIED','HTTP_PORT_ALREADY_OCCUPIED'},
        'WORKER_BOUNDARY':{'HOST_SENTINEL_CONTACTED','STORAGE_BUDGET_EXCEEDED','STORAGE_MEASUREMENT_FAILED','HOST_GATEWAY_NOT_DENIED','WORKER_WALL_TIMEOUT','STORAGE_TERMINATION_UNVERIFIED','APP_CGROUP_ENFORCEMENT_UNAVAILABLE','HTTP_LISTENER_UID_UNVERIFIED'},
    }
    return next((k for k,v in groups.items() if code in v),'UNCLASSIFIED_OR_EVIDENCE_FAILURE')
