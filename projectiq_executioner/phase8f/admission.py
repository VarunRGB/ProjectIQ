"""Automatic compatibility assessment, explicitly NOT launch authorization."""
import re
from ..phase8a.planner import digest
from ..phase8d.planner import compile_runtime
from ..orchestration.contracts import validate_request, source_matches_url
from ..orchestration.adapters import pinned_checkout
from .qualification import UNRESOLVED

# Narrow initial target: already live-verified single-component runtimes.
INITIAL_ADAPTERS = frozenset({'STATIC_HTTP','PHP_HTTP','NODE_HTTP','NODE_CLI','PY_CLI','PY_ASGI','PY_DJANGO','NODE_SVELTEKIT'})


def assess(plan, source):
    """No saved report or supplied policy flag can grant runtime permission."""
    r = {'schema_version':'8F-A.admission.1','source':source,'compatibility':'NOT_VERIFIED',
         'decision':'DENY_AUTOMATIC_EXECUTION','runtime_permission':False,
         'automatic_untrusted_execution':False,'vm_start_attempts':0,
         'qualification_blockers':list(UNRESOLVED),'compatibility_blockers':[]}
    blockers = r['compatibility_blockers']
    try:
        if plan['source'] != source or not re.fullmatch('[0-9a-f]{40}',source['commit_sha']) or plan['plan_sha256'] != digest({k:v for k,v in plan.items() if k!='plan_sha256'}):
            raise ValueError('PLAN_IDENTITY_OR_DIGEST_INVALID')
        if plan['runtime_permission'] is not False or plan['automatic_untrusted_execution'] is not False or type(plan['vm_launches']) is not int or plan['vm_launches'] != 0:
            raise ValueError('PLAN_PERMISSION_INVALID')
        r['plan_sha256'] = plan['plan_sha256']
        if plan['snapshot_inspection']['issues'] != []:
            blockers.append('SOURCE_SCAN_INCOMPLETE')
        rows = plan['components']
        if len(rows) != 1 or plan['composition'] != 'SINGLE_COMPONENT':
            blockers.append('SINGLE_COMPONENT_REQUIRED')
        else:
            row = rows[0]; r.update(adapter=row['adapter'],component_id=row['id'])
            if row['status'] != 'REVIEWED_CANDIDATE' or row['blockers'] != [] or row['permission'] is not False:
                blockers.append('RUNTIME_PLAN_BLOCKED')
            if row['adapter'] not in INITIAL_ADAPTERS or row['optional_toolchain'] is not False:
                blockers.append('INITIAL_ADAPTER_SCOPE_UNSUPPORTED')
            if row['dependency']['kind'] not in ('NONE','NPM','PYTHON'):
                blockers.append('DEPENDENCY_ROUTE_UNSUPPORTED')
            if any(s != 'sqlite' for s in row['service_hints']):
                blockers.append('EXTERNAL_SERVICE_UNSUPPORTED')
        r['compatibility'] = 'CANDIDATE_ONLY' if not blockers else 'BLOCKED'
    except (ValueError,KeyError,TypeError,AttributeError):
        blockers.append('INVALID_PLAN'); r['compatibility']='BLOCKED'
    r['result_sha256'] = digest(r)
    return r


def plan_remote(url, ref):
    if not isinstance(ref,str) or not re.fullmatch('[0-9a-f]{40}',ref):
        raise ValueError('EXACT_PIN_REQUIRED')
    req = validate_request({'submission_id':'PHASE8F-A','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    with pinned_checkout(req['repository_url'],ref) as (source,root):
        if not source_matches_url(source,req['repository_url']) or source['commit_sha']!=ref:
            raise ValueError('SOURCE_IDENTITY_MISMATCH')
        _,plan,_ = compile_runtime(root,source=source)
        return assess(plan,source)
