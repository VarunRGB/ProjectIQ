"""Fail-closed admission assessment. No execution tokens or policy overrides.

An assessment is an advisory record, not an authorization. The current reviewed
QEMU workers cannot admit untrusted submissions; the legacy gate is unchanged.
"""
from __future__ import annotations
import re
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.planner import compile_dependencies
from .artifacts import inspect_prepared

SUPPORTED = {'static_html_v1', 'php_mysql_v1', 'node_vite_offline_v1',
             'node_express_offline_v1', 'node_fullstack_offline_v1', 'python_web_offline_v1'}
HARD_GAPS = (
    'DEDICATED_UNTRUSTED_WORKER_NOT_IMPLEMENTED',
    'INDEPENDENT_WORKER_SECURITY_QUALIFICATION_MISSING',
    'GLOBAL_GUEST_DISK_GROWTH_LIMIT_UNVERIFIED',
    'PER_RUN_TRUSTED_WORKER_IDENTITY_UNVERIFIED',
    'HOST_AND_HYPERVISOR_ISOLATION_UNVERIFIED',
)
PREP_GAPS = ('PREPARATION_EGRESS_CONTAINMENT_UNVERIFIED',
             'PREPARATION_STORAGE_AND_PROCESS_BOUNDARY_UNVERIFIED')


def _source_identity(source):
    if (type(source) is dict and source.get('kind') == 'github'
            and isinstance(source.get('repository'), str)
            and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', source['repository'])
            and isinstance(source.get('commit_sha'), str)
            and re.fullmatch(r'[0-9a-f]{40}', source['commit_sha'])):
        return source['repository'] + '@' + source['commit_sha']
    return None


def _evaluate(plan, recipe, *, pinned_acquisition=False, approved=(), worker=None,
              preparation_dir=None):
    """Called only with newly compiled plans; no supplied plan can grant authority."""
    identity = _source_identity(recipe['source']) if pinned_acquisition else None
    approval = bool(identity and identity in approved)
    components = recipe['components']
    jobs = recipe['jobs']
    prep_blockers = set()
    runtime_blockers = set()
    if not identity:
        prep_blockers.add('SOURCE_NOT_VERIFIED_PINNED_GITHUB'); runtime_blockers.add('SOURCE_NOT_VERIFIED_PINNED_GITHUB')
    if not components:
        prep_blockers.add('NO_APPLICATION_COMPONENTS'); runtime_blockers.add('NO_APPLICATION_COMPONENTS')
    if recipe['snapshot_inspection']['issues']:
        prep_blockers.add('BOUNDED_SOURCE_SCAN_INCOMPLETE'); runtime_blockers.add('BOUNDED_SOURCE_SCAN_INCOMPLETE')
    for c in components:
        prep_blockers.update(c['blockers'])
        runtime_blockers.update(c['runtime_blockers'])
        if c['status'] in {'BLOCKED', 'ADAPTER_EXPANSION_REQUIRED', 'MULTI_COMPONENT_RESOLVER_REQUIRED'}:
            prep_blockers.add(c['status'])
        if c['adapter_candidate'] not in SUPPORTED:
            runtime_blockers.add('SUPPORTED_RUNTIME_ADAPTER_NOT_ESTABLISHED')
    adapter = jobs[0]['adapter'] if len(jobs) == 1 else components[0]['adapter_candidate'] if len(components) == 1 else None
    if adapter not in SUPPORTED:
        runtime_blockers.add('WHOLE_REPOSITORY_ADAPTER_NOT_ESTABLISHED')
    if len(components) > 1 and not (len(jobs) == 1 and adapter == 'node_fullstack_offline_v1' and set(jobs[0]['components']) == {c['component_id'] for c in components}):
        runtime_blockers.add('MULTI_COMPONENT_RUNTIME_UNVERIFIED')
    if recipe['status'] == 'PREPARATION_BLOCKED':
        prep_blockers.add('NO_COMPATIBLE_PREPARATION_RECIPE')
    runtime_blockers.update(prep_blockers)
    artifacts = {'status': 'NOT_REQUIRED' if recipe['status'] == 'NO_PACKAGE_JOB_REQUIRED' else 'NOT_SUPPLIED', 'permission': False}
    if preparation_dir is not None:
        artifacts = inspect_prepared(preparation_dir, recipe) if identity else {'status':'REJECTED','error_code':'SOURCE_NOT_VERIFIED_PINNED_GITHUB','permission':False}
    if jobs and artifacts['status'] != 'MATCHED_REVIEWED_ARTIFACTS_NOT_AUTHORIZED':
        runtime_blockers.add('OFFLINE_ARTIFACTS_NOT_VERIFIED')
    if preparation_dir is not None and artifacts['status'] == 'REJECTED':
        runtime_blockers.add('PREPARATION_RECEIPT_REJECTED')
    worker = dict(worker or {'status':'NOT_INSPECTED'})
    # Never echo a caller-provided positive permission assertion as our own.
    if any(worker.get(k) is True for k in ('automatic_admission_supported','runtime_permission','dependency_preparation_permission')):
        worker={'status':'UNTRUSTED_WORKER_ASSERTION_REJECTED'}
    worker.update(automatic_admission_supported=False,runtime_permission=False,dependency_preparation_permission=False,security_attestation='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
    # This implementation deliberately has no branch that authorizes arbitrary
    # execution, even if a caller supplies an edited or fabricated worker record.
    worker_gaps = list(HARD_GAPS)
    if worker.get('status') != 'REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED':
        worker_gaps.append('CURRENT_WORKER_PREFLIGHT_NOT_VERIFIED')
    prep_status = ('NO_PACKAGE_JOB_REQUIRED' if recipe['status']=='NO_PACKAGE_JOB_REQUIRED' and not prep_blockers
                   else 'BLOCKED_INPUT_OR_RESOLVER' if prep_blockers or len(jobs)!=1
                   else 'BLOCKED_AUTOMATIC_WORKER_UNQUALIFIED')
    runtime_status = ('BLOCKED_INPUT_OR_RUNTIME_PREREQUISITES' if runtime_blockers else 'BLOCKED_AUTOMATIC_WORKER_UNQUALIFIED')
    result = {'schema_version':'8C.1','phase':'8C','source':recipe['source'],
              'phase8a_plan_sha256':plan['plan_sha256'],'phase8b_recipe_sha256':recipe['recipe_sha256'],
              'source_acquisition':'PINNED_GITHUB_SNAPSHOT' if identity else 'LOCAL_UNVERIFIED',
              'adapter_candidate':adapter,'component_count':len(components),
              'worker':worker,'artifact_evidence':artifacts,
              'automatic_preparation':{'status':prep_status,'input_blockers':sorted(prep_blockers),'worker_blockers':worker_gaps+list(PREP_GAPS) if jobs else [],'authorized':False},
              'automatic_runtime':{'status':runtime_status,'input_blockers':sorted(runtime_blockers),'worker_blockers':worker_gaps,'authorized':False},
              'reviewed_route':{'exact_sha_in_operator_config':approval,'preparation_recipe_candidate':approval and prep_status=='BLOCKED_AUTOMATIC_WORKER_UNQUALIFIED', 'runtime_adapter_candidate':approval and adapter in SUPPORTED,'status':'EXISTING_REVIEWED_GATE_REQUIRED' if approval else 'NOT_APPROVED','permission':False},
              'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
              'runtime_permission':False,'dependency_preparation_permission':False,
              'preparation_vms_started':0,'application_vms_started':0,'repository_commands_executed':0,
              'status':'AUTOMATIC_ADMISSION_BLOCKED',
              'limitations':['Source and artifact checks are advisory, not worker attestation.', 'No permission can be imported from JSON, a plan checksum, or approval membership.', 'The reviewed workers and their exact-SHA gates remain unchanged.']}
    result['assessment_sha256']=digest(result)
    return result


def assess_directory(root, *, worker=None):
    plan, recipe = compile_dependencies(Path(root))
    return plan, recipe, _evaluate(plan, recipe, worker=worker)


def assess_repository(url, ref=None, *, approved=(), worker=None, preparation_dir=None):
    from ..orchestration.adapters import pinned_checkout
    from ..orchestration.contracts import validate_request, source_matches_url
    req = validate_request({'submission_id':'PHASE8C-ASSESS','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    with pinned_checkout(req['repository_url'],req['ref']) as (source, checkout):
        if not source_matches_url(source,req['repository_url']) or not _source_identity(source):
            raise ValueError('SOURCE_IDENTITY_MISMATCH')
        if ref and re.fullmatch(r'[0-9a-fA-F]{40}',ref) and source['commit_sha'] != ref.lower():
            raise ValueError('SOURCE_IDENTITY_MISMATCH')
        plan, recipe = compile_dependencies(checkout,source=source)
        return plan, recipe, _evaluate(plan, recipe, pinned_acquisition=True, approved=approved, worker=worker,preparation_dir=preparation_dir)
