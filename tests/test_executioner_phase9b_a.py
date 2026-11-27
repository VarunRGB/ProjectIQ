import copy
import json
from contextlib import contextmanager

import pytest

from projectiq_executioner.phase9a import planner
from projectiq_executioner.phase9b import compiler as c
from projectiq_executioner.phase9b.__main__ import main
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.orchestration import adapters
from test_executioner_phase9a import flask, express, write, SRC, URL


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path/'src';root.mkdir();calls=[]
    @contextmanager
    def fetch(url, ref):
        assert url == URL and ref == SRC['commit_sha'];calls.append((url,ref));yield SRC, root
    monkeypatch.setattr(adapters,'pinned_checkout',fetch)
    return root,calls


def generate(tmp_path):
    out=tmp_path/'original';plan=planner.plan_repository(URL,SRC['commit_sha'],out)
    return out/'functional_plan.json',plan


def run(tmp_path,path,name='compiled'):
    return c.compile_repository(URL,SRC['commit_sha'],path,tmp_path/name)


def save(path,obj):
    path.write_text(json.dumps(obj),encoding='utf-8')


def resign(obj,key='plan_sha256'):
    obj[key]=digest({k:v for k,v in obj.items() if k!=key})


def test_flask_compile(tmp_path,checkout):
    root,calls=checkout;flask(root);path,plan=generate(tmp_path);r=run(tmp_path,path)
    assert len(calls)==2 and r['source_rederivation']=='EXACT_CANONICAL_PLAN_MATCH'
    assert r['functional_plan_sha256']==plan['plan_sha256']
    assert r['status']=='COMPILED_TESTS_AVAILABLE' and r['summary']=={'proposed_cases':1,'compiled_tests':1,'deferred_cases':0,'deferred_workflows':0,'compiled_components':1}
    g=r['components'][0];assert g['guest_service_port']==8765
    assert g['tests'][0]['assertions']==[{'kind':'HTTP_STATUS_EQUALS','expected':200},{'kind':'BODY_TEXT_EQUALS','expected':'Hello, World!'}]
    assert r['vm_start_attempts']==r['functional_tests_executed']==0 and r['runtime_permission'] is False and r['grade_credit'] is None
    assert r['request_policy']['follow_redirects'] is False and r['request_policy']['target']=='GUEST_LOOPBACK_ONLY'
    assert r==run(tmp_path,path,'again')
    assert r['compiled_sha256']==digest({k:v for k,v in r.items() if k!='compiled_sha256'})
    assert json.loads((tmp_path/'compiled'/'compiled_plan.json').read_text())==r


def test_express_deferral(tmp_path,checkout):
    root,_=checkout;express(root);path,plan=generate(tmp_path);r=run(tmp_path,path)
    g=r['components'][0];assert g['guest_service_port']==8080
    assert {t['path'] for t in g['tests']}=={'/','/posts','/posts/data'}
    assert all(t['method']=='GET' and t['status']=='COMPILED_NOT_EXECUTED' for t in g['tests'])
    assert r['summary']['deferred_cases']==len(plan['cases'])-3
    assert len(r['deferred_workflows'])==1
    assert 'ADAPTIVE_WORKFLOW_RUNNER_NOT_IMPLEMENTED' in r['deferred_workflows'][0]['reasons']
    assert {x['case_id'] for x in r['deferred_cases']}|{t['case_id'] for t in g['tests']}=={t['id'] for t in plan['cases']}


@pytest.mark.parametrize('change',['oracle','route','permission','workflow','case_id','number_type','evidence','runtime_digest'])
def test_resigned_tampering(tmp_path,checkout,change):
    root,_=checkout;express(root);path,plan=generate(tmp_path)
    case=next(x for x in plan['cases'] if x['status']=='ORACLE_PROPOSED')
    if change=='oracle':case['assertions'][0]['expected']=500
    if change=='route':case['path_template']='//example.com'
    if change=='permission':plan['runtime_permission']=True
    if change=='workflow':plan['workflows'][0]['steps'][0]['method']='GET'
    if change=='case_id':case['id']='FT-'+'A'*16
    if change=='number_type':case['assertions'][0]['expected']=200.0
    if change=='evidence':case['evidence'][0]['sha256']='a'*64
    if change=='runtime_digest':plan['runtime_plan_sha256']='b'*64
    resign(plan);save(path,plan)
    with pytest.raises(ValueError,match='PHASE9B_PLAN_REDERIVATION_MISMATCH'):run(tmp_path,path)
    assert not (tmp_path/'compiled'/'compiled_plan.json').exists()


def test_stale_source(tmp_path,checkout):
    root,_=checkout;flask(root);path,_=generate(tmp_path);flask(root,"return 'different'")
    with pytest.raises(ValueError,match='PHASE9B_PLAN_REDERIVATION_MISMATCH'):run(tmp_path,path)


@pytest.mark.parametrize('mutation',['bad_sha','duplicate_json','different_source','nonfinite'])
def test_reject_before_checkout(tmp_path,checkout,mutation):
    root,calls=checkout;flask(root);path,p=generate(tmp_path)
    if mutation=='bad_sha':p['plan_sha256']='b'*64;save(path,p)
    elif mutation=='duplicate_json':path.write_text('{"a":1,"a":2}')
    elif mutation=='nonfinite':path.write_text('{"a":NaN}')
    else:p['source']['commit_sha']='b'*40;resign(p);save(path,p)
    with pytest.raises(ValueError):run(tmp_path,path)
    assert len(calls)==1 and not (tmp_path/'compiled').exists()


def test_exact_ref_and_fresh_output(tmp_path,checkout):
    root,calls=checkout;flask(root);path,_=generate(tmp_path)
    with pytest.raises(ValueError,match='EXACT_REF_REQUIRED'):c.compile_repository(URL,'main',path,tmp_path/'out')
    run(tmp_path,path)
    with pytest.raises(ValueError):run(tmp_path,path)
    assert len(calls)==2


def test_no_oracles(tmp_path,checkout):
    root,_=checkout;flask(root,'return str(42)');path,_=generate(tmp_path);r=run(tmp_path,path)
    assert r['status']=='NO_COMPILED_TESTS' and r['summary']['deferred_cases']>=1


def test_static_bytes(tmp_path,checkout):
    root,_=checkout;write(root,'index.html','<h1>hello</h1>');path,_=generate(tmp_path);r=run(tmp_path,path)
    assert r['components'][0]['tests'][0]['assertions'][1]['kind']=='HTTP_BODY_SHA256_EQUALS'


def test_fastapi(tmp_path,checkout):
    root,_=checkout;write(root,'main.py',"from fastapi import FastAPI\napp=FastAPI()\n@app.get('/health')\ndef health():\n    return {'healthy': True}\n")
    write(root,'requirements.txt','fastapi==0.115.6\nuvicorn==0.34.0\n');path,_=generate(tmp_path);r=run(tmp_path,path)
    assert any({'kind':'JSON_EQUALS','expected':{'healthy':True}} in t['assertions'] for g in r['components'] for t in g['tests'])


def test_declared_status_only_and_conflict(tmp_path,checkout):
    root,_=checkout;flask(root);write(root,'openapi.json',json.dumps({'openapi':'3.0.3','paths':{'/':{'get':{'responses':{'200':{'description':'ok'}}}}}}))
    path,_=generate(tmp_path);r=run(tmp_path,path);t=r['components'][0]['tests'][0]
    assert t['declared_contract'] is True and t['assertions']==[{'kind':'HTTP_STATUS_EQUALS','expected':200}]
    flask(root,"return 'different', 201");path=tmp_path/'conflict'/'functional_plan.json';planner.plan_repository(URL,SRC['commit_sha'],path.parent);r=run(tmp_path,path,'conflict_result')
    assert r['summary']['compiled_tests']==0
    assert any('DECLARATION_IMPLEMENTATION_CONFLICT' in x['reasons'] for x in r['deferred_cases'])


@pytest.mark.parametrize('kind,value',[('HTTP_STATUS_EQUALS',True),('HTTP_STATUS_EQUALS',200.0),('BODY_TEXT_EQUALS',None),('BODY_TEXT_EQUALS',''),('SHELL_COMMAND','echo'),('JSON_EQUALS',{'secret':'no'})])
def test_oracle_types(kind,value):
    case={'oracle_basis':'IMPLEMENTATION_LITERAL','declared_contract':False,'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':200},{'kind':kind,'expected':value}]}
    with pytest.raises(ValueError):c._assertions(case)


def test_budget(tmp_path,checkout):
    root,_=checkout;flask(root)
    with (root/'app.py').open('a') as f:
        for i in range(35):f.write(f"\n@app.route('/r{i:02}')\ndef r{i}(): return 'ok'\n")
    path,_=generate(tmp_path);r=run(tmp_path,path)
    assert r['summary']['compiled_tests']==32 and r['summary']['deferred_cases']==4
    assert all('TEST_BUDGET_REACHED' in t['reasons'] for t in r['deferred_cases'])


def test_cli(tmp_path,checkout,capsys):
    root,_=checkout;flask(root);path,_=generate(tmp_path)
    assert main(['--repo',URL,'--ref',SRC['commit_sha'],'--from-plan',str(path),'--out',str(tmp_path/'compiled')])==0
    assert 'VM attempts: 0; functional tests executed: 0' in capsys.readouterr().out
