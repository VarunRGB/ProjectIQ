"""Run against the complete ProjectIQ checkout (not the Executioner-only export)."""
import argparse
import pytest
from projectiq_executioner.phase9b import unified as u
from projectiq_executioner.phase8a.planner import digest
from projectiq_core.collector import make_submission, live_sources, collect
from projectiq_core.adapters import normalize, AdapterError
from projectiq_core.contracts import Receipt
from projectiq_core.orchestrator import add_evaluate_parser, _frontend_agents, DEFAULT_RUBRIC
from projectiq_core.phase2.evidence import build_evidence
from projectiq_core.phase2.intelligence import runtime_inventory
from projectiq_core.phase2.contracts import Rubric
from projectiq_core.collector import load_json
from test_executioner_phase9a import flask, URL, SRC
from test_executioner_phase9b_a import checkout
from test_executioner_phase9b_d import fake_functional


def test_collector_bridge_and_rubric_boundary(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);calls=fake_functional(monkeypatch)
    p=tmp_path/'report.txt';p.write_text('A Flask service provides a greeting.',encoding='utf-8')
    sub,data=make_submission(p,'Demo','DEMO',URL,SRC['commit_sha'])
    op=u.DemoOptions(tmp_path/'vm_config.json',True)
    funcs=live_sources(sub,agents=('executioner',),summary_only=True,demo=op)
    import projectiq_core.ieee_integration as ieee
    monkeypatch.setattr(ieee,'run_ieee_check',lambda p:{'status':'NOT_ASSESSED_IN_TEST'})
    ev=collect(submission=sub,report_bytes=data,out=tmp_path/'run',mode='LIVE',sources=funcs,progress=lambda _:None)
    assert len(calls)==1 and ev.agents['executioner'].status=='PARTIAL'
    d=ev.agents['executioner'].details['trusted_demo'];assert d['status']=='FUNCTIONAL_EVIDENCE_COMPLETE' and d['functional_inventory']['summary']['independent_pass']==1
    bundle=build_evidence(tmp_path/'run'/'evaluation.json')
    assert bundle.agent_checks['executioner']=='MATCHED'
    assert bundle.observations['executioner']['trusted_demo']['scope']=='CHARACTERIZATION_DISPLAY_ONLY'
    assert not runtime_inventory(bundle)[0], 'Characterization is not an independently reviewed oracle'
    rubric=Rubric.model_validate(load_json(DEFAULT_RUBRIC))
    cards=_frontend_agents(ev,bundle,rubric)
    assert cards['executioner']['details']['trusted_demo']['functional_tests_executed']==1


def test_default_static_and_explicit_config(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);calls=fake_functional(monkeypatch)
    p=tmp_path/'report.txt';p.write_text('Report',encoding='utf-8');sub,_=make_submission(p,'Demo','DEMO',URL,SRC['commit_sha']);out=tmp_path/'out';out.mkdir()
    funcs=live_sources(sub,agents=('executioner',),summary_only=True)
    r=funcs['executioner'](p,out)
    assert 'trusted_demo' not in r and not calls and r['effective_mode']=='STATIC'
    with pytest.raises(ValueError):live_sources(sub,agents=('executioner',),demo={'trust_demo_source':True})
    with pytest.raises(ValueError):live_sources(sub,agents=('integrity',),demo=u.DemoOptions(tmp_path/'vm.json',True))


def test_evaluate_parser_demo_options(tmp_path):
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True);add_evaluate_parser(s)
    args=['evaluate','--report','report.txt','--out','out','--repo',URL]
    assert u.demo_options(p.parse_args(args)) is None
    options=u.demo_options(p.parse_args(args+['--trust-demo-source','--demo-vm-config','vm_config.json']))
    assert options.trust_demo_source and not options.confirm_disposable_state


def test_normalizer_refuses_demo_credit(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);fake_functional(monkeypatch)
    r=u.evaluate_project(URL,SRC['commit_sha'],tmp_path/'run',submission_id='DEMO',options=u.DemoOptions(tmp_path/'vm.json',True))
    d=r['trusted_demo'];d['grade_credit']=100;d['demo_sha256']=digest({k:v for k,v in d.items() if k!='demo_sha256'})
    with pytest.raises(AdapterError):normalize('executioner',r,Receipt(path='native/executioner.json',sha256='a'*64,origin='LIVE'))
