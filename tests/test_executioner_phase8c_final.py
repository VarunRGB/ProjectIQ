import hashlib,json,shutil,subprocess,sys,time,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from projectiq_executioner.phase8c_final import pypi as p, storage as s, dispatch as d, python_guest as g, python_worker as w, profiles
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import checked_file,CLEAN


def wheel(name='tiny',version='1.0',filename=None,**kw):
    f=filename or f'{name}-{version}-py3-none-any.whl'
    return {'filename':f,'url':'https://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/'+f,'digests':{'sha256':'a'*64},'size':100,'packagetype':'bdist_wheel','yanked':False,**kw}

class Metadata:
    def __init__(self,records):self.records=records;self.queries=[]
    def get(self,name,version=None):
        self.queries.append((name,version));r=self.records[name]
        if version is None:return {'releases':{v:[wheel(name,v)] for v in r}}
        return {'info':{'name':name,'version':version,'requires_dist':r[version]},'urls':[wheel(name,version)]}


def test_direct_pins():assert p.direct_pins('# comment\nFlask==3.1.1\nJinja2==3.1.6')=={'flask':'3.1.1','jinja2':'3.1.6'}
@pytest.mark.parametrize('text',['flask>=3','flask[async]==3.1.1','-r other.txt','foo @ https://evil.test/a.whl','flask==3\nFlask==4','--index-url https://evil.test','foo==1.0rc1',''])
def test_direct_pin_refusals(text):
    with pytest.raises(ValueError):p.direct_pins(text)
@pytest.mark.parametrize('filename,rank',[('tiny-1.0-py3-none-any.whl',100),('tiny-1.0-cp312-cp312-manylinux_2_17_x86_64.whl',0),('tiny-1.0-cp38-abi3-manylinux2014_x86_64.whl',10),('tiny-1.0-cp313-cp313-manylinux2014_x86_64.whl',None),('tiny-1.0-cp312-cp312-win_amd64.whl',None),('tiny-1.0-cp312-cp312-manylinux_2_28_x86_64.whl',None),('other-1.0-py3-none-any.whl',None)])
def test_target_wheel_tags(filename,rank):assert p.wheel_rank(filename,'tiny','1.0')==rank
@pytest.mark.parametrize('url',['http://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/tiny-1.0-py3-none-any.whl','https://evil.test/packages/aa/bb/'+'c'*60+'/tiny-1.0-py3-none-any.whl','https://files.pythonhosted.org:443/packages/aa/bb/'+'c'*60+'/tiny-1.0-py3-none-any.whl','https://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/tiny-1.0-py3-none-any.whl?x=1','https://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/other.whl'])
def test_file_url_refused(url):
    with pytest.raises(ValueError):p.file_url(url,'tiny-1.0-py3-none-any.whl')
def test_backtracking_and_markers():
    m=Metadata({'app':{'1.0':['dep>=1','other==1.0','missing; sys_platform == "win32"']},'dep':{'1.0':[],'2.0':[]},'other':{'1.0':['dep<2']}})
    lock=p.resolve({'app':'1.0'},'3.12.3',m);assert {r['name']:r['version'] for r in lock['wheels']}=={'app':'1.0','dep':'1.0','other':'1.0'}
    assert lock['lock_sha256']==digest({k:v for k,v in lock.items() if k!='lock_sha256'})
    assert all(name!='missing' for name,_ in m.queries)
@pytest.mark.parametrize('dependency',['dep @ https://evil.test/pkg.whl','dep[extra]>=1','dep; platform_release=="1"'])
def test_transitive_unsupported(dependency):
    with pytest.raises(ValueError):p.resolve({'app':'1.0'},'3.12.3',Metadata({'app':{'1.0':[dependency]}}))
def test_conflict():
    with pytest.raises(ValueError,match='NO_COMPATIBLE'):p.resolve({'app':'1.0','dep':'2.0'},'3.12.3',Metadata({'app':{'1.0':['dep<2']},'dep':{'2.0':[]}}))
def test_target_markers():assert p.target('3.12.3')['python_full_version']=='3.12.3'
@pytest.mark.parametrize('version',['3.11.8','3.13.0','latest','3.12'])
def test_bad_target(version):
    with pytest.raises(ValueError):p.target(version)

class Response:
    def __init__(self,status=200,body=b'abc',headers=None):self.status=status;self.body=body;self.headers=headers or {}
    def getheader(self,k,default=None):return self.headers.get(k,default)
    def read(self,n):b=self.body[:n];self.body=self.body[n:];return b
class Connection:
    def __init__(self,response):self.response=response;self.closed=False
    def request(self,method,path,headers):assert method=='GET' and path.startswith('/pypi/')
    def getresponse(self):return self.response
    def close(self):self.closed=True
@pytest.mark.parametrize('status,body,headers,good',[(200,b'abc',{},True),(302,b'abc',{},False),(200,b'abcd',{},False),(200,b'abc',{'Content-Encoding':'gzip'},False),(200,b'abc',{'Content-Length':'2'},False)])
def test_bounded_https(status,body,headers,good):
    con=Connection(Response(status,body,headers));got=bytearray();args=('pypi.org','/pypi/tiny/json',3,time.monotonic()+2,got.extend)
    if good:assert p._get(*args,connection=lambda h:con)==3 and got==b'abc'
    else:
        with pytest.raises(ValueError):p._get(*args,connection=lambda h:con)
    assert con.closed

def test_fetch_hash_and_cleanup(tmp_path,monkeypatch):
    row=wheel();data=b'wheel byte fixture';row={'name':'tiny','version':'1.0','filename':row['filename'],'url':row['url'],'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    def get(host,path,limit,deadline,consume):assert host=='files.pythonhosted.org';consume(data);return len(data)
    monkeypatch.setattr(p,'_get',get);out=tmp_path/'good.zip';assert p.fetch_wheels({'wheels':[row]},out,time.monotonic()+2)==len(data)
    with zipfile.ZipFile(out) as z:assert z.read('wheels/'+row['filename'])==data
    bad=tmp_path/'bad.zip'
    with pytest.raises(ValueError,match='HASH_MISMATCH'):p.fetch_wheels({'wheels':[{**row,'sha256':'b'*64}]},bad,time.monotonic()+2)
    assert not bad.exists() and not (tmp_path/'.wheel-staging').exists()

class Proc:
    dead=False
    def poll(self):return 0 if self.dead else None
    def kill(self):self.dead=True

def test_storage_guard_terminates(tmp_path,monkeypatch):
    monkeypatch.setattr(s.shutil,'disk_usage',lambda root:SimpleNamespace(free=1000));pr=Proc();watch=s.DiskWatchdog(tmp_path,pr,max_bytes=10,min_free=1)
    (tmp_path/'run.qcow2').write_bytes(b'0'*9);assert watch.sample() and not pr.dead
    (tmp_path/'run.qcow2').write_bytes(b'0'*10);assert not watch.sample() and pr.dead and watch.tripped.is_set();assert watch.report()['global_disk_bound_verified'] is False

def test_storage_guard_low_free(tmp_path,monkeypatch):
    monkeypatch.setattr(s.shutil,'disk_usage',lambda root:SimpleNamespace(free=0));pr=Proc();watch=s.DiskWatchdog(tmp_path,pr,min_free=1);assert not watch.sample() and pr.dead

def test_storage_link_rejected(tmp_path):
    (tmp_path/'target').mkdir()
    try:(tmp_path/'alias').symlink_to(tmp_path/'target',target_is_directory=True)
    except OSError:pytest.skip('symlink permissions unavailable')
    with pytest.raises(ValueError,match='STORAGE_TREE_UNSAFE'):s.measure(tmp_path)

@pytest.mark.parametrize('requested,approved,cleanup,failed,expected',[(False,False,'NOT_STARTED',False,'STATIC_ONLY'),(True,False,'NOT_STARTED',False,'BLOCKED'),(True,True,'UNVERIFIED',False,'BLOCKED'),(True,True,CLEAN,True,'BLOCKED'),(True,True,CLEAN,False,'REVIEWED')])
def test_runtime_routes(requested,approved,cleanup,failed,expected):assert d.choose_runtime(requested=requested,approved=approved,prior_cleanup=cleanup,preparation_failed=failed)[0]==expected

def test_python_guest_receipt():
    req={'bundle_sha256':'a'*64,'pip_sha256':'b'*64,'lock_sha256':'c'*64,'python_version':'3.12.3','wheels':[{}]};r={**{k:v for k,v in req.items() if k!='wheels'},'status':'PYTHON_OFFLINE_LOCK_VERIFIED','wheel_count':1,'offline_resolution_verified':True,'offline_install_verified':True,'host_gateway_denied':True,'pip_preexec_handoffs':2,'workspace_mib':1536,'repository_code_executed':False}
    assert w.validate_receipt(r,req)
    for k,v in [('wheel_count',True),('offline_install_verified',1),('pip_preexec_handoffs',1),('repository_code_executed',True),('lock_sha256','d'*64)]:assert not w.validate_receipt({**r,k:v},req)


def test_pip_report_matches_exact_set(tmp_path):
    path=tmp_path/'report.json';row={'metadata':{'name':'Tiny','version':'1.0'},'download_info':{'archive_info':{'hashes':{'sha256':'a'*64}}}}
    path.write_text(json.dumps({'install':[row]}));g.verify_report(path,{'tiny':('1.0','a'*64)})
    with pytest.raises(ValueError):g.verify_report(path,{'tiny':('2.0','a'*64)})


def test_actual_local_pip_offline_report(tmp_path):
    # Synthetic trusted wheel: no internet, no project code, no user environment modifications.
    import importlib.util
    if importlib.util.find_spec('pip') is None:pytest.skip('optional pip local experiment')
    name='piq_demo';version='1.0';dist=f'{name}-{version}.dist-info';file=tmp_path/f'{name}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(file,'w') as z:
        z.writestr(name+'/__init__.py','VALUE = 1\n');z.writestr(dist+'/METADATA','Metadata-Version: 2.1\nName: piq-demo\nVersion: 1.0\n');z.writestr(dist+'/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n');z.writestr(dist+'/RECORD','')
    sha=hashlib.sha256(file.read_bytes()).hexdigest();req=tmp_path/'req.txt';req.write_text(f'piq-demo==1.0 --hash=sha256:{sha}\n');report=tmp_path/'report.json'
    cp=subprocess.run([sys.executable,'-m','pip','--isolated','install','--no-index','--find-links',str(tmp_path),'--only-binary=:all:','--ignore-installed','--no-cache-dir','--no-compile','--disable-pip-version-check','--require-hashes','--target',str(tmp_path/'site'),'--report',str(report),'-r',str(req)],capture_output=True,timeout=45)
    assert cp.returncode==0,cp.stderr.decode();g.verify_report(report,{'piq-demo':('1.0',sha)})


def test_transient_config_preserves_approval(tmp_path,monkeypatch):
    from projectiq_executioner.phase2.vm_worker import orchestrator
    identity='Owner/Repo@'+'a'*40;cfg=tmp_path/'config.json';config={'approved_snapshots':[identity],'base_image':'private','node_vite_offline':{}};cfg.write_text(json.dumps(config));before=checked_file(cfg,100000)
    monkeypatch.setattr(orchestrator,'load_config',lambda path:json.loads(Path(path).read_text()))
    with profiles.transient_config(cfg,before['sha256'],'node_vite_offline',identity,{'candidate':True}) as ephemeral:
        assert ephemeral!=cfg and json.loads(ephemeral.read_text())['approved_snapshots']==[identity]
    assert not ephemeral.exists() and checked_file(cfg,100000)==before
    with pytest.raises(ValueError,match='APPROVAL_REQUIRED'):
        with profiles.transient_config(cfg,before['sha256'],'node_vite_offline','Other/Repo@'+'a'*40,{}):pass


def test_profile_wrong_artifact_refused(tmp_path):
    source={'repository':'Owner/Repo','commit_sha':'a'*40};plan={'source':source,'components':[{}]};recipe={'source':source,'components':[{'adapter_candidate':'node_vite_offline_v1','root':'.','dependency_inputs':[{'path':'package-lock.json','sha256':'a'*64}]}]}
    artifact=tmp_path/'npm-cache.zip';artifact.write_bytes(b'x');node=tmp_path/'node.tar';node.write_bytes(b'node');prep={'cleanup':CLEAN,'assembly':{'cleanup':CLEAN,'runtime_permission':False,'status':'OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED','offline_reuse_observed':True,'npm_ci_preexec_handoffs':2,'host_gateway_sentinel_denied':True,'artifact':{'sha256':'z'*64}}}
    with pytest.raises(ValueError,match='ARTIFACT_MISMATCH'):profiles.profile_from_current(plan,recipe,prep,tmp_path,node_tarball=node,node_sha256=hashlib.sha256(b'node').hexdigest())

@pytest.mark.parametrize('mode',['success','gateway','bad_hash','resolve_fail','install_fail','bad_report','handoff_missing'])
def test_python_guest_actual_contract(tmp_path,monkeypatch,mode):
    base=tmp_path/'controller';base.mkdir();work=tmp_path/'workspace';work.mkdir();archive=base/'python_wheelhouse.zip';data=b'wheel fixture';filename='tiny-1.0-py3-none-any.whl';sha=hashlib.sha256(data).hexdigest();archive.write_bytes(b'outer fixture')
    handoffs=[];calls=[];shared=[]
    def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    req={'bundle_sha256':hashfile(archive),'pip_sha256':'b'*64,'lock_sha256':'c'*64,'python_version':'3.12.3','sentinel_port':54321,'direct_pins':{'tiny':'1.0'},'wheels':[{'name':'tiny','version':'1.0','filename':filename,'sha256':'d'*64 if mode=='bad_hash' else sha}]}
    def stage(meta,base):assert meta=={'workspace_disk_mib':1536};shutil.move(base/'python_wheelhouse.zip',work/'python_wheelhouse.zip');return work
    def unpack(a,dest):dest.mkdir();path=dest/filename;path.write_bytes(data);return [path]
    def run(args,**kw):
        calls.append(args);assert kw['project_code'] is True
        if len(calls)==1:return SimpleNamespace(returncode=int(mode=='gateway'))
        assert kw['audit_kind']=='pip_install' and '--no-index' in args and '--only-binary=:all:' in args and '--no-compile' in args
        path=Path(args[args.index('--report')+1]);assert path.parent==work/'reports'
        if len(calls)==2:assert '--dry-run' in args and '-c' in args
        else:assert '--require-hashes' in args and '--target' in args
        path.write_text(json.dumps({'install':[{'metadata':{'name':'tiny','version':'2.0' if mode=='bad_report' else '1.0'},'download_info':{'archive_info':{'hashes':{'sha256':sha}}}}]}))
        if mode!='handoff_missing':handoffs.append('pip_install')
        return SimpleNamespace(returncode=int(mode=='resolve_fail' and len(calls)==2 or mode=='install_fail' and len(calls)==3))
    fake=SimpleNamespace(_verify_execution_identity=lambda base:None,_stage_bounded_workspace=stage,_verify_evaluation_cgroup=lambda work:None,_APP_CGROUP_ACTIVE=False,_APP_BUILD_HANDOFFS=handoffs,_trusted_process=run,_sha256=hashfile,_unpack_python_wheelhouse=unpack,_share_tree=lambda path,writable:shared.append((path.name,writable)),_python_pip_wheel=lambda work,req:work/'pip.whl',_verify_python_pip=lambda work,pip:{})
    monkeypatch.setattr(g,'sys',SimpleNamespace(version_info=(3,12,3)))
    if mode=='success':
        result=g.assemble(fake,base,req);assert w.validate_receipt(result,req) and ('python-wheels',False) in shared and len(calls)==3
    else:
        with pytest.raises(ValueError):g.assemble(fake,base,req)

@pytest.mark.parametrize('mode',['success','cleanup_failure','bad_receipt','no_approval','bounded_volume_missing','watchdog_trip'])
def test_python_worker_lifecycle(tmp_path,monkeypatch,mode):
    import types
    from projectiq_executioner.phase2 import vm_worker as pkg
    prefix='projectiq_executioner.phase2.vm_worker.'
    cfg=tmp_path/'config.json';cfg.write_text('{}');wheelhouse=tmp_path/'wheelhouse.zip';wheelhouse.write_bytes(b'fixture');pip=tmp_path/'pip-25.2-py3-none-any.whl';pip.write_bytes(b'pip')
    source={'repository':'Owner/Repo','commit_sha':'a'*40};identity=source['repository']+'@'+source['commit_sha'];lock={'target_python':'3.12.3','direct_pins':{'tiny':'1.0'},'wheels':[{'name':'tiny','version':'1.0','filename':'tiny-1.0-py3-none-any.whl','sha256':'a'*64}]};lock['lock_sha256']=digest(lock)
    config={'approved_snapshots':[] if mode=='no_approval' else [identity],'accelerator':'whpx'};calls=[]
    def module(name,**attrs):
        m=types.ModuleType(prefix+name);m.__dict__.update(attrs);monkeypatch.setitem(sys.modules,prefix+name,m);monkeypatch.setattr(pkg,name,m,raising=False)
    module('orchestrator',load_config=lambda path:config)
    module('d5d_host_acl_audit',SUCCESS='OK',audit=lambda p:{'status':'OK','checks':{str(i):'PASS' for i in range(5)}})
    class Managed:
        cleanup_verified=False
        def __init__(self,c):pass
        def open(self):self.folder=tmp_path/'managed';self.folder.mkdir();calls.append('open');return self.folder
        def mark_launching(self):calls.append('launching')
        def mark_launched(self,p):calls.append('launched')
        def mark_booted(self):calls.append('booted')
        def mark_stopped(self):calls.append('stopped')
        def close(self):
            calls.append('close');self.cleanup_verified=mode!='cleanup_failure'
            if self.cleanup_verified:shutil.rmtree(self.folder)
    module('d5c_runtime_workspace',ManagedRuntimeRun=Managed)
    class VM:
        def __init__(self,c,work,port,**kw):self.workspace_disk_mib=c['workspace_disk_mib'];self.port=port;self.proc=None
        def network_spec(self):return f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22'
        def start(self,seed):calls.append('start');self.proc=Proc()
        def stop(self):calls.append('stop');self.proc.dead=True
    class SSH:
        def __init__(self,c,work,port,key):self.work=work
        def wait_ssh(self,vm,seconds):calls.append('ssh')
        def copy(self,p,n):pass
        def copy_offline_asset(self,p,n):pass
        def ssh(self,*args,**kwargs):calls.append('guest');return SimpleNamespace(returncode=0)
        def receive(self,name,path):
            req=json.loads((self.work/'request.json').read_text());guest={**{k:req[k] for k in ('bundle_sha256','pip_sha256','lock_sha256','python_version')},'status':'PYTHON_OFFLINE_LOCK_VERIFIED','wheel_count':1,'offline_resolution_verified':True,'offline_install_verified':mode!='bad_receipt','host_gateway_denied':True,'pip_preexec_handoffs':2,'workspace_mib':1536,'repository_code_executed':False};path.write_text(json.dumps(guest))
    module('qemu',QemuWorker=VM,SSHControl=SSH,verify_host_toolchain=lambda c:{'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'},make_ssh_key=lambda c,w:(w/'key','pub'),make_nocloud_iso=lambda *a,**kw:tmp_path/'seed.iso',_free_loopback_port=lambda:32123,effective_vm_vcpus=lambda *a:1)
    def sentinel(sock,stop,contact):
        import socket
        while not stop.is_set():
            try:c,_=sock.accept()
            except socket.timeout:continue
            except OSError:return
            with c:contact.set()
    module('d5d_boundary_acceptance',_sentinel_loop=sentinel)
    monkeypatch.setattr(w,'inspect_volume',lambda path:{'free_bytes':30*s.GIB,'volume_capacity_bounded':mode!='bounded_volume_missing'})
    monkeypatch.setattr(s.shutil,'disk_usage',lambda root:SimpleNamespace(free=0 if mode=='watchdog_trip' else 100*s.GIB))
    kwargs=dict(source=source,lock=lock,wheelhouse=wheelhouse,vm_config=cfg,pip_wheel=pip,pip_sha256=hashlib.sha256(pip.read_bytes()).hexdigest(),require_bounded_volume=mode=='bounded_volume_missing')
    if mode=='no_approval':
        with pytest.raises(ValueError,match='APPROVAL_REQUIRED'):w.assemble_reviewed(**kwargs)
        assert not calls;return
    result=w.assemble_reviewed(**kwargs);assert result['runtime_permission'] is False
    if mode=='bounded_volume_missing':assert 'start' not in calls and result['error_code']=='SEPARATE_BOUNDED_VOLUME_REQUIRED';return
    assert calls.index('stop')<calls.index('close')
    if mode=='success':assert result['status']=='PYTHON_WHEELHOUSE_VERIFIED_REVIEWED_NOT_AUTHORIZED' and result['cleanup']==CLEAN and result['storage_watchdog']['samples']>=1
    else:assert result['status']=='PYTHON_OFFLINE_ASSEMBLY_FAILED' and 'artifact' not in result

@pytest.mark.parametrize('mode',['success','wrong_digest','wrong_manifest','extra_member'])
def test_reuse_npm_bundle(tmp_path,mode):
    from projectiq_executioner.phase8c_final.reuse import copy_verified_npm
    data=b'fixture';sha=hashlib.sha512(data).hexdigest();name=sha+'.tgz';inputs={'manifest':{'name':'fixture'},'lock':{'lockfileVersion':3},'downloads':[{'filename':name,'sha512':sha}],'platform_skipped':0};archive=tmp_path/'original.zip';dest=tmp_path/'copy.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('inputs/package.json',json.dumps({} if mode=='wrong_manifest' else inputs['manifest']));z.writestr('inputs/package-lock.json',json.dumps(inputs['lock']));z.writestr('inputs/tarballs/'+name,b'wrong' if mode=='wrong_digest' else data)
        if mode=='extra_member':z.writestr('unexpected',b'x')
    if mode=='success':assert copy_verified_npm(archive,dest,inputs)['new_registry_downloads']==0 and dest.read_bytes()==archive.read_bytes()
    else:
        with pytest.raises(ValueError):copy_verified_npm(archive,dest,inputs)
        assert not dest.exists()
    assert archive.exists()


def test_static_dispatch_and_unapproved_runtime(tmp_path,monkeypatch):
    import types,contextlib
    from projectiq_executioner.orchestration import adapters,contracts
    source={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40};root=tmp_path/'source';root.mkdir();(root/'index.html').write_text('<html><body>demo</body></html>')
    @contextlib.contextmanager
    def pinned(url,ref):yield source,root
    monkeypatch.setattr(adapters,'pinned_checkout',pinned);monkeypatch.setattr(contracts,'validate_request',lambda v:v);monkeypatch.setattr(contracts,'source_matches_url',lambda *a:True);monkeypatch.setattr(d,'source_approved',lambda *a:False)
    calls=[];fake=types.ModuleType('projectiq_executioner.orchestration.core')
    class Op:
        def __init__(self,**kw):assert not kw
    def evaluate(req,output_dir,operator):
        assert req['mode']=='STATIC' and req['max_vm_launches']==0 and req['ref']==source['commit_sha'];calls.append(req);output_dir.mkdir();(output_dir/'executioner_result.json').write_text('{}');return {'status':'COMPLETED','source':source,'vm_attempts_scheduled':0}
    fake.evaluate=evaluate;fake.OperatorContext=Op;monkeypatch.setitem(sys.modules,fake.__name__,fake)
    result=d.run('https://github.com/Owner/Repo',None,tmp_path/'static');assert result['runtime']['route']=='STATIC_ONLY' and len(calls)==1
    cfg=tmp_path/'config.json';cfg.write_text('{}');result=d.run('https://github.com/Owner/Repo',None,tmp_path/'blocked',run_reviewed=True,vm_config=cfg,static=False)
    assert result['runtime']['status']=='BLOCKED' and result['automatic_runtime_authorized'] is False and len(calls)==1

@pytest.mark.parametrize('state,verified,cleanup,count,passed',[('PASS',True,CLEAN,1,True),('FAIL',True,CLEAN,1,False),('PASS',False,CLEAN,1,False),('PASS',True,'UNVERIFIED',1,False),('PASS',True,CLEAN,0,False)])
def test_reviewed_readiness_requires_verified_suite(state,verified,cleanup,count,passed):
    assert d.readiness_pass({'status':'COMPLETED_WITH_LIMITATIONS','vm_attempts_scheduled':count,'suites':[{'kind':'readiness','state':state,'vm_verified':verified,'cleanup':cleanup}]}) is passed

@pytest.mark.parametrize('mode',['success','cleanup_failure','unapproved'])
def test_dispatch_prepared_python_to_reviewed_runtime(tmp_path,monkeypatch,mode):
    import types,contextlib
    from projectiq_executioner.orchestration import adapters,contracts
    from projectiq_executioner.phase2.vm_worker import orchestrator
    source={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40};identity=source['repository']+'@'+source['commit_sha'];root=tmp_path/'source';root.mkdir();(root/'app.py').write_text('from flask import Flask\napp = Flask(__name__)\n@app.route("/")\ndef home(): return "ok"\n');(root/'requirements.txt').write_text('Flask==3.1.1\n')
    @contextlib.contextmanager
    def pinned(url,ref):yield source,root
    monkeypatch.setattr(adapters,'pinned_checkout',pinned);monkeypatch.setattr(contracts,'validate_request',lambda v:v);monkeypatch.setattr(contracts,'source_matches_url',lambda *a:True)
    cfg=tmp_path/'config.json';config={'approved_snapshots':[] if mode=='unapproved' else [identity],'base_image':'private','python_web_offline':{}};cfg.write_text(json.dumps(config));original=checked_file(cfg,100000)
    monkeypatch.setattr(orchestrator,'load_config',lambda path:json.loads(Path(path).read_text()))
    pip=tmp_path/'pip-25.2-py3-none-any.whl';pip.write_bytes(b'pip');pip_sha=hashlib.sha256(b'pip').hexdigest();calls=[]
    def download(inputs,out,version):
        assert inputs['pins']=={'flask':'3.1.1'} and version=='3.12.3';calls.append('fetch');bundle=out/'python-wheelhouse.zip';bundle.write_bytes(b'wheelhouse');return bundle,{'lock_sha256':'a'*64},{'wheel_count':1}
    monkeypatch.setattr(d,'_python_download',download)
    def assemble(**kw):
        calls.append('assemble');assert kw['source']==source and kw['pip_sha256']==pip_sha
        return {'status':'PYTHON_WHEELHOUSE_VERIFIED_REVIEWED_NOT_AUTHORIZED' if mode=='success' else 'PYTHON_OFFLINE_ASSEMBLY_FAILED','cleanup':CLEAN if mode=='success' else 'UNVERIFIED','vm_start_attempts':1,'runtime_permission':False,'artifact':checked_file(kw['wheelhouse'],120000000),'guest':{'offline_resolution_verified':True,'offline_install_verified':True,'pip_preexec_handoffs':2}}
    monkeypatch.setattr(w,'assemble_reviewed',assemble)
    fake=types.ModuleType('projectiq_executioner.orchestration.core');temp_configs=[]
    class Op:
        def __init__(self,**kw):self.vm_config=kw['vm_config']
    def evaluate(req,output_dir,operator):
        calls.append('runtime');assert req['mode']=='REVIEWED' and req['ref']==source['commit_sha'] and req['max_vm_launches']==1 and req['run_readiness']=='YES'
        temp_configs.append(operator.vm_config);assert operator.vm_config!=cfg
        current=json.loads(operator.vm_config.read_text());assert current['approved_snapshots']==[identity] and current['python_web_offline'][identity]['wheelhouse_sha256']==hashlib.sha256(b'wheelhouse').hexdigest()
        assert current['python_web_offline'][identity]['requirements_sha256']==hashlib.sha256((root/'requirements.txt').read_bytes()).hexdigest()
        output_dir.mkdir();(output_dir/'executioner_result.json').write_text('{}')
        return {'status':'COMPLETED_WITH_LIMITATIONS','source':source,'vm_attempts_scheduled':1,'suites':[{'kind':'readiness','state':'PASS','vm_verified':True,'cleanup':CLEAN}]}
    fake.evaluate=evaluate;fake.OperatorContext=Op;monkeypatch.setitem(sys.modules,fake.__name__,fake)
    result=d.run('https://github.com/Owner/Repo',source['commit_sha'],tmp_path/'result',fetch=True,assemble=True,run_reviewed=True,vm_config=cfg,pip_wheel=pip,pip_sha256=pip_sha,static=False)
    assert checked_file(cfg,100000)==original and result['automatic_runtime_authorized'] is False
    if mode=='success':assert calls==['fetch','assemble','runtime'] and result['runtime']['reviewed_readiness_verified'] is True and not temp_configs[0].exists()
    elif mode=='cleanup_failure':assert calls==['fetch','assemble'] and result['runtime']['status']=='BLOCKED' and result['status']=='FAILED_OR_BLOCKED'
    else:assert calls==['fetch'] and result['runtime']['status']=='BLOCKED' and result['preparation']['assembly_status']=='BLOCKED_EXISTING_REVIEWED_GATE_REQUIRED'
