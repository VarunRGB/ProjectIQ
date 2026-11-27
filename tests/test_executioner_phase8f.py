import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from projectiq_executioner.phase8f import admission as a, qualification as q
from projectiq_executioner.phase8f.__main__ import main
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8d.planner import compile_runtime
from projectiq_executioner.phase8e.corpus import controls, write_tree

SOURCE={'kind':'github','repository':'Example/Review','commit_sha':'a'*40}

@pytest.mark.parametrize('name,adapter,files,blockers,inferred',controls(),ids=[r[0] for r in controls()])
def test_actual_planner_controls(tmp_path,name,adapter,files,blockers,inferred):
    root=tmp_path/'src';write_tree(root,files)
    _,plan,_=compile_runtime(root,source=SOURCE,allow_inferred=inferred)
    r=a.assess(plan,SOURCE)
    expected=not blockers and adapter in a.INITIAL_ADAPTERS
    assert (r['compatibility']=='CANDIDATE_ONLY')==expected,r
    assert r['decision']=='DENY_AUTOMATIC_EXECUTION'
    assert r['runtime_permission'] is False and r['automatic_untrusted_execution'] is False and r['vm_start_attempts']==0
    assert r['result_sha256']==digest({k:v for k,v in r.items() if k!='result_sha256'})

@pytest.fixture
def plan(tmp_path):
    root=tmp_path/'src';write_tree(root,{'index.html':'<h1>hello</h1>'});return compile_runtime(root,source=SOURCE)[1]

@pytest.mark.parametrize('field,value',[('runtime_permission',True),('automatic_untrusted_execution',True),('vm_launches',True),('composition','OTHER'),('components',[]),('snapshot_inspection',{'issues':['X']}),('source',{'commit_sha':'b'*40})])
def test_rehashed_bad_plan(plan,field,value):
    plan[field]=value;plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
    r=a.assess(plan,SOURCE);assert r['compatibility']=='BLOCKED' and r['runtime_permission'] is False

def test_digest_tampering(plan):
    plan['status']='altered';assert a.assess(plan,SOURCE)['compatibility']=='BLOCKED'

def test_no_qualification_even_all_observations_good(tmp_path,monkeypatch):
    p=tmp_path/'vm.json';p.write_text(json.dumps({'base_image':'not-used','base_sha256':'a'*64,'approved_snapshots':[]}))
    monkeypatch.setattr(q,'verify_host_toolchain',lambda _: {'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED','template_sha256_matches':True})
    monkeypatch.setattr(q.acl,'audit',lambda _: {'status':q.acl.SUCCESS,'checks':{k:'PASS' for k in ('parent_acl','root_acl','key_acl','lease_acl','registered_root_valid')}})
    monkeypatch.setattr(q,'ManagedRuntimeRun',lambda _: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(q,'inspect_volume',lambda _: {'total_bytes':32*q.GIB,'free_bytes':30*q.GIB,'separate_volume_observed':True,'volume_capacity_bounded':True})
    before=p.read_bytes();r=q.qualify(p);assert p.read_bytes()==before
    assert r['checks']['toolchain_image']==r['checks']['registered_root_acl']==r['checks']['separate_bounded_volume']=='OBSERVED'
    assert r['qualification_blockers']==list(q.UNRESOLVED) and r['runtime_permission'] is False
    assert r['vm_start_attempts']==0 and r['config_modified'] is False

def test_missing_config(tmp_path):
    r=q.qualify(tmp_path/'no.json');assert r['status']=='AUTOMATIC_EXECUTION_NOT_QUALIFIED';assert r['runtime_permission'] is False
    assert r['diagnostics']['config']=='CONFIG_UNAVAILABLE_INVALID_OR_CHANGED'

def test_remote_exact_pin_only(monkeypatch):
    monkeypatch.setattr(a,'pinned_checkout',lambda *args: pytest.fail('must not acquire'))
    with pytest.raises(ValueError,match='EXACT_PIN_REQUIRED'):a.plan_remote('https://github.com/Example/Review','main')

def test_remote_fresh_checkout(tmp_path,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'<h1>hello</h1>'})
    @contextmanager
    def checkout(url,ref):
        assert ref==SOURCE['commit_sha'];yield SOURCE,root
    monkeypatch.setattr(a,'pinned_checkout',checkout)
    r=a.plan_remote('https://github.com/Example/Review',SOURCE['commit_sha']);assert r['compatibility']=='CANDIDATE_ONLY' and r['runtime_permission'] is False

def test_cli_qualification_output_is_not_permission(tmp_path):
    out=tmp_path/'out';assert main(['qualify','--vm-config',str(tmp_path/'missing'),'--out',str(out)])==0
    r=json.loads((out/'worker_qualification.json').read_text());assert r['runtime_permission'] is False
    assert main(['qualify','--vm-config',str(tmp_path/'missing'),'--out',str(out)])==2
