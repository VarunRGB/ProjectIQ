"""Offline repair regression: actual static evaluator, bounded port inference and recovery."""
from contextlib import contextmanager
from pathlib import Path
import pytest
from projectiq_executioner.phase8d.planner import infer_node_port, compile_runtime
from projectiq_executioner.phase8e.corpus import node, write_tree
from projectiq_executioner.phase8f import integration, recover_static
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import checked_file
from projectiq_executioner.phase8c_final.dispatch import write
from projectiq_executioner.orchestration import adapters

SRC={'kind':'github','repository':'Example/Repo','commit_sha':'a'*40}
URL='https://github.com/Example/Repo'

@pytest.mark.parametrize('body,port,blocker',[
    ('const port=8080; // port\napp.listen(port,()=>{})',8080,None),
    ('const PORT = 3000\napp.listen({port: PORT})',3000,None),
    ('app.listen(4000)',4000,None),
    ('app.listen({port: 4001})',4001,None),
    ('const p=80; app.listen(p)',8765,'NODE_LISTEN_PORT_AMBIGUOUS_OR_INVALID'),
    ('const p=8080; app.listen(p); app.listen(3000)',8765,'NODE_LISTEN_PORT_AMBIGUOUS_OR_INVALID'),
    ('const p=8080; { const p=3000; app.listen(p); }',8765,None),
    ('let p=8080; app.listen(p)',8765,None),
    ('const p=process.env.PORT || 8080; app.listen(p)',8765,None),
    ('// app.listen(1234)\nconst s="app.listen(4321)"; app.listen(8765)',8765,None),
    ('const p=8080+1; app.listen(p)',8765,None),
    ('app.listen(p); const p=8080;',8765,None),
])
def test_ports(body,port,blocker):
    assert infer_node_port(body)==(port,blocker)


def test_actual_planner_const_port(tmp_path):
    root=tmp_path/'src';write_tree(root,node(['express'],{'index.js':'const app=require("express")();\nconst port=8080;\napp.listen(port,()=>{});'},main='index.js'))
    _,plan,_=compile_runtime(root,source=SRC);row=plan['components'][0]
    assert row['service_port']==8080 and row['status']=='REVIEWED_CANDIDATE'


def actual_static_checkout(tmp_path,monkeypatch):
    root=tmp_path/'static-src';root.mkdir();(root/'index.html').write_text('<html><body>Hello</body></html>')
    @contextmanager
    def checkout(url,ref):
        assert url==URL and ref==SRC['commit_sha'];yield SRC,root
    monkeypatch.setattr(adapters,'pinned_checkout',checkout)


def test_real_static_evaluator_after_demo(tmp_path,monkeypatch):
    actual_static_checkout(tmp_path,monkeypatch)
    def fake_demo(url,ref,out,**kw):
        out.mkdir();r={'source':SRC,'status':'DEMO_REVIEWED_SMOKE_COMPLETE','vm_start_attempts':1,'vm_attempt_count_uncertain':False,'components':[],'original_config_unchanged':True,'temporary_approval_cleanup':'REMOVED'};r['result_sha256']=digest(r);write(out/'demo_result.json',r);return r
    monkeypatch.setattr(integration,'run_demo',fake_demo)
    result=integration.evaluate_trusted_demo(URL,None,tmp_path/'out',vm_config=tmp_path/'unused',trust_demo_source=True)
    assert result['status']=='DEMO_AND_STATIC_COMPLETE',result
    assert result['static']['status'] in ('COMPLETED','COMPLETED_WITH_LIMITATIONS')


def test_recovery_never_dispatches(tmp_path,monkeypatch):
    actual_static_checkout(tmp_path,monkeypatch)
    prior=tmp_path/'prior';dp=prior/'demo';dp.mkdir(parents=True)
    cid='component-'+'a'*16;dest=dp/cid;dest.mkdir()
    d={'status':'REVIEWED_COMPONENT_SMOKE_COMPLETE'};d['result_sha256']=digest(d);write(dest/'dispatch_result.json',d)
    ev={'status':'REVIEWED_SMOKE_VERIFIED','source':SRC};row={'id':cid,'root':'.','adapter':'STATIC_HTTP','dispatch_report':cid+'/dispatch_result.json','dispatch_sha256':d['result_sha256'],'evidence':ev}
    demo={'status':'DEMO_REVIEWED_SMOKE_COMPLETE','source':SRC,'operator_trust_asserted':True,'automatic_untrusted_execution':False,'original_config_unchanged':True,'temporary_approval_cleanup':'REMOVED','vm_attempt_count_uncertain':False,'vm_start_attempts':1,'components':[row]};demo['result_sha256']=digest(demo);write(dp/'demo_result.json',demo)
    old={'schema_version':'8F-D.integrated.1','mode':'OPERATOR_TRUSTED_LOCAL_DEMO','submission_id':'PHASE8F-DEMO','source':SRC,'static':None,'vm_start_attempts':1,'smoke':{'status':demo['status'],'report':'demo/demo_result.json','sha256':checked_file(dp/'demo_result.json',4000000)['sha256'],'components':[row]}};old['result_sha256']=digest(old);write(prior/'integrated_result.json',old)
    monkeypatch.setattr(recover_static,'audit_runtime',lambda *a,**kw:ev)
    def forbidden(*a,**kw):raise AssertionError('No demo dispatch permitted')
    monkeypatch.setattr(integration,'run_demo',forbidden)
    before=(prior/'integrated_result.json').read_bytes()
    r=recover_static.recover(prior,tmp_path/'recovered')
    assert r['status']=='DEMO_AND_STATIC_COMPLETE' and r['vm_start_attempts']==0 and r['prior_vm_start_attempts']==1
    assert (prior/'integrated_result.json').read_bytes()==before
    (dp/'demo_result.json').write_text('{}')
    with pytest.raises(ValueError):recover_static.recover(prior,tmp_path/'bad')
