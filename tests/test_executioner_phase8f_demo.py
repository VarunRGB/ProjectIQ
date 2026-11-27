"""Offline demo tests: no network, no VMs, no real package acquisition."""
import json
from contextlib import contextmanager
import pytest
from projectiq_executioner.phase8f import demo as d
from projectiq_executioner.phase8f.__main__ import main
from projectiq_executioner.phase8e.corpus import controls, write_tree, export_files
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.bridge import checked_file,CLEAN
from projectiq_executioner.phase8c_final.dispatch import source_approved
from projectiq_executioner.orchestration import adapters
from projectiq_executioner.phase8d import worker

SOURCE={'kind':'github','repository':'Example/Review','commit_sha':'a'*40}
URL='https://github.com/Example/Review'

@pytest.fixture
def cfg(tmp_path,monkeypatch):
    monkeypatch.setattr(d,'assert_no_qemu',lambda:None)
    p=tmp_path/'vm.json';p.write_text(json.dumps({'base_image':'unused','base_sha256':'b'*64,'approved_snapshots':['Other/Repo@'+'c'*40]}));return p


def checkout(monkeypatch,root,source=SOURCE):
    refs=[]
    @contextmanager
    def pinned(url,ref):
        refs.append(ref);yield source,root
    monkeypatch.setattr(d,'pinned_checkout',pinned);monkeypatch.setattr(adapters,'pinned_checkout',pinned)
    return refs


def invoke(tmp_path,cfg,**opts):
    return d.run_demo(URL,opts.pop('ref',None),tmp_path/'out',vm_config=cfg,trust_demo_source=True,**opts)


def test_explicit_trust_required(tmp_path,cfg,monkeypatch):
    monkeypatch.setattr(d,'pinned_checkout',lambda *args:pytest.fail('unexpected acquisition'))
    with pytest.raises(ValueError,match='EXPLICIT_DEMO_TRUST_REQUIRED'):
        d.run_demo(URL,None,tmp_path/'out',vm_config=cfg)
    assert not (tmp_path/'out').exists()


def test_cli_requires_trust(tmp_path,cfg):
    with pytest.raises(SystemExit) as e:main(['demo','--repo',URL,'--vm-config',str(cfg),'--out',str(tmp_path/'out')])
    assert e.value.code==2


def test_temporary_approval_exact_and_cleanup(tmp_path,cfg):
    before=checked_file(cfg,100_000)
    with pytest.raises(RuntimeError):
        with d.temporary_approval(cfg,before,SOURCE) as (path,receipt):
            assert checked_file(path,100_000)==receipt
            assert json.loads(path.read_text())['approved_snapshots']==['Example/Review@'+'a'*40]
            assert not source_approved(SOURCE,cfg) and source_approved(SOURCE,path)
            raise RuntimeError('simulated failure')
    assert not path.parent.exists() and checked_file(cfg,100_000)==before


@pytest.mark.parametrize('name,adapter,files,blockers,inferred',controls(),ids=[r[0] for r in controls()])
def test_all_planner_controls(tmp_path,cfg,monkeypatch,name,adapter,files,blockers,inferred):
    root=tmp_path/'src';write_tree(root,files);checkout(monkeypatch,root)
    calls=[];temps=[]
    # Fake trusted assets are only used in this offline boundary test.
    node=tmp_path/'node-v24.22.3-linux-x64.tar.xz';node.write_bytes(b'fake')
    pip=tmp_path/'pip-25.2-py3-none-any.whl';pip.write_bytes(b'fake')
    def fake(url,ref,dest,**opts):
        calls.append((url,ref,opts));temps.append(opts['vm_config'])
        assert ref==SOURCE['commit_sha'] and source_approved(SOURCE,opts['vm_config'])
        assert opts['run_reviewed'] is True and opts['max_vms']==1
        raise RuntimeError('mock dispatch interrupted')
    monkeypatch.setattr(d.runtime,'dispatch',fake)
    r=invoke(tmp_path,cfg,allow_inferred=inferred,enable_optional_toolchains=True,node_tarball=node,node_sha256=checked_file(node,100)['sha256'],pip_wheel=pip,pip_sha256=checked_file(pip,100)['sha256'])
    assert bool(calls)==(not blockers),r
    assert r['automatic_untrusted_execution'] is False and r['original_config_unchanged'] is True
    assert r['vm_attempt_count_uncertain']==bool(calls) and r['status']=='FAILED_OR_BLOCKED'
    assert r['temporary_approval_cleanup']==('REMOVED' if calls else 'NOT_CREATED')
    assert all(not t.exists() for t in temps)
    assert r['result_sha256']==digest({k:v for k,v in r.items() if k!='result_sha256'})


def test_actual_dispatch_with_synthetic_worker(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'<h1>Demo</h1>'});refs=checkout(monkeypatch,root)
    seen=[]
    def fake_worker(**opts):
        source=opts['source'];plan=opts['plan'];row=opts['component'];path=opts['vm_config'];seen.append(path)
        assert source_approved(source,path) and not source_approved(source,cfg)
        ev={'status':'HTTP_READINESS_OBSERVED','source':source,'component_id':row['id'],'adapter':row['adapter'],'snapshot_sha256':checked_file(opts['snapshot'],30_000_000)['sha256'],'plan_sha256':plan['plan_sha256'],'dependency_kind':'NONE','dependency_sha256':None,'host_gateway_denied':True,'workspace_mib':1536,'build_handoffs':0,'npm_install_handoffs':0,'pip_install_handoffs':0,'functional_tests_executed':0,'server_placement_observed':True,'application_uid_listener_observed':True,'service_port':row['service_port'],'http':{'status':'HTTP_2XX_OBSERVED','path':row['probe_path'],'http_status':200}}
        return {'status':'REVIEWED_HTTP_READINESS_VERIFIED','stage':'COMPLETE','source':source,'component_id':row['id'],'adapter':row['adapter'],'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','cleanup':CLEAN,'vm_start_attempts':1,'functional_tests_executed':0,'network':'QEMU_RESTRICT_ON','evidence':ev,'storage_watchdog':{'tripped':False,'reason':None,'samples':2,'peak_logical_file_bytes':100,'budget_bytes':1000,'global_disk_bound_verified':False}}
    monkeypatch.setattr(worker,'execute_reviewed',fake_worker)
    r=invoke(tmp_path,cfg)
    assert r['status']=='DEMO_REVIEWED_SMOKE_COMPLETE',r
    assert refs==[None,SOURCE['commit_sha']] and r['vm_start_attempts']==1
    assert r['components'][0]['evidence']['http_status']==200 and r['temporary_approval_cleanup']=='REMOVED'
    assert r['original_config_unchanged'] is True and not seen[0].exists()
    assert not source_approved(SOURCE,cfg)
    # Ordinary 8D must still reject this exact same source with original config.
    normal=d.runtime.dispatch(URL,SOURCE['commit_sha'],tmp_path/'ordinary',run_reviewed=True,vm_config=cfg)
    assert normal['error_code']=='EXISTING_OPERATOR_APPROVAL_REQUIRED' and normal['vm_start_attempts']==0
    assert len(seen)==1


@pytest.mark.parametrize('opts,expected',[({'ref':'b'*40},'SOURCE_IDENTITY_MISMATCH'),({},'EXPLICIT_MULTI_COMPONENT_SELECTION_REQUIRED'),({'all_independent':True},'VM_BUDGET_EXCEEDED'),({'component':'not-found'},'COMPONENT_SELECTION_NOT_UNIQUE')])
def test_selection_and_pin_denials(tmp_path,cfg,monkeypatch,opts,expected):
    root=tmp_path/'src';write_tree(root,export_files());checkout(monkeypatch,root)
    monkeypatch.setattr(d.runtime,'dispatch',lambda *a,**k:pytest.fail('unexpected launch'))
    r=invoke(tmp_path,cfg,**opts);assert r['error_code']==expected and r['vm_start_attempts']==0 and r['temporary_approval_cleanup']=='NOT_CREATED'


def test_missing_node_asset(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'server.js':'const http=require("http");http.createServer((q,s)=>s.end("ok")).listen(8765)'});checkout(monkeypatch,root)
    r=invoke(tmp_path,cfg);assert r['error_code']=='NODE_ASSET_MISMATCH' and r['temporary_approval_cleanup']=='NOT_CREATED'


def test_optional_toolchain_requires_opt_in(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'main.c':'int main(void){return 0;}'});checkout(monkeypatch,root)
    r=invoke(tmp_path,cfg);assert r['error_code']=='OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED'


def test_config_change_during_preflight(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'hi'})
    @contextmanager
    def pinned(*args):
        yield SOURCE,root
        cfg.write_text('{}')
    monkeypatch.setattr(d,'pinned_checkout',pinned)
    r=invoke(tmp_path,cfg);assert r['status']=='FAILED_OR_BLOCKED' and r['original_config_unchanged'] is False and r['vm_start_attempts']==0


def test_missing_pip_asset(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'main.py':'from fastapi import FastAPI\napp=FastAPI()','requirements.txt':'fastapi>=0.115,<1\nuvicorn>=0.30,<1\n'});checkout(monkeypatch,root)
    r=invoke(tmp_path,cfg);assert r['error_code']=='PIP_ASSET_MISMATCH' and r['temporary_approval_cleanup']=='NOT_CREATED'


def test_all_independent_stops_at_first_failure(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'a/index.html':'<h1>A</h1>','b/index.php':'<?php echo "B";'});checkout(monkeypatch,root)
    calls=[]
    def fake(*args,**opts):
        calls.append(opts['component']);raise KeyboardInterrupt()
    monkeypatch.setattr(d.runtime,'dispatch',fake)
    r=invoke(tmp_path,cfg,all_independent=True,max_vms=2)
    assert len(calls)==1 and r['vm_attempt_count_uncertain'] is True and r['temporary_approval_cleanup']=='REMOVED'


def test_wrong_repository_identity(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'hi'});checkout(monkeypatch,root,{**SOURCE,'repository':'Other/Repo'})
    r=invoke(tmp_path,cfg);assert r['error_code']=='SOURCE_IDENTITY_MISMATCH' and r['vm_start_attempts']==0


def test_fresh_output_required(tmp_path,cfg):
    (tmp_path/'out').mkdir();p=tmp_path/'out'/'preserve';p.write_text('original')
    with pytest.raises(ValueError,match='OUTPUT_NOT_FRESH'):invoke(tmp_path,cfg)
    assert p.read_text()=='original'


def test_known_dispatch_refusal_preserves_code(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'hi'});checkout(monkeypatch,root)
    def fake_worker(**opts):raise ValueError('HOST_TOOLCHAIN_UNVERIFIED')
    monkeypatch.setattr(worker,'execute_reviewed',fake_worker)
    r=invoke(tmp_path,cfg)
    assert r['error_code']=='DEMO_RUNTIME_NOT_VERIFIED' and r['components'][0]['error_code']=='HOST_TOOLCHAIN_UNVERIFIED'
    assert r['vm_start_attempts']==0 and not r['vm_attempt_count_uncertain'] and r['temporary_approval_cleanup']=='REMOVED'


def test_busy_worker_refused(tmp_path,cfg,monkeypatch):
    root=tmp_path/'src';write_tree(root,{'index.html':'hi'});checkout(monkeypatch,root)
    def busy():raise ValueError('QEMU_PROCESS_PRESENT_OR_UNVERIFIED')
    monkeypatch.setattr(d,'assert_no_qemu',busy)
    r=invoke(tmp_path,cfg);assert r['error_code']=='QEMU_PROCESS_PRESENT_OR_UNVERIFIED' and r['temporary_approval_cleanup']=='NOT_CREATED'
