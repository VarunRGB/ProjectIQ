"""Offline corpus and acceptance-ledger tests; no GitHub calls or VM starts."""
import copy, json, contextlib, hashlib, zipfile
from pathlib import Path
import pytest
from projectiq_executioner.phase8e import corpus as c, campaign as e, audit as a
from projectiq_executioner.phase8e.__main__ import bundle_reports
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8d.planner import SUPPORTED
from projectiq_executioner.phase8d.worker import BUILDS,CLI
from projectiq_executioner.phase8b.bridge import CLEAN
from projectiq_executioner.phase8c_final.dispatch import write

SOURCE={'kind':'github','repository':'Example/Review','commit_sha':'a'*40}

@pytest.mark.parametrize('name,adapter,files,blockers,inf',c.controls(),ids=[x[0] for x in c.controls()])
def test_corpus_plan(tmp_path,name,adapter,files,blockers,inf):
    p=tmp_path/'src';c.write_tree(p,files);plan=e.save_plan(p,tmp_path/'out',allow_inferred=inf)
    result=e.classify_plan(plan,adapter,expected_blockers=blockers)
    assert result['outcome']==('EXPECTED_BLOCK' if blockers else 'CANDIDATE'),result


def test_fixture_export_and_multi(tmp_path):
    p=tmp_path/'src';c.write_tree(p,c.export_files());e.verify_fixture_tree(p)
    assert not list(p.rglob('package-lock.json'))
    plan=e.save_plan(p,tmp_path/'out',allow_inferred=True)
    assert len(plan['components'])==len(c.SMOKE)==14
    assert all(e.classify_plan(plan,adapter,k)['outcome']=='CANDIDATE' for k,(adapter,_) in c.SMOKE.items())
    assert plan['composition']=='INDEPENDENT_COMPONENTS_ONLY_RELATIONSHIPS_UNVERIFIED'

@pytest.mark.parametrize('change',['extra','changed','missing'])
def test_fixture_tree_refuses_modified_source(tmp_path,change):
    p=tmp_path/'src';c.write_tree(p,c.export_files());f=p/'static'/'index.html'
    if change=='extra':(p/'evil.py').write_text('print(0)')
    elif change=='changed':f.write_text('changed')
    else:f.unlink()
    with pytest.raises(ValueError):e.verify_fixture_tree(p)


def test_local_campaign(tmp_path):
    out=tmp_path/'campaign';r=e.campaign(out,local_only=True)
    assert r['status']=='PLANNING_CAMPAIGN_COMPLETE' and r['summary']['synthetic_controls_passed']==35 and r['summary']['synthetic_adapter_types_planned']==21
    assert r['summary']['new_live_smoke_passes']==r['summary']['live_attempts_observed']==0 and not r['summary']['live_attempt_count_uncertain']
    assert a.read(out/'campaign_result.json','campaign_sha256')==r
    assert (out/'coverage.md').is_file()

@pytest.mark.parametrize('args',[{'max_vms':0},{'max_vms':True},{'max_vms':9},{'live_cases':['x','x'],'vm_config':'config'},{'live_cases':['x']},{'local_only':True,'vm_config':'config'},{'fetch':True},{'fixture_repo':'https://github.com/a/b'},{'fixture_repo':'http://github.com/a/b','fixture_ref':'a'*40}])
def test_invalid_campaign_options(tmp_path,args):
    out=tmp_path/'out'
    with pytest.raises(ValueError):e.campaign(out,**args)
    assert not out.exists()


def rehash(obj,field):
    obj[field]=digest({k:v for k,v in obj.items() if k!=field});return obj


def make_run(path,adapter='STATIC_HTTP',root='.',source=SOURCE,fixture=None):
    # Actual Phase 8D plan, synthetic worker receipt, no VM.
    files=next(f for _,ad,f,b,_ in c.controls() if ad==adapter and not b)
    p=fixture or path.parent/(path.name+'-source')
    if fixture is None:c.write_tree(p,files)
    plan=e.save_plan(p,path,source,allow_inferred=True);row=next(r for r in plan['components'] if r['root']==root);assert row['adapter']==adapter
    comp=row['id'];dep=row['dependency']['kind'];(path/comp).mkdir()
    bundle=None
    if dep!='NONE':
        f=path/comp/('manifest-inputs.zip' if dep=='NPM' else 'python-wheelhouse.zip');f.write_bytes(b'synthetic bytes');bundle=hashlib.sha256(f.read_bytes()).hexdigest()
    ev={'status':'CLI_EXIT_ZERO_OBSERVED' if adapter in CLI else 'HTTP_READINESS_OBSERVED','source':source,'component_id':comp,'adapter':adapter,'snapshot_sha256':'b'*64,'plan_sha256':plan['plan_sha256'],'dependency_kind':dep,'dependency_sha256':bundle,'host_gateway_denied':True,'workspace_mib':1536,'build_handoffs':int(adapter in BUILDS),'npm_install_handoffs':int(dep=='NPM'),'pip_install_handoffs':2 if dep=='PYTHON' else 0,'functional_tests_executed':0}
    if adapter in CLI:ev.update(cli_exit_code=0,cli_preexec_handoff=True)
    else:ev.update(server_placement_observed=True,application_uid_listener_observed=True,service_port=row['service_port'],http={'status':'HTTP_2XX_OBSERVED','path':row['probe_path'],'http_status':200})
    status='REVIEWED_CLI_EXIT_ZERO_VERIFIED' if adapter in CLI else 'REVIEWED_HTTP_READINESS_VERIFIED'
    w={'status':status,'stage':'COMPLETE','source':source,'component_id':comp,'adapter':adapter,'runtime_permission':False,'security_attestation':a.ATTESTATION,'cleanup':CLEAN,'vm_start_attempts':1,'functional_tests_executed':0,'network':'QEMU_RESTRICT_ON','evidence':ev,'storage_watchdog':{'tripped':False,'reason':None,'samples':2,'peak_logical_file_bytes':100,'budget_bytes':1000,'global_disk_bound_verified':False}}
    rehash(w,'result_sha256');write(path/comp/'runtime_result.json',w)
    d={'schema_version':'8D.dispatch.1','status':'REVIEWED_COMPONENT_SMOKE_COMPLETE','stage':'COMPLETE','source':source,'plan_sha256':plan['plan_sha256'],'runtime_permission':False,'automatic_untrusted_execution':False,'security_attestation':a.ATTESTATION,'functional_tests_executed':0,'vm_start_attempts':1,'exact_sha_approved':True,'snapshot':{'sha256':'b'*64},'components':[{'id':comp,'root':root,'adapter':adapter,'report':comp+'/runtime_result.json','status':status,'cleanup':CLEAN,'result_sha256':w['result_sha256']}]}
    rehash(d,'result_sha256');write(path/'dispatch_result.json',d);return d,w

@pytest.mark.parametrize('adapter',sorted(SUPPORTED))
def test_runtime_auditor_accepts_exact_smoke_shape(tmp_path,adapter):
    path=tmp_path/'run';make_run(path,adapter)
    r=a.audit_runtime(path,SOURCE,'.',adapter,require_artifacts=True)
    assert r['status']=='REVIEWED_SMOKE_VERIFIED' and r['functional_tests_executed']==0 and r['vm_attempts']==1

@pytest.mark.parametrize('target,key,value',[
 ('w','cleanup','UNVERIFIED'),('w','network','ON'),('w','runtime_permission',True),('w','security_attestation','ATTESTED'),('w','functional_tests_executed',1),('w','vm_start_attempts',True),('w','source',{'commit_sha':'x'}),('w','stage','OTHER'),('w','adapter','NODE_HTTP'),('guard','tripped',True),('guard','samples',0),('guard','samples',True),('guard','peak_logical_file_bytes',1000),('guard','global_disk_bound_verified',True),('ev','host_gateway_denied',False),('ev','application_uid_listener_observed',False),('ev','snapshot_sha256','c'*64),('ev','plan_sha256','c'*64),('ev','build_handoffs',1),('ev','functional_tests_executed',1),('http','http_status',302),('http','path','/unexpected'),('d','exact_sha_approved',False),('d','automatic_untrusted_execution',True),('d','vm_start_attempts',2),('ref','report','../../bad.json'),('ref','root','another'),('ref','result_sha256','c'*64)])
def test_rehashed_tampering_refused(tmp_path,target,key,value):
    path=tmp_path/'run';d,w=make_run(path);comp=w['component_id']
    obj={'w':w,'guard':w['storage_watchdog'],'ev':w['evidence'],'http':w['evidence']['http'],'d':d,'ref':d['components'][0]}[target];obj[key]=value
    if target in {'w','guard','ev','http'}:
        rehash(w,'result_sha256');d['components'][0]['result_sha256']=w['result_sha256'];(path/comp/'runtime_result.json').write_text(json.dumps(w))
    rehash(d,'result_sha256');(path/'dispatch_result.json').write_text(json.dumps(d))
    with pytest.raises(ValueError):a.audit_runtime(path,SOURCE,'.','STATIC_HTTP')


def test_plain_checksum_tamper(tmp_path):
    path=tmp_path/'run';d,w=make_run(path);d['stage']='TAMPER';(path/'dispatch_result.json').write_text(json.dumps(d))
    with pytest.raises(ValueError,match='REPORT_DIGEST_MISMATCH'):a.audit_runtime(path,SOURCE,'.','STATIC_HTTP')


def test_package_artifact_rehashed(tmp_path):
    path=tmp_path/'run';d,w=make_run(path,'PY_ASGI');(path/w['component_id']/'python-wheelhouse.zip').write_bytes(b'different')
    with pytest.raises(ValueError,match='DEPENDENCY_ARTIFACT_CHANGED'):a.audit_runtime(path,SOURCE,'.','PY_ASGI',require_artifacts=True)


def test_negative_classification_does_not_count_candidate():
    plan={'components':[{'root':'.','status':'REVIEWED_CANDIDATE','blockers':[],'adapter':'STATIC_HTTP','dependency':{'kind':'NONE'},'optional_toolchain':False}]}
    assert e.classify_plan(plan,'STATIC_HTTP',expected_blockers=['SOME_BLOCKER'])['outcome']=='UNEXPECTED_ADMISSION'


def test_public_case_pin_shape_and_uniqueness():
    assert len(c.PUBLIC)==len({x[0] for x in c.PUBLIC})==7
    for _,repo,sha,adapter,_,_ in c.PUBLIC:
        assert e.exact_source('https://github.com/'+repo,sha)['repository']==repo and adapter in SUPPORTED


def test_live_requires_existing_approval_and_does_not_dispatch(tmp_path,monkeypatch):
    fixture=tmp_path/'src';c.write_tree(fixture,c.export_files())
    @contextlib.contextmanager
    def pinned(url,sha):yield SOURCE,fixture
    from projectiq_executioner.orchestration import adapters
    monkeypatch.setattr(adapters,'pinned_checkout',pinned)
    cfg=tmp_path/'cfg.json';cfg.write_text('{}');monkeypatch.setattr(e,'source_approved',lambda *x:False)
    monkeypatch.setattr(e.runtime,'dispatch',lambda *x,**k:pytest.fail('dispatch must not run'))
    r=e.campaign(tmp_path/'out',skip_public=True,fixture_repo='https://github.com/Example/Review',fixture_ref='a'*40,vm_config=cfg,live_cases=['fixture-static'])
    assert r['error_code']=='EXISTING_OPERATOR_APPROVAL_REQUIRED' and not r['live_cases'] and r['summary']['live_attempts_observed']==0
    assert cfg.read_text()=='{}'


def test_campaign_stops_on_first_unverified_worker(tmp_path,monkeypatch):
    fixture=tmp_path/'src';c.write_tree(fixture,c.export_files())
    @contextlib.contextmanager
    def pinned(url,sha):yield SOURCE,fixture
    from projectiq_executioner.orchestration import adapters
    monkeypatch.setattr(adapters,'pinned_checkout',pinned)
    cfg=tmp_path/'cfg.json';cfg.write_text('{}');monkeypatch.setattr(e,'source_approved',lambda *x:True);monkeypatch.setattr(e,'assert_no_qemu',lambda:None)
    calls=[]
    def failed(url,sha,dest,**opts):calls.append(dest);raise RuntimeError('unknown worker outcome')
    monkeypatch.setattr(e.runtime,'dispatch',failed)
    r=e.campaign(tmp_path/'out',skip_public=True,fixture_repo='https://github.com/Example/Review',fixture_ref='a'*40,vm_config=cfg,live_cases=['fixture-static','fixture-php'])
    assert r['status']=='STOPPED_ON_GATE' and len(calls)==1 and r['summary']['live_attempt_count_uncertain'] and r['summary']['new_live_smoke_passes']==0


def test_bundle_only_safe_named_reports(tmp_path):
    root=tmp_path/'out';root.mkdir();(root/'campaign_result.json').write_text('{}');(root/'snapshot.zip').write_bytes(b'not report');(root/'vm_config.json').write_text('secret');dest=tmp_path/'review.zip';bundle_reports(root,dest)
    with zipfile.ZipFile(dest) as z:assert z.namelist()==['campaign_result.json']


def test_selected_smoke_batch_verified_sequentially(tmp_path,monkeypatch):
    fixture=tmp_path/'src';c.write_tree(fixture,c.export_files())
    @contextlib.contextmanager
    def pinned(url,sha):yield SOURCE,fixture
    from projectiq_executioner.orchestration import adapters
    monkeypatch.setattr(adapters,'pinned_checkout',pinned)
    cfg=tmp_path/'cfg.json';cfg.write_text('{}');monkeypatch.setattr(e,'source_approved',lambda *x:True);monkeypatch.setattr(e,'assert_no_qemu',lambda:None)
    calls=[]
    def run(url,sha,dest,**opts):
        root=opts['component'];calls.append(root);assert opts['max_vms']==1 and opts['run_reviewed'] is True and opts['fetch'] is False
        return make_run(dest,c.SMOKE[root][0],root,SOURCE,fixture)[0]
    monkeypatch.setattr(e.runtime,'dispatch',run)
    r=e.campaign(tmp_path/'out',skip_public=True,fixture_repo='https://github.com/Example/Review',fixture_ref='a'*40,vm_config=cfg,live_cases=['fixture-static','fixture-php'])
    assert r['status']=='REVIEWED_SELECTED_CAMPAIGN_COMPLETE',r
    assert calls==['static','php'] and r['summary']['new_live_adapter_types']==2 and r['summary']['live_attempts_observed']==2
    assert r['summary']['functional_tests_executed']==0 and not r['automatic_untrusted_execution']


def test_public_refusal_backstop_and_zero_vm(tmp_path,monkeypatch):
    # Pin a small mocked public candidate. Exercise real plan, refusal receipt shape.
    p=tmp_path/'src';c.write_tree(p,c.SMOKE['static'][1]);cfg=tmp_path/'cfg.json';cfg.write_text('{}')
    monkeypatch.setattr(e,'PUBLIC',(('public-x','Example/Review','a'*40,'STATIC_HTTP',(),'candidate'),))
    @contextlib.contextmanager
    def pinned(url,sha):yield SOURCE,p
    from projectiq_executioner.orchestration import adapters
    monkeypatch.setattr(adapters,'pinned_checkout',pinned);monkeypatch.setattr(e,'source_approved',lambda *x:False)
    def refuse(url,sha,dest,**opts):
        assert opts['component']=='__8e_negative_never_exists__' and opts['run_reviewed'] is True and not opts.get('fetch')
        plan=e.save_plan(p,dest,SOURCE)
        d={'schema_version':'8D.dispatch.1','source':SOURCE,'plan_sha256':plan['plan_sha256'],'runtime_permission':False,'automatic_untrusted_execution':False,'security_attestation':a.ATTESTATION,'functional_tests_executed':0,'vm_start_attempts':0,'status':'FAILED_OR_BLOCKED','error_code':'EXISTING_OPERATOR_APPROVAL_REQUIRED','exact_sha_approved':False,'components':[]}
        write(dest/'dispatch_result.json',rehash(d,'result_sha256'));return d
    monkeypatch.setattr(e.runtime,'dispatch',refuse)
    r=e.campaign(tmp_path/'out',vm_config=cfg)
    assert r['negative_control']['status']=='UNAPPROVED_REFUSAL_VERIFIED' and r['summary']['live_attempts_observed']==0
