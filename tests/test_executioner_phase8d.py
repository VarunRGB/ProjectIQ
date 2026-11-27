"""Offline tests for generalized runtime plans and the reviewed worker contract.
Actual local tools run only tiny operator-created fixtures, never fetched code.
"""
import base64,contextlib,copy,hashlib,json,os,shutil,stat,subprocess,sys,tarfile,time,types,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from projectiq_executioner.phase8d import planner as pl, requirements as rq, versions as vs, resolver as rv, assets as a, dispatch as d, worker as w, guest as g, snapshot as sn, toolchains as tc
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import checked_file,CLEAN
from projectiq_executioner.phase8c2 import broker

SOURCE={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40}

def put(root,files):
    root.mkdir(exist_ok=True,parents=True)
    for n,s in files.items():
        p=root/n;p.parent.mkdir(parents=True,exist_ok=True)
        if isinstance(s,bytes):p.write_bytes(s)
        else:p.write_text(s,encoding='utf-8')
    return root


def plan(root,**kwargs):return pl.compile_runtime(root,source=SOURCE,**kwargs)[1]

def row(root,**kwargs):
    rows=plan(root,**kwargs)['components'];assert len(rows)==1;return rows[0]


def node_files(deps,*,extra=None,files=None,lock=True):
    pkg={'name':'demo','version':'1.0.0','dependencies':{n:'1.0.0' for n in deps},**(extra or {})}
    result={'package.json':json.dumps(pkg),**(files or {})}
    if lock:
        packages={'':{k:v for k,v in pkg.items() if k in ('name','version','dependencies','devDependencies','optionalDependencies','peerDependencies')}}
        for n in set(deps)|set(pkg.get('devDependencies',{})):
            packages['node_modules/'+n]={'version':'1.0.0','resolved':broker.exact_url(n,'1.0.0'),'integrity':'sha512-'+base64.b64encode(hashlib.sha512(n.encode()).digest()).decode()}
        result['package-lock.json']=json.dumps({'name':'demo','version':'1.0.0','lockfileVersion':3,'packages':packages})
    return result


@pytest.mark.parametrize('v,c,ok',[
 ('v24.22.3','>=20.9.0',True),('v24.22.3','^20.0.0',False),('v20.12.0','^20',True),('v0.2.9','^0.2.3',True),('v0.3.0','^0.2.3',False),('v0.0.2','^0.0.1',False),('v20.4.1','~20.4',True),('v20.5.0','~20.4',False),('v24.0.0','18.x || >= 22',True),('v22.0.0','18 - 20',False),('v20.9.1','18 - 20',True),('v20.9.0','>=20.9 <21',True),('v21.0.0','<=20',False),('v20.0.0','>19',True),('v20.1.0','20.*',True),('v24.22.3','*',True),('v24.22.3','lts/*',None),('v24.22.3','^22.0.0-beta.1',None),('v24.22.3','>=20 ||',None),('v24.22.3','22.x.1',None),('v24.22.3','latest',None),('v24.22.3',None,None)])
def test_node_constraint(v,c,ok):assert vs.node_constraint(v,c) is ok

@pytest.mark.parametrize('v,c,pin,ok',[('3.12.3','>=3.10,<3.13',False,True),('3.12.3','>=3.13',False,False),('3.12.3','3.12',True,True),('3.12.3','3.11.7',True,False),('3.12.3','latest',True,None)])
def test_python_constraint(v,c,pin,ok):assert vs.python_constraint(v,c,file_pin=pin) is ok


def test_normalize_ranges_markers():
    assert rq.normalize(['Flask>=2.3','flask<4','numpy>=1.26; python_version >= "3.12"','winonly==1; sys_platform == "win32"','# comment'],'3.12.3')=={'flask':'<4,>=2.3','numpy':'>=1.26'}
@pytest.mark.parametrize('req',['-r more.txt','--index-url https://bad.invalid','foo @ https://bad.invalid/foo.whl','foo[async]>=1','foo===weird','foo; platform_release=="1"','foo; extra=="dev"'])
def test_unsupported_python_requirements(req):
    with pytest.raises(ValueError):rq.normalize([req],'3.12.3')


def test_inference_aliases_and_local_modules():
    deps,unknown,syntax=rq.infer({'app.py':'import os,sys\nfrom PIL import Image\nimport sklearn\nimport local\nfrom . import ignored','local.py':'import requests','tests/test_x.py':'import weird_unknown'})
    assert deps=={'pillow':'','requests':'','scikit-learn':''} and not unknown and not syntax

def test_inference_refuses_unknown_dynamic():
    deps,unknown,_=rq.infer({'app.py':'import something_unrecognized\nimport importlib\nimportlib.import_module("foo")'})
    assert deps=={} and unknown==['DYNAMIC_IMPORT','something_unrecognized']

@pytest.mark.parametrize('deps,adapter,more',[
 (['vite','react'],'NODE_VITE',{'index.html':'<html>ok</html>'}),(['next','react','react-dom'],'NODE_NEXT',{'app/page.js':'export default function Page(){return null}'}),(['react-scripts','react'],'NODE_CRA',{'src/index.js':'console.log(1)'}),(['astro'],'NODE_ASTRO',{'src/pages/index.astro':'<h1>ok</h1>'}),(['nuxt'],'NODE_NUXT',{'app.vue':'<template>ok</template>'}),(['@sveltejs/kit','@sveltejs/adapter-node','vite'],'NODE_SVELTEKIT',{'src/routes/+page.svelte':'<h1>ok</h1>'}),(['@angular/core','@angular/cli'],'NODE_ANGULAR',{'angular.json':json.dumps({'projects':{'app':{'projectType':'application'}}})}),(['express'],'NODE_HTTP',{'server.js':'const e=require("express");e().listen(8765)'}),(['fastify'],'NODE_HTTP',{'server.js':'const f=require("fastify");f().listen({port:8765})'}),(['koa'],'NODE_HTTP',{'server.js':'const K=require("koa");new K().listen(8765)'})])
def test_node_adapter_plans(tmp_path,deps,adapter,more):
    r=row(put(tmp_path,node_files(deps,files=more)));assert r['adapter']==adapter and r['status']=='REVIEWED_CANDIDATE',r;assert r['dependency']['kind']=='NPM' and r['permission'] is False


def test_select_compatible_provisioned_node(tmp_path):
    root=put(tmp_path,node_files(['vite'],extra={'engines':{'node':'^20.0.0'}},files={'index.html':'<html></html>'}));r=row(root,node_versions=['v24.22.3','v20.19.0','v22.16.0']);assert r['toolchain']=='v20.19.0' and r['status']=='REVIEWED_CANDIDATE'
    assert 'PROVISIONED_NODE_VERSION_INCOMPATIBLE_OR_UNRESOLVED' in row(root)['blockers']


def test_locked_engine_selects_compatible_runtime(tmp_path):
    files=node_files(['vite'],files={'index.html':'<html></html>'});lock=json.loads(files['package-lock.json']);lock['packages']['node_modules/vite']['engines']={'node':'>=20 <23'};files['package-lock.json']=json.dumps(lock)
    r=row(put(tmp_path,files),node_versions=['v24.22.3','v22.16.0']);assert r['toolchain']=='v22.16.0' and not r['blockers']


def test_npm_version_constraints_preserved(tmp_path):
    r=row(put(tmp_path,node_files(['vite'],extra={'packageManager':'npm@10.9.2','engines':{'npm':'>=10'}},files={'index.html':'<html></html>'})));assert r['dependency']['npm_constraints']==['>=10','10.9.2']

@pytest.mark.parametrize('mutation,blocker',[
 ({'.npmrc':'registry=https://bad.invalid'},'NPM_CONFIG_REQUIRES_REVIEW'),({'pnpm-lock.yaml':'lockfileVersion: 9'},'NPM_CONFIG_OR_OTHER_MANAGER_REQUIRES_REVIEW'),({'.nvmrc':'lts/*'},'PROVISIONED_NODE_VERSION_INCOMPATIBLE_OR_UNRESOLVED'),({'Dockerfile':'FROM node:20'},'DEPLOYMENT_OR_SUBMODULE_POLICY_UNRESOLVED')])
def test_node_negative_inputs(tmp_path,mutation,blocker):
    r=row(put(tmp_path,node_files(['vite'],files={'index.html':'<html></html>',**mutation})));assert blocker in r['blockers'] and r['status']=='BLOCKED'


def test_node_no_lock_blocks(tmp_path):
    r=row(put(tmp_path,node_files(['next'],lock=False,files={'app/page.js':'export default function Page(){return null}'})));assert 'NPM_LOCK_REQUIRED' in r['blockers']

def test_node_unapproved_script_is_not_executed(tmp_path):
    r=row(put(tmp_path,node_files(['express'],extra={'scripts':{'start':'node server.js && curl bad.invalid'}},files={'server.js':'require("express")().listen(8765)'})));assert 'NODE_START_COMMAND_UNSUPPORTED' in r['blockers']

def test_node_missing_manifest_import_block(tmp_path):
    r=row(put(tmp_path,{'server.js':'require("express")().listen(8765)'}));assert 'NODE_THIRD_PARTY_IMPORTS_WITHOUT_DECLARATION' in r['blockers']

def test_node_builtin_http(tmp_path):
    r=row(put(tmp_path,{'server.js':'require("node:http").createServer(()=>{}).listen(8765)'}));assert r['adapter']=='NODE_HTTP' and r['dependency']['kind']=='NONE' and not r['blockers']


def test_external_service_blocks_next(tmp_path):
    r=row(put(tmp_path,node_files(['next','next-cloudinary'],files={'app/page.js':'const API_URL = "https://external.invalid"; export default function Page(){return null}'})));assert r['adapter']=='NODE_NEXT' and 'EXTERNAL_OR_DATABASE_SERVICE_UNRESOLVED' in r['blockers']

@pytest.mark.parametrize('files,adapter,entry,dependency',[
 ({'src/web.py':'from flask import Flask as F\nweb=F(__name__)','requirements.txt':'Flask>=3,<4'},'PY_FLASK','src/web.py','PYTHON'),
 ({'service.py':'from fastapi import FastAPI\napi=FastAPI()','pyproject.toml':'[project]\nname="demo"\nversion="1.0"\ndependencies=["fastapi>=0.100","uvicorn>=0.20"]'},'PY_ASGI','service.py','PYTHON'),
 ({'manage.py':'import django','requirements.txt':'django>=4,<6'},'PY_DJANGO','manage.py','PYTHON'),
 ({'dashboard.py':'import streamlit as st\nst.write("ok")','requirements.txt':'streamlit>=1'},'PY_STREAMLIT','dashboard.py','PYTHON'),
 ({'main.py':'if __name__ == "__main__":\n print("ok")'},'PY_CLI','main.py','NONE'),
 ({'app.py':'from flask import Flask, jsonify\nweb=Flask(__name__)\nthing=jsonify("x")','requirements.txt':'Flask==3.1.1'},'PY_FLASK','app.py','PYTHON')])
def test_python_adapter_plans(tmp_path,files,adapter,entry,dependency):
    r=row(put(tmp_path,files));assert r['adapter']==adapter and r['entrypoint']==entry and r['dependency']['kind']==dependency and not r['blockers'],r


def test_inferred_dependency_optin(tmp_path):
    root=put(tmp_path,{'app.py':'from flask import Flask\napp=Flask(__name__)\nimport requests'})
    assert 'INFERRED_DEPENDENCIES_REQUIRE_OPERATOR_OPT_IN' in row(root)['blockers'];r=row(root,allow_inferred=True);assert not r['blockers'] and r['dependency']['requirements']=={'flask':'','requests':''}


def test_asgi_server_inferred_only_with_optin(tmp_path):
    root=put(tmp_path,{'main.py':'from fastapi import FastAPI\napp=FastAPI()','requirements.txt':'fastapi>=1'})
    assert 'ASGI_SERVER_DEPENDENCY_UNDECLARED' in row(root)['blockers'];r=row(root,allow_inferred=True);assert r['dependency']['requirements']['uvicorn']=='' and not r['blockers']


def test_ambiguous_python_entrypoint(tmp_path):
    r=row(put(tmp_path,{'app.py':'from flask import Flask\na=Flask(__name__)\nb=Flask(__name__)','requirements.txt':'Flask==3.1.1'}));assert 'PYTHON_ENTRYPOINT_AMBIGUOUS' in r['blockers']


def test_python_runtime_pin_incompatible(tmp_path):
    r=row(put(tmp_path,{'app.py':'from flask import Flask\na=Flask(__name__)','requirements.txt':'Flask==3.1.1','.python-version':'3.11.9'}));assert 'PROVISIONED_PYTHON_VERSION_INCOMPATIBLE_OR_UNRESOLVED' in r['blockers']

@pytest.mark.parametrize('files,adapter,optional',[
 ({'index.html':'<html></html>'},'STATIC_HTTP',False),({'index.php':'<?php echo "ok"; ?>'},'PHP_HTTP',False),({'main.c':'int main(){return 0;}'},'NATIVE_C',True),({'main.cpp':'int main(){return 0;}'},'NATIVE_CPP',True),({'Main.java':'class Main {public static void main(String[] a){}}'},'JAVA_SINGLE',True),({'main.go':'package main\nfunc main() {}'},'GO_SINGLE',True),({'main.rs':'fn main() {}'},'RUST_SINGLE',True)])
def test_other_adapters(tmp_path,files,adapter,optional):
    r=row(put(tmp_path,files));assert r['adapter']==adapter and r['optional_toolchain'] is optional and not r['blockers'],r


def test_module_resolver_not_faked(tmp_path):
    r=row(put(tmp_path,{'go.mod':'module demo\ngo 1.22','main.go':'package main\nfunc main(){}'}));assert r['adapter']=='GO_SINGLE' and 'BUILD_SYSTEM_OR_MODULE_RESOLVER_NOT_IMPLEMENTED' in r['blockers']


def test_independent_components_selection(tmp_path):
    p=plan(put(tmp_path,{'frontend/index.html':'<html></html>','backend/main.py':'if __name__ == "__main__":\n print(1)'}));assert p['composition']=='INDEPENDENT_COMPONENTS_ONLY_RELATIONSHIPS_UNVERIFIED';assert len(d.select_components(p,all_independent=True))==2;assert d.select_components(p,'backend')[0]['adapter']=='PY_CLI'
    with pytest.raises(ValueError):d.select_components(p)


def test_snapshot_bind_and_filter(tmp_path):
    root=put(tmp_path/'src',{'index.html':'<html></html>','asset.txt':'a','.env':'SECRET=private','node_modules/hidden.js':'bad','key.pem':'secret'});p=plan(root);dest=tmp_path/'snapshot.zip';receipt=sn.make_snapshot(root,dest,p['snapshot_inspection']);assert receipt['scope']=='FILTERED_8A_INVENTORY'
    with zipfile.ZipFile(dest) as z:assert set(z.namelist())=={'source/index.html','source/asset.txt'}
    (root/'index.html').write_text('changed')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):sn.make_snapshot(root,tmp_path/'changed.zip',p['snapshot_inspection'])


def wheel(name,version,data=b'x'):
    fn=f'{name}-{version}-py3-none-any.whl';return {'filename':fn,'url':'https://files.pythonhosted.org/packages/aa/bb/'+'c'*60+'/'+fn,'digests':{'sha256':hashlib.sha256(data).hexdigest()},'size':len(data),'packagetype':'bdist_wheel','yanked':False}
class Metadata:
    def __init__(self,data):self.data=data;self.calls=[]
    def get(self,n,v=None):
        self.calls.append((n,v));row=self.data[n]
        return {'releases':{x:[wheel(n,x)] for x in row}} if v is None else {'info':{'name':n,'version':v,'requires_dist':row[v]},'urls':[wheel(n,v)]}


def test_ranged_solver_backtracks_direct_versions():
    m=Metadata({'app':{'2.0':['dep>=2'],'1.0':['dep<2']},'dep':{'1.0':[],'2.0':[]}});lock=rv.resolve_requirements({'app':'>=1','dep':'<2'},'3.12.3',m);assert lock['direct_pins']=={'app':'1.0','dep':'1.0'};assert lock['direct_requirements']=={'app':'>=1','dep':'<2'};a.validate_lock(lock,lock['direct_requirements'],'3.12.3')


def test_ranged_solver_exact_optimization():
    m=Metadata({'app':{'1.0':['dep>=1; sys_platform == "linux"','unused; sys_platform == "win32"']},'dep':{'1.0':[],'2.0':[]}});lock=rv.resolve_requirements({'app':'==1.0'},'3.12.3',m);assert ('app',None) not in m.calls and all(n!='unused' for n,_ in m.calls);assert {x['name']:x['version'] for x in lock['wheels']}=={'app':'1.0','dep':'2.0'}


def test_ranged_solver_conflict():
    with pytest.raises(ValueError,match='NO_COMPATIBLE'):rv.resolve_requirements({'app':'>=2','dep':'<2'},'3.12.3',Metadata({'app':{'2.0':['dep>=2']},'dep':{'1.0':[],'2.0':[]}}))

@pytest.mark.parametrize('mode',['ok','wrong_hash','extra','wrong_pin','changed_lock'])
def test_reuse_python(tmp_path,mode):
    requirements={'tiny':'>=1,<2'};lock=rv.resolve_requirements(requirements,'3.12.3',Metadata({'tiny':{'1.0':[]}}));prev=tmp_path/'prev';prev.mkdir();dest=tmp_path/'dest';dest.mkdir()
    if mode=='changed_lock':lock['target_python']='3.12.4'
    if mode=='wrong_pin':lock['direct_pins']['tiny']='2.0';lock['lock_sha256']=digest({k:v for k,v in lock.items() if k!='lock_sha256'})
    (prev/'python-lock.json').write_text(json.dumps(lock));f=lock['wheels'][0]['filename']
    with zipfile.ZipFile(prev/'python-wheelhouse.zip','w') as z:
        z.writestr('wheels/'+f,b'y' if mode=='wrong_hash' else b'x')
        if mode=='extra':z.writestr('wheels/extra.whl',b'x')
    if mode=='ok':
        bundle,verified,stats=a.copy_verified_python(prev,dest,requirements,'3.12.3');assert verified==lock and stats['new_registry_downloads']==0 and bundle.is_file()
    else:
        with pytest.raises(ValueError):a.copy_verified_python(prev,dest,requirements,'3.12.3')
        assert not (dest/'python-wheelhouse.zip').exists()


def test_catalog_hash_selection(tmp_path):
    archive=tmp_path/'node-v22.16.0-linux-x64.tar.xz';archive.write_bytes(b'fixture');catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps({'runtimes':[{'version':'v22.16.0','tarball':archive.name,'sha256':hashlib.sha256(b'fixture').hexdigest()}]}));assert tc.load_node_catalog(catalog)['v22.16.0'][0]==archive
    archive.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='NODE_ASSET_MISMATCH'):tc.load_node_catalog(catalog)


def receipt(req):
    result={k:req[k] for k in ('source','component_id','adapter','snapshot_sha256','plan_sha256','dependency_kind')};result.update(dependency_sha256=req.get('bundle_sha256'),status='HTTP_READINESS_OBSERVED',host_gateway_denied=True,workspace_mib=1536,build_handoffs=int(req['adapter'] in w.BUILDS),npm_install_handoffs=int(req['dependency_kind']=='NPM'),pip_install_handoffs=2 if req['dependency_kind']=='PYTHON' else 0,functional_tests_executed=0,server_placement_observed=True,application_uid_listener_observed=True,http={'status':'HTTP_2XX_OBSERVED','path':'/','http_status':200});return result

def request(adapter='NODE_NEXT',dep='NPM'):
    return {'source':SOURCE,'component_id':'component-x','adapter':adapter,'snapshot_sha256':'a'*64,'plan_sha256':'b'*64,'dependency_kind':dep,'bundle_sha256':'c'*64 if dep!='NONE' else None,'probe_path':'/'}

@pytest.mark.parametrize('field,value',[('source',{}),('component_id','other'),('adapter','PY_FLASK'),('snapshot_sha256','bad'),('plan_sha256','bad'),('dependency_sha256','bad'),('host_gateway_denied',False),('workspace_mib',96),('build_handoffs',0),('npm_install_handoffs',True),('functional_tests_executed',1),('server_placement_observed',False),('application_uid_listener_observed',False),('http',{'status':'HTTP_2XX_OBSERVED','path':'/','http_status':302}),('http',{'status':'HTTP_2XX_OBSERVED','path':'/admin/delete','http_status':200})])
def test_receipt_rejects_invalid_evidence(field,value):
    req=request();raw=receipt(req);assert w.validate_receipt(raw,req);raw[field]=value;assert not w.validate_receipt(raw,req)


def test_cli_evidence_not_http_correctness():
    req=request('PY_CLI','NONE');raw=receipt(req);assert not w.validate_receipt(raw,req);raw.update(status='CLI_EXIT_ZERO_OBSERVED',cli_exit_code=0,cli_preexec_handoff=True);assert w.validate_receipt(raw,req);raw['cli_exit_code']=False;assert not w.validate_receipt(raw,req)


def test_payload_is_standalone_trusted_code():
    text=w.trusted_guest_payload();compile(text,'trusted guest','exec');assert b"_piq_verify['assemble']" in text and b"_piq_version['node_constraint']" in text

@pytest.mark.parametrize('kind,package,binname,output',[('NODE_NEXT','next','next',None),('NODE_VITE','vite','vite','dist/index.html'),('NODE_CRA','react-scripts','react-scripts','build/index.html'),('NODE_ASTRO','astro','astro','dist/index.html'),('NODE_NUXT','nuxt','nuxt','.output/server/index.mjs'),('NODE_SVELTEKIT','vite','vite','build/index.js'),('NODE_ANGULAR','@angular/cli','ng','dist/projectiq/browser/index.html')])
def test_fixed_framework_launch_recipes(tmp_path,monkeypatch,kind,package,binname,output):
    root=put(tmp_path/'src',{'node_modules/'+package+'/package.json':json.dumps({'name':package,'bin':{binname:'./tool.js'}}),'node_modules/'+package+'/tool.js':'// synthetic bin'});work=tmp_path/'work';work.mkdir();calls=[]
    def build(fake,cmd,cwd,env,tag,timeout):
        calls.append(cmd);assert cmd[0]=='/trusted/node' and cmd[2]=='build' and tag=='phase8d_build' and 'npm' not in cmd
        if output:put(root,{output:'<html>ok</html>'})
        return True
    monkeypatch.setattr(g,'audited',build);cmd,cli,count=g.launch_recipe(None,work,root,{'adapter':kind,'entrypoint':None},'/trusted/node',{});assert not cli and count==1 and len(calls)==1
    if kind=='NODE_NEXT':assert cmd[-5:]==['start','--hostname','127.0.0.1','--port','8765']
    elif kind in {'NODE_NUXT','NODE_SVELTEKIT'}:assert cmd[0]=='/trusted/node' and Path(cmd[-1]) == root / output
    else:assert 'http.server' in cmd


def test_bin_cannot_escape_package(tmp_path):
    root=put(tmp_path,{'node_modules/vite/package.json':json.dumps({'name':'vite','bin':{'vite':'../../untrusted.js'}}),'untrusted.js':'bad'});
    with pytest.raises(ValueError):g.npm_bin(root,'vite','vite')


def test_python_launch_uses_discovered_module(tmp_path):
    root=put(tmp_path,{'src/web.py':'# fixture'});cmd,cli,count=g.launch_recipe(None,tmp_path,root,{'adapter':'PY_FLASK','entrypoint':'src/web.py','application_symbol':'web'},None,{});assert 'src.web:web' in cmd and '--no-reload' in cmd and not cli and count==0


def test_kernel_listener_uid_table(tmp_path):
    p=tmp_path/'tcp';p.write_text('header\n0: 0100007F:223D 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1001 0 12345\n');assert g.listener_uid(8765,1001,(p,));assert not g.listener_uid(8765,1002,(p,));assert not g.listener_uid(9999,1001,(p,))


@pytest.mark.skipif(os.name=='nt',reason='Optional POSIX synthetic HTTP/UID control')
def test_actual_loopback_http_and_listener_uid(tmp_path):
    import http.server,threading
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302 if self.path=='/' else 200);self.send_header('Location','http://127.0.0.1:9/not-followed');self.end_headers()
        def log_message(self,*a):pass
    with http.server.HTTPServer(('127.0.0.1',0),Handler) as server:
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            proc=SimpleNamespace(poll=lambda:None);result=g.probe_http(proc,('/', '/health'),seconds=2,port=server.server_port);assert result['http_status']==200 and result['path']=='/health';assert g.listener_uid(server.server_port,os.getuid())
        finally:server.shutdown();t.join(2)


@pytest.mark.parametrize('mode',['plan','unapproved','success','cleanup_failure','blocked','budget','config_changed'])
def test_dispatch_lifecycle_and_admission(tmp_path,monkeypatch,mode):
    from projectiq_executioner.orchestration import adapters,contracts
    from projectiq_executioner.phase2.vm_worker import orchestrator
    root=put(tmp_path/'source',{'index.html':'<html>ok</html>'} if mode!='budget' else {'a/index.html':'<html></html>','b/index.html':'<html></html>'})
    if mode=='blocked':put(root,{'Dockerfile':'FROM scratch'})
    @contextlib.contextmanager
    def pinned(url,ref):yield SOURCE,root
    monkeypatch.setattr(adapters,'pinned_checkout',pinned);monkeypatch.setattr(contracts,'validate_request',lambda v:v);monkeypatch.setattr(contracts,'source_matches_url',lambda *a:True)
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'approved_snapshots':[] if mode=='unapproved' else ['Owner/Repo@'+'a'*40]}));monkeypatch.setattr(orchestrator,'load_config',lambda p:json.loads(Path(p).read_text()))
    calls=[];original=d.make_snapshot
    def snapshot(root,out,inspection):
        r=original(root,out,inspection)
        if mode=='config_changed':cfg.write_text('{}')
        return r
    monkeypatch.setattr(d,'make_snapshot',snapshot)
    def execute(**kw):
        calls.append(kw['component']['adapter']);assert kw['snapshot'].is_file() and kw['source']==SOURCE
        return {'status':'REVIEWED_HTTP_READINESS_VERIFIED' if mode=='success' else 'REVIEWED_RUNTIME_FAILED_OR_UNVERIFIED','cleanup':CLEAN if mode=='success' else 'UNVERIFIED','vm_start_attempts':1}
    monkeypatch.setattr(w,'execute_reviewed',execute)
    opts={} if mode=='plan' else {'run_reviewed':True,'vm_config':cfg,'all_independent':mode=='budget'}
    result=d.dispatch('https://github.com/Owner/Repo',SOURCE['commit_sha'],tmp_path/'out',**opts)
    assert result['automatic_untrusted_execution'] is False and result['runtime_permission'] is False and not (tmp_path/'out/snapshot.zip').exists()
    if mode=='success':assert calls==['STATIC_HTTP'] and result['status']=='REVIEWED_COMPONENT_SMOKE_COMPLETE'
    elif mode=='cleanup_failure':assert calls==['STATIC_HTTP'] and result['status']=='FAILED_OR_BLOCKED'
    elif mode=='plan':assert not calls and result['status']=='PLANNED_NOT_EXECUTED'
    else:assert not calls and result['status']=='FAILED_OR_BLOCKED'

@pytest.mark.parametrize('body,port,blocked', [('require("http").createServer(()=>{}).listen(3000)',3000,False),('require("http").createServer(()=>{}).listen(80)',8765,True),('require("http").createServer(()=>{}).listen(process.env.PORT)',8765,False),('server.listen(3000);server.listen(4000)',8765,True)])
def test_node_literal_port_selection(tmp_path,body,port,blocked):
    r=row(put(tmp_path,{'server.js':body}));assert r['service_port']==port and ('NODE_LISTEN_PORT_AMBIGUOUS_OR_INVALID' in r['blockers'])==blocked

@pytest.mark.parametrize('mode',['success','cleanup_failure','bad_receipt','no_approval','bounded_volume_missing','watchdog_trip'])
def test_worker_lifecycle(tmp_path,monkeypatch,mode):
    from projectiq_executioner.phase2 import vm_worker as pkg
    from projectiq_executioner.phase8c_final import storage as s
    prefix='projectiq_executioner.phase2.vm_worker.'
    cfg=tmp_path/'config.json';cfg.write_text('{}');root=put(tmp_path/'source',{'index.html':'<html>ok</html>'});p=plan(root);snapshot=tmp_path/'snapshot.zip';sn.make_snapshot(root,snapshot,p['snapshot_inspection'])
    config={'approved_snapshots':[] if mode=='no_approval' else ['Owner/Repo@'+'a'*40],'accelerator':'whpx'};calls=[]
    def module(name,**attrs):
        m=types.ModuleType(prefix+name);m.__dict__.update(attrs);monkeypatch.setitem(sys.modules,prefix+name,m);monkeypatch.setattr(pkg,name,m,raising=False)
    module('orchestrator',load_config=lambda path:config)
    module('d5d_host_acl_audit',SUCCESS='OK',audit=lambda path:{'status':'OK','checks':{str(i):'PASS' for i in range(5)}})
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
        def copy(self,p,n,**kw):pass
        def copy_offline_asset(self,p,n):pass
        def ssh(self,*args,**kwargs):calls.append('guest');return SimpleNamespace(returncode=0)
        def receive(self,name,path):
            req=json.loads((self.work/'request.json').read_text());req['bundle_sha256']=None;raw=receipt(req);raw['service_port']=req['service_port'];raw['host_gateway_denied']=mode!='bad_receipt';path.write_text(json.dumps(raw))
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
    kwargs=dict(source=SOURCE,plan=p,component=p['components'][0],snapshot=snapshot,vm_config=cfg,expected_config=checked_file(cfg,100_000),require_bounded_volume=mode=='bounded_volume_missing')
    if mode=='no_approval':
        with pytest.raises(ValueError,match='APPROVAL_REQUIRED'):w.execute_reviewed(**kwargs)
        assert not calls;return
    result=w.execute_reviewed(**kwargs);assert result['runtime_permission'] is False
    if mode=='bounded_volume_missing':assert 'start' not in calls and result['error_code']=='SEPARATE_BOUNDED_VOLUME_REQUIRED';return
    assert calls.index('stop')<calls.index('close')
    if mode=='success':assert result['status']=='REVIEWED_HTTP_READINESS_VERIFIED' and result['cleanup']==CLEAN and result['storage_watchdog']['samples']>=1,result
    else:assert result['status']=='REVIEWED_RUNTIME_FAILED_OR_UNVERIFIED' and 'evidence' not in result

@pytest.mark.skipif(sys.platform!='linux' or not shutil.which('node') or not shutil.which('npm') or not Path('/usr/bin/python3').is_file(),reason='Optional local synthetic Linux node/npm experiment')
def test_actual_offline_npm_install_then_build(tmp_path):
    # A tiny operator-created stand-in for a framework binary. No registry or submitted code.
    import io
    node=Path(shutil.which('node'));npm=Path(shutil.which('npm'));version=subprocess.check_output([str(node),'--version'],text=True).strip()
    tgz=tmp_path/'fixture.tgz'
    with tarfile.open(tgz,'w:gz') as t:
        for name,data in {'package/package.json':json.dumps({'name':'vite','version':'1.0.0','bin':{'vite':'tool.js'},'scripts':{'install':'exit 99'}}),'package/tool.js':"const fs=require('fs');fs.mkdirSync('dist',{recursive:true});fs.writeFileSync('dist/index.html','<html>synthetic build</html>');"}.items():
            b=data.encode();info=tarfile.TarInfo(name);info.size=len(b);info.mode=0o755 if name.endswith('.js') else 0o644;t.addfile(info,io.BytesIO(b))
    files=node_files(['vite'],files={'index.html':'<html>source</html>'});lock=json.loads(files['package-lock.json']);lock['packages']['node_modules/vite']['integrity']='sha512-'+base64.b64encode(hashlib.sha512(tgz.read_bytes()).digest()).decode();files['package-lock.json']=json.dumps(lock);root=put(tmp_path/'source',files);work=tmp_path/'work';work.mkdir();inputs=broker.compile_inputs(json.loads(files['package.json']),lock)
    def fetch(job,p,remaining,deadline):shutil.copyfile(tgz,p);return p.stat().st_size
    bundle=work/'npm_cache.zip';broker.build_bundle(inputs,bundle,fetch=fetch);runtime=work/'node_runtime.tar.xz';runtime.write_bytes(b'fake asset; local executable used')
    home=work/'home';home.mkdir();(home/'empty').write_text('');(home/'global').write_text('');handoffs=[]
    def proc(cmd,*,env=None,cwd=None,timeout=30,project_code=False,audit_kind=None):
        if audit_kind=='npm_ci':assert '--offline' in cmd and '--ignore-scripts' in cmd
        cp=subprocess.run(cmd,env={'PATH':str(node.parent)+':/usr/bin:/bin','HOME':str(home),**(env or {})},cwd=cwd,timeout=timeout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if cp.returncode==0 and audit_kind:handoffs.append(audit_kind)
        if cp.returncode:raise AssertionError(cp.stderr.decode(errors='replace')[-2000:])
        return cp
    def extract(archive,out,**kw):
        out.mkdir();
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                rel=info.filename.removeprefix('inputs/');p=out/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(info))
        return out
    fake=SimpleNamespace(_sha256=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),_node_tar_extract=lambda *a:(node,npm),_share_tree=lambda *a,**kw:None,_trusted_process=proc,safe_extract=extract,_isolated_npm_config_paths=lambda p:{'NPM_CONFIG_USERCONFIG':str(home/'empty'),'NPM_CONFIG_GLOBALCONFIG':str(home/'global')},_APP_BUILD_HANDOFFS=handoffs,_BUILD_KINDS={'npm_ci'})
    req={'node_sha256':fake._sha256(runtime),'node_version':version,'dependency_kind':'NPM','bundle_sha256':fake._sha256(bundle),'adapter':'NODE_VITE','entrypoint':None}
    exe,env=g.install_node(fake,work,req,root,vs.node_constraint);cmd,cli,count=g.launch_recipe(fake,work,root,req,exe,env)
    assert handoffs==['npm_ci','phase8d_build'] and not cli and count==1 and 'http.server' in cmd and (root/'dist/index.html').read_text()=='<html>synthetic build</html>'
