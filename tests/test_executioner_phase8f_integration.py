"""8F-C/D local tests; no network or real VM launches."""
import json
from contextlib import contextmanager
from pathlib import Path
import pytest
from projectiq_executioner.phase8f import demo,profile,integration
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import checked_file
from projectiq_executioner.phase8c_final.dispatch import write
from projectiq_executioner.phase8f.__main__ import main

SRC={'kind':'github','repository':'Example/Repo','commit_sha':'a'*40}
URL='https://github.com/Example/Repo'

@pytest.fixture
def local(tmp_path):
    cfg=tmp_path/'vm_config.json';cfg.write_text(json.dumps({'base_image':'unused','base_sha256':'b'*64,'approved_snapshots':[]}))
    node=tmp_path/'node-v24.22.3-linux-x64.tar.xz';node.write_bytes(b'fake-node')
    pip=tmp_path/'pip-25.2-py3-none-any.whl';pip.write_bytes(b'fake-pip')
    opts={'node_tarball':node,'node_sha256':checked_file(node,100)['sha256'],'pip_wheel':pip,'pip_sha256':checked_file(pip,100)['sha256']}
    return cfg,tmp_path/profile.NAME,opts


def test_profile_roundtrip(local):
    cfg,p,opts=local;before=cfg.read_bytes();rec=profile.configure(p,**opts)
    assert profile.load_profile(p)==(opts,rec) and cfg.read_bytes()==before
    with pytest.raises(ValueError,match='ALREADY_EXISTS'):profile.configure(p,**opts)

@pytest.mark.parametrize('field',['node_sha256','pip_sha256'])
def test_wrong_digest_not_written(local,field):
    _,p,opts=local;opts[field]='0'*64
    with pytest.raises(ValueError,match='HASH_MISMATCH'):profile.configure(p,**opts)
    assert not p.exists()

@pytest.mark.parametrize('asset',['node_tarball','pip_wheel'])
def test_tampered_assets(local,asset):
    _,p,opts=local;profile.configure(p,**opts);opts[asset].write_bytes(b'changed')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):profile.load_profile(p)

@pytest.mark.parametrize('change',[{'trust_demo_source':True},{'approved_snapshots':[]},{'schema_version':'wrong'}])
def test_profile_cannot_grant_trust(local,change):
    _,p,opts=local;profile.configure(p,**opts);obj=json.loads(p.read_text());obj.update(change);p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='PROFILE_INVALID'):profile.load_profile(p)


def test_configure_cli(local):
    cfg,p,opts=local
    args=['configure-demo-assets','--vm-config',str(cfg)]
    for k,v in opts.items():args+=['--'+k.replace('_','-'),str(v)]
    assert main(args)==0 and p.exists()


def test_wrong_profile_filename(local):
    _,p,opts=local
    with pytest.raises(ValueError,match='PATH_INVALID'):profile.configure(p.with_name('other.json'),**opts)


def checkout(monkeypatch,tmp_path,files):
    root=tmp_path/'src';root.mkdir()
    for name,body in files.items():(root/name).write_text(body)
    @contextmanager
    def pinned(url,ref):yield SRC,root
    monkeypatch.setattr(demo,'pinned_checkout',pinned)
    monkeypatch.setattr(demo,'assert_no_qemu',lambda:None)

from projectiq_executioner.phase8e.corpus import node

@pytest.mark.parametrize('kind,files', [('NPM',node(['express'],{'server.js':'require("express")().listen(8765)'})),('PYTHON',{'main.py':'from fastapi import FastAPI\napp=FastAPI()','requirements.txt':'fastapi>=0.115,<1\nuvicorn>=0.30,<1\n'})])
def test_url_only_profile_propagates_assets(tmp_path,local,monkeypatch,kind,files):
    cfg,p,opts=local;profile.configure(p,**opts);checkout(monkeypatch,tmp_path,files);seen=[]
    def fake(*a,**k):seen.append((a,k));raise RuntimeError('offline dispatch stub')
    monkeypatch.setattr(demo.runtime,'dispatch',fake)
    r=demo.run_demo(URL,None,tmp_path/'out',vm_config=cfg,trust_demo_source=True,require_unapproved=True,expected_dependency=kind)
    assert len(seen)==1,r
    assert seen[0][0][1]==SRC['commit_sha'] and seen[0][1]['fetch']
    assert all(seen[0][1][k]==v for k,v in opts.items())
    assert r['original_exact_sha_approved'] is False and r['original_config_unchanged'] and r['temporary_approval_cleanup']=='REMOVED'


def test_expected_dependency_mismatch(tmp_path,local,monkeypatch):
    cfg,_,_=local;checkout(monkeypatch,tmp_path,{'index.html':'hi'})
    r=demo.run_demo(URL,None,tmp_path/'out',vm_config=cfg,trust_demo_source=True,expected_dependency='NPM')
    assert r['error_code']=='DEMO_DEPENDENCY_EXPECTATION_MISMATCH' and r['vm_start_attempts']==0


def test_already_approved_not_acceptance(tmp_path,local,monkeypatch):
    cfg,_,_=local;c=json.loads(cfg.read_text());c['approved_snapshots']=['Example/Repo@'+'a'*40];cfg.write_text(json.dumps(c));checkout(monkeypatch,tmp_path,{'index.html':'hi'})
    r=demo.run_demo(URL,None,tmp_path/'out',vm_config=cfg,trust_demo_source=True,require_unapproved=True)
    assert r['error_code']=='DEMO_SOURCE_ALREADY_APPROVED' and r['vm_start_attempts']==0


def test_explicit_profile_conflict(tmp_path,local):
    cfg,p,opts=local;profile.configure(p,**opts)
    r=demo.run_demo(URL,None,tmp_path/'out',vm_config=cfg,trust_demo_source=True,profile=p,**opts)
    assert r['error_code']=='DEMO_ASSET_PROFILE_CONFLICT'


def test_integrated_requires_trust(tmp_path,local):
    with pytest.raises(ValueError,match='EXPLICIT_DEMO_TRUST_REQUIRED'):integration.evaluate_trusted_demo(URL,None,tmp_path/'out',vm_config=local[0])

@pytest.mark.parametrize('demo_status,static_source,expected',[('DEMO_REVIEWED_SMOKE_COMPLETE',SRC,'DEMO_AND_STATIC_COMPLETE'),('FAILED_OR_BLOCKED',SRC,'COMPLETED_WITH_LIMITATIONS'),('DEMO_REVIEWED_SMOKE_COMPLETE',{**SRC,'commit_sha':'b'*40},'INCOMPLETE')])
def test_integrated_pins_static_and_separates_evidence(tmp_path,local,monkeypatch,demo_status,static_source,expected):
    def fake_demo(url,ref,out,**options):
        assert ref is None and options['trust_demo_source'];out.mkdir();r={'source':SRC,'status':demo_status,'vm_start_attempts':1,'vm_attempt_count_uncertain':False,'components':[],'original_config_unchanged':True,'temporary_approval_cleanup':'REMOVED'};r['result_sha256']=digest(r);write(out/'demo_result.json',r);return r
    def fake_static(request,*,output_dir):
        assert request['ref']==SRC['commit_sha'] and request['mode']=='STATIC' and request['max_vm_launches']==0
        output_dir.mkdir();r={'source':static_source,'effective_mode':'STATIC','vm_attempts_scheduled':0,'status':'COMPLETED_WITH_LIMITATIONS'};write(output_dir/'executioner_result.json',r);return r
    monkeypatch.setattr(integration,'run_demo',fake_demo);monkeypatch.setattr(integration,'evaluate',fake_static)
    r=integration.evaluate_trusted_demo(URL,None,tmp_path/'out',trust_demo_source=True,vm_config=local[0]);assert r['status']==expected and r['functional_tests_executed']==0 and r['vm_start_attempts']==1
    assert r['result_sha256']==digest({k:v for k,v in r.items() if k!='result_sha256'})
