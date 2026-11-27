import copy,hashlib,json,signal,threading
from html import escape
from http.server import BaseHTTPRequestHandler,HTTPServer
from urllib.parse import parse_qs
from contextlib import contextmanager
import pytest
from projectiq_executioner.phase9b import adaptive_runner as a,adaptive_compiler as c,http_runner as h,execution as e
from projectiq_executioner.phase8d import dispatch,worker
from projectiq_executioner.phase8a.planner import digest
from test_executioner_phase9a import express,write,SRC,URL,build
from test_executioner_phase9b_a import checkout,generate,run
from test_executioner_phase9b_b import suite,test_case,planner_runtime


def fixture(root):
    express(root)
    src=(root/'index.js').read_text();src=src.replace('app.listen(port,()=>{});',"let data=[{id:'seed',content:'seed text'}]; app.use(express.urlencoded({extended:true})); app.listen(port,()=>{});")
    src=src.replace('data.push({content})',"data.push({id:'new-id',content})").replace("res.render('show.ejs',{post});","let id=req.params.id;let post=data.find(d=>d.id===id);res.render('show.ejs',{post});")
    write(root,'index.js',src)
    write(root,'views/posts.ejs','<h1>Data -</h1><% for (d of data) { %><h3>id: <%=d.id%></h3><p>content: <%=d.content%></p><% } %>')
    write(root,'views/show.ejs','<h1> <%= post.id %></h1><p> <%= post.content %> </p>')


def contract(root):
    proposal=build(root);_,runtime,_=planner_runtime(root);return c.compile_workflow(root,proposal,runtime['components'][0])


def test_compile_source(tmp_path):
    fixture(tmp_path);w=contract(tmp_path);assert w['create_field']=='content' and w['update_field']=='update' and w['read_template']=='/posts/:id';assert w==contract(tmp_path)
    assert a.contract_hash(w)==digest(w)

@pytest.mark.parametrize('change',['template','detail','no_array','no_urlencoded','wrong_redirect','bad_form','unescaped','extra_row'])
def test_unsupported_profile(tmp_path,change):
    fixture(tmp_path);p=tmp_path/'index.js';s=p.read_text()
    if change=='template':write(tmp_path,'views/posts.ejs','<h1>Only heading</h1>')
    elif change=='detail':write(tmp_path,'views/show.ejs','<h1><%= post.id %></h1><p>not value</p>')
    elif change=='no_array':p.write_text(s.replace("let data=[{id:'seed',content:'seed text'}]","let data=external"))
    elif change=='no_urlencoded':p.write_text(s.replace('app.use(express.urlencoded({extended:true}));',''))
    elif change=='wrong_redirect':p.write_text(s.replace("res.redirect('/posts');});\napp.delete", "res.redirect('/elsewhere');});\napp.delete"))
    elif change=='bad_form':write(tmp_path,'views/create.ejs','<form action="/posts/data" method="POST"><textarea name="wrong"></textarea><button>Submit</button></form>')
    elif change=='unescaped':p=tmp_path/'views/posts.ejs';p.write_text(p.read_text().replace('<%=d.content%>','<%-d.content%>'))
    elif change=='extra_row':p=tmp_path/'views/posts.ejs';p.write_text(p.read_text()+'<p>unrelated</p>')
    with pytest.raises(ValueError,match='ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED'):contract(tmp_path)


@contextmanager
def app_server(monkeypatch,mode='good'):
    if not hasattr(signal,'SIGALRM'):
        monkeypatch.setattr(h.signal,'SIGALRM',14,raising=False);monkeypatch.setattr(h.signal,'ITIMER_REAL',0,raising=False);monkeypatch.setattr(h.signal,'signal',lambda *_:None);monkeypatch.setattr(h.signal,'setitimer',lambda *_:None,raising=False)
    state={'seed':'original & intact'};seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def response(self,code,body='',location=None):
            self.send_response(code)
            if location:self.send_header('Location',location)
            self.end_headers();self.wfile.write(body.encode())
        def payload(self):return parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
        def record(self):
            seen.append((self.command,self.path));assert self.headers.get('Cookie') is None and self.headers.get('Accept-Encoding')=='identity'
        def do_GET(self):
            self.record()
            if self.path=='/':return self.response(200,'Hello')
            if self.path=='/posts/data':return self.response(200,'<button>Submit</button>')
            if self.path=='/posts':
                body=''.join(f'<h3>id: {escape(k)}</h3><p>content: {escape(v)}</p>' for k,v in state.items())
                if mode=='malformed':body='<h3>wrong shape</h3>'
                return self.response(200,body)
            ident=self.path.split('/')[-1];v=state.get(ident)
            return self.response(200,f'<h1>{escape(ident)}</h1><p>{escape(v or "")}</p>')
        def do_POST(self):
            self.record();v=self.payload()['content'][0];state['new-id']=v
            if mode=='duplicate':state['another-id']=v
            if mode=='create_corrupt':state['seed']='changed'
            if mode=='unsafe_id':state['../escape']=state.pop('new-id')
            return self.response(302,location='https://outside.invalid/' if mode=='redirect' else '/posts')
        def do_PATCH(self):
            self.record();v=self.payload()['update'][0]
            if mode!='no_update':state[self.path.split('/')[2]]=v
            if mode=='update_corrupt':state['seed']='changed'
            return self.response(302,location='/posts')
        def do_DELETE(self):
            self.record()
            if mode!='no_delete':state.pop(self.path.split('/')[2],None)
            if mode=='delete_corrupt':state.pop('seed',None)
            return self.response(302,location='/posts')
    srv=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:yield srv.server_port,state,seen
    finally:srv.shutdown();srv.server_close();thread.join()

@pytest.mark.parametrize('mode,status,last,count',[('good','PASS','VERIFY_REMOVED',9),('duplicate','FAIL','LIST_CREATED',3),('create_corrupt','FAIL','LIST_CREATED',3),('unsafe_id','INCONCLUSIVE','LIST_CREATED',3),('redirect','FAIL','CREATE',2),('malformed','INCONCLUSIVE','BASELINE',1),('no_update','FAIL','READ_UPDATED',6),('update_corrupt','FAIL','VERIFY_UPDATED_LIST',7),('no_delete','FAIL','VERIFY_REMOVED',9),('delete_corrupt','FAIL','VERIFY_REMOVED',9)])
def test_live_http_controls(tmp_path,monkeypatch,mode,status,last,count):
    fixture(tmp_path);w=contract(tmp_path);s=suite(test_case());s['workflow']=w
    with app_server(monkeypatch,mode) as (port,state,seen):
        result=h.execute_suite(s,port,lambda:True);assert h.validate_results(result,s)==result
        wf=result['workflow'];assert wf['status']==status and wf['steps'][-1]['role']==last and wf['requests_attempted']==count
        assert len(seen)==count+1 and result['cases'][0]['status']=='PASS'
        if status=='PASS':assert state=={'seed':'original & intact'} and seen[-2]==('DELETE','/posts/new-id/delete')
        if count<8:assert not any(x[0]=='DELETE' for x in seen)
        assert 'new-id' not in json.dumps(result) and 'piq-create-' not in json.dumps(result) and 'original & intact' not in json.dumps(result)

@pytest.mark.parametrize('mutation',['digest','status','order','count','raw','truncate','http'])
def test_report_tamper(tmp_path,monkeypatch,mutation):
    fixture(tmp_path);w=contract(tmp_path);s=suite(test_case());s['workflow']=w
    with app_server(monkeypatch) as (port,_,__):r=h.execute_suite(s,port,lambda:True)
    wf=r['workflow']
    if mutation=='digest':wf['contract_sha256']='b'*64
    elif mutation=='status':wf['status']='FAIL'
    elif mutation=='order':wf['steps'][1]['role']='DELETE'
    elif mutation=='count':wf['requests_attempted']=2
    elif mutation=='raw':wf['steps'][0]['body']='raw'
    elif mutation=='truncate':wf['steps'].pop();wf['requests_attempted']-=1
    elif mutation=='http':wf['steps'][1]['http_status']=200
    with pytest.raises(ValueError):h.validate_results(r,s)

@pytest.mark.parametrize('field,value',[('list_path','//evil'),('read_template','/posts/:other'),('update_template','/elsewhere/:id'),('create_field','password'),('delete_template','/posts/:id/../evil')])
def test_invalid_contract(tmp_path,field,value):
    fixture(tmp_path);w=contract(tmp_path);w[field]=value
    with pytest.raises(ValueError):a.validate_workflow(w)


def test_stop_when_listener_lost(tmp_path,monkeypatch):
    fixture(tmp_path);w=contract(tmp_path);calls=iter([True,False])
    with app_server(monkeypatch) as (port,_,seen):r=a.execute(w,port,lambda:next(calls),h.bounded_exchange)
    assert r['status']=='NOT_RUN' and r['requests_attempted']==1 and seen==[('GET','/posts')]


def test_actual_rederivation(tmp_path,checkout):
    root,_=checkout;fixture(root);path,_=generate(tmp_path);compiled=run(tmp_path,path);_,plan,_=planner_runtime(root);out=tmp_path/'dispatch';out.mkdir()
    s=e.rederive_suite(compiled,SRC,root,plan,plan['components'][0],out,adaptive=True)
    assert s['workflow']==contract(root) and len(s['tests'])==3
    assert json.loads((out/'functional_rederived'/'adaptive_contract.json').read_text())==s['workflow']


def test_explicit_optin(tmp_path):
    with pytest.raises(ValueError,match='EXPLICIT_DISPOSABLE_STATE_CONFIRMATION_REQUIRED'):e.run_functional(URL,SRC['commit_sha'],tmp_path/'x',tmp_path/'out',vm_config=tmp_path/'cfg',trust_demo_source=True,adaptive=True)
    assert not (tmp_path/'out').exists()


def test_guest_payload_actual_namespaces(tmp_path,monkeypatch):
    fixture(tmp_path);w=contract(tmp_path);s=suite(test_case());s['workflow']=w
    # Execute exactly the embedded trusted namespaces, excluding the actual VM main.
    code=worker.trusted_guest_payload(True).decode();prefix=code[:code.index('_piq_version=')];ns={};exec(prefix,ns)
    with app_server(monkeypatch) as (port,_,__):r=ns['_piq_functional']['execute_suite'](s,port,lambda:True)
    assert r['workflow']['status']=='PASS' and h.validate_results(r,s)==r


def test_alternative_resource_and_field_names(tmp_path):
    fixture(tmp_path)
    for p in [tmp_path/'index.js',*list((tmp_path/'views').glob('*.ejs'))]:
        p.write_text(p.read_text().replace('/posts','/tasks').replace('content','label').replace('update','modify'))
    w=contract(tmp_path);assert w['create_path']=='/tasks/data' and w['read_template']=='/tasks/:id' and w['create_field']=='label' and w['update_field']=='modify'


def test_dispatch_refuses_unsupported_before_fetch(tmp_path,checkout,monkeypatch):
    root,_=checkout;express(root);path,_=generate(tmp_path);compiled=run(tmp_path,path)
    cfg=tmp_path/'config.json';cfg.write_text('{}');monkeypatch.setattr(dispatch,'source_approved',lambda *_:True)
    def forbidden(*a,**k):pytest.fail('must not acquire dependencies or run VM')
    monkeypatch.setattr(dispatch,'_npm_download',forbidden);monkeypatch.setattr(worker,'execute_reviewed',forbidden)
    r=dispatch.dispatch(URL,SRC['commit_sha'],tmp_path/'dispatch',run_reviewed=True,fetch=True,vm_config=cfg,functional_plan=compiled,adaptive=True,confirm_disposable_state=True)
    assert r['error_code']=='ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED' and r['vm_start_attempts']==0


@pytest.mark.parametrize('mode',['good','no_delete'])
def test_actual_dispatch_and_collector(tmp_path,checkout,monkeypatch,mode):
    root,_=checkout;fixture(root);path,_=generate(tmp_path);compiled=run(tmp_path,path);cfg=tmp_path/'config.json';cfg.write_text('{}')
    node=tmp_path/'node-v24.22.3-linux-x64.tar.xz';node.write_bytes(b'node');monkeypatch.setattr(dispatch,'source_approved',lambda *_:True)
    def fetch(req,out):
        bundle=out/'manifest-inputs.zip';bundle.write_bytes(b'bundle');return bundle,{}
    monkeypatch.setattr(dispatch,'_npm_download',fetch)
    def execute(**kw):
        s=kw['functional_suite'];c=kw['component'];p=kw['plan']
        with app_server(monkeypatch,mode) as (port,_,__):r=h.execute_suite(s,port,lambda:True)
        count=sum(x['request_attempted'] for x in r['cases'])
        ev={'source':SRC,'component_id':c['id'],'adapter':c['adapter'],'snapshot_sha256':hashlib.sha256(kw['snapshot'].read_bytes()).hexdigest(),'plan_sha256':p['plan_sha256'],'dependency_kind':'NPM','dependency_sha256':hashlib.sha256(kw['bundle'].read_bytes()).hexdigest(),'host_gateway_denied':True,'workspace_mib':1536,'build_handoffs':0,'npm_install_handoffs':1,'pip_install_handoffs':0,'functional_tests_executed':count,'status':'HTTP_READINESS_OBSERVED','service_port':c['service_port'],'http':{'status':'HTTP_2XX_OBSERVED','http_status':200,'path':'/'},'server_placement_observed':True,'application_uid_listener_observed':True,'functional':r}
        return {'source':SRC,'component_id':c['id'],'adapter':c['adapter'],'status':'REVIEWED_HTTP_READINESS_VERIFIED','stage':'COMPLETE','cleanup':e.CLEAN,'vm_start_attempts':1,'network':'QEMU_RESTRICT_ON','runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','functional_tests_executed':count,'storage_watchdog':{'tripped':False,'reason':None,'samples':1,'peak_logical_file_bytes':1,'budget_bytes':2},'evidence':ev}
    monkeypatch.setattr(worker,'execute_reviewed',execute)
    dest=tmp_path/'dispatch';d=dispatch.dispatch(URL,SRC['commit_sha'],dest,run_reviewed=True,fetch=True,vm_config=cfg,node_tarball=node,node_sha256=hashlib.sha256(node.read_bytes()).hexdigest(),functional_plan=compiled,adaptive=True,confirm_disposable_state=True)
    assert d['status']=='REVIEWED_FUNCTIONAL_EVIDENCE_COMPLETE'
    evidence=e.collect_evidence(dest,d,compiled,adaptive=True);assert evidence['workflow']['status']==('PASS' if mode=='good' else 'FAIL') and evidence['workflow']['requests_attempted']==9
    assert evidence['adaptive_contract_sha256']==d['adaptive_contract_sha256']
    with pytest.raises(ValueError):e.collect_evidence(dest,d,compiled)
    p=dest/'functional_rederived'/'adaptive_contract.json';w=json.loads(p.read_text());w['create_field']='tampered';p.write_text(json.dumps(w))
    with pytest.raises(ValueError,match='ADAPTIVE_CONTRACT_MISMATCH'):e.collect_evidence(dest,d,compiled,adaptive=True)


def test_destructured_id_source(tmp_path):
    fixture(tmp_path);p=tmp_path/'index.js';p.write_text(p.read_text().replace('let id=req.params.id;','let {id}=req.params;'))
    assert contract(tmp_path)['read_template']=='/posts/:id'
