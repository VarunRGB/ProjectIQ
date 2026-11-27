from pathlib import Path
import base64,hashlib,json,sys,types,zipfile
from contextlib import contextmanager
import pytest
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.planner import compile_dependencies,npm_check,pip_check
from projectiq_executioner.phase8b.bridge import _verify_prepared,prepare_reviewed,CLEAN

SRI='sha512-'+base64.b64encode(bytes(64)).decode()
SOURCE={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40}

def node_data():
    deps={'react':'^19.0.0','vite':'^6.0.0'}
    pkg={'name':'example','version':'1.0.0','scripts':{'build':'vite build','dev':'vite'},'dependencies':deps}
    lock={'lockfileVersion':3,'packages':{'':{'dependencies':deps},**{'node_modules/'+n:{'version':'1.0.0','resolved':'https://registry.npmjs.org/'+n+'/-/'+n+'-1.0.0.tgz','integrity':SRI} for n in deps}}}
    return pkg,lock

def node_files(pkg,lock):return {'package.json':json.dumps(pkg),'package-lock.json':json.dumps(lock)}

def write(root,files):
    for name,data in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data,encoding='utf-8')

def test_npm_valid():
    assert npm_check(node_files(*node_data()),'.')[0]==[]

@pytest.mark.parametrize('url',['http://registry.npmjs.org/a.tgz','https://evil.invalid/a.tgz','https://registry.npmjs.org.evil.invalid/a.tgz','https://registry.npmjs.org@evil.invalid/a.tgz','https://user@registry.npmjs.org/a.tgz','https://registry.npmjs.org/a.tgz?secret=1','file:a.tgz','https://registry.npmjs.org:443/a.tgz','https://registry.npmjs.org/a.tgz#fragment'])
def test_registry_boundary(url):
    p,l=node_data();l['packages']['node_modules/react']['resolved']=url
    assert 'NPM_NONREGISTRY_LOCK_RECORD' in npm_check(node_files(p,l),'.')[0]

@pytest.mark.parametrize('value',[None,'sha1-abc','sha512-invalid','sha512-'+base64.b64encode(bytes(32)).decode()])
def test_bad_integrity(value):
    p,l=node_data();l['packages']['node_modules/react']['integrity']=value
    assert 'NPM_STRONG_INTEGRITY_MISSING' in npm_check(node_files(p,l),'.')[0]

@pytest.mark.parametrize('change,code',[
 ('workspaces','NPM_WORKSPACE_REQUIRES_SEPARATE_RESOLVER'),('mismatch','NPM_ROOT_MANIFEST_LOCK_MISMATCH'),('lifecycle','NPM_PROJECT_LIFECYCLE_REQUIRES_POLICY'),('link','NPM_NONREGISTRY_LOCK_RECORD'),('version','NPM_MANIFEST_OR_LOCK_INVALID'),('git','NPM_NONSTANDARD_DEPENDENCY_SPEC'),('traversal','NPM_NONREGISTRY_LOCK_RECORD')])
def test_npm_edge_cases(change,code):
    p,l=node_data()
    if change=='workspaces':p['workspaces']=['apps/*']
    elif change=='mismatch':p['dependencies']={'other':'1.0.0'}
    elif change=='lifecycle':p['scripts']['preinstall']='untrusted instruction'
    elif change=='link':l['packages']['node_modules/react']['link']=True
    elif change=='version':l['lockfileVersion']=1
    elif change=='git':p['dependencies']['react']='git+https://example.invalid/a';l['packages']['']['dependencies']=p['dependencies']
    elif change=='traversal':l['packages']['node_modules/../bad']={}
    assert code in npm_check(node_files(p,l),'.')[0]

@pytest.mark.parametrize('line',['Flask>=3','flask','-r more.txt','--index-url https://evil.invalid','foo @ https://evil.invalid/foo.whl','-e .','git+https://example.invalid/foo','foo==1.*','foo==1; python_version > "3"','foo==1 --hash=sha256:abc'])
def test_pip_blocks_non_simple(line):
    assert 'PYTHON_NON_SIMPLE_EXACT_REQUIREMENT' in pip_check({'requirements.txt':line},'.')[0]

def test_pip_exact():
    issues,meta=pip_check({'requirements.txt':'# comment\nFlask==3.1.1\nuvicorn[standard]==0.34.0\n'},'.')
    assert not issues and meta['direct_pins']==2

def test_pip_duplicate():
    assert 'PYTHON_DUPLICATE_REQUIREMENT' in pip_check({'requirements.txt':'foo-bar==1\nfoo_bar==2'},'.')[0]

def test_static(tmp_path):
    write(tmp_path,{'index.html':'<html>hello</html>'});p,r=compile_dependencies(tmp_path)
    assert r['status']=='NO_PACKAGE_JOB_REQUIRED' and not r['jobs'] and r['execution_authorized'] is False

def test_flask(tmp_path):
    write(tmp_path,{'app.py':'from flask import Flask\napp = Flask(__name__)','requirements.txt':'Flask==3.1.1'})
    p,r=compile_dependencies(tmp_path,source=SOURCE)
    assert r['jobs'][0]['adapter']=='python_web_offline_v1'
    assert r['recipe_sha256']==digest({k:v for k,v in r.items() if k!='recipe_sha256'})
    assert compile_dependencies(tmp_path,source=SOURCE,expected_plan=p)[1]==r
    (tmp_path/'requirements.txt').write_text('Flask==3.1.2')
    with pytest.raises(ValueError,match='PHASE8A_PLAN_MISMATCH'):compile_dependencies(tmp_path,source=SOURCE,expected_plan=p)

def test_npm_real_shape(tmp_path):
    write(tmp_path,{**node_files(*node_data()),'index.html':'<html></html>','vite.config.js':'export default {}'})
    _,r=compile_dependencies(tmp_path)
    assert r['jobs'][0]['adapter']=='node_vite_offline_v1'
    assert r['dependency_preparation_authorized'] is False
    assert r['components'][0]['dependency_metadata']['locked_package_records']==2

def test_npm_config_blocks(tmp_path):
    write(tmp_path,{**node_files(*node_data()),'index.html':'<html></html>','.npmrc':'registry=https://evil.invalid'})
    _,r=compile_dependencies(tmp_path)
    assert not r['jobs'] and 'REPOSITORY_PACKAGE_CONFIG_REQUIRES_REVIEW' in r['components'][0]['blockers']

@pytest.mark.parametrize('file,content',[('Cargo.toml','[package]\nname="demo"\nversion="1.0.0"'),('go.mod','module example.com/demo\ngo 1.22'),('pom.xml','<project></project>'),('composer.json','{"require":{"laravel/framework":"^11.0"}}'),('pubspec.yaml','name: demo'),('Gemfile','gem "rails"'),('CMakeLists.txt','project(demo)'),('pyproject.toml','[tool.poetry]\nname="demo"')])
def test_other_managers_not_silently_prepared(tmp_path,file,content):
    write(tmp_path,{file:content});_,r=compile_dependencies(tmp_path)
    assert not r['jobs'] and r['status']=='PREPARATION_BLOCKED'

@pytest.fixture
def fake_worker(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir();write(repo,{'requirements.txt':'Flask==3.1.1','app.py':'from flask import Flask\napp = Flask(__name__)'})
    def mod(name,**fields):
        m=types.ModuleType(name);m.__dict__.update(fields);monkeypatch.setitem(sys.modules,name,m)
    @contextmanager
    def checkout(url,ref):yield SOURCE,repo
    mod('projectiq_executioner.orchestration.adapters',pinned_checkout=checkout)
    mod('projectiq_executioner.orchestration.contracts',validate_request=lambda x:x,source_matches_url=lambda s,u:True)
    cfg={'approved_snapshots':['Owner/Repo@'+'a'*40]}
    mod('projectiq_executioner.phase2.vm_worker.orchestrator',load_config=lambda _:cfg)
    asset=tmp_path/'pip-25.2-py3-none-any.whl';asset.write_bytes(b'fixture tool')
    calls=[]
    def backend(**kw):
        calls.append(kw);dest=kw['output_dir'];cache=dest/'python-wheelhouse.zip'
        with zipfile.ZipFile(cache,'w') as z:z.writestr('fixture.whl',b'fixture bytes')
        h=hashlib.sha256(cache.read_bytes()).hexdigest();reqhash=hashlib.sha256((repo/'requirements.txt').read_bytes()).hexdigest()
        row={'wheelhouse_zip':str(cache),'wheelhouse_sha256':h,'requirements_sha256':reqhash,'pip_wheel':str(asset.resolve()),'pip_sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'python_version':'3.12','framework':'flask'}
        (dest/'python_web_profile.json').write_text(json.dumps({'python_web_offline':{'Owner/Repo@'+'a'*40:row}}))
        return {'status':'OFFLINE_PYTHON_WHEELHOUSE_PREPARED_NOT_SANDBOX_ATTESTED','source':'Owner/Repo@'+'a'*40,'cleanup':CLEAN,'network':'EXPLICITLY_AUTHORIZED_PREPARATION_VM_ONLY','execution_vm_egress':'DENY','student_package_builds':'DISABLED_BINARY_WHEELS_ONLY','wheelhouse_sha256':h,'requirements_sha256':reqhash,'python_version':'3.12','framework':'flask'}
    mod('projectiq_executioner.phase2.phase2d.python_cache_prep',prepare_python_cache=backend)
    args=dict(out=tmp_path/'out',vm_config=tmp_path/'private.json',allow_network=True,pip_wheel=asset,pip_sha256=hashlib.sha256(asset.read_bytes()).hexdigest())
    return args,cfg,calls,backend,repo

def test_reviewed_mock_bridge(fake_worker):
    args,cfg,calls,_,_=fake_worker;r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['status']=='PREPARED_REVIEWED_ARTIFACTS' and r['runtime_permission'] is False and len(calls)==1
    assert calls[0]['allow_trusted_pypi_network'] is True and r['application_vms_started']==0

def test_reviewed_refused_without_approval(fake_worker):
    args,cfg,calls,_,_=fake_worker;cfg['approved_snapshots']=[]
    with pytest.raises(ValueError,match='EXISTING_OPERATOR_APPROVAL_REQUIRED'):prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert not calls and not args['out'].exists()

def test_reviewed_refused_without_network(fake_worker):
    args,cfg,calls,_,_=fake_worker;args['allow_network']=False
    with pytest.raises(ValueError,match='EXPLICIT_PREPARATION_NETWORK_PERMISSION_REQUIRED'):prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert not calls

@pytest.mark.parametrize('mutation',['cleanup','source','hash','manifest','profile'])
def test_worker_receipt_rejected(fake_worker,monkeypatch,mutation):
    args,cfg,calls,backend,repo=fake_worker
    def bad(**kw):
        r=backend(**kw)
        if mutation=='cleanup':r['cleanup']='UNKNOWN'
        elif mutation=='source':r['source']='Other/Repo@'+'a'*40
        elif mutation=='hash':r['wheelhouse_sha256']='0'*64
        elif mutation=='manifest':r['requirements_sha256']='0'*64
        elif mutation=='profile':(kw['output_dir']/'python_web_profile.json').write_text('{}')
        return r
    monkeypatch.setattr(sys.modules['projectiq_executioner.phase2.phase2d.python_cache_prep'],'prepare_python_cache',bad)
    r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['status']=='PREPARATION_FAILED_OR_UNVERIFIED' and not r['artifacts'] and r['cleanup']=='UNVERIFIED'

def test_combined_node_recipe(tmp_path):
    front,lock=node_data();back={'name':'api','scripts':{'start':'node app.js'},'dependencies':{'express':'^5.0.0'}}
    bl={'lockfileVersion':3,'packages':{'':{'dependencies':back['dependencies']},'node_modules/express':{'version':'5.0.0','resolved':'https://registry.npmjs.org/express/-/express-5.0.0.tgz','integrity':SRI}}}
    write(tmp_path, {**{'frontend/'+k:v for k,v in node_files(front,lock).items()},'frontend/index.html':'<html></html>',**{'backend/'+k:v for k,v in node_files(back,bl).items()},'backend/app.js':"const express=require('express');const app=express();app.get('/',(req,res)=>res.json({ok:true}));app.listen(3000);"})
    _,r=compile_dependencies(tmp_path)
    assert len(r['jobs'])==1 and r['jobs'][0]['adapter']=='node_fullstack_offline_v1'
    assert len(r['jobs'][0]['components'])==2

def test_unrelated_multi_component_no_job(tmp_path):
    write(tmp_path,{**{'frontend/'+k:v for k,v in node_files(*node_data()).items()},'frontend/index.html':'<html></html>','backend/app.py':'from flask import Flask\napp = Flask(__name__)','backend/requirements.txt':'Flask==3.1.1'})
    _,r=compile_dependencies(tmp_path);assert not r['jobs'];assert 'MULTI_COMPONENT_RESOLVER_REQUIRED' in {c['status'] for c in r['components']}

def test_npm_bridge_mock(tmp_path,monkeypatch,fake_worker):
    args,cfg,calls,_,repo=fake_worker
    for p in repo.iterdir():p.unlink()
    write(repo,{**node_files(*node_data()),'index.html':'<html></html>'})
    asset=tmp_path/'node-v24.22.3-linux-x64.tar.xz';asset.write_bytes(b'trusted mock node');asset_hash=hashlib.sha256(asset.read_bytes()).hexdigest()
    def backend(**kw):
        calls.append(kw);dest=kw['output_dir'];cache=dest/'node-vite-npm-cache.zip'
        with zipfile.ZipFile(cache,'w') as z:z.writestr('cache-fixture',b'test')
        h=hashlib.sha256(cache.read_bytes()).hexdigest();lh=hashlib.sha256((repo/'package-lock.json').read_bytes()).hexdigest()
        (dest/'node_vite_profile.json').write_text(json.dumps({'node_vite_offline':{'Owner/Repo@'+'a'*40:{'npm_cache_zip':str(cache),'npm_cache_sha256':h,'lockfile_sha256':lh,'node_version':'v24.22.3','node_sha256':asset_hash,'node_tarball':str(asset.resolve())}}}))
        return {'status':'OFFLINE_CACHE_PREPARED_NOT_SANDBOX_ATTESTED','source':'Owner/Repo@'+'a'*40,'cleanup':CLEAN,'network':'EXPLICITLY_AUTHORIZED_PREPARATION_VM_ONLY','execution_vm_egress':'DENY','student_install_scripts':'DISABLED','npm_cache_sha256':h,'lockfile_sha256':lh,'node_version':'v24.22.3'}
    m=types.ModuleType('projectiq_executioner.phase2.phase2d.cache_prep');m.prepare_cache=backend;monkeypatch.setitem(sys.modules,m.__name__,m)
    args.update(node_tarball=asset,node_sha256=asset_hash);r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['status']=='PREPARED_REVIEWED_ARTIFACTS' and calls[0]['adapter']=='node_vite_offline_v1'
    assert (args['out']/r['artifacts'][0]['artifact']['path']).is_file()

def test_worker_exception_does_not_claim_cleanup(fake_worker,monkeypatch):
    args,cfg,calls,_,_=fake_worker
    def fail(**kw):raise RuntimeError('secret details not for public report')
    monkeypatch.setattr(sys.modules['projectiq_executioner.phase2.phase2d.python_cache_prep'],'prepare_python_cache',fail)
    r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['status']=='PREPARATION_FAILED_OR_UNVERIFIED' and r['cleanup']=='UNVERIFIED' and 'secret details' not in json.dumps(r)


# Finalization: the bridge exports only allowlisted failure codes, not logs.
class CachePreparationError(ValueError):
    pass

class PythonCacheError(ValueError):
    pass

@pytest.mark.parametrize('exc,stage,code', [
    (CachePreparationError('Disposable npm preparation failed: NODE_VITE_NPM_FILE_PERMISSION\nOperator-only sanitized npm error: secret'), 'REPORTED_GUEST_PREPARATION', 'NODE_VITE_NPM_FILE_PERMISSION'),
    (PythonCacheError('Disposable Python wheel preparation failed: PYTHON_WEB_WHEEL_DOWNLOAD_FAILED'), 'REPORTED_GUEST_PREPARATION', 'PYTHON_WEB_WHEEL_DOWNLOAD_FAILED'),
    (CachePreparationError('Disposable npm preparation failed: secret'), 'REPORTED_GUEST_PREPARATION', 'UNCLASSIFIED_GUEST_FAILURE'),
    (CachePreparationError('QEMU or pinned guest image is not verified'), 'REPORTED_LEGACY_PREFLIGHT', 'TOOLCHAIN_OR_IMAGE_UNVERIFIED'),
    (PythonCacheError('Pinned repository not approved in local VM config'), 'REPORTED_LEGACY_PREFLIGHT', 'OPERATOR_APPROVAL_REQUIRED'),
    (CachePreparationError('Returned offline npm cache SHA256 mismatched'), 'ARTIFACT_TRANSFER_OR_SAVE', 'LEGACY_ARTIFACT_HASH_MISMATCH'),
    (PythonCacheError('Python preparation VM cleanup or time limit failed'), 'CLEANUP_OR_TIMEOUT', 'LEGACY_CLEANUP_OR_TIMEOUT'),
    (RuntimeError('secret'), 'LEGACY_PREPARER_UNKNOWN', 'LEGACY_PREPARATION_FAILED'),
    (RuntimeError('QEMU or pinned guest image is not verified'), 'LEGACY_PREPARER_UNKNOWN', 'LEGACY_PREPARATION_FAILED'),
    (KeyboardInterrupt(), 'LEGACY_PREPARER_UNKNOWN', 'OPERATOR_INTERRUPTED'),
])
def test_safe_failure_details(exc,stage,code):
    from projectiq_executioner.phase8b.bridge import _failure_details
    assert _failure_details(exc)=={'failure_stage':stage,'error_code':code}

@pytest.mark.parametrize('message,code', [('PREPARED_MANIFEST_MISMATCH','PREPARED_MANIFEST_MISMATCH'), ('secret','ARTIFACT_VERIFICATION_FAILED')])
def test_safe_receipt_failure(message,code):
    from projectiq_executioner.phase8b.bridge import _failure_details
    assert _failure_details(ValueError(message),verification=True)=={'failure_stage':'ARTIFACT_VERIFICATION','error_code':code}


def test_guest_failure_receipt_preserves_code(fake_worker,monkeypatch):
    args,cfg,calls,_,_=fake_worker
    def fail(**kw):
        raise PythonCacheError('Disposable Python wheel preparation failed: PYTHON_WEB_WHEEL_DOWNLOAD_FAILED')
    monkeypatch.setattr(sys.modules['projectiq_executioner.phase2.phase2d.python_cache_prep'],'prepare_python_cache',fail)
    r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['error_code']=='PYTHON_WEB_WHEEL_DOWNLOAD_FAILED'
    assert r['failure_stage']=='REPORTED_GUEST_PREPARATION'
    assert r['cleanup']=='UNVERIFIED' and r['runtime_permission'] is False and not r['artifacts']
    assert r['result_sha256']==digest({k:v for k,v in r.items() if k!='result_sha256'})


def test_receipt_failure_stage(fake_worker,monkeypatch):
    args,cfg,calls,backend,_=fake_worker
    def fail(**kw):
        r=backend(**kw);r['requirements_sha256']='0'*64;return r
    monkeypatch.setattr(sys.modules['projectiq_executioner.phase2.phase2d.python_cache_prep'],'prepare_python_cache',fail)
    r=prepare_reviewed('https://github.com/Owner/Repo','a'*40,**args)
    assert r['error_code']=='PREPARED_MANIFEST_MISMATCH'
    assert r['failure_stage']=='ARTIFACT_VERIFICATION'
    assert r['cleanup']=='UNVERIFIED' and r['runtime_permission'] is False and not r['artifacts']
