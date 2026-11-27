import copy
import json
import sys
import types
import zipfile
from contextlib import contextmanager
from pathlib import Path
import pytest
from projectiq_executioner.phase8a.planner import digest
from projectiq_executioner.phase8b.planner import compile_dependencies
from projectiq_executioner.phase8b.bridge import checked_file, CLEAN, PROFILES
from projectiq_executioner.phase8c.policy import _evaluate, assess_directory, assess_repository
from projectiq_executioner.phase8c.artifacts import inspect_prepared
from projectiq_executioner.phase8c.worker import _run_clean_probes
from projectiq_executioner.phase8c.__main__ import main

SOURCE={'kind':'github','repository':'Owner/Repo','commit_sha':'a'*40}
IDENTITY='Owner/Repo@'+'a'*40


def save(path,obj):path.write_text(json.dumps(obj),encoding='utf-8')
def sign(receipt):
    receipt.pop('result_sha256',None);receipt['result_sha256']=digest(receipt);return receipt

def static(root):
    (root/'index.html').write_text('<html>example</html>');return compile_dependencies(root,source=SOURCE)

def flask(root):
    (root/'app.py').write_text('from flask import Flask\napp = Flask(__name__)');(root/'requirements.txt').write_text('Flask==3.1.1');return compile_dependencies(root,source=SOURCE)

def node(root):
    import base64
    deps={'vite':'^6.0.0','react':'^19.0.0'}
    save(root/'package.json',{'name':'fixture','scripts':{'build':'vite build'},'dependencies':deps})
    save(root/'package-lock.json',{'lockfileVersion':3,'packages':{'':{'dependencies':deps},**{'node_modules/'+x:{'version':'1.0.0','resolved':'https://registry.npmjs.org/'+x+'/-/'+x+'-1.0.0.tgz','integrity':'sha512-'+base64.b64encode(bytes(64)).decode()} for x in deps}}})
    (root/'index.html').write_text('<html></html>');return compile_dependencies(root,source=SOURCE)

def make_receipt(directory,recipe,*,member=None):
    dest=directory/'prepared';dest.mkdir(parents=True)
    job=recipe['jobs'][0];adapter=job['adapter'];is_py=adapter=='python_web_offline_v1';cache,name,key=PROFILES[adapter]
    c=dest/cache;p=dest/name
    with zipfile.ZipFile(c,'w') as z:z.writestr(member or ('wheels/example-1.0-py3-none-any.whl' if is_py else 'npm-cache/_cacache/content-v2/sha512/test'),b'example')
    h=checked_file(c,100000);manifest_name='requirements.txt' if is_py else 'package-lock.json';manifest_key='requirements_sha256' if is_py else 'lockfile_sha256'
    manifest={manifest_key:next(x['sha256'] for x in recipe['components'][0]['dependency_inputs'] if x['path']==manifest_name)}
    profile={**manifest,**({'wheelhouse_sha256':h['sha256'],'python_version':'3.12','framework':'flask','pip_sha256':'b'*64,'pip_wheel':'NEVER_OPEN_THIS_PATH'} if is_py else {'npm_cache_sha256':h['sha256'],'node_version':'v24.22.3','node_sha256':'b'*64,'node_tarball':'NEVER_OPEN_THIS_PATH'})}
    save(p,{key:{IDENTITY:profile}})
    receipt={'schema_version':'8B.1','source':SOURCE,'recipe_sha256':recipe['recipe_sha256'],'status':'PREPARED_REVIEWED_ARTIFACTS','application_vms_started':0,'runtime_permission':False,'cleanup':CLEAN,'preparation_attempts':1,'artifacts':[{'status':'PREPARED_REVIEWED_ARTIFACTS','adapter':adapter,'components':job['components'],'cleanup':CLEAN,'artifact':{'path':'prepared/'+cache,**h},'profile':{'path':'prepared/'+name,**checked_file(p,65536)},'manifest_hashes':manifest,'runtime_permission':False,'attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'}]}
    save(directory/'preparation_result.json',sign(receipt));return receipt,c,p

@pytest.mark.parametrize('approved',[(),(IDENTITY,)])
def test_static_never_authorizes(tmp_path,approved):
    p,r=static(tmp_path);a=_evaluate(p,r,pinned_acquisition=True,approved=approved,worker={'status':'REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED','automatic_admission_supported':True,'runtime_permission':True})
    assert a['automatic_preparation']['status']=='NO_PACKAGE_JOB_REQUIRED'
    assert a['automatic_runtime']['status']=='BLOCKED_AUTOMATIC_WORKER_UNQUALIFIED'
    assert a['runtime_permission'] is False and a['dependency_preparation_permission'] is False
    assert a['reviewed_route']['exact_sha_in_operator_config']==bool(approved)
    assert a['reviewed_route']['permission'] is False
    assert a['assessment_sha256']==digest({k:v for k,v in a.items() if k!='assessment_sha256'})

@pytest.mark.parametrize('builder',[flask,node])
def test_recipes_blocked_for_automatic_preparation(tmp_path,builder):
    p,r=builder(tmp_path);a=_evaluate(p,r,pinned_acquisition=True)
    assert a['automatic_preparation']['status']=='BLOCKED_AUTOMATIC_WORKER_UNQUALIFIED'
    assert 'PREPARATION_EGRESS_CONTAINMENT_UNVERIFIED' in a['automatic_preparation']['worker_blockers']
    assert 'OFFLINE_ARTIFACTS_NOT_VERIFIED' in a['automatic_runtime']['input_blockers']

@pytest.mark.parametrize('builder',[flask,node])
def test_existing_artifact_matches_without_permission(tmp_path,builder):
    root=tmp_path/'src';root.mkdir();p,r=builder(root);out=tmp_path/'prep';make_receipt(out,r)
    a=_evaluate(p,r,pinned_acquisition=True,approved=(IDENTITY,),preparation_dir=out)
    assert a['artifact_evidence']['status']=='MATCHED_REVIEWED_ARTIFACTS_NOT_AUTHORIZED'
    assert a['artifact_evidence']['provenance']=='SAVED_RECEIPT_CONSISTENCY_ONLY'
    assert 'OFFLINE_ARTIFACTS_NOT_VERIFIED' not in a['automatic_runtime']['input_blockers']
    assert a['runtime_permission'] is False and a['artifact_evidence']['permission'] is False
    assert 'NEVER_OPEN_THIS_PATH' not in json.dumps(a)

@pytest.mark.parametrize('mutation,code',[
 ('unsigned','RECEIPT_CHECKSUM_MISMATCH'),('source','RECEIPT_SOURCE_OR_RECIPE_MISMATCH'),('recipe','RECEIPT_SOURCE_OR_RECIPE_MISMATCH'),('permission','RECEIPT_SCOPE_OR_CLEANUP_INVALID'),('cleanup','RECEIPT_SCOPE_OR_CLEANUP_INVALID'),('job','ARTIFACT_JOB_MISMATCH'),('row_permission','ARTIFACT_SCOPE_MISMATCH'),('path','ARTIFACT_HASH_OR_PATH_MISMATCH'),('cache','ARTIFACT_HASH_OR_PATH_MISMATCH'),('manifest','ARTIFACT_MANIFEST_MISMATCH'),('profile_identity','PROFILE_IDENTITY_MISMATCH'),('profile_cache','PROFILE_CACHE_MISMATCH'),('profile_toolchain','PROFILE_TOOLCHAIN_HASH_INVALID'),('profile_runtime','PROFILE_RUNTIME_UNSUPPORTED')])
def test_reject_artifact_mutations(tmp_path,mutation,code):
    root=tmp_path/'src';root.mkdir();_,r=flask(root);out=tmp_path/'prep';receipt,c,p=make_receipt(out,r);row=receipt['artifacts'][0]
    if mutation=='unsigned':receipt['cleanup']='bad'
    elif mutation=='source':receipt['source']={**SOURCE,'commit_sha':'c'*40}
    elif mutation=='recipe':receipt['recipe_sha256']='0'*64
    elif mutation=='permission':receipt['runtime_permission']=True
    elif mutation=='cleanup':receipt['cleanup']='UNVERIFIED'
    elif mutation=='job':receipt['artifacts']=[]
    elif mutation=='row_permission':row['runtime_permission']=True
    elif mutation=='path':row['artifact']['path']='../../private.zip'
    elif mutation=='cache':c.write_bytes(b'tampered')
    elif mutation=='manifest':row['manifest_hashes']['requirements_sha256']='0'*64
    else:
        profile=json.loads(p.read_text());inner=profile['python_web_offline'][IDENTITY]
        if mutation=='profile_identity':profile['python_web_offline']={'Different/Repo@'+'a'*40:inner}
        elif mutation=='profile_cache':inner['wheelhouse_sha256']='0'*64
        elif mutation=='profile_toolchain':inner['pip_sha256']='bad'
        elif mutation=='profile_runtime':inner['python_version']='3.13'
        save(p,profile);row['profile'].update(checked_file(p,65536))
    save(out/'preparation_result.json',receipt if mutation=='unsigned' else sign(receipt))
    result=inspect_prepared(out,r);assert result=={'status':'REJECTED','error_code':code,'permission':False}

@pytest.mark.parametrize('member,code',[('../escape.whl','ARCHIVE_UNSAFE_MEMBER'),('/absolute.whl','ARCHIVE_UNSAFE_MEMBER'),('wheels/../../bad.whl','ARCHIVE_UNSAFE_MEMBER'),('wheels/C:bad.whl','ARCHIVE_UNSAFE_MEMBER'),('other/example.whl','ARCHIVE_WRONG_FAMILY'),('wheels/source.tar.gz','ARCHIVE_WRONG_FAMILY')])
def test_archive_members(tmp_path,member,code):
    root=tmp_path/'src';root.mkdir();_,r=flask(root);out=tmp_path/'prep';make_receipt(out,r,member=member)
    assert inspect_prepared(out,r)['error_code']==code

def test_local_never_claims_approval(tmp_path):
    static(tmp_path);_,_,a=assess_directory(tmp_path)
    assert a['source_acquisition']=='LOCAL_UNVERIFIED' and a['reviewed_route']['status']=='NOT_APPROVED'
    assert 'SOURCE_NOT_VERIFIED_PINNED_GITHUB' in a['automatic_runtime']['input_blockers']

def test_next_not_automatic(tmp_path):
    save(tmp_path/'package.json',{'dependencies':{'next':'16.2.1','react':'19.2.4'}});(tmp_path/'app').mkdir();(tmp_path/'app/page.tsx').write_text('export default function Page(){return null}')
    p,r=compile_dependencies(tmp_path,source=SOURCE);a=_evaluate(p,r,pinned_acquisition=True)
    assert a['automatic_preparation']['status']=='BLOCKED_INPUT_OR_RESOLVER'
    assert a['automatic_runtime']['status']=='BLOCKED_INPUT_OR_RUNTIME_PREREQUISITES'

def test_assess_directory_cli(tmp_path):
    src=tmp_path/'src';src.mkdir();static(src);out=tmp_path/'result'
    assert main(['assess','--source-dir',str(src),'--out',str(out)])==3
    a=json.loads((out/'admission_assessment.json').read_text());assert a['runtime_permission'] is False
    with pytest.raises(SystemExit):main(['assess','--source-dir',str(src),'--out',str(out)])

def module(monkeypatch, name, **attrs):
    import importlib

    m = types.ModuleType(name)
    m.__dict__.update(attrs)

    parent_name, _, child_name = name.rpartition(".")
    parent = importlib.import_module(parent_name)

    monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.setattr(parent, child_name, m, raising=False)

    return m
    
@pytest.mark.parametrize('wrong',[False,True])
def test_pinned_acquisition(monkeypatch,tmp_path,wrong):
    static(tmp_path)
    @contextmanager
    def checkout(url,ref):yield {**SOURCE,'commit_sha':'b'*40 if wrong else 'a'*40},tmp_path
    module(monkeypatch,'projectiq_executioner.orchestration.adapters',pinned_checkout=checkout)
    module(monkeypatch,'projectiq_executioner.orchestration.contracts',validate_request=lambda r:r,source_matches_url=lambda s,u:True)
    if wrong:
        with pytest.raises(ValueError,match='SOURCE_IDENTITY_MISMATCH'):assess_repository('https://github.com/Owner/Repo','a'*40)
    else:assert assess_repository('https://github.com/Owner/Repo','a'*40)[2]['source_acquisition']=='PINNED_GITHUB_SNAPSHOT'

@pytest.mark.parametrize('failure',[None,'gate2','gate3','gate4','exception'])
def test_probe_sequence_stops_on_first_failure(monkeypatch,failure):
    prefix='projectiq_executioner.phase2.vm_worker.';calls=[]
    def run(label):
        def invoke(*args,**kw):
            assert kw['run_clean_vm'] is True;calls.append(label)
            if failure=='exception':raise RuntimeError('secret')
            return {'label':label,'cleanup':'UNVERIFIED' if label==failure else CLEAN}
        return invoke
    for name,label in [('d5d_process_acceptance','gate2'),('d5d_boundary_acceptance','gate3'),('d5d_host_runtime_acceptance','gate4')]:module(monkeypatch,prefix+name,run=run(label))
    module(monkeypatch,prefix+'d5e_closeout',_verify_diagnostic=lambda label,raw:raw['label']==label and raw['cleanup']==CLEAN)
    record={'clean_probe_attempts':0,'probe_results':{}}
    _run_clean_probes({},'a'*64,record)
    assert len(calls)=={None:3,'gate2':1,'gate3':2,'gate4':3,'exception':1}[failure]
    assert (record['qualification_status']=='THREE_LEGACY_BASELINES_OBSERVED_NOT_ATTESTED')==(failure is None)
    assert 'secret' not in json.dumps(record)

@pytest.mark.parametrize('state',['normal','acl_fail','image_fail','different_network','forged_config','live_missing_pin','wrong_pin','live_good','exception'])
def test_worker_inspection_never_authorizes(monkeypatch,tmp_path,state):
    from projectiq_executioner.phase8c import worker as w
    cfg_path=tmp_path/'vm_config.json';save(cfg_path,{'private':'never export'})
    cfg={'base_sha256':'b'*64,'approved_snapshots':[IDENTITY], 'automatic_admission_supported':True,'attestation':'TRUST_ME'}
    prefix='projectiq_executioner.phase2.vm_worker.'
    def verify(c):
        if state=='exception':raise RuntimeError('private details')
        return {'status':'BLOCKED' if state=='image_fail' else 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED','toolchain':{'qemu-system-x86_64':'mock-qemu'}}
    class FakeWorker:
        def __init__(self,config,workdir,port,*,trusted_dependency_fetch=False):
            self.workspace_disk_mib=0;self.port=port;self.prep=trusted_dependency_fetch
        def network_spec(self):
            return 'unexpected' if state=='different_network' else 'user,id=net0,'+('' if self.prep else 'restrict=on,')+f'hostfwd=tcp:127.0.0.1:{self.port}-:22'
        def start(self,*args):raise AssertionError('must not launch')
    module(monkeypatch,prefix+'orchestrator',load_config=lambda _:cfg)
    module(monkeypatch,prefix+'qemu',verify_host_toolchain=verify,QemuWorker=FakeWorker)
    module(monkeypatch,prefix+'d5d_host_acl_audit',SUCCESS='OK',audit=lambda _: {'status':'FAIL' if state=='acl_fail' else 'OK','security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','acl_modified':False,'vm_launched':False,'checks':{str(i):'PASS' for i in range(5)}})
    module(monkeypatch,prefix+'d5e_closeout',OPEN_BLOCKERS=('KNOWN_GAP',))
    real=w.checked_file
    monkeypatch.setattr(w,'checked_file',lambda path,limit:real(path,limit) if Path(path)==cfg_path else {'sha256':'a'*64,'bytes':20})
    runs=[]
    def probes(*args):runs.append(True);args[2]['qualification_status']='THREE_LEGACY_BASELINES_OBSERVED_NOT_ATTESTED'
    monkeypatch.setattr(w,'_run_clean_probes',probes)
    live=state in ('live_missing_pin','wrong_pin','live_good')
    pin='a'*64 if state=='live_good' else 'b'*64 if state=='wrong_pin' else None
    result=w.inspect_worker(cfg_path,run_clean_vms=live,qemu_sha256=pin)
    assert result['automatic_admission_supported'] is False and result['runtime_permission'] is False
    assert result['worker_report_sha256']==digest({k:v for k,v in result.items() if k!='worker_report_sha256'})
    assert len(runs)==(1 if state=='live_good' else 0)
    if state in ('acl_fail','image_fail','different_network','exception'):assert result['status']=='REVIEWED_WORKER_PREFLIGHT_UNVERIFIED'
    else:assert result['status']=='REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED'
    if state in ('live_missing_pin','wrong_pin'):assert result['qualification_status']=='LIVE_PROBES_BLOCKED_BY_PREFLIGHT_OR_PIN'
    assert 'private details' not in json.dumps(result) and 'TRUST_ME' not in json.dumps(result)


def test_forged_worker_assertion_rejected(tmp_path):
    p,r=static(tmp_path);a=_evaluate(p,r,pinned_acquisition=True,worker={'status':'REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED','runtime_permission':True})
    assert a['worker']['status']=='UNTRUSTED_WORKER_ASSERTION_REJECTED'
    assert a['worker']['runtime_permission'] is False


def test_preparation_and_runtime_scope_distinct(tmp_path):
    p,r=node(tmp_path);a=_evaluate(p,r,pinned_acquisition=True)
    assert 'PREPARATION_EGRESS_CONTAINMENT_UNVERIFIED' in a['automatic_preparation']['worker_blockers']
    assert 'PREPARATION_EGRESS_CONTAINMENT_UNVERIFIED' not in a['automatic_runtime']['worker_blockers']
    assert a['preparation_vms_started']==a['application_vms_started']==a['repository_commands_executed']==0
