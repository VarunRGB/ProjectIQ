"""Report-layer source and reviewed-evidence enrichment (never authorizes execution)."""
from __future__ import annotations
from collections import defaultdict
import re

_SERVER={'file_endpoint','declared_http_route','file_based_route','declared_api_contract'}
_CLIENT={'client_http_call','form_action'}
_METHODS={'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS','TRACE','CONNECT'}

def route_inventory(manifest):
    """Count server route records by unique path/method; preserve client observations.

    A unique suffix match is only an intended-method hint, not URL resolution
    or proof of server enforcement. Never modify the Phase 3 plan or oracle IDs.
    """
    raw=[x for x in (manifest.get('routes') or []) if type(x) is dict]
    server={}
    for r in raw:
        if r.get('kind') not in _SERVER or not isinstance(r.get('path'),str):continue
        path=r['path']
        if r['kind']=='file_endpoint':path='/'+path.lstrip('/')
        key=(path,r.get('method') or 'UNKNOWN')
        s=server.setdefault(key,{'path':path,'method':key[1],'kind':r['kind'],
                                  'confidence':r.get('confidence'),'client_methods':[],
                                  'method_basis':'STATIC_SERVER_OBSERVATION','client_references':0})
    clients=[x for x in raw if x.get('kind') in _CLIENT]
    for c in clients:
        path=c.get('path'); method=c.get('method')
        if not isinstance(path,str) or method not in _METHODS:continue
        if path.startswith(('http:','https:','//')):continue
        # Do not turn dynamic routes, queries or arbitrary URLs into local paths.
        if not re.fullmatch(r'(?:\.\./|\./)*[A-Za-z0-9_./-]+',path):continue
        cleaned=path
        while cleaned.startswith(('../','./')):cleaned=cleaned.split('/',1)[1]
        cleaned='/'+cleaned.lstrip('/')
        matches=[s for s in server.values() if s['path']==cleaned and
                 (s['method']==method or s['method']=='UNKNOWN')]
        if len(matches)!=1:continue
        s=matches[0];s['client_references']+=1
        s['client_methods']=sorted(set(s['client_methods']+[method]))
    for s in server.values():
        if s['method']=='UNKNOWN' and len(s['client_methods'])==1:
            s['intended_method']=s['client_methods'][0];s['method_basis']='CLIENT_REFERENCE_HINT_ONLY'
        else:s['intended_method']=s['method']
    return {'route_records':len(raw),'server_endpoint_count':len(server),
            'client_route_records':len(clients),'server_endpoints':sorted(server.values(),key=lambda s:(s['path'],s['method']))[:400],
            'count_scope':'UNIQUE_STATIC_SERVER_PATH_METHOD_RECORDS_NOT_VERIFIED_URLS'}

def plan_review_hints(plan, inventory, manifest):
    """Additional proposed coverage, deliberately outside the approved plan."""
    cases=(plan or {}).get('test_cases') or []
    covered={((c.get('target') or {}).get('path')) for c in cases if type(c) is dict}
    hints=[]
    for s in inventory.get('server_endpoints',[]):
        method=s['intended_method'];path=s['path']
        if method=='UNKNOWN':families=['CONFIRM_HTTP_METHOD','EXPECTED_RESPONSE']
        elif method in ('POST','PUT','PATCH','DELETE'):families=['VALID_INPUT','INVALID_INPUT','AUTHORIZATION_IF_REQUIRED','STATE_CHANGE_IF_APPLICABLE']
        else:families=['EXPECTED_RESPONSE','ACCESS_CONTROL_IF_REQUIRED']
        hints.append({'path':path,'method':method,'method_basis':s['method_basis'],
                      'scenarios':families,'status':'PROPOSED_NOT_APPROVED'})
    for e in manifest.get('entrypoints',[]):
        if type(e) is not dict or e.get('kind')!='web_page':continue
        p=e.get('path')
        if not isinstance(p,str) or not re.fullmatch(r'[A-Za-z0-9_./-]+\.html?',p) or '..' in p.split('/'):continue
        p='/'+p.lstrip('/')
        if p not in covered:hints.append({'path':p,'method':'GET','method_basis':'STATIC_PAGE_CANDIDATE',
                                         'scenarios':['PAGE_READINESS','UI_BEHAVIOR_IF_REVIEWED'],'status':'PROPOSED_NOT_APPROVED'})
    return hints[:400]

def scenario_links(kind, compiled):
    """Copy only identifiers and HTTP metadata from an approved host compiler.

    Do not export request bodies, fixture values, credentials or JSON expected
    values. A scenario is narrower than a route specification.
    """
    if kind=='api': rows=[(x.get('id'),x) for x in compiled.get('tests',[])]
    elif kind in ('auth','db_postconditions','crud'):
        a=compiled.get('guest') if kind=='auth' else compiled.get('auth')
        a=a or {}; rows=[(name.upper(),a.get(name) or {}) for name in ('unauthenticated','registration','login','authenticated')] if kind=='auth' else []
        if kind=='crud':rows=[(x.get('role'),{'phase3_test_id':x.get('test_id'),'method':x.get('method'),'path':x.get('path')}) for x in (compiled.get('crud') or {}).get('steps',[])]
    elif kind=='dependent_workflow':rows=[(x.get('role'),{'phase3_test_id':x.get('test_id'),'method':x.get('method'),'path':x.get('path')}) for x in (compiled.get('workflow') or {}).get('steps',[])]
    else:return []
    out=[]
    for ident,x in rows:
        parent=x.get('phase3_test_id')
        if not isinstance(ident,str) or not isinstance(parent,str):continue
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,90}',ident) or not re.fullmatch(r'TC-[A-F0-9]{16}',parent):continue
        method=x.get('method');path=x.get('path')
        if method not in _METHODS or not isinstance(path,str) or not path.startswith('/') or len(path)>230:continue
        assertions=[]
        for a in x.get('assertions') or []:
            if type(a) is not dict or not isinstance(a.get('kind'),str):continue
            k=a['kind']; row={'kind':k}
            if k=='HTTP_STATUS_EQUALS' and type(a.get('expected')) is int and 100<=a['expected']<=599:row['expected']=a['expected']
            assertions.append(row)
        expected=x.get('expected')
        if not assertions and type(expected) is dict:
            status=expected.get('status')
            if type(status) is int and 100<=status<=599:assertions.append({'kind':'HTTP_STATUS_EQUALS','expected':status})
            if expected.get('json_subset') is not None:assertions.append({'kind':'JSON_BODY_SUBSET_EQUALS'})
        out.append({'check_id':ident,'phase3_test_id':parent,'method':method,'path':path,'expected_assertions':assertions})
    return out

def bounded_observations(kind, report, links):
    """Extract safe, bounded outcomes from already validated runner reports."""
    rows=report.get('results') or report.get('checks') or report.get('scenarios') or []
    if kind in ('dependent_workflow','crud'):
        # The validated guest report provides an aggregate workflow verdict
        # and individual scalar observations, NOT independent step verdicts.
        # Keep it one scenario; do not inflate test counts.
        status=report.get('status','')
        if workflow_metrics(kind,report) and status.endswith(('PASSED','FAILED','INCONCLUSIVE')):
            return [{'id':'WORKFLOW' if kind=='dependent_workflow' else 'CRUD_WORKFLOW',
                     'result':'PASS' if status.endswith('PASSED') else 'FAIL' if status.endswith('FAILED') else 'INCONCLUSIVE',
                     'issue_code':report.get('issue_code')}]
    by_id={x['check_id']:x for x in links}
    output=[]
    if type(rows) is not list:return output
    for row in rows[:400]:
        if type(row) is not dict:continue
        item={k:row[k] for k in ('test_id','id','step','result','status','http_status','issue_code','reason_code') if k in row}
        if kind in ('browser_ui','browser_login'):
            if 'id' not in item and isinstance(row.get('tag'),str):item['id']=row['tag']
            if 'result' not in item:
                flag=row.get('matched',row.get('passed'))
                item['result']='PASS' if flag is True else 'FAIL' if flag is False else 'INCONCLUSIVE'
        if kind=='scenario_isolation' and row.get('status')=='RESET_VERIFIED':item['result']='PASS'
        ident=item.get('test_id',item.get('id',item.get('step')))
        if ident in by_id:
            link=by_id[ident];item.update({k:v for k,v in link.items() if k!='check_id'})
        if type(row.get('assertions')) is list:
            item['assertions']=[{'kind':a['kind'],'result':a['result']} for a in row['assertions'][:32]
                                if type(a) is dict and isinstance(a.get('kind'),str) and a.get('result') in ('PASS','FAIL')]
        for k in ('before_matches','after_matches','expected_fields_match'):
            if type(row.get(k)) in (int,bool):item[k]=row[k]
        output.append(item)
    return output

_METRIC_KEYS={
    'dependent_workflow':('discovery_http','discovery_matches','captured_matches_seed','mutation_http','mutation_subset_match','verification_http','remaining_matches'),
    'crud':('before_count','create_http','create_success','read_http','read_matches','id_is_valid','created_db_count','created_db_fields_match','update_http','update_success','read_updated_http','updated_matches','updated_api_fields_match','updated_db_count','updated_db_fields_match','delete_http','delete_success','read_deleted_http','deleted_matches','deleted_db_count'),
    'db_fixture':('table_count','seeded_rows'),
}

def workflow_metrics(kind, report):
    """Expose only known numeric/boolean witness fields, never source values."""
    raw=report if kind=='db_fixture' else report.get('observations')
    if type(raw) is not dict:return {}
    return {k:raw[k] for k in _METRIC_KEYS.get(kind,()) if k in raw and (raw[k] is None or type(raw[k]) in (bool,int))}
