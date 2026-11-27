"""Bounded functional proposals from a pinned source; no student code execution.

Implementation-derived expectations are characterization hypotheses, not independent
requirements, and are never grade credit. Phase 9B must rederive and validate any
plan, establish runner capability and independently authorize each execution.
"""
from __future__ import annotations
import ast
import json
import re
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

from ..orchestration import adapters
from ..orchestration.contracts import validate_request, validate_source, source_matches_url
from ..phase3.generator import generate_test_plan
from ..phase3.contracts import validate_test_plan
from ..phase3.source_contracts import source_bytes
from ..phase8a.planner import digest
from ..phase8a.scanner import inspect
from ..phase8d.planner import compile_runtime, safe_path
from ..phase8d.dispatch import fresh
from ..phase8c_final.dispatch import write

MAX_CASES = 240
MAX_ROUTES = 150
MAX_COMPONENTS = 24
MAX_TEMPLATE = 128_000
MAX_LITERAL = 512
METHODS = {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'}
PATH = re.compile(r'/[A-Za-z0-9_./~-]*(?::[A-Za-z_][A-Za-z0-9_]*[A-Za-z0-9_./~-]*)*\Z')
FIELD = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')


def local_path(value):
    return isinstance(value,str) and len(value)<=200 and bool(PATH.fullmatch(value)) and '//' not in value and not any(p in ('.','..') for p in value.split('/'))


def literal(value):
    """Keep declarative JSON small; never accept code, NaN or arbitrary objects."""
    try:
        s=json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)
        if len(s)>MAX_LITERAL:return False
        # Only shallow JSON scalars/arrays/objects. No credential-like names.
        def valid(v,depth=0):
            if depth>4:return False
            if isinstance(v,str):return len(v)<=MAX_LITERAL and all(ord(c)>=32 or c in '\n\r\t' for c in v)
            if v is None or type(v) in (bool,int,float):return True
            if isinstance(v,list):return len(v)<=20 and all(valid(x,depth+1) for x in v)
            if isinstance(v,dict):return len(v)<=20 and all(isinstance(k,str) and not re.search(r'password|secret|token|credential|api.?key',k,re.I) and valid(k,depth+1) and valid(x,depth+1) for k,x in v.items())
            return False
        return valid(value)
    except (TypeError,ValueError,OverflowError,RecursionError):return False


def evidence(path,line,reason,raw):
    return {'path':path,'line':line,'reason':reason,'sha256':sha256(raw).hexdigest()}


def check(kind,expected):return {'kind':kind,'expected':expected}


def row(method,path,ev,*,assertions=(),basis=None,fields=(),redirect=None):
    return {'method':method,'path':path,'evidence':ev,'assertions':list(assertions),'basis':basis,'input_fields':sorted(set(fields)),'redirect':redirect}


def python_routes(raw,path,symbol,adapter):
    try:tree=ast.parse(raw.decode('utf-8-sig'))
    except (SyntaxError,UnicodeError,ValueError,RecursionError):return []
    rows=[]
    for fn in tree.body:
        if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        for d in fn.decorator_list:
            if not isinstance(d,ast.Call) or not isinstance(d.func,ast.Attribute) or not isinstance(d.func.value,ast.Name) or d.func.value.id!=symbol or not d.args or not isinstance(d.args[0],ast.Constant):continue
            url=d.args[0].value; m=d.func.attr.upper()
            if not local_path(url):continue
            if m=='ROUTE':
                if adapter!='PY_FLASK':continue
                methods=next((k.value for k in d.keywords if k.arg=='methods'),None)
                if methods is None:meth=['GET']
                elif isinstance(methods,(ast.List,ast.Tuple)) and all(isinstance(x,ast.Constant) and isinstance(x.value,str) for x in methods.elts):meth=[x.value.upper() for x in methods.elts]
                else:continue
            else:meth=[m]
            for method in meth:
                if method not in METHODS:continue
                ev=[evidence(path,d.lineno,'DIRECT_PYTHON_ROUTE',raw)]
                assertions=[]
                # No inferred response when decorators, parameters, or config change the contract.
                body=list(fn.body)
                if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):body=body[1:]
                args=fn.args
                if method=='GET' and ':' not in url and len(fn.decorator_list)==1 and not (args.args or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg) and len(body)==1 and isinstance(body[0],ast.Return) and len(d.args)==1:
                    allowed={'methods'} if adapter=='PY_FLASK' else {'status_code'}
                    if all(k.arg in allowed for k in d.keywords):
                        status=200; val=body[0].value
                        if adapter=='PY_FLASK' and isinstance(val,ast.Tuple) and len(val.elts)==2 and isinstance(val.elts[1],ast.Constant) and type(val.elts[1].value) is int:status=val.elts[1].value;val=val.elts[0]
                        if adapter=='PY_ASGI':
                            for k in d.keywords:
                                status=k.value.value if isinstance(k.value,ast.Constant) and type(k.value.value) is int else 0
                        try:v=ast.literal_eval(val)
                        except (ValueError,TypeError,RecursionError,MemoryError):v=object()
                        if literal(v) and 200<=status<=599:
                            if adapter=='PY_FLASK' and isinstance(v,str) and v:assertions=[check('HTTP_STATUS_EQUALS',status),check('BODY_TEXT_EQUALS',v)]
                            elif adapter=='PY_ASGI' or isinstance(v,dict):assertions=[check('HTTP_STATUS_EQUALS',status),check('JSON_EQUALS',v)]
                        if assertions:ev.append(evidence(path,body[0].lineno,'LITERAL_RETURN',raw))
                rows.append(row(method,url,ev,assertions=assertions,basis='IMPLEMENTATION_LITERAL' if assertions else None))
    return rows[:MAX_ROUTES]


# Small lexical subset: strings and comments are tokens, not route syntax.
# Reject templates/regex ambiguity rather than pretending to parse full JS.
TOKEN=re.compile(r"//[^\r\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\\r\n])*'|\"(?:\\.|[^\"\\\r\n])*\"|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\d+|=>|===|==|!==|!=|[^\s]")


def tokens(text):
    return [(m[0],m.start()) for m in TOKEN.finditer(text) if not m[0].startswith(('//','/*'))]


def plain_string(t):
    if len(t)>=2 and t[0] in "'\"" and t[-1]==t[0] and '\\' not in t:return t[1:-1]
    return None


def matching(ts,start):
    stack=[];pairs={'(':')','{':'}','[':']'}
    for i in range(start,len(ts)):
        t=ts[i]
        if t in pairs:stack.append(pairs[t])
        elif t in pairs.values():
            if not stack or stack.pop()!=t:return None
            if not stack:return i
    return None


class Markup(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.markers=[];self.tag=None;self.parts=[];self.forms=[];self.form=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('h1','h2','h3','a','button') and self.tag is None and 'hidden' not in a and a.get('aria-hidden')!='true' and not re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',a.get('style') or '',re.I):self.tag=tag;self.parts=[]
        if tag=='form':self.form={'action':a.get('action'),'method':str(a.get('method','GET')).upper(),'fields':[]}
        if self.form is not None and tag in ('input','textarea','select') and isinstance(a.get('name'),str) and FIELD.fullmatch(a['name']) and (a.get('type') or '').lower() not in ('hidden','password','file','submit','button'):
            self.form['fields'].append(a['name'])
    def handle_data(self,data):
        if self.tag:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag==self.tag:
            text=' '.join(''.join(self.parts).split())
            if 3<=len(text)<=100 and literal(text) and re.search(r'[A-Za-z]',text):self.markers.append(text)
            self.tag=None;self.parts=[]
        if tag=='form' and self.form is not None:self.forms.append(self.form);self.form=None


def template_info(root,rel):
    raw=source_bytes(root,rel,limit=MAX_TEMPLATE)
    if raw is None:return [],[],None
    try:text=raw.decode('utf-8-sig');parser=Markup();parser.feed(text.split('<%',1)[0]);parser.close()
    except (ValueError,UnicodeError,RecursionError):return [],[],None
    # Only unconditional markup preceding any template expression; no script/template execution.
    return sorted(set(parser.markers))[:4],parser.forms[:12],raw


def express_routes(root,raw,path,component):
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:return []
    tok=tokens(text);ts=[t for t,_ in tok];rows=[]
    if len(ts)>100_000:return []
    # Reject regex literal ambiguity and computed/template route definitions.
    if '/' in ts:return []
    factories=set()
    for i in range(len(ts)-5):
        if ts[i] in ('const','let','var') and ts[i+2:i+5]==['=','require','('] and plain_string(ts[i+5])=='express' and i+6<len(ts) and ts[i+6]==')':factories.add(ts[i+1])
    apps={ts[i+1] for i in range(len(ts)-5) if ts[i] in ('const','let','var') and ts[i+2]=='=' and ts[i+3] in factories and ts[i+4:i+6]==['(',')']}
    attempts=0
    for i in range(len(ts)-8):
        if ts[i] not in apps or ts[i+1]!='.' or ts[i+2].upper() not in METHODS or ts[i+3]!='(' or ts[i+5]!=',':continue
        url=plain_string(ts[i+4]);method=ts[i+2].upper()
        if not local_path(url):continue
        attempts+=1
        if attempts>MAX_ROUTES:break
        end=matching(ts,i+3)
        if end is None:continue
        # Only simple two-argument inline arrow handlers; do not cross middleware.
        header=ts[i+6:i+13]
        if len(header)!=7 or header[0]!='(' or header[2]!=',' or header[4:]!=[')', '=>','{']:continue
        req,res=header[1],header[3]
        close=matching(ts,i+12)
        if close is None or close!=end-1:continue
        body=ts[i+13:close];line=text.count('\n',0,tok[i][1])+1;ev=[evidence(path,line,'DIRECT_EXPRESS_ROUTE',raw)]
        assertions=[];basis=None;fields=[];redirect=None
        # literal send / render with no side effects or conditional paths
        simple=body[:-1] if body and body[-1]==';' else body
        if simple and simple[0]=='return':simple=simple[1:]
        if method=='GET' and ':' not in url and len(simple)>=6 and simple[:2]==[res,'.'] and simple[3]=='(' and matching(simple,3)==len(simple)-1:
            val=plain_string(simple[4])
            if simple[2]=='send' and len(simple)==6 and val and literal(val):assertions=[check('HTTP_STATUS_EQUALS',200),check('BODY_TEXT_EQUALS',val)];basis='IMPLEMENTATION_LITERAL'
            if simple[2]=='render' and val and safe_path(val) and val.endswith('.ejs') and not re.search(r'(?i)(?:^|[._/-])(?:secret|credentials?|private[-_]?key)(?:[._/-]|$)',val) and (len(simple)==6 or simple[5]==','):
                rel=('' if component=='.' else component+'/')+'views/'+val
                markers,forms,traw=template_info(root,rel)
                if traw is not None and markers:
                    assertions=[check('HTTP_STATUS_EQUALS',200)]+[check('HTML_TEXT_CONTAINS',m) for m in markers];basis='IMPLEMENTATION_TEMPLATE'
                    ev.append(evidence(rel,1,'STATIC_TEMPLATE_PREFIX',traw))
                for form in forms:
                    if local_path(form['action']) and form['method'] in ('POST','PUT','PATCH','DELETE'):
                        fields.append({'method':form['method'],'path':form['action'],'fields':sorted(set(form['fields'])),'evidence':evidence(rel,1,'STATIC_TEMPLATE_FORM',traw)})
        input_fields=[]
        for j in range(len(body)-4):
            if body[j:j+4]==[req,'.','body','.'] and FIELD.fullmatch(body[j+4]):input_fields.append(body[j+4])
            if body[j:j+3]==[req,'.','body'] and j>=4 and body[j-1]=='=' and body[j-2]=='}':
                k=j-3
                while k>=0 and (FIELD.fullmatch(body[k]) or body[k]==','):k-=1
                if k>=0 and body[k]=='{':input_fields.extend(x for x in body[k+1:j-2] if FIELD.fullmatch(x))
        for j in range(len(body)-5):
            if body[j:j+4]==[res,'.','redirect','('] and body[j+5]==')' and local_path(plain_string(body[j+4])):redirect=plain_string(body[j+4])
        r=row(method,url,ev,assertions=assertions,basis=basis,fields=input_fields,redirect=redirect);r['forms']=fields;rows.append(r)
        if len(rows)>=MAX_ROUTES:break
    return rows


def workflow_expectations(role):
    # Symbols, not executable substitutions; a later runner must implement and validate them.
    return {
        'CREATE':[{'kind':'RECORD_CREATED','identity':'CREATED_RECORD','value':'CREATE_VALUE'}],
        'LIST_CREATED':[{'kind':'UNIQUE_RECORD_MATCH','identity':'CREATED_RECORD','value':'CREATE_VALUE'}],
        'READ_CREATED':[{'kind':'RECORD_VALUE_EQUALS','identity':'CREATED_RECORD','value':'CREATE_VALUE'}],
        'UPDATE':[{'kind':'RECORD_CHANGED','identity':'CREATED_RECORD','value':'UPDATE_VALUE'}],
        'READ_UPDATED':[{'kind':'RECORD_VALUE_EQUALS','identity':'CREATED_RECORD','value':'UPDATE_VALUE'}],
        'DELETE':[{'kind':'RECORD_REMOVED','identity':'CREATED_RECORD'}],
        'VERIFY_REMOVED':[{'kind':'RECORD_ABSENT','identity':'CREATED_RECORD'},{'kind':'UNRELATED_RECORDS_PRESERVED'}],
    }[role]


def workflow(rows,cid,runtime_blockers=()):
    """A deliberately non-executable workflow with unresolved identity/state bindings."""
    by={(r['method'],r['path']):r for r in rows};result=[]
    for create in rows:
        if create['method']!='POST' or not create['input_fields'] or not create['redirect'] or ':' in create['path']:continue
        listing=create['redirect']; listing_row=by.get(('GET',listing))
        if not listing_row:continue
        # Resource family inferred from the declared redirect and shared parameter prefix.
        prefix=listing.rstrip('/')+'/'
        reads=[r for r in rows if r['method']=='GET' and r['path'].startswith(prefix) and re.fullmatch(r':[A-Za-z_]\w*',r['path'][len(prefix):])]
        if len(reads)!=1:continue
        read=reads[0];target=read['path'];updates=[r for r in rows if r['method'] in ('PUT','PATCH') and (r['path']==target or r['path'].startswith(target+'/')) and r['input_fields']]
        deletes=[r for r in rows if r['method']=='DELETE' and (r['path']==target or r['path'].startswith(target+'/'))]
        if len(updates)!=1 or len(deletes)!=1:continue
        up,de=updates[0],deletes[0]
        # Do not propose credentials or destructive operations outside a disposable isolated state.
        fields=create['input_fields']+up['input_fields']
        if any(re.search(r'password|secret|token|credential|api.?key',x,re.I) for x in fields):continue
        forms=[f for r in rows for f in r.get('forms',[]) if f['method']=='POST' and f['path']==create['path']]
        result.append({'id':'WF-'+digest([cid,create['path'],target,up['path'],de['path']])[:16].upper(),'component_id':cid,'status':'NEEDS_BINDING_AND_REVIEW','oracle_basis':'INFERRED_RESOURCE_LIFECYCLE','scope':'ISOLATED_DISPOSABLE_STATE_ONLY','runtime_blockers':list(runtime_blockers),'binding_requirements':['UNIQUE_SYNTHETIC_VALUES_AND_FIELD_VALIDATION','CORRELATE_CREATED_RECORD_TO_ID_WITHOUT_GUESSING','CONFIRM_STATE_RESET_AND_NO_EXTERNAL_SIDE_EFFECTS','CONFIRM_RESPONSE_AND_POSTCONDITION_ORACLES'],'steps':[{'role':role,'method':r['method'],'path_template':r['path'],'input_fields':r['input_fields'],'expected_behavior':expected,'proposed_assertions':workflow_expectations(role),'evidence':r['evidence']} for role,r,expected in [('CREATE',create,'Create one uniquely identifiable record'),('LIST_CREATED',listing_row,'Observe created record and bind its identity'),('READ_CREATED',read,'Observe created value for the bound identity'),('UPDATE',up,'Change the bound record to a different unique value'),('READ_UPDATED',read,'Observe updated value for the same identity'),('DELETE',de,'Remove only the created record'),('VERIFY_REMOVED',listing_row,'Verify created record is absent; unrelated records remain')]],'matching_create_forms':forms,'symbols':{'CREATE_VALUE':'UNIQUE_SYNTHETIC_INPUT_REQUIRED','UPDATE_VALUE':'DISTINCT_SYNTHETIC_INPUT_REQUIRED','CREATED_RECORD':'CAPTURE_FROM_CORRELATED_OBSERVATION_REQUIRED'},'execution':'NOT_PERFORMED'})
    return result[:12]


def build_plan(root,source,manifest,runtime,phase3):
    source=validate_source(source);root=Path(root)
    if validate_source(runtime.get('source'))!=source or validate_source(phase3.get('source'))!=source or validate_source(manifest.get('source'))!=source:raise ValueError('PHASE9_SOURCE_MISMATCH')
    validate_test_plan(phase3)
    if generate_test_plan(manifest,None,source_root=root,source_verified=True)!=phase3:raise ValueError('PHASE9_PHASE3_PLAN_MISMATCH')
    if runtime.get('plan_sha256')!=digest({k:v for k,v in runtime.items() if k!='plan_sha256'}):raise ValueError('PHASE9_RUNTIME_PLAN_MISMATCH')
    scan=inspect(root);inspection=runtime['snapshot_inspection']
    if digest(scan.digests)!=inspection['inspected_content_sha256'] or digest(scan.paths)!=inspection['inventory_sha256'] or sorted(scan.issues)!=inspection['issues']:raise ValueError('PHASE9_SOURCE_CHANGED')
    cases=[];workflows=[];issues=set(scan.issues);discovery=[]
    components=runtime['components'];roots={c['root'] for c in components}
    if len(components)>MAX_COMPONENTS:issues.add('COMPONENT_LIMIT_REACHED')
    for c in components[:MAX_COMPONENTS]:
        cid=c['id'];base=c['root'];prefix='' if base=='.' else base+'/';adapter=c['adapter'];entry=c['entrypoint'];rows=[]
        if isinstance(entry,str) and safe_path(entry) and prefix+entry in scan.digests:
            path=prefix+entry;raw=source_bytes(root,path,limit=512_000)
            if raw is not None and sha256(raw).hexdigest()!=scan.digests[path]:raise ValueError('PHASE9_SOURCE_CHANGED')
            if raw is not None and adapter in ('PY_FLASK','PY_ASGI'):rows=python_routes(raw,path,c['application_symbol'],adapter)
            elif raw is not None and adapter=='NODE_HTTP':rows=express_routes(root,raw,path,base)
            elif raw is not None and adapter=='STATIC_HTTP':
                # Reuse the existing exact-byte characterization oracle for static resources.
                rows=[row('GET','/',[evidence(path,None,'STATIC_ENTRY_RESOURCE',raw)],assertions=[check('HTTP_STATUS_EQUALS',200),check('HTTP_BODY_SHA256_EQUALS',sha256(raw).hexdigest())],basis='STATIC_RESOURCE_BYTES')]
        duplicates=Counter((r['method'],r['path']) for r in rows)
        rows=[r for r in rows if duplicates[(r['method'],r['path'])]==1]
        if any(n>1 for n in duplicates.values()):issues.add('AMBIGUOUS_DIRECT_ROUTE_DECLARATION')
        discovered=set()
        def add(r,basis,assertions,blockers):
            key=(r['method'],r['path']);discovered.add(key)
            cases.append({'id':'FT-'+digest([cid,*key])[:16].upper(),'component_id':cid,'adapter':adapter,'method':key[0],'path_template':key[1],'input_fields':r.get('input_fields',[]),'assertions':assertions,'oracle_basis':basis,'declared_contract':basis=='REPOSITORY_DECLARED_OPENAPI','status':'ORACLE_PROPOSED' if assertions and not blockers else 'NEEDS_REVIEW','blockers':sorted(set(blockers)),'evidence':r['evidence'],'execution':'NOT_PERFORMED'})
        # OpenAPI declarations from Phase 3 inventory remain independent of implementation extraction.
        contracts={}
        for spec in phase3['inventory'].get('source_contracts',[]):
            p=spec.get('file');url=spec.get('path')
            if not isinstance(p,str) or not p.startswith(prefix) or any(p.startswith(q+'/') for q in roots if q!=base and q.startswith(prefix)) or not local_path(url) or ':' in url or not spec.get('assertable'):continue
            raw=source_bytes(root,p)
            if raw is not None and p in scan.paths:contracts[url]=evidence(p,None,'DECLARED_OPENAPI_GET_200',raw)
        for r in rows:
            blockers=list(c['blockers'])
            if r['method']!='GET':blockers.append('MUTATION_OR_METHOD_REQUIRES_REVIEW')
            if ':' in r['path']:blockers.append('PATH_BINDING_REQUIRED')
            basis=r['basis'];assertions=r['assertions']
            if r['method']=='GET' and r['path'] in contracts:
                # Do not silently resolve a contradiction between implementation and declared contract.
                if any(a['kind']=='HTTP_STATUS_EQUALS' and a['expected']!=200 for a in assertions):blockers.append('DECLARATION_IMPLEMENTATION_CONFLICT')
                r['evidence'].append(contracts.pop(r['path']));basis='REPOSITORY_DECLARED_OPENAPI';assertions=[check('HTTP_STATUS_EQUALS',200)]
            if not assertions:blockers.append('EXPECTED_BEHAVIOR_NOT_ESTABLISHED')
            if adapter not in ('NODE_HTTP','PY_FLASK','PY_ASGI','STATIC_HTTP'):blockers.append('FUNCTIONAL_RUNNER_CAPABILITY_UNESTABLISHED')
            if scan.issues:blockers.append('SOURCE_SCAN_INCOMPLETE')
            add(r,basis,assertions,blockers)
        for url,ev in sorted(contracts.items()):
            add(row('GET',url,[ev]),'REPOSITORY_DECLARED_OPENAPI',[check('HTTP_STATUS_EQUALS',200)],list(c['blockers'])+(['SOURCE_SCAN_INCOMPLETE'] if scan.issues else [])+([] if adapter in ('NODE_HTTP','PY_FLASK','PY_ASGI','STATIC_HTTP') else ['FUNCTIONAL_RUNNER_CAPABILITY_UNESTABLISHED']))
        # Preserve broader discovery, but don't promote client calls or readiness to functional credit.
        for old in phase3['test_cases']:
            method=old['target']['method'];url=old['target']['path'];key=(method,url)
            if old['category'] not in ('HTTP_ROUTE','UI_PAGE') or method not in METHODS or not local_path(url) or key in discovered:continue
            ev=[]
            for e in old['evidence']:
                p=e['path']
                if e['source']!='repository' or not p.startswith(prefix) or any(p.startswith(q+'/') for q in roots if q!=base and q.startswith(prefix)):continue
                raw=source_bytes(root,p,limit=512_000)
                if raw is not None:ev.append(evidence(p,e['line'],'PHASE3_DISCOVERY_ONLY',raw))
            if ev:add(row(method,url,ev),None,[],list(c['blockers'])+['EXPECTED_BEHAVIOR_NOT_ESTABLISHED','ROUTE_OWNERSHIP_OR_CONTEXT_REVIEW_REQUIRED'])
        workflows.extend(workflow(rows,cid,c['blockers']) if adapter=='NODE_HTTP' else [])
        discovery.append({'component_id':cid,'root':base,'adapter':adapter,'runtime_status':c['status'],'runtime_blockers':c['blockers'],'direct_routes':len(rows),'phase3_inventory_retained':True})
    cases.sort(key=lambda x:(x['component_id'],x['path_template'],x['method']));workflows.sort(key=lambda x:x['id'])
    if len(cases)>MAX_CASES:issues.add('CASE_LIMIT_REACHED');cases=cases[:MAX_CASES]
    if len(workflows)>24:issues.add('WORKFLOW_LIMIT_REACHED');workflows=workflows[:24]
    result={'schema_version':'9A.1','stage':'FUNCTIONAL_TEST_PLANNING','source':source,'runtime_plan_sha256':runtime['plan_sha256'],'phase3_plan_sha256':digest(phase3),'status':'PROPOSALS_AVAILABLE' if cases or workflows else 'NO_SUPPORTED_FUNCTIONAL_PROPOSALS','runtime_permission':False,'automatic_untrusted_execution':False,'functional_tests_executed':0,'vm_start_attempts':0,'grade_credit':None,'prior_runtime_evidence_used':False,'llm_used':False,'components':discovery,'cases':cases,'workflows':workflows,'summary':{'cases':len(cases),'oracle_proposed':sum(c['status']=='ORACLE_PROPOSED' for c in cases),'needs_review':sum(c['status']=='NEEDS_REVIEW' for c in cases),'declared_contract_cases':sum(c['declared_contract'] for c in cases),'workflows_needing_binding':len(workflows)},'issues':sorted(issues),'limitations':['Implementation-derived expectations are characterization hypotheses, not independent functional specifications. Repository OpenAPI declarations are not teacher-approved requirements.','All proposals require independent validation, authorization and a suitable runner in Phase 9B. No test or readiness credit.','Only bounded direct Flask/FastAPI and CommonJS Express entrypoint patterns, literal responses, static resource byte hashes, EJS static prefixes and Phase 3 OpenAPI GET declarations gain new oracles. Other discoveries remain review-only.','Reports contain source-derived literal text and should be kept local. No runtime receipts are consumed; exact source identity permits later evidence joining.','No authentication, database provisioning, dynamic imports, browser execution, external services, negative status guesses or automatic mutation execution.']}
    result['plan_sha256']=digest(result);return result


def verify_rederived_plan(root,source,manifest,runtime,phase3,proposed):
    """Compare exact regenerated bytes/structure; never authorize a runtime from a plan."""
    expected=build_plan(root,source,manifest,runtime,phase3)
    if not isinstance(proposed,dict) or proposed!=expected:raise ValueError('PHASE9_PLAN_REDERIVATION_MISMATCH')
    return expected


def plan_repository(url,ref,out):
    req=validate_request({'submission_id':'PHASE9A','repository_url':url,'ref':ref,'mode':'STATIC','max_vm_launches':0});out=Path(out).absolute()
    with adapters.pinned_checkout(req['repository_url'],req['ref']) as (source,root):
        if not source_matches_url(source,req['repository_url']) or (ref and re.fullmatch(r'[0-9a-fA-F]{40}',ref) and source['commit_sha']!=ref.lower()):raise ValueError('PHASE9_SOURCE_MISMATCH')
        if out.is_relative_to(Path(root).absolute()):raise ValueError('PHASE9_OUTPUT_INSIDE_SOURCE')
        out=fresh(out)
        manifest=adapters.inspect_source(root,source,out/'phase1')
        base,runtime,_=compile_runtime(root,source=source)
        phase3=generate_test_plan(manifest,None,source_root=root,source_verified=True)
        result=build_plan(root,source,manifest,runtime,phase3)
        write(out/'runtime_plan.json',runtime);write(out/'phase3_test_plan.json',phase3);write(out/'functional_plan.json',result)
    return result
