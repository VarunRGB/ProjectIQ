"""Recheck previously prepared reviewed artifacts without trusting saved permissions.
Never extracts an archive, executes code, or follows profile-supplied absolute paths.
"""
from pathlib import Path
import re, stat, zipfile
from ..phase8a.planner import digest
from ..phase8b.planner import load_object, PY
from ..phase8b.bridge import checked_file, PROFILES, CLEAN

HEX = re.compile('[0-9a-f]{64}\\Z')

def _require(ok, code):
    if not ok: raise ValueError(code)

def _read(path, limit):
    evidence = checked_file(path, limit)
    obj = load_object(path.read_text(encoding='utf-8-sig'))
    _require(checked_file(path, limit)==evidence,'FILE_CHANGED')
    return obj, evidence

def _zip_inventory(path, is_py):
    # Metadata checks only, no decompression or extraction. Legacy offline
    # workers retain responsibility for validating/extracting these archives.
    count=0; total=0; names=set()
    with zipfile.ZipFile(path) as z:
        _require(len(z.infolist()) <= (150 if is_py else 12000),'ARCHIVE_LIMIT')
        for item in z.infolist():
            name=item.filename; parts=name.rstrip('/').split('/')
            _require(bool(name) and len(name)<=512 and '\\' not in name and ':' not in name and not any(ord(c)<32 for c in name) and all(p not in ('','.','..') for p in parts),'ARCHIVE_UNSAFE_MEMBER')
            mode=(item.external_attr>>16)&0xffff
            _require(stat.S_IFMT(mode) in (0,stat.S_IFREG,stat.S_IFDIR) and not item.flag_bits&1,'ARCHIVE_UNSAFE_MEMBER')
            _require(name.casefold() not in names,'ARCHIVE_DUPLICATE_MEMBER'); names.add(name.casefold())
            if item.is_dir():continue
            _require(item.file_size <= (35_000_000 if is_py else 20_000_000),'ARCHIVE_LIMIT')
            if is_py:_require(len(parts)==2 and parts[0]=='wheels' and name.endswith('.whl'),'ARCHIVE_WRONG_FAMILY')
            else:_require(len(parts)>=2 and parts[0]=='npm-cache' and '_logs' not in parts,'ARCHIVE_WRONG_FAMILY')
            count+=1; total+=item.file_size
            _require(count <= (150 if is_py else 12000) and total <= (180_000_000 if is_py else 250_000_000),'ARCHIVE_LIMIT')
    _require(count>0,'ARCHIVE_EMPTY')
    return {'file_count':count,'declared_uncompressed_bytes':total,'inspection':'ZIP_METADATA_ONLY_NOT_INSTALL_VERIFICATION'}

def inspect_prepared(directory, recipe):
    """Matches 8B receipt/profile/cache to newly compiled source. Not provenance."""
    try:
        root=Path(directory).absolute()
        receipt,_ = _read(root/'preparation_result.json', 256_000)
        unsigned={k:v for k,v in receipt.items() if k!='result_sha256'}
        _require(receipt.get('result_sha256')==digest(unsigned),'RECEIPT_CHECKSUM_MISMATCH')
        _require(receipt.get('schema_version')=='8B.1' and receipt.get('source')==recipe['source'] and receipt.get('recipe_sha256')==recipe['recipe_sha256'],'RECEIPT_SOURCE_OR_RECIPE_MISMATCH')
        _require(receipt.get('status')=='PREPARED_REVIEWED_ARTIFACTS' and receipt.get('cleanup')==CLEAN and receipt.get('runtime_permission') is False and type(receipt.get('application_vms_started')) is int and receipt['application_vms_started']==0 and type(receipt.get('preparation_attempts')) is int and receipt['preparation_attempts']==1,'RECEIPT_SCOPE_OR_CLEANUP_INVALID')
        rows=receipt.get('artifacts'); jobs=recipe['jobs']
        _require(type(rows) is list and len(rows)==len(jobs)==1,'ARTIFACT_JOB_MISMATCH')
        row=rows[0]; job=jobs[0]; adapter=job['adapter']; is_py=adapter==PY
        _require(row.get('adapter')==adapter and row.get('components')==job['components'] and row.get('status')=='PREPARED_REVIEWED_ARTIFACTS' and row.get('cleanup')==CLEAN and row.get('runtime_permission') is False and row.get('attestation')=='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','ARTIFACT_SCOPE_MISMATCH')
        cache, profile_name, profile_key=PROFILES[adapter]; cache_path=root/'prepared'/cache
        profile_path=root/'prepared'/profile_name
        limit=120_000_000 if is_py else 170_000_000
        artifact=checked_file(cache_path, limit)
        profile, profile_evidence=_read(profile_path,65536)
        _require(row.get('artifact')=={'path':'prepared/'+cache,**artifact} and row.get('profile')=={'path':'prepared/'+profile_name,**profile_evidence},'ARTIFACT_HASH_OR_PATH_MISMATCH')
        identity=recipe['source']['repository']+'@'+recipe['source']['commit_sha']
        _require(set(profile)=={profile_key} and type(profile[profile_key]) is dict and set(profile[profile_key])=={identity},'PROFILE_IDENTITY_MISMATCH')
        p=profile[profile_key][identity]
        _require(type(p) is dict,'PROFILE_SHAPE_INVALID')
        key='wheelhouse_sha256' if is_py else 'npm_cache_sha256'
        _require(p.get(key)==artifact['sha256'],'PROFILE_CACHE_MISMATCH')
        toolkey='pip_sha256' if is_py else 'node_sha256'
        _require(isinstance(p.get(toolkey),str) and HEX.fullmatch(p[toolkey]),'PROFILE_TOOLCHAIN_HASH_INVALID')
        _require((p.get('python_version')=='3.12' and p.get('framework') in ('flask','fastapi')) if is_py else bool(re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}',str(p.get('node_version')))),'PROFILE_RUNTIME_UNSUPPORTED')
        comps=[c for c in recipe['components'] if c['component_id'] in job['components']]
        def manifest(c,name):
            target=name if c['root']=='.' else c['root']+'/'+name
            return next((x['sha256'] for x in c['dependency_inputs'] if x['path']==target),None)
        if is_py: expected={'requirements_sha256':manifest(comps[0],'requirements.txt')}
        elif adapter=='node_fullstack_offline_v1':
            expected={label+'_lockfile_sha256':manifest(next(c for c in comps if c['adapter_candidate']==a),'package-lock.json') for label,a in [('frontend','node_vite_offline_v1'),('backend','node_express_offline_v1')]}
        else:expected={'lockfile_sha256':manifest(comps[0],'package-lock.json')}
        _require(all(isinstance(v,str) and HEX.fullmatch(v) and p.get(k)==v for k,v in expected.items()) and row.get('manifest_hashes')==expected,'ARTIFACT_MANIFEST_MISMATCH')
        inventory=_zip_inventory(cache_path,is_py)
        _require(checked_file(cache_path,limit)==artifact,'FILE_CHANGED')
        return {'status':'MATCHED_REVIEWED_ARTIFACTS_NOT_AUTHORIZED','adapter':adapter,'artifact':artifact,'profile_sha256':profile_evidence['sha256'],'manifest_hashes':expected,'inventory':inventory,'permission':False,'provenance':'SAVED_RECEIPT_CONSISTENCY_ONLY','toolchain_asset_reverified':False}
    except Exception as exc:
        # No raw exceptions, arbitrary paths, or profile values in assessment.
        codes={'RECEIPT_CHECKSUM_MISMATCH','RECEIPT_SOURCE_OR_RECIPE_MISMATCH','RECEIPT_SCOPE_OR_CLEANUP_INVALID','ARTIFACT_JOB_MISMATCH','ARTIFACT_SCOPE_MISMATCH','ARTIFACT_HASH_OR_PATH_MISMATCH','PROFILE_IDENTITY_MISMATCH','PROFILE_SHAPE_INVALID','PROFILE_CACHE_MISMATCH','PROFILE_TOOLCHAIN_HASH_INVALID','PROFILE_RUNTIME_UNSUPPORTED','ARTIFACT_MANIFEST_MISMATCH','ARCHIVE_UNSAFE_MEMBER','ARCHIVE_WRONG_FAMILY','ARCHIVE_DUPLICATE_MEMBER','ARCHIVE_LIMIT','ARCHIVE_EMPTY','FILE_CHANGED','LINKED_ARTIFACT'}
        return {'status':'REJECTED','error_code':str(exc) if isinstance(exc,ValueError) and str(exc) in codes else 'ARTIFACT_EVIDENCE_INVALID_OR_UNREADABLE','permission':False}
