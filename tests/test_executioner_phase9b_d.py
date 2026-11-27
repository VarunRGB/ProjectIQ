import argparse
import copy
import json
from pathlib import Path
import pytest
from projectiq_executioner.phase9b import unified as u
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import CLEAN
from projectiq_executioner.phase9b.adaptive_runner import ROLES, MUTATIONS
from test_executioner_phase9a import flask, express, write, SRC, URL
from test_executioner_phase9b_a import checkout
from test_executioner_phase9b_c import fixture


def opt(tmp_path,**kw):return u.DemoOptions(tmp_path/'private-vm.json',True,**kw)
def run(tmp_path,**kw):return u.evaluate_project(URL,SRC['commit_sha'],tmp_path/'run',submission_id='DEMO',options=opt(tmp_path,**kw))
def save(out,name,r):
    out.mkdir();r['result_sha256']=digest(r);u.write(out/name,r);return r


def fake_functional(monkeypatch,*,mode='pass',calls=None):
    calls=[] if calls is None else calls
    def native(url,sha,compiled_path,out,**kw):
        calls.append(kw);assert url==URL and sha==SRC['commit_sha'] and kw['trust_demo_source'] is True
        if mode=='exception':raise RuntimeError('private runtime data')
        c=u.read_object(compiled_path);g=next(x for x in c['components'] if x['component_id']==kw['component'])
        rows=[{'case_id':t['case_id'],'status':'PASS','request_attempted':True,'http_status':t['assertions'][0]['expected'],'assertions':[{'kind':a['kind'],'matched':True,'error_code':None} for a in t['assertions']],'error_code':None} for t in g['tests']]
        if mode=='fail':rows[0]['status']='FAIL';rows[0]['assertions'][-1]['matched']=False
        if mode=='tamper':rows[0]['status']='PASS';rows[0]['assertions'][-1]['matched']=False
        e={'component_id':g['component_id'],'cleanup':CLEAN,'cases':rows}
        r={'source':SRC,'status':'FUNCTIONAL_EVIDENCE_COMPLETE','compiled_sha256':c['compiled_sha256'],'automatic_untrusted_execution':False,'runtime_permission':False,'vm_start_attempts':1,'vm_attempt_count_uncertain':False,'functional_count_uncertain':False,'functional_tests_executed':len(rows),'temporary_approval_cleanup':'REMOVED','original_config_unchanged':True,'evidence':e}
        if kw['adaptive']:
            p=u.read_object(compiled_path.parent/'functional_plan.json');w=u._adaptive(url,SRC,p,next(x for x in u.read_object(compiled_path.parent/'runtime_plan.json')['components'] if x['id']==g['component_id']))
            steps=[{'role':role,'status':'PASS','http_status':302 if role in MUTATIONS else 200,'matched':True,'error_code':None} for role in ROLES]
            e['workflow']={'workflow_id':w['workflow_id'],'contract_sha256':digest(w),'status':'PASS','requests_attempted':9,'steps':steps}
            r.update(workflow_status='PASS',workflow_requests_attempted=9,workflow_count_uncertain=False)
        if mode=='blocked':r.update(status='INCOMPLETE',vm_start_attempts=0,error_code='EXISTING_OPERATOR_APPROVAL_REQUIRED',temporary_approval_cleanup='NOT_CREATED',evidence=None)
        if mode=='cleanup':r['temporary_approval_cleanup']='UNVERIFIED'
        if mode=='wrong_source':r['source']={**SRC,'commit_sha':'b'*40}
        return save(out,'functional_result.json',r)
    monkeypatch.setattr(u,'run_functional',native)
    monkeypatch.setattr(u,'run_demo',lambda *a,**k:pytest.fail('unexpected smoke rerun'))
    return calls


def test_unified_real_static_flask(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);calls=fake_functional(monkeypatch)
    r=run(tmp_path);d=u.validate_extension(r)
    old=json.loads((tmp_path/'run'/'static'/'executioner_result.json').read_text())
    assert d['status']=='FUNCTIONAL_EVIDENCE_COMPLETE' and d['selected_path']=='INDEPENDENT_HTTP' and len(calls)==1
    assert r['test_case_matrix']==old['test_case_matrix'] and r['suites']==old['suites']==[] and r['code_quality']==old['code_quality']
    assert r['mode']==r['effective_mode']=='STATIC' and r['vm_attempts_scheduled']==0 and d['vm_start_attempts']==1
    assert d['functional_inventory']['summary']['independent_pass']==1
    assert 'Trusted local demonstration' in (tmp_path/'run'/'executioner_report.md').read_text()
    assert d['grade_credit'] is None and r['source']==SRC


@pytest.mark.parametrize('confirm,profile,adaptive',[(True,'full',True),(False,'full',False),(True,'unsupported',False)])
def test_adaptive_selection(tmp_path,checkout,monkeypatch,confirm,profile,adaptive):
    root,_=checkout;(fixture if profile=='full' else express)(root);calls=fake_functional(monkeypatch)
    r=run(tmp_path,confirm_disposable_state=confirm);d=u.validate_extension(r)
    assert len(calls)==1 and calls[0]['adaptive']==adaptive and d['status']=='FUNCTIONAL_EVIDENCE_COMPLETE',d
    inv=d['functional_inventory'];assert inv['summary']['independent_pass']==3
    assert inv['summary']['unique_cases_with_attempted_request']==(7 if adaptive else 3)
    assert inv['summary']['workflow_only_request_cases']==(4 if adaptive else 0)
    assert d['workflow_requests_attempted']==(9 if adaptive else 0)
    if adaptive:
        assert inv['workflow']['status']=='PASS' and len(inv['workflow']['steps'])==9
        assert all(c['independent_outcome']=='NOT_RUN' for c in inv['cases'] if c['method']!='GET' or ':' in c['path_template'])


@pytest.mark.parametrize('mode,expected',[('fail','FUNCTIONAL_EVIDENCE_COMPLETE'),('blocked','RUNTIME_INCOMPLETE'),('cleanup','SECURITY_HOLD'),('wrong_source','SECURITY_HOLD'),('exception','SECURITY_HOLD'),('tamper','SECURITY_HOLD')])
def test_no_retry_and_no_false_pass(tmp_path,checkout,monkeypatch,mode,expected):
    root,_=checkout;flask(root);calls=fake_functional(monkeypatch,mode=mode)
    r=run(tmp_path);d=u.validate_extension(r);assert d['status']==expected and len(calls)==1,d
    if mode=='fail':
        assert d['functional_inventory']['summary']['independent_fail']==1
        assert d['diagnostics'][0]['code']=='TEST_ASSERTION_MISMATCH' and d['diagnostics'][0]['student_defect']=='NOT_ESTABLISHED'
    if expected=='SECURITY_HOLD':assert r['status']=='SECURITY_HOLD'
    if mode!='fail':assert d['functional_inventory']['summary']['independent_pass']==0


def test_smoke_fallback(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root,"return str(42)");calls=[]
    monkeypatch.setattr(u,'run_functional',lambda *a,**k:pytest.fail('unexpected functional execution'))
    def native(url,sha,out,**kw):
        calls.append(kw);assert sha==SRC['commit_sha']
        r={'source':SRC,'status':'DEMO_REVIEWED_SMOKE_COMPLETE','automatic_untrusted_execution':False,'runtime_permission':False,'vm_start_attempts':1,'vm_attempt_count_uncertain':False,'temporary_approval_cleanup':'REMOVED','original_config_unchanged':True,'components':[{'id':kw['component'],'evidence':{'cleanup':CLEAN,'status':'REVIEWED_SMOKE_VERIFIED','http_status':200}}]}
        return save(out,'demo_result.json',r)
    monkeypatch.setattr(u,'run_demo',native)
    d=u.validate_extension(run(tmp_path));assert d['status']=='SMOKE_EVIDENCE_COMPLETE' and len(calls)==1 and d['functional_tests_executed']==0 and d['functional_inventory']['summary']['unique_cases_with_attempted_request']==0


def test_unsupported_static_only(tmp_path,checkout,monkeypatch):
    root,_=checkout;write(root,'README.md','A project without a supported runtime.');calls=fake_functional(monkeypatch)
    d=u.validate_extension(run(tmp_path));assert d['status']=='STATIC_ONLY_LIMITED' and not calls and d['vm_start_attempts']==0


def test_explicit_component(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);calls=fake_functional(monkeypatch)
    d=u.validate_extension(run(tmp_path,component='missing'));assert d['status']=='STATIC_ONLY_LIMITED' and not calls


@pytest.mark.parametrize('args',[['--demo-vm-config','vm.json'],['--trust-demo-source'],['--confirm-disposable-state']])
def test_operator_options_refuse_partial(args):
    p=argparse.ArgumentParser();u.add_demo_arguments(p)
    assert u.demo_options(p.parse_args([])) is None
    with pytest.raises(ValueError):u.demo_options(p.parse_args(args))


def test_default_and_opt_in_parser():
    p=argparse.ArgumentParser();u.add_demo_arguments(p)
    d=u.demo_options(p.parse_args(['--trust-demo-source','--demo-vm-config','vm.json']));assert d.trust_demo_source and not d.confirm_disposable_state


def test_tampered_extension(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);fake_functional(monkeypatch);r=run(tmp_path)
    d=r['trusted_demo'];d['grade_credit']=100
    with pytest.raises(ValueError):u.validate_extension(r)
    d['demo_sha256']=digest({k:v for k,v in d.items() if k!='demo_sha256'})
    with pytest.raises(ValueError):u.validate_extension(r)
