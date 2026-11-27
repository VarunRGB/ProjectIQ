"""Bridge freshly verified artifacts into existing exact-SHA reviewed runtime profiles.
Private transient config is created only for a same-invocation reviewed run.
Never extend approved_snapshots, mutate original configuration or accept an imported receipt.
"""
from contextlib import contextmanager
from pathlib import Path
import json, tempfile, re
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c2.broker import require


def _manifest(c,name):
    path=name if c['root']=='.' else c['root']+'/'+name
    rows=[r['sha256'] for r in c['dependency_inputs'] if r['path']==path]
    require(len(rows)==1 and isinstance(rows[0],str) and re.fullmatch('[0-9a-f]{64}',rows[0]),'PROFILE_MANIFEST_HASH_UNAVAILABLE');return rows[0]


def profile_from_current(plan,recipe,prep,out,*,node_tarball=None,node_sha256=None,pip_wheel=None,pip_sha256=None):
    require(len(recipe['components'])==1 and len(plan['components'])==1 and plan['source']==recipe['source'],'PROFILE_COMPONENT_UNSUPPORTED')
    c=recipe['components'][0];adapter=c['adapter_candidate'];assembly=prep.get('assembly',{})
    require(prep.get('cleanup')==CLEAN and assembly.get('cleanup')==CLEAN and assembly.get('runtime_permission') is False,'PREPARATION_NOT_VERIFIED')
    identity=recipe['source']['repository']+'@'+recipe['source']['commit_sha'];out=Path(out).absolute()
    if adapter in {'node_vite_offline_v1','node_express_offline_v1'}:
        require(assembly.get('status')=='OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED' and assembly.get('offline_reuse_observed') is True and assembly.get('npm_ci_preexec_handoffs')==2 and assembly.get('host_gateway_sentinel_denied') is True,'PREPARATION_NOT_VERIFIED')
        archive=out/'npm-cache.zip';asset=checked_file(archive,170_000_000);require(asset==assembly['artifact'] and checked_file(node_tarball,65_000_000)['sha256']==node_sha256,'PROFILE_ARTIFACT_MISMATCH')
        key='node_vite_offline' if adapter=='node_vite_offline_v1' else 'node_express_offline'
        profile={'node_version':'v24.22.3','node_tarball':str(Path(node_tarball).absolute()),'node_sha256':node_sha256,'npm_cache_zip':str(archive),'npm_cache_sha256':asset['sha256'],'lockfile_sha256':_manifest(c,'package-lock.json')}
    elif adapter=='python_web_offline_v1':
        guest=assembly.get('guest',{});require(assembly.get('status')=='PYTHON_WHEELHOUSE_VERIFIED_REVIEWED_NOT_AUTHORIZED' and guest.get('offline_resolution_verified') is True and guest.get('offline_install_verified') is True and guest.get('pip_preexec_handoffs')==2,'PREPARATION_NOT_VERIFIED')
        archive=out/'python-wheelhouse.zip';asset=checked_file(archive,120_000_000);require(asset==assembly['artifact'] and checked_file(pip_wheel,5_000_000)['sha256']==pip_sha256,'PROFILE_ARTIFACT_MISMATCH')
        hints={x.lower() for x in plan['components'][0]['framework_hints']} & {'flask','fastapi'};require(len(hints)==1,'PYTHON_FRAMEWORK_UNSUPPORTED')
        key='python_web_offline';profile={'python_version':'3.12','framework':next(iter(hints)),'pip_wheel':str(Path(pip_wheel).absolute()),'pip_sha256':pip_sha256,'wheelhouse_zip':str(archive),'wheelhouse_sha256':asset['sha256'],'requirements_sha256':_manifest(c,'requirements.txt')}
    else:raise ValueError('PROFILE_ADAPTER_UNSUPPORTED')
    return key,identity,profile


@contextmanager
def transient_config(path,expected_hash,key,identity,profile):
    from ..phase2.vm_worker.orchestrator import load_config
    path=Path(path);before=checked_file(path,100_000);require(before['sha256']==expected_hash,'OPERATOR_CONFIG_CHANGED');config=load_config(path)
    require(checked_file(path,100_000)==before and identity in config['approved_snapshots'],'EXISTING_OPERATOR_APPROVAL_REQUIRED')
    # Copy the existing registry; the old exact source approval is unchanged.
    entries=config.get(key,{})
    require(type(entries) is dict,'OPERATOR_PROFILE_CONFIG_INVALID');config[key]={**entries,identity:profile}
    with tempfile.TemporaryDirectory(prefix='projectiq_8c_reviewed_config_') as td:
        temp=Path(td)/'vm_config.json'
        with temp.open('x',encoding='utf-8') as f:json.dump(config,f,indent=2,allow_nan=False);f.write('\n')
        yield temp
