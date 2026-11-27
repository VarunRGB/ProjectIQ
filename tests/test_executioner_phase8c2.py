import base64,hashlib,json,subprocess,shutil,tarfile,zipfile,time
from pathlib import Path
import pytest
from projectiq_executioner.phase8c2 import broker as b

DATA=b'not executed archive bytes'
SRI='sha512-'+base64.b64encode(hashlib.sha512(DATA).digest()).decode()

def fixture():
    p={'name':'probe','version':'1.0.0','scripts':{'build':'do not run'},'dependencies':{'tiny':'1.0.0'}}
    l={'name':'probe','version':'1.0.0','lockfileVersion':3,'packages':{'':{k:v for k,v in p.items() if k!='scripts'},'node_modules/tiny':{'version':'1.0.0','resolved':b.exact_url('tiny','1.0.0'),'integrity':SRI}}}
    return p,l

def test_compile_sanitizes_and_is_repeatable():
    p,l=fixture();x=b.compile_inputs(p,l)
    assert 'scripts' not in x['manifest'];assert len(x['downloads'])==1
    assert b.compile_inputs(x['manifest'],x['lock'])==x

@pytest.mark.parametrize('url',['http://registry.npmjs.org/tiny/-/tiny-1.0.0.tgz','https://evil.test/tiny/-/tiny-1.0.0.tgz','https://registry.npmjs.org/tiny/-/tiny-1.0.0.tgz?x=1','https://registry.npmjs.org/tiny/../tiny/-/tiny-1.0.0.tgz','https://registry.npmjs.org/other/-/other-1.0.0.tgz','https://registry.npmjs.org/tiny/-/tiny-2.0.0.tgz','https://registry.npmjs.org@127.0.0.1/tiny/-/tiny-1.0.0.tgz'])
def test_bad_urls(url):
    p,l=fixture();l['packages']['node_modules/tiny']['resolved']=url
    with pytest.raises(ValueError):b.compile_inputs(p,l)

@pytest.mark.parametrize('name',['../foo','@../foo','foo/bar','foo%2fbar','foo?x','foo\\bar'])
def test_bad_names(name):
    with pytest.raises(ValueError):b.exact_url(name,'1.0.0')

@pytest.mark.parametrize('ip',['127.0.0.1','10.0.0.1','172.16.0.1','192.168.1.1','169.254.169.254','0.0.0.0','224.0.0.1','240.0.0.1','100.64.0.1','::1','not-an-ip'])
def test_block_nonpublic(ip):assert not b.public_address(ip)

def test_public():assert b.public_address('1.1.1.1')

def test_optional_target_filter():
    p,l=fixture();l['packages']['node_modules/other']={'version':'1.0.0','resolved':b.exact_url('other','1.0.0'),'integrity':SRI,'optional':True,'os':['win32']}
    x=b.compile_inputs(p,l);assert x['platform_skipped']==1 and len(x['downloads'])==1
    l['packages']['node_modules/other']['optional']=False
    with pytest.raises(ValueError,match='MANDATORY_PACKAGE_PLATFORM_MISMATCH'):b.compile_inputs(p,l)

class Response:
    def __init__(self,body,status=200,headers=None):self.body=body;self.status=status;self.headers=headers or {}
    def getheader(self,k,default=None):return self.headers.get(k,default)
    def read(self,n):v=self.body[:n];self.body=self.body[n:];return v
class Connection:
    def __init__(self,res):self.res=res;self.closed=False
    def request(self,method,path,headers):assert method=='GET';assert path.startswith('/tiny/-/');assert headers['Accept-Encoding']=='identity'
    def getresponse(self):return self.res
    def close(self):self.closed=True

def test_download_valid(tmp_path):
    p,l=fixture();job=b.compile_inputs(p,l)['downloads'][0];c=Connection(Response(DATA,headers={'Content-Length':str(len(DATA))}))
    assert b.fetch_one(job,tmp_path/'archive',100,time.monotonic()+5,lambda:c)==len(DATA);assert c.closed

@pytest.mark.parametrize('body,status,headers,cap',[(DATA,302,{},100),(DATA,200,{'Content-Encoding':'gzip'},100),(DATA,200,{'Content-Length':'200'},100),(DATA,200,{},2),(b'wrong',200,{},100),(b'',200,{},100),(DATA,200,{'Content-Length':'1'},100)])
def test_download_refused(tmp_path,body,status,headers,cap):
    p,l=fixture();job=b.compile_inputs(p,l)['downloads'][0];c=Connection(Response(body,status,headers))
    with pytest.raises(ValueError):b.fetch_one(job,tmp_path/'archive',cap,time.monotonic()+5,lambda:c)
    assert c.closed

def test_bundle_cleanup(tmp_path):
    p,l=fixture();x=b.compile_inputs(p,l)
    def fail(job,path,*args):path.write_bytes(DATA);raise ValueError('failed')
    with pytest.raises(ValueError):b.build_bundle(x,tmp_path/'out.zip',fetch=fail)
    assert list(tmp_path.iterdir())==[]

def test_bundle_only_manifest_and_tarballs(tmp_path):
    p,l=fixture();x=b.compile_inputs(p,l)
    def fetch(job,path,*args):path.write_bytes(DATA);return len(DATA)
    out=tmp_path/'out.zip';stats=b.build_bundle(x,out,fetch=fetch)
    assert stats['download_count']==1
    with zipfile.ZipFile(out) as z:
        assert len(z.namelist())==3;assert 'scripts' not in json.loads(z.read('inputs/package.json'))
        assert z.read('inputs/tarballs/'+x['downloads'][0]['filename'])==DATA
    with pytest.raises(ValueError):b.build_bundle(x,out,fetch=fetch)

@pytest.mark.skipif(shutil.which('npm') is None or __import__('os').name=='nt',reason='Optional trusted local npm executable not installed')
def test_real_local_npm_offline_cache_reuse(tmp_path):
    # No internet and no external project: construct a small inert test tarball.
    import os
    root=tmp_path/'src';root.mkdir();(root/'package.json').write_text(json.dumps({'name':'tiny','version':'1.0.0','scripts':{'install':'exit 99'}}))
    tar=tmp_path/'tiny.tgz'
    with tarfile.open(tar,'w:gz') as z:z.add(root,arcname='package')
    p,l=fixture();l['packages']['node_modules/tiny']['integrity']='sha512-'+base64.b64encode(hashlib.sha512(tar.read_bytes()).digest()).decode();x=b.compile_inputs(p,l)
    cache=tmp_path/'cache';env={**os.environ,'NPM_CONFIG_USERCONFIG':str(tmp_path/'user.npmrc'),'NPM_CONFIG_GLOBALCONFIG':str(tmp_path/'global.npmrc'),'NPM_CONFIG_UPDATE_NOTIFIER':'false','NPM_CONFIG_REGISTRY':'https://registry.npmjs.org/'}
    for i in range(2):
        work=tmp_path/('work'+str(i));work.mkdir();lock=json.loads(json.dumps(x['lock']))
        if i==0:lock['packages']['node_modules/tiny']['resolved']='file:'+str(tar)
        (work/'package.json').write_text(json.dumps(x['manifest']));(work/'package-lock.json').write_text(json.dumps(lock))
        r=subprocess.run([shutil.which('npm'),'ci','--offline','--ignore-scripts','--no-audit','--no-fund','--cache',str(cache)],cwd=work,env=env,capture_output=True,timeout=30,shell=False)
        assert r.returncode==0,r.stderr.decode(errors='replace');assert (work/'node_modules/tiny/package.json').is_file()

@pytest.mark.parametrize('spec',['file:../../outside','git+https://example.org/repo','https://example.org/p.tgz','npm:other@1.0.0'])
def test_nonregistry_transitive_refused(spec):
    p,l=fixture();l['packages']['node_modules/tiny']['dependencies']={'another':spec}
    with pytest.raises(ValueError,match='NONREGISTRY_TRANSITIVE_SPEC'):b.compile_inputs(p,l)

@pytest.mark.parametrize('mode',['success','gateway','first_fail','second_fail','handoff_missing'])
def test_guest_assembly_contract(tmp_path,mode):
    from types import SimpleNamespace
    from projectiq_executioner.phase8c2.guest import assemble
    base=tmp_path/'controller';base.mkdir();work=tmp_path/'workspace';work.mkdir();p,l=fixture();x=b.compile_inputs(p,l)
    def fetch(job,path,*args):path.write_bytes(DATA);return len(DATA)
    b.build_bundle(x,base/'npm_cache.zip',fetch=fetch);(base/'node_runtime.tar.xz').write_bytes(b'node')
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    req={'bundle_sha256':sha(base/'npm_cache.zip'),'node_sha256':sha(base/'node_runtime.tar.xz'),'node_version':'v24.22.3','sentinel_port':54321}
    calls=[];handoffs=[];shared=[]
    def stage(meta,base):
        assert meta=={'workspace_disk_mib':1536}
        for n in ('npm_cache.zip','node_runtime.tar.xz'):shutil.move(base/n,work/n)
        return work
    def extract(archive,dest,max_total):
        # Fixture-only fake safe_extract, no production use.
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                path=dest/n.split('/',1)[1];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(z.read(n))
        return dest
    def run(args,**kwargs):
        assert kwargs['project_code'] is True;calls.append(args)
        if len(calls)==1:return SimpleNamespace(returncode=int(mode=='gateway'))
        assert kwargs['audit_kind']=='npm_ci' and all(s in args for s in ('--offline','--ignore-scripts','--no-audit','--no-fund'))
        assert kwargs['env']['NPM_CONFIG_OFFLINE']=='true';assert kwargs['env']['NPM_CONFIG_IGNORE_SCRIPTS']=='true'
        assert 'scripts' not in json.loads((kwargs['cwd']/'package.json').read_text())
        lock=json.loads((kwargs['cwd']/'package-lock.json').read_text());resolved=lock['packages']['node_modules/tiny']['resolved']
        if len(calls)==2:assert resolved.startswith('file:');(work/'npm-cache'/'item').write_bytes(DATA)
        else:assert resolved==b.exact_url('tiny','1.0.0');assert kwargs['cwd'].name=='verify-project'
        if mode!='handoff_missing':handoffs.append('npm_ci')
        return SimpleNamespace(returncode=int(mode=='first_fail' and len(calls)==2 or mode=='second_fail' and len(calls)==3))
    g=SimpleNamespace(_verify_execution_identity=lambda x:None,_stage_bounded_workspace=stage,_verify_evaluation_cgroup=lambda x:None,_APP_CGROUP_ACTIVE=False,_APP_BUILD_HANDOFFS=handoffs,_sha256=sha,safe_extract=extract,_share_tree=lambda p,writable:shared.append((p.name,writable)),_node_tar_extract=lambda *a:(work/'node_runtime'/'node',work/'node_runtime'/'npm'),_isolated_npm_config_paths=lambda w:{},_trusted_process=run)
    if mode=='success':
        result=assemble(g,base,req);assert result['offline_reuse'] is True and result['npm_ci_preexec_handoffs']==2 and result['repository_scripts_executed'] is False
        assert ('node_runtime',False) in shared and ('inputs',False) in shared
        with zipfile.ZipFile(base/'npm_cache.zip') as z:assert z.namelist()==['npm-cache/item']
    else:
        with pytest.raises(ValueError):assemble(g,base,req)
        assert not (base/'npm_cache.zip').exists()

@pytest.mark.parametrize('mode',['success','cleanup_failure','bad_receipt','no_approval'])
def test_host_worker_lifecycle_simulated(tmp_path,monkeypatch,mode):
    import sys,types,socket,threading
    from projectiq_executioner.phase8c2 import worker as w
    from projectiq_executioner.phase8b.bridge import CLEAN
    prefix='projectiq_executioner.phase2.vm_worker.'
    out=tmp_path/'out';out.mkdir();bundle=tmp_path/'input.zip';bundle.write_bytes(DATA)
    node=tmp_path/'node-v24.22.3-linux-x64.tar.xz';node.write_bytes(b'node')
    source={'repository':'Owner/Repo','commit_sha':'a'*40};identity=source['repository']+'@'+source['commit_sha']
    calls=[];state={}
    def module(name,**attrs):
        # Patch sys.modules and the package attribute for imports from package.
        m=types.ModuleType(prefix+name);m.__dict__.update(attrs);monkeypatch.setitem(sys.modules,prefix+name,m)
        import projectiq_executioner.phase2.vm_worker as pkg
        monkeypatch.setattr(pkg,name,m,raising=False)
    config={'approved_snapshots':[] if mode=='no_approval' else [identity],'accelerator':'whpx'}
    module('orchestrator',load_config=lambda p:config)
    module('d5d_host_acl_audit',SUCCESS='ACL_PASS',audit=lambda p:{'status':'ACL_PASS','checks':{str(i):'PASS' for i in range(5)}})
    class Managed:
        cleanup_verified=False
        def __init__(self,c):pass
        def open(self):
            calls.append('open');self.folder=tmp_path/'managed';self.folder.mkdir();return self.folder
        def mark_launching(self):calls.append('launching')
        def mark_launched(self,p):calls.append('launched')
        def mark_booted(self):calls.append('booted')
        def mark_stopped(self):calls.append('stopped')
        def close(self):
            calls.append('close');self.cleanup_verified=mode!='cleanup_failure'
            if self.cleanup_verified:shutil.rmtree(self.folder)
    module('d5c_runtime_workspace',ManagedRuntimeRun=Managed)
    class Proc:
        dead=False
        def poll(self):return 0 if self.dead else None
        def kill(self):self.dead=True
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
        def ssh(self,*args,**kwargs):
            calls.append('guest');return types.SimpleNamespace(returncode=0)
        def receive(self,name,path):
            req=json.loads((self.work/'request.json').read_text());p=self.work/'fixture-cache'
            with zipfile.ZipFile(p,'w') as z:z.writestr('npm-cache/item',DATA)
            state['cache']=p.read_bytes();p.unlink()
            guest={'status':'OFFLINE_CACHE_ASSEMBLED','bundle_sha256':req['bundle_sha256'],'node_sha256':req['node_sha256'],'npm_cache_sha256':hashlib.sha256(state['cache']).hexdigest(),'offline_reuse':True,'host_gateway_denied':True,'npm_ci_preexec_handoffs':1 if mode=='bad_receipt' else 2,'repository_scripts_executed':False,'workspace_mib':1536}
            path.write_text(json.dumps(guest))
        def receive_offline_cache(self,p):p.write_bytes(state['cache'])
    module('qemu',QemuWorker=VM,SSHControl=SSH,verify_host_toolchain=lambda c:{'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'},make_ssh_key=lambda c,w:(w/'key','pub'),make_nocloud_iso=lambda *a,**kw:tmp_path/'seed.iso',_free_loopback_port=lambda:32123,effective_vm_vcpus=lambda *a:1)
    def sentinel(sock,stop,contact):
        while not stop.is_set():
            try:c,_=sock.accept()
            except socket.timeout:continue
            except OSError:return
            with c:contact.set()
    module('d5d_boundary_acceptance',_sentinel_loop=sentinel)
    monkeypatch.setattr(w.shutil,'disk_usage',lambda p:types.SimpleNamespace(free=100*1024**3))
    kwargs=dict(source=source,bundle=bundle,out=out,vm_config=tmp_path/'config.json',node_tarball=node,node_sha256=hashlib.sha256(node.read_bytes()).hexdigest())
    if mode=='no_approval':
        with pytest.raises(ValueError,match='EXISTING_OPERATOR_APPROVAL_REQUIRED'):w.assemble_reviewed(**kwargs)
        assert not calls;return
    result=w.assemble_reviewed(**kwargs)
    assert calls.index('stop')<calls.index('close');assert result['runtime_permission'] is False
    if mode=='success':assert result['status']=='OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED' and result['cleanup']==CLEAN and (out/'npm-cache.zip').is_file()
    else:assert result['status']=='OFFLINE_ASSEMBLY_FAILED' and not (out/'npm-cache.zip').exists() and not (out/'cache.pending').exists()
