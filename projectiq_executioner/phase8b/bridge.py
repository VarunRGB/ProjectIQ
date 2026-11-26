"""Bridge to existing reviewed, network-authorized preparation VMs.

No new trust mechanism: the legacy helpers independently revalidate the exact
source, adapter, trusted toolchain, manifests, guest result and cleanup.
"""
from __future__ import annotations
import hashlib,json,re,stat,zipfile
from pathlib import Path
from ..phase8a.planner import digest
from .planner import compile_dependencies,load_object,NODE,PY

MAX_ARTIFACT=1024*1024*1024
CLEAN='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
PROFILES={
 'node_vite_offline_v1':('node-vite-npm-cache.zip','node_vite_profile.json','node_vite_offline'),
 'node_express_offline_v1':('node-express-npm-cache.zip','node_express_profile.json','node_express_offline'),
 'node_fullstack_offline_v1':('node-fullstack-npm-cache.zip','node_fullstack_profile.json','node_fullstack_offline'),
 PY:('python-wheelhouse.zip','python_web_profile.json','python_web_offline')}

# Fixed messages emitted by the reviewed legacy preparers. Do not export raw
# exceptions: they may include local paths, remote output or credentials.
_PREFLIGHT_ERRORS = {
    'Unsupported Node npm cache adapter': 'UNSUPPORTED_PREPARATION_BACKEND',
    'Explicit --allow-trusted-npm-network approval required': 'PREPARATION_NETWORK_DENIED',
    'Explicit --allow-trusted-pypi-network approval required': 'PREPARATION_NETWORK_DENIED',
    'Expected exact 40-character pinned GitHub commit SHA': 'EXACT_PIN_REQUIRED',
    'Only pinned official Node 24 Linux x64 is enabled': 'NODE_VERSION_UNSUPPORTED',
    'Official Node SHA256 is invalid': 'NODE_ASSET_HASH_INVALID',
    'Official Linux Node archive is missing or SHA256 mismatched': 'NODE_ASSET_MISMATCH',
    'Pinned pip wheel SHA-256 is invalid': 'PIP_ASSET_HASH_INVALID',
    'Official pip 25.2 wheel missing or SHA-256 mismatched': 'PIP_ASSET_MISMATCH',
    'GitHub source is not the requested pinned commit': 'SOURCE_IDENTITY_MISMATCH',
    'GitHub source differs from requested pinned commit': 'SOURCE_IDENTITY_MISMATCH',
    'Operator has not approved the pinned fixture in vm_config.json': 'OPERATOR_APPROVAL_REQUIRED',
    'Pinned repository not approved in local VM config': 'OPERATOR_APPROVAL_REQUIRED',
    'QEMU or pinned guest image is not verified': 'TOOLCHAIN_OR_IMAGE_UNVERIFIED',
    'QEMU toolchain or pinned guest image not verified': 'TOOLCHAIN_OR_IMAGE_UNVERIFIED',
    'Output path is not a directory': 'OUTPUT_PATH_INVALID',
    'Output exists but is not a directory': 'OUTPUT_PATH_INVALID',
    'Refusing to overwrite prepared offline cache or profile': 'OUTPUT_COLLISION',
    'Refusing to overwrite existing Python wheelhouse or profile': 'OUTPUT_COLLISION',
    'Pinned snapshot archive exceeds approved size': 'SNAPSHOT_LIMIT',
    'Pinned repository archive exceeds pilot limit': 'SNAPSHOT_LIMIT',
    'Repository does not match the selected Node/npm-lock adapter': 'LEGACY_ADAPTER_MISMATCH',
    'Repository not recognized as standalone Flask/FastAPI pilot': 'LEGACY_ADAPTER_MISMATCH',
    'D4 two-service manifests are unsupported': 'LEGACY_MANIFEST_UNSUPPORTED',
    'Approved fixture has no npm lockfile': 'NPM_LOCK_MISSING',
    'Node manifest/lockfile or entrypoint is unsupported by offline pilot': 'LEGACY_MANIFEST_UNSUPPORTED',
    'Pinned Python requirements missing': 'PYTHON_REQUIREMENTS_MISSING',
    'Python requirements or fixed entrypoint not supported': 'LEGACY_MANIFEST_UNSUPPORTED',
}
_TRANSFER_ERRORS = {
    'Returned offline npm cache SHA256 mismatched',
    'Downloaded offline Python wheelhouse hash mismatch',
    'Copied offline npm cache SHA256 mismatched',
    'Saved Python wheelhouse hash mismatch',
}
_CLEANUP_ERRORS = {
    'Preparation VM cleanup or wall-time gate failed',
    'Python preparation VM cleanup or time limit failed',
}
_NPM_SUFFIXES = {
    'CACHE_PREPARATION_FAILED', 'CACHE_ARCHIVE_INVALID', 'NPM_TLS_CERTIFICATE',
    'NPM_NETWORK_DNS', 'NPM_NETWORK_TIMEOUT', 'NPM_NETWORK_CONNECTIVITY',
    'NPM_DISK_SPACE', 'NPM_FILE_PERMISSION', 'NPM_INTEGRITY',
    'NPM_LOCKFILE_INCONSISTENT', 'NPM_DEPENDENCY_CONFLICT', 'NPM_REGISTRY_DENIED',
    'NPM_REGISTRY_NOT_FOUND', 'NPM_PLATFORM_UNSUPPORTED', 'NPM_FAILURE_UNCLASSIFIED',
    'NPM_AUTH_REQUIRED', 'NPM_MISSING_FILE', 'NPM_INVALID_PACKAGE',
    'NPM_UNSUPPORTED_PROTOCOL', 'NPM_PROCESS_TERMINATED',
    'NPM_UNCLASSIFIED_REGISTRY_REACHABLE', 'NPM_REGISTRY_PROBE_HTTP_ERROR',
    'NPM_REGISTRY_PROBE_CONNECTION_FAILED', 'MANIFEST_OR_LOCKFILE_UNSUPPORTED',
    'LOCKFILE_HASH_MISMATCH', 'RUNTIME_EXTRACTION_FAILED',
    'RUNTIME_VERSION_MISMATCH', 'NODE_HASH_MISMATCH', 'ENTRYPOINT_UNSUPPORTED',
}
_GUEST_CODES = {
    prefix + suffix for prefix in ('NODE_VITE_', 'NODE_EXPRESS_') for suffix in _NPM_SUFFIXES
} | {
    'UNCLASSIFIED_GUEST_FAILURE', 'NODE_FULLSTACK_INVALID_REQUEST',
    'NODE_FULLSTACK_INVALID_SERVICE_ROOTS', 'NODE_FULLSTACK_NODE_HASH_MISMATCH',
    'NODE_FULLSTACK_NODE_VERSION_MISMATCH', 'NODE_FULLSTACK_UNSUPPORTED_MANIFEST',
    'NODE_FULLSTACK_CACHE_FETCH_FAILED', 'NODE_FULLSTACK_CACHE_ARCHIVE_INVALID',
    'PYTHON_WEB_INVALID_REQUEST', 'PYTHON_WEB_RUNTIME_VERSION_MISMATCH',
    'PYTHON_WEB_REQUIREMENTS_UNSUPPORTED', 'PYTHON_WEB_REQUIREMENTS_HASH_MISMATCH',
    'PYTHON_WEB_PIP_BOOTSTRAP_INVALID', 'PYTHON_WEB_PIP_VERSION_MISMATCH',
    'PYTHON_WEB_ENTRYPOINT_UNSUPPORTED', 'PYTHON_WEB_INVALID_PROJECT_ROOT',
    'PYTHON_WEB_WHEEL_DOWNLOAD_FAILED', 'PYTHON_WEB_WHEELHOUSE_INVALID',
}
_RECEIPT_CODES = {
    'PREPARER_RECEIPT_INVALID', 'PREPARER_POLICY_INVALID', 'LINKED_ARTIFACT',
    'ARTIFACT_SIZE_OR_TYPE', 'ARTIFACT_SIZE_LIMIT', 'ARTIFACT_CHANGED',
    'CACHE_HASH_OR_FORMAT_MISMATCH', 'PROFILE_IDENTITY_MISMATCH',
    'PROFILE_SHAPE_INVALID', 'PROFILE_TOOLCHAIN_MISMATCH', 'PREPARER_RUNTIME_MISMATCH',
    'PROFILE_CACHE_MISMATCH', 'PREPARED_MANIFEST_MISMATCH', 'DUPLICATE_JSON_KEY',
    'NONFINITE_JSON', 'EXPECTED_OBJECT',
}


def _failure_details(exc, *, verification=False):
    """Classify only known codes; never derive permission/cleanup from errors."""
    if isinstance(exc, KeyboardInterrupt):
        return {'failure_stage': 'ARTIFACT_VERIFICATION' if verification else 'LEGACY_PREPARER_UNKNOWN',
                'error_code': 'OPERATOR_INTERRUPTED'}
    message = str(exc)
    if verification:
        return {'failure_stage': 'ARTIFACT_VERIFICATION',
                'error_code': message if message in _RECEIPT_CODES else 'ARTIFACT_VERIFICATION_FAILED'}
    if type(exc).__name__ in {'CachePreparationError', 'PythonCacheError'}:
        if message in _PREFLIGHT_ERRORS:
            return {'failure_stage': 'REPORTED_LEGACY_PREFLIGHT', 'error_code': _PREFLIGHT_ERRORS[message]}
        if message in _TRANSFER_ERRORS:
            return {'failure_stage': 'ARTIFACT_TRANSFER_OR_SAVE', 'error_code': 'LEGACY_ARTIFACT_HASH_MISMATCH'}
        if message in _CLEANUP_ERRORS:
            return {'failure_stage': 'CLEANUP_OR_TIMEOUT', 'error_code': 'LEGACY_CLEANUP_OR_TIMEOUT'}
        for prefix in ('Disposable npm preparation failed: ', 'Disposable Python wheel preparation failed: '):
            if message.startswith(prefix):
                # Legacy optional operator hint follows a newline. Never export it.
                code = message[len(prefix):].split('\n', 1)[0]
                return {'failure_stage': 'REPORTED_GUEST_PREPARATION',
                        'error_code': code if code in _GUEST_CODES else 'UNCLASSIFIED_GUEST_FAILURE'}
    return {'failure_stage': 'LEGACY_PREPARER_UNKNOWN', 'error_code': 'LEGACY_PREPARATION_FAILED'}

def checked_file(path,limit):
    p=Path(path).absolute()
    for part in (p,*p.parents):
        s=part.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ValueError('LINKED_ARTIFACT')
    s=p.stat()
    if not stat.S_ISREG(s.st_mode) or not 0<s.st_size<=limit:raise ValueError('ARTIFACT_SIZE_OR_TYPE')
    h=hashlib.sha256()
    with p.open('rb') as f:
        total=0
        while block:=f.read(1024*1024):
            total+=len(block)
            if total>limit:raise ValueError('ARTIFACT_SIZE_LIMIT')
            h.update(block)
    if total!=s.st_size or p.stat().st_mtime_ns!=s.st_mtime_ns:raise ValueError('ARTIFACT_CHANGED')
    return {'sha256':h.hexdigest(),'bytes':total}

def _verify_prepared(raw,job,recipe,dest,toolchain):
    adapter=job['adapter'];cache_name,profile_name,key=PROFILES[adapter];is_py=adapter==PY
    identity=recipe['source']['repository']+'@'+recipe['source']['commit_sha']
    status='OFFLINE_PYTHON_WHEELHOUSE_PREPARED_NOT_SANDBOX_ATTESTED' if is_py else 'OFFLINE_CACHE_PREPARED_NOT_SANDBOX_ATTESTED'
    if not isinstance(raw,dict) or raw.get('status')!=status or raw.get('source')!=identity or raw.get('cleanup')!=CLEAN or raw.get('network')!='EXPLICITLY_AUTHORIZED_PREPARATION_VM_ONLY' or raw.get('execution_vm_egress')!='DENY':raise ValueError('PREPARER_RECEIPT_INVALID')
    if is_py and raw.get('student_package_builds')!='DISABLED_BINARY_WHEELS_ONLY' or not is_py and raw.get('student_install_scripts')!='DISABLED':raise ValueError('PREPARER_POLICY_INVALID')
    c=dest/cache_name;p=dest/profile_name
    receipt=checked_file(c,MAX_ARTIFACT);pr=checked_file(p,65536)
    hashkey='wheelhouse_sha256' if is_py else 'npm_cache_sha256';pathkey='wheelhouse_zip' if is_py else 'npm_cache_zip'
    if receipt['sha256']!=raw.get(hashkey) or not zipfile.is_zipfile(c):raise ValueError('CACHE_HASH_OR_FORMAT_MISMATCH')
    profile=load_object(p.read_text(encoding='utf-8'))
    if set(profile)!={key} or set(profile[key])!={identity}:raise ValueError('PROFILE_IDENTITY_MISMATCH')
    row=profile[key][identity]
    if not isinstance(row,dict):raise ValueError('PROFILE_SHAPE_INVALID')
    if is_py:
        expected={'pip_sha256':toolchain['sha256'],'python_version':'3.12'};tool_key='pip_wheel'
    else:
        expected={'node_sha256':toolchain['sha256'],'node_version':toolchain['version']};tool_key='node_tarball'
    if any(row.get(k)!=v for k,v in expected.items()) or Path(row.get(tool_key,'')).absolute()!=Path(toolchain['path']).absolute():raise ValueError('PROFILE_TOOLCHAIN_MISMATCH')
    if is_py and (raw.get('python_version')!='3.12' or raw.get('framework') not in ('flask','fastapi') or row.get('framework')!=raw.get('framework')):raise ValueError('PREPARER_RUNTIME_MISMATCH')
    if not is_py and raw.get('node_version')!=toolchain['version']:raise ValueError('PREPARER_RUNTIME_MISMATCH')
    if row.get(hashkey)!=receipt['sha256'] or Path(row.get(pathkey,'')).absolute()!=c.absolute():raise ValueError('PROFILE_CACHE_MISMATCH')
    # Match actual manifest inputs from regenerated snapshot to helper outputs.
    components=[r for r in recipe['components'] if r['component_id'] in job['components']]
    def manifest_hash(component,name):
        return next((r['sha256'] for r in component['dependency_inputs'] if r['path']==(name if component['root']=='.' else component['root']+'/'+name)),None)
    if is_py:matches={'requirements_sha256':manifest_hash(components[0],'requirements.txt')}
    elif adapter=='node_fullstack_offline_v1':
        front=next(r for r in components if r['adapter_candidate']=='node_vite_offline_v1');back=next(r for r in components if r['adapter_candidate']=='node_express_offline_v1')
        matches={'frontend_lockfile_sha256':manifest_hash(front,'package-lock.json'),'backend_lockfile_sha256':manifest_hash(back,'package-lock.json')}
    else:matches={'lockfile_sha256':manifest_hash(components[0],'package-lock.json')}
    if any(v is None or raw.get(k)!=v or row.get(k)!=v for k,v in matches.items()):raise ValueError('PREPARED_MANIFEST_MISMATCH')
    return {'status':'PREPARED_REVIEWED_ARTIFACTS','adapter':adapter,'components':job['components'],'cleanup':CLEAN,'artifact':{'path':'prepared/'+cache_name,**receipt},'profile':{'path':'prepared/'+profile_name,**pr},'manifest_hashes':matches,'runtime_permission':False,'attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'}

def prepare_reviewed(repository_url,ref,*,out,vm_config,allow_network=False,node_tarball=None,node_sha256=None,node_version='v24.22.3',pip_wheel=None,pip_sha256=None,expected_plan=None):
    """Perform a single compatible whole-repo preparation job. No application VM.

    `out` must be trusted fresh directory. Caller is the operator, not student.
    No arbitrary backend injection or subprocess command from source is accepted.
    """
    from ..orchestration.adapters import pinned_checkout
    from ..orchestration.contracts import validate_request,source_matches_url
    req=validate_request({'submission_id':'PHASE8B-PREP','repository_url':repository_url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    if not isinstance(ref,str) or not re.fullmatch('[0-9a-f]{40}',ref):raise ValueError('EXACT_PIN_REQUIRED')
    if allow_network is not True:raise ValueError('EXPLICIT_PREPARATION_NETWORK_PERMISSION_REQUIRED')
    from ..phase2.vm_worker.orchestrator import load_config
    config=load_config(Path(vm_config))
    with pinned_checkout(req['repository_url'],ref) as (source,checkout):
        if not source_matches_url(source,req['repository_url']) or source['commit_sha']!=ref:raise ValueError('SOURCE_IDENTITY_MISMATCH')
        identity=source['repository']+'@'+ref
        if identity not in config['approved_snapshots']:raise ValueError('EXISTING_OPERATOR_APPROVAL_REQUIRED')
        _,recipe=compile_dependencies(checkout,source=source,expected_plan=expected_plan)
    if len(recipe['jobs'])!=1:raise ValueError('NO_SINGLE_COMPATIBLE_PREPARATION_JOB')
    job=recipe['jobs'][0];adapter=job['adapter'];out=Path(out).absolute()
    # Fail before launching if trusted tool assets absent; legacy helper hashes again.
    if adapter==PY:
        if pip_wheel is None or not isinstance(pip_sha256,str) or checked_file(pip_wheel,5_000_000)['sha256']!=pip_sha256:raise ValueError('TRUSTED_PIP_ASSET_REQUIRED')
        toolchain={'path':str(Path(pip_wheel).resolve()),'sha256':pip_sha256}
    elif adapter in NODE:
        if node_tarball is None or not isinstance(node_sha256,str) or checked_file(node_tarball,65_000_000)['sha256']!=node_sha256:raise ValueError('TRUSTED_NODE_ASSET_REQUIRED')
        toolchain={'path':str(Path(node_tarball).resolve()),'sha256':node_sha256,'version':node_version}
    else:raise ValueError('UNSUPPORTED_PREPARATION_BACKEND')
    out.mkdir(parents=True,exist_ok=False);dest=out/'prepared';dest.mkdir()
    with (out/'dependency_plan.json').open('x',encoding='utf-8') as f:json.dump(recipe,f,indent=2);f.write('\n')
    result={'schema_version':'8B.1','source':source,'recipe_sha256':recipe['recipe_sha256'],'status':'PREPARATION_FAILED_OR_UNVERIFIED','application_vms_started':0,'runtime_permission':False,'cleanup':'UNVERIFIED','preparation_attempts':1,'artifacts':[]}
    verification=False
    try:
        if adapter==PY:
            from ..phase2.phase2d.python_cache_prep import prepare_python_cache
            raw=prepare_python_cache(repo_url=req['repository_url'],commit_sha=ref,vm_config=Path(vm_config),pip_wheel=Path(pip_wheel),pip_sha256=pip_sha256,output_dir=dest,allow_trusted_pypi_network=True)
        else:
            from ..phase2.phase2d.cache_prep import prepare_cache
            raw=prepare_cache(repo_url=req['repository_url'],commit_sha=ref,vm_config=Path(vm_config),node_tarball=Path(node_tarball),node_sha256=node_sha256,node_version=node_version,output_dir=dest,allow_trusted_npm_network=True,adapter=adapter)
        verification=True
        verified=_verify_prepared(raw,job,recipe,dest,toolchain)
        result.update(status='PREPARED_REVIEWED_ARTIFACTS',cleanup=CLEAN,artifacts=[verified])
    except (Exception,KeyboardInterrupt) as exc:
        # No assumption about whether a guest launched or cleanup succeeded.
        result['error_type']=type(exc).__name__
        result.update(_failure_details(exc, verification=verification))
        result['required_action']='STOP_AND_INSPECT_EXISTING_PREPARER_AND_CLEANUP_BEFORE_RETRY'
    result['result_sha256']=digest(result)
    with (out/'preparation_result.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2);f.write('\n')
    return result
