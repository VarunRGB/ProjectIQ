import copy
import hashlib
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler,HTTPServer
from types import SimpleNamespace
from pathlib import Path
import pytest
from projectiq_executioner.phase9b import http_runner as h,execution as e
from projectiq_executioner.phase8d import guest,worker,dispatch
from projectiq_executioner.phase8a.planner import digest
from test_executioner_phase9b_a import checkout,generate,run
from test_executioner_phase9a import flask, SRC, URL


def test_case(path='/',kind='BODY_TEXT_EQUALS',expected='Hello',status=200):
    return {'case_id':'FT-0000000000000001','method':'GET','path':path,'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':status},{'kind':kind,'expected':expected}]}
test_case.__test__=False

def suite(*tests):return {'compiled_sha256':'a'*64,'tests':list(tests)}

@pytest.fixture
def server(monkeypatch):
    seen=[]
    # Windows has no SIGALRM; run bounded local HTTP controls without the Linux wall timer.
    if not hasattr(signal,'SIGALRM'):
        monkeypatch.setattr(h.signal,'SIGALRM',14,raising=False);monkeypatch.setattr(h.signal,'ITIMER_REAL',0,raising=False)
        monkeypatch.setattr(h.signal,'signal',lambda *_:None);monkeypatch.setattr(h.signal,'setitimer',lambda *_:None,raising=False)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            seen.append((self.path,self.headers.get('Cookie'),self.headers.get('Accept-Encoding')))
            code,body,headers= {
                '/':(200,b'Hello',{}),'/bad':(200,b'Wrong',{}),'/status':(503,b'Hello',{}),
                '/json':(200,b'{"a":true,"b":[2]}',{}),'/dup':(200,b'{"a":1,"a":2}',{}),
                '/html':(200,b'<head><title>hidden</title></head><p>Click <b>here</b></p><script>SECRET</script>',{}),
                '/big':(200,b'x'*(h.MAX_BYTES+1),{}),'/encoded':(200,b'Hello',{'Content-Encoding':'gzip'}),
                '/redirect':(302,b'Hello',{'Location':'/','Set-Cookie':'x=y'}),'/invalid':(200,b'\xff',{}),
                '/short':(200,b'Hello',{'Content-Length':'100'}),'/slow':(200,b'Hello',{})
            }[self.path]
            if self.path=='/slow':time.sleep(.25)
            self.send_response(code)
            for k,v in headers.items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(body)
            except OSError:pass
    srv=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:yield srv.server_port,seen
    finally:srv.shutdown();srv.server_close();thread.join()

@pytest.mark.parametrize('path,kind,expected,status,outcome,error',[('/', 'BODY_TEXT_EQUALS','Hello',200,'PASS',None),('/bad','BODY_TEXT_EQUALS','Hello',200,'FAIL',None),('/status','BODY_TEXT_EQUALS','Hello',200,'FAIL',None),('/json','JSON_EQUALS',{'b':[2],'a':True},200,'PASS',None),('/json','JSON_EQUALS',{'a':1,'b':[2]},200,'FAIL',None),('/dup','JSON_EQUALS',{'a':2},200,'INCONCLUSIVE',None),('/html','HTML_TEXT_CONTAINS','Click here',200,'PASS',None),('/html','HTML_TEXT_CONTAINS','SECRET',200,'FAIL',None),('/big','BODY_TEXT_EQUALS','Hello',200,'INCONCLUSIVE','RESPONSE_TOO_LARGE'),('/encoded','BODY_TEXT_EQUALS','Hello',200,'INCONCLUSIVE','UNSUPPORTED_CONTENT_ENCODING'),('/redirect','BODY_TEXT_EQUALS','Hello',200,'FAIL',None),('/invalid','BODY_TEXT_EQUALS','Hello',200,'INCONCLUSIVE',None),('/short','BODY_TEXT_EQUALS','Hello',200,'INCONCLUSIVE','HTTP_TRANSPORT_ERROR'),('/','HTTP_BODY_SHA256_EQUALS',hashlib.sha256(b'Hello').hexdigest(),200,'PASS',None)])
def test_http_controls(server,path,kind,expected,status,outcome,error):
    port,seen=server;s=suite(test_case(path,kind,expected,status));r=h.execute_suite(s,port,lambda:True);assert h.validate_results(r,s)==r
    row=r['cases'][0];assert row['status']==outcome and row['error_code']==error
    assert seen==[(path,None,'identity')]
    assert 'Hello' not in json.dumps(r) and 'SECRET' not in json.dumps(r)

@pytest.mark.skipif(not hasattr(signal,'SIGALRM'),reason='real Linux guest wall timer')
def test_wall_timeout(server,monkeypatch):
    monkeypatch.setattr(h,'TIMEOUT',.05);port,_=server;s=suite(test_case('/slow'));r=h.execute_suite(s,port,lambda:True);assert r['cases'][0]['error_code']=='REQUEST_TIMEOUT'

@pytest.mark.parametrize('path',['//example.com','http://localhost/','/../x','/x?y=1','/x%2f','/x:id','/x\r\n'])
def test_unsafe_path(path):
    with pytest.raises(ValueError):h.validate_suite(suite(test_case(path)))

def test_not_run(server):
    port,seen=server;s=suite(test_case());r=h.execute_suite(s,port,lambda:False);assert h.validate_results(r,s)==r and r['cases'][0]['status']=='NOT_RUN' and not seen

@pytest.mark.parametrize('mutation',['status','id','count','matched','raw','digest'])
def test_receipt_mutation(server,mutation):
    port,_=server;s=suite(test_case());r=h.execute_suite(s,port,lambda:True);row=r['cases'][0]
    if mutation=='status':row['status']='FAIL'
    if mutation=='id':row['case_id']='FT-0000000000000002'
    if mutation=='count':r['cases']=[]
    if mutation=='matched':row['assertions'][0]['matched']=False
    if mutation=='raw':row['body']='secret'
    if mutation=='digest':r['compiled_sha256']='b'*64
    with pytest.raises(ValueError):h.validate_results(r,s)

@pytest.mark.parametrize('tamper',[False,True])
def test_rederive_real(tmp_path,checkout,tamper):
    root,_=checkout;flask(root);path,_=generate(tmp_path);compiled=run(tmp_path,path);_,plan,_=planner_runtime(root);dest=tmp_path/'dispatch';dest.mkdir()
    if tamper:
        compiled['components'][0]['tests'][0]['assertions'][1]['expected']='fake';compiled['compiled_sha256']=digest({k:v for k,v in compiled.items() if k!='compiled_sha256'})
        with pytest.raises(ValueError,match='FUNCTIONAL_PLAN_REDERIVATION_MISMATCH'):e.rederive_suite(compiled,SRC,root,plan,plan['components'][0],dest)
    else:
        s=e.rederive_suite(compiled,SRC,root,plan,plan['components'][0],dest);assert s['tests'][0]['assertions'][1]['expected']=='Hello, World!'

def planner_runtime(root):
    from projectiq_executioner.phase8d.planner import compile_runtime
    return compile_runtime(root,source=SRC)


def test_trust_required(tmp_path):
    with pytest.raises(ValueError,match='EXPLICIT_DEMO_TRUST_REQUIRED'):e.run_functional(URL,SRC['commit_sha'],tmp_path/'missing',tmp_path/'out',vm_config=tmp_path/'cfg')
    assert not (tmp_path/'out').exists()


def test_guest_hook_before_stop(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();(work/'snapshot.zip').write_bytes(b'x');root=work/'checkout';root.mkdir();(root/'index.html').write_text('x');events=[]
    proc=SimpleNamespace(poll=lambda:None);g=SimpleNamespace(_sha256=lambda _: 'a'*64,safe_extract=lambda *_:root,_share_tree=lambda *a,**k:None,_APP_BUILD_HANDOFFS=[],_APP_SERVER_PLACEMENTS=[],pwd=SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=1001)),APP_USER='app')
    def popen(*a,**k):g._APP_SERVER_PLACEMENTS.append(1);events.append('start');return proc
    g._project_popen=popen;g._d4_stop_process=lambda _:events.append('stop')
    monkeypatch.setattr(guest,'init',lambda *_:work);monkeypatch.setattr(guest,'probe_http',lambda *a,**k:events.append('ready') or {'status':'HTTP_2XX_OBSERVED','http_status':200,'path':'/'})
    monkeypatch.setattr(guest,'listener_uid',lambda *_:True);monkeypatch.setattr(guest,'launch_recipe',lambda *_:(['mock'],False,0))
    def execute(s,port,available):
        assert available();events.append('functional');return {'compiled_sha256':s['compiled_sha256'],'cases':[{'request_attempted':True}]}
    monkeypatch.setattr(guest,'_piq_functional',{'execute_suite':execute},raising=False)
    req={'adapter':'STATIC_HTTP','dependency_kind':'NONE','source':SRC,'component_id':'component-test','snapshot_sha256':'a'*64,'plan_sha256':'b'*64,'root':'.','probe_path':'/','service_port':18765,'functional_suite':suite(test_case())}
    # Mock bind only to avoid using an external or occupied test port.
    sock=SimpleNamespace(bind=lambda *_:None,__enter__=lambda self:self,__exit__=lambda *a:None)
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def bind(self,*_):pass
    monkeypatch.setattr(guest.socket,'socket',lambda *_:Socket())
    monkeypatch.setattr(guest,'open',lambda *a,**k:open(Path(tmp_path)/'log','wb'),raising=False)
    r=guest.run(g,tmp_path,req,None,None);assert events==['start','ready','functional','stop'] and r['functional_tests_executed']==1


def test_payload_compiles():
    assert '_piq_functional=' not in worker.trusted_guest_payload().decode()
    compile(worker.trusted_guest_payload(True),'<guest>','exec')


def receipt(s,rows):
    req={'source':SRC,'component_id':'component-x','adapter':'PY_FLASK','snapshot_sha256':'a'*64,'plan_sha256':'b'*64,'dependency_kind':'PYTHON','bundle_sha256':'c'*64,'service_port':8765,'probe_path':'/','functional_suite':s}
    ev={k:req[k] for k in ('source','component_id','adapter','snapshot_sha256','plan_sha256','dependency_kind','service_port')}
    ev.update(dependency_sha256=req['bundle_sha256'],host_gateway_denied=True,workspace_mib=1536,build_handoffs=0,npm_install_handoffs=0,pip_install_handoffs=2,status='HTTP_READINESS_OBSERVED',http={'status':'HTTP_2XX_OBSERVED','path':'/','http_status':200},server_placement_observed=True,application_uid_listener_observed=True,functional=rows,functional_tests_executed=sum(r['request_attempted'] for r in rows['cases']))
    return ev,req


def test_worker_accepts_fail_as_evidence_not_pass(server):
    port,_=server;s=suite(test_case('/bad'));r=h.execute_suite(s,port,lambda:True);ev,req=receipt(s,r)
    assert worker.validate_receipt(ev,req) and r['cases'][0]['status']=='FAIL'
    ev['functional_tests_executed']=0;assert not worker.validate_receipt(ev,req)
    ev['functional_tests_executed']=1;ev['functional']['cases'][0]['status']='PASS';assert not worker.validate_receipt(ev,req)


def test_legacy_receipt_unchanged(server):
    port,_=server;s=suite(test_case());ev,req=receipt(s,h.execute_suite(s,port,lambda:True));req.pop('functional_suite')
    assert not worker.validate_receipt(ev,req)
    ev.pop('functional');ev['functional_tests_executed']=0;assert worker.validate_receipt(ev,req)


def test_budget_and_duplicate_refusal():
    s=suite(*[dict(test_case('/'+str(i)),case_id=f'FT-{i:016X}') for i in range(33)])
    with pytest.raises(ValueError):h.validate_suite(s)
    with pytest.raises(ValueError):h.validate_suite(suite(test_case(),test_case()))


def test_dispatch_rederives_before_dependency_or_vm(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);path,_=generate(tmp_path);compiled=run(tmp_path,path)
    compiled['components'][0]['tests'][0]['assertions'][1]['expected']='forged';compiled['compiled_sha256']=digest({k:v for k,v in compiled.items() if k!='compiled_sha256'})
    cfg=tmp_path/'config.json';cfg.write_text('{}');monkeypatch.setattr(dispatch,'source_approved',lambda *_:True)
    def forbidden(*a,**k):pytest.fail('must not acquire dependencies or run VM')
    monkeypatch.setattr(dispatch,'_python_fetch',forbidden);monkeypatch.setattr(worker,'execute_reviewed',forbidden)
    r=dispatch.dispatch(URL,SRC['commit_sha'],tmp_path/'dispatch',run_reviewed=True,fetch=True,vm_config=cfg,functional_plan=compiled)
    assert r['error_code']=='FUNCTIONAL_PLAN_REDERIVATION_MISMATCH' and r['vm_start_attempts']==0


def test_collect_evidence_actual_dispatch_contract(tmp_path,checkout,monkeypatch):
    root,_=checkout;flask(root);path,_=generate(tmp_path);compiled=run(tmp_path,path);cfg=tmp_path/'config.json';cfg.write_text('{}')
    pip=tmp_path/'pip-25.2-py3-none-any.whl';pip.write_bytes(b'pip');monkeypatch.setattr(dispatch,'source_approved',lambda *_:True)
    def fetch(req,out,version):
        bundle=out/'python-wheelhouse.zip';bundle.write_bytes(b'bundle');return bundle,{},{}
    monkeypatch.setattr(dispatch,'_python_fetch',fetch)
    def execute(**kw):
        s=kw['functional_suite'];c=kw['component'];p=kw['plan'];test=s['tests'][0];rows=h.outcomes(test,200,b'wrong');row={'case_id':test['case_id'],'status':'FAIL','request_attempted':True,'http_status':200,'assertions':rows,'error_code':None}
        ev,req=receipt(s,{'compiled_sha256':s['compiled_sha256'],'cases':[row]});ev.update(component_id=c['id'],snapshot_sha256=hashlib.sha256(kw['snapshot'].read_bytes()).hexdigest(),plan_sha256=p['plan_sha256'],dependency_sha256=hashlib.sha256(kw['bundle'].read_bytes()).hexdigest())
        return {'source':SRC,'component_id':c['id'],'adapter':c['adapter'],'status':'REVIEWED_HTTP_READINESS_VERIFIED','stage':'COMPLETE','cleanup':e.CLEAN,'vm_start_attempts':1,'network':'QEMU_RESTRICT_ON','runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','functional_tests_executed':1,'storage_watchdog':{'tripped':False,'reason':None,'samples':1,'peak_logical_file_bytes':1,'budget_bytes':2},'evidence':ev}
    monkeypatch.setattr(worker,'execute_reviewed',execute)
    dest=tmp_path/'dispatch';r=dispatch.dispatch(URL,SRC['commit_sha'],dest,run_reviewed=True,fetch=True,vm_config=cfg,pip_wheel=pip,pip_sha256=hashlib.sha256(pip.read_bytes()).hexdigest(),functional_plan=compiled)
    assert r['status']=='REVIEWED_FUNCTIONAL_EVIDENCE_COMPLETE';assert e.collect_evidence(dest,r,compiled)['cases'][0]['status']=='FAIL'
    p=dest/r['components'][0]['report'];w=json.loads(p.read_text());w['cleanup']='UNVERIFIED';w['result_sha256']=digest({k:v for k,v in w.items() if k!='result_sha256'});p.write_text(json.dumps(w))
    with pytest.raises(ValueError):e.collect_evidence(dest,r,compiled)
