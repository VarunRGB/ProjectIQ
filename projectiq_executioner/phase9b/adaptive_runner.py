"""Narrow data-only EJS record lifecycle runner. No repository code evaluated here.
Uses the Phase 4 bounded row selector and 9B-B bounded HTTP transport.
"""
import re
import hashlib
import json
import uuid
from html.parser import HTMLParser
from urllib.parse import urlencode

ROLES=('BASELINE','CREATE','LIST_CREATED','READ_CREATED','UPDATE','READ_UPDATED','VERIFY_UPDATED_LIST','DELETE','VERIFY_REMOVED')
MUTATIONS={'CREATE','UPDATE','DELETE'}
ERRORS={'REQUEST_TIMEOUT','HTTP_TRANSPORT_ERROR','RESPONSE_TOO_LARGE','UNSUPPORTED_CONTENT_ENCODING','BODY_DECODE_ERROR','RECORD_OBSERVATION_UNAVAILABLE','APPLICATION_NOT_AVAILABLE'}
IDENT=re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
FIELD=re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
LABEL=re.compile(r'[A-Za-z0-9 _-]{0,32}:?\Z')
PATH=re.compile(r'/[A-Za-z0-9_./~-]+\Z')


def require(ok):
    if not ok:raise ValueError('ADAPTIVE_CONTRACT_INVALID')


def safe_path(p):
    return isinstance(p,str) and len(p)<=200 and PATH.fullmatch(p) and '//' not in p and not any(x in ('.','..') for x in p.split('/'))


def validate_workflow(w):
    require(type(w) is dict and set(w)=={'workflow_id','list_path','create_path','read_template','update_template','update_method','delete_template','create_field','update_field','id_label','value_label','format'})
    require(isinstance(w['workflow_id'],str) and re.fullmatch(r'WF-[A-F0-9]{16}',w['workflow_id']))
    require(w['format']=='EJS_H3_P_H1_P_V1' and w['update_method'] in ('PATCH','PUT'))
    require(safe_path(w['list_path']) and safe_path(w['create_path']) and w['create_path']!=w['list_path'])
    require(w['create_path'].startswith(w['list_path'].rstrip('/')+'/'))
    read=w['read_template'];require(isinstance(read,str) and read==w['list_path'].rstrip('/')+'/:id')
    for k in ('update_template','delete_template'):
        p=w[k];require(isinstance(p,str) and (p==read or p.startswith(read+'/')) and p.count(':id')==1 and safe_path(p.replace(':id','binding')))
    for k in ('create_field','update_field'):
        v=w[k];require(isinstance(v,str) and FIELD.fullmatch(v) and not re.search('password|secret|token|credential|api.?key',v,re.I))
    for k in ('id_label','value_label'):
        v=w[k];require(isinstance(v,str) and LABEL.fullmatch(v) and v.endswith(':'))
    require(w['id_label']!=w['value_label'])
    return w


class RecordMarkup(HTMLParser):
    """Only plain h3/p or h1/p records. Reject nested/ambiguous markup."""
    def __init__(self,tags):
        super().__init__(convert_charrefs=True);self.tags=tags;self.items=[];self.active=None;self.text=[];self.hidden=[]
    def handle_starttag(self,tag,attrs):
        if self.hidden:return
        if tag in ('script','style','template','head'):
            require(self.active is None);self.hidden.append(tag);return
        if self.active is not None:require(False)
        if tag in self.tags:self.active=tag;self.text=[]
    def handle_startendtag(self,tag,attrs):
        if self.active is not None or tag in self.tags:require(False)
    def handle_endtag(self,tag):
        if self.hidden:
            if tag==self.hidden[-1]:self.hidden.pop()
            return
        if self.active is not None:
            require(tag==self.active);self.items.append((tag,''.join(self.text).strip()));self.active=None
            require(len(self.items)<=2048)
        elif tag in self.tags:require(False)
    def handle_data(self,data):
        if self.active is not None:self.text.append(data)
    def finish(self):require(self.active is None and not self.hidden);return self.items


def pairs(body,tags):
    p=RecordMarkup(tags);p.feed(body.decode('utf-8'));p.close();items=p.finish()
    require(len(items)%2==0 and all(items[i][0]==tags[i%2] for i in range(len(items))))
    return [(items[i][1],items[i+1][1]) for i in range(0,len(items),2)]


def parse_list(body,w):
    rows={}
    for ident,value in pairs(body,('h3','p')):
        require(ident.startswith(w['id_label']) and value.startswith(w['value_label']))
        ident=ident[len(w['id_label']):].strip();value=value[len(w['value_label']):].strip()
        require(IDENT.fullmatch(ident) and ident not in rows and len(value)<=4096)
        rows[ident]=value
    return rows


def parse_detail(body):
    rows=pairs(body,('h1','p'));require(len(rows)==1 and IDENT.fullmatch(rows[0][0]) and len(rows[0][1])<=4096);return rows[0]


def select(rows,value):
    # Reuse the original Phase 4 bounded dependent-workflow row selector,
    # with HTML observations adapted into its existing list-of-records contract.
    selector=globals().get('_phase4_select')
    if selector is None:
        from ..phase4.guest_workflow import _select as selector
    return selector([{'id':i,'value':v} for i,v in rows.items()],'value',value)


def contract_hash(w):
    return hashlib.sha256(json.dumps(w,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def execute(w,port,available,exchange):
    validate_workflow(w);require(type(port) is int and 1024<=port<=65535)
    nonce=uuid.uuid4().hex;created='piq-create-'+nonce;updated='piq-update-'+nonce
    baseline=None;identity=None;rows=[]
    for role in ROLES:
        if not available():
            rows.append({'role':role,'status':'NOT_RUN','http_status':None,'matched':None,'error_code':'APPLICATION_NOT_AVAILABLE'});break
        method='GET';path=w['list_path'];body=None
        if role=='CREATE':method='POST';path=w['create_path'];body=urlencode({w['create_field']:created}).encode('ascii')
        elif role in ('READ_CREATED','READ_UPDATED'):path=w['read_template'].replace(':id',identity)
        elif role=='UPDATE':method=w['update_method'];path=w['update_template'].replace(':id',identity);body=urlencode({w['update_field']:updated}).encode('ascii')
        elif role=='DELETE':method='DELETE';path=w['delete_template'].replace(':id',identity)
        def observe(code,raw,locations):
            nonlocal baseline,identity
            if role in MUTATIONS:return code in (302,303) and locations==[w['list_path']]
            if code!=200:return False
            if role in ('READ_CREATED','READ_UPDATED'):
                i,v=parse_detail(raw);return i==identity and v==(created if role=='READ_CREATED' else updated)
            current=parse_list(raw,w)
            if role=='BASELINE':baseline=current;return not select(current,created) and not select(current,updated)
            if role=='LIST_CREATED':
                matches=select(current,created)
                if len(matches)!=1:return False
                candidate=matches[0]['id']
                if candidate in baseline or current!={**baseline,candidate:created}:return False
                identity=candidate;return True
            if role=='VERIFY_UPDATED_LIST':return current=={**baseline,identity:updated}
            if role=='VERIFY_REMOVED':return current==baseline
            raise ValueError('ADAPTIVE_CONTRACT_INVALID')
        code,matched,error=exchange(method,path,port,body,observe)
        status='INCONCLUSIVE' if error is not None else 'PASS' if matched is True else 'FAIL'
        rows.append({'role':role,'status':status,'http_status':code,'matched':matched,'error_code':error})
        if status!='PASS':break
    result={'workflow_id':w['workflow_id'],'contract_sha256':contract_hash(w),'status':'PASS' if len(rows)==len(ROLES) and all(r['status']=='PASS' for r in rows) else rows[-1]['status'],'requests_attempted':sum(r['status']!='NOT_RUN' for r in rows),'steps':rows}
    return validate_report(result,w)


def validate_report(r,w):
    validate_workflow(w);require(type(r) is dict and set(r)=={'workflow_id','contract_sha256','status','requests_attempted','steps'} and r['workflow_id']==w['workflow_id'] and r['contract_sha256']==contract_hash(w))
    steps=r['steps'];require(type(steps) is list and 1<=len(steps)<=len(ROLES))
    for i,s in enumerate(steps):
        require(type(s) is dict and set(s)=={'role','status','http_status','matched','error_code'} and s['role']==ROLES[i])
        code=s['http_status'];match=s['matched'];err=s['error_code'];status=s['status']
        require(code is None or type(code) is int and 100<=code<=599)
        require(status in ('PASS','FAIL','INCONCLUSIVE','NOT_RUN') and (i==len(steps)-1 or status=='PASS'))
        if status=='NOT_RUN':require(code is None and match is None and err=='APPLICATION_NOT_AVAILABLE')
        elif status=='INCONCLUSIVE':require(match is None and err in ERRORS-{'APPLICATION_NOT_AVAILABLE'})
        else:
            require(type(match) is bool and match==(status=='PASS') and err is None and code is not None)
            if status=='PASS':require(code in (302,303) if s['role'] in MUTATIONS else code==200)
    require(type(r['requests_attempted']) is int and r['requests_attempted']==sum(s['status']!='NOT_RUN' for s in steps))
    require(r['status']==steps[-1]['status'] and (r['status']!='PASS' or len(steps)==len(ROLES)))
    return r
