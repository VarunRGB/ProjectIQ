import json
from contextlib import contextmanager
from pathlib import Path
import pytest
from projectiq_executioner.phase9a import planner as p
from projectiq_executioner.orchestration import adapters
from projectiq_executioner.phase3.generator import generate_test_plan
from projectiq_executioner.phase8d.planner import compile_runtime
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.models import RepositorySource
from projectiq_executioner.analyzer import analyze_directory

SRC={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40}
URL='https://github.com/Owner/Repo'

def write(root,path,text):
    q=root/path;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(text,encoding='utf-8')

def flask(root,body="return 'Hello, World!'",decorator="@app.route('/')"):
    write(root,'app.py',f'from flask import Flask\napp = Flask(__name__)\n{decorator}\ndef hello():\n    {body}\n')
    write(root,'requirements.txt','Flask==3.1.1\n')

def express(root):
    write(root,'package.json',json.dumps({'name':'fixture','main':'index.js','dependencies':{'express':'^5.1.0','ejs':'^3.1.10'}}))
    # No registry access: minimal syntactically valid planning fixture lockfile.
    import base64
    deps={'express':'^5.1.0','ejs':'^3.1.10'}
    write(root,'package-lock.json',json.dumps({'lockfileVersion':3,'packages':{'':{'dependencies':deps},**{'node_modules/'+n:{'version':v[1:],'resolved':'https://registry.npmjs.org/'+n+'/-/'+n+'-'+v[1:]+'.tgz','integrity':'sha512-'+base64.b64encode((n.encode()*64)[:64]).decode()} for n,v in deps.items()}}}))
    write(root,'index.js', '''const express=require('express');
const app=express(); const port=8080;
app.listen(port,()=>{});
app.get('/',(req,res)=>{res.render('index.ejs');});
app.get('/posts',(req,res)=>{res.render('posts.ejs',{data});});
app.get('/posts/data',(req,res)=>{res.render('create.ejs',{data});});
app.post('/posts/data',(req,res)=>{let {content}=req.body; data.push({content}); res.redirect('/posts');});
app.get('/posts/:id',(req,res)=>{res.render('show.ejs',{post});});
app.patch('/posts/:id/update',(req,res)=>{let content=req.body.update; post.content=content; res.redirect('/posts');});
app.delete('/posts/:id/delete',(req,res)=>{data=data.filter(x=>x.id!==req.params.id);res.redirect('/posts');});
''')
    write(root,'views/index.ejs','<a href="/posts">Click here</a>')
    write(root,'views/posts.ejs','<h1>Data -</h1><% for (d of data) { %><p>Never unconditional</p><% } %>')
    write(root,'views/create.ejs','<form action="/posts/data" method="POST"><textarea name="content"></textarea><button>Submit</button></form>')

def build(root):
    src=RepositorySource(kind='github',repository=SRC['repository'],commit_sha=SRC['commit_sha'],url=URL,requested_ref=SRC['commit_sha'],resolved_ref=SRC['commit_sha'])
    manifest=analyze_directory(root,src).to_dict();_,runtime,_=compile_runtime(root,source=SRC)
    phase3=generate_test_plan(manifest,None,source_root=root,source_verified=True)
    return p.build_plan(root,SRC,manifest,runtime,phase3)

def test_flask_literal(tmp_path):
    flask(tmp_path);r=build(tmp_path);case=next(c for c in r['cases'] if c['path_template']=='/')
    assert case['status']=='ORACLE_PROPOSED' and case['assertions']==[p.check('HTTP_STATUS_EQUALS',200),p.check('BODY_TEXT_EQUALS','Hello, World!')]
    assert not case['declared_contract'] and r['functional_tests_executed']==r['vm_start_attempts']==0 and not r['runtime_permission']
    assert r==build(tmp_path)
    assert r['plan_sha256']==digest({k:v for k,v in r.items() if k!='plan_sha256'})

@pytest.mark.parametrize('body,decorator',[("return str(42)","@app.route('/')"),("return 'x'","@app.route('/', methods=['POST'])"),("return 'x'","@app.route('/', defaults={'x':1})"),("return 'x'","@app.route('/<name>')"),("raise RuntimeError('x')","@app.route('/')")])
def test_no_guessed_python_oracles(tmp_path,body,decorator):
    flask(tmp_path,body,decorator);r=build(tmp_path);assert not any(c['assertions'] for c in r['cases'])

def test_fastapi(tmp_path):
    write(tmp_path,'main.py',"from fastapi import FastAPI\napp=FastAPI()\n@app.get('/health')\ndef health():\n    return {'healthy': True}\n")
    write(tmp_path,'requirements.txt','fastapi==0.115.6\nuvicorn==0.34.0\n')
    r=build(tmp_path);assert any(p.check('JSON_EQUALS',{'healthy':True}) in c['assertions'] for c in r['cases'])

def test_express_workflow(tmp_path):
    express(tmp_path);r=build(tmp_path);assert r['summary']['workflows_needing_binding']==1,r
    wf=r['workflows'][0];assert [s['role'] for s in wf['steps']]==['CREATE','LIST_CREATED','READ_CREATED','UPDATE','READ_UPDATED','DELETE','VERIFY_REMOVED']
    assert wf['steps'][0]['input_fields']==['content'] and wf['steps'][3]['input_fields']==['update']
    assert [{k:v for k,v in f.items() if k!='evidence'} for f in wf['matching_create_forms']]==[{'method':'POST','path':'/posts/data','fields':['content']}]
    assert wf['matching_create_forms'][0]['evidence']['path']=='views/create.ejs'
    assert all(c['status']=='NEEDS_REVIEW' for c in r['cases'] if c['method']!='GET' or ':' in c['path_template'])
    assert any(p.check('HTML_TEXT_CONTAINS','Data -') in c['assertions'] for c in r['cases'])
    assert not any(p.check('HTML_TEXT_CONTAINS','Never unconditional') in c['assertions'] for c in r['cases'])
    assert wf['steps'][-1]['proposed_assertions']==[{'kind':'RECORD_ABSENT','identity':'CREATED_RECORD'},{'kind':'UNRELATED_RECORDS_PRESERVED'}]
    assert wf['execution']=='NOT_PERFORMED' and wf['status']=='NEEDS_BINDING_AND_REVIEW'

def test_comments_not_routes(tmp_path):
    raw=b"const express=require('express');const app=express(); // app.get('/fake',(q,s)=>{s.send('fake')});\nconst x=\"app.get('/also-fake')\"; app.get('/ok',(q,s)=>{s.send('ok')});"
    r=p.express_routes(tmp_path,raw,'index.js','.');assert [x['path'] for x in r]==['/ok']

def test_openapi(tmp_path):
    flask(tmp_path,"return 'Hello', 201")
    write(tmp_path,'openapi.json',json.dumps({'openapi':'3.0.3','paths':{'/':{'get':{'responses':{'200':{'description':'ok'}}}}}}))
    r=build(tmp_path);c=next(c for c in r['cases'] if c['path_template']=='/')
    assert c['oracle_basis']=='REPOSITORY_DECLARED_OPENAPI' and 'DECLARATION_IMPLEMENTATION_CONFLICT' in c['blockers']

def test_cli_path_and_identity(tmp_path,monkeypatch):
    root=tmp_path/'src';root.mkdir();flask(root)
    @contextmanager
    def checkout(url,ref):
        assert url==URL and ref=='a'*40;yield SRC,root
    monkeypatch.setattr(adapters,'pinned_checkout',checkout)
    out=tmp_path/'out';r=p.plan_repository(URL,'a'*40,out)
    assert json.loads((out/'functional_plan.json').read_text())==r
    assert (out/'phase1'/'manifest.json').exists() and (out/'phase3_test_plan.json').exists()
    with pytest.raises(ValueError):p.plan_repository(URL,'a'*40,out)

@pytest.mark.parametrize('path',['//evil.com','http://example.com','/../escape','/x?y=z','/x#fragment','/x%2f','/x\\y'])
def test_unsafe_paths(path):assert not p.local_path(path)


def test_static_resource(tmp_path):
    write(tmp_path,'index.html','<!DOCTYPE html><h1>Hello</h1>')
    r=build(tmp_path);assert any(c['oracle_basis']=='STATIC_RESOURCE_BYTES' and c['status']=='ORACLE_PROPOSED' for c in r['cases'])


def test_rederive_rejects_tamper(tmp_path):
    flask(tmp_path);r=build(tmp_path)
    src=RepositorySource(kind='github',repository=SRC['repository'],commit_sha=SRC['commit_sha'],url=URL,requested_ref=SRC['commit_sha'],resolved_ref=SRC['commit_sha'])
    manifest=analyze_directory(tmp_path,src).to_dict();_,runtime,_=compile_runtime(tmp_path,source=SRC)
    phase3=generate_test_plan(manifest,None,source_root=tmp_path,source_verified=True)
    assert p.verify_rederived_plan(tmp_path,SRC,manifest,runtime,phase3,r)==r
    r['cases'][0]['assertions'][0]['expected']=500;r['plan_sha256']=digest({k:v for k,v in r.items() if k!='plan_sha256'})
    with pytest.raises(ValueError,match='PHASE9_PLAN_REDERIVATION_MISMATCH'):p.verify_rederived_plan(tmp_path,SRC,manifest,runtime,phase3,r)
    write(tmp_path,'app.py',"from flask import Flask\napp=Flask(__name__)\n@app.route('/')\ndef x(): return 'changed'\n")
    with pytest.raises(ValueError):p.build_plan(tmp_path,SRC,manifest,runtime,phase3)


def test_duplicate_route_abstains(tmp_path):
    flask(tmp_path)
    with (tmp_path/'app.py').open('a') as f:f.write("\n@app.route('/')\ndef again(): return 'different'\n")
    r=build(tmp_path);assert 'AMBIGUOUS_DIRECT_ROUTE_DECLARATION' in r['issues'] and not any(c['assertions'] for c in r['cases'])


def test_rejects_source_identity_mismatch(tmp_path,monkeypatch):
    root=tmp_path/'src';root.mkdir();flask(root)
    @contextmanager
    def checkout(url,ref):yield {**SRC,'commit_sha':'b'*40},root
    monkeypatch.setattr(adapters,'pinned_checkout',checkout)
    out=tmp_path/'out'
    with pytest.raises(ValueError,match='PHASE9_SOURCE_MISMATCH'):p.plan_repository(URL,'a'*40,out)
    assert not out.exists()
