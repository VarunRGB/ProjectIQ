"""Standalone trusted guest HTTP runner. Only standard library; no source execution.
Also imported on host to validate the narrowed receipt. Never grants admission.
"""
import hashlib
import http.client
import json
import re
import signal
from html.parser import HTMLParser

MAX_BYTES = 262144
MAX_TESTS = 32
TIMEOUT = 5
HEX = re.compile(r'[0-9a-f]{64}\Z')
ID = re.compile(r'FT-[0-9A-F]{16}\Z')
PATH = re.compile(r'/[A-Za-z0-9_./~-]*\Z')
KINDS = {'HTTP_STATUS_EQUALS','BODY_TEXT_EQUALS','JSON_EQUALS','HTML_TEXT_CONTAINS','HTTP_BODY_SHA256_EQUALS'}
ERRORS = {'REQUEST_TIMEOUT','HTTP_TRANSPORT_ERROR','RESPONSE_TOO_LARGE','UNSUPPORTED_CONTENT_ENCODING','BODY_DECODE_ERROR','JSON_PARSE_ERROR','HTML_PARSE_ERROR','APPLICATION_NOT_AVAILABLE'}


def require(ok):
    if not ok: raise ValueError('FUNCTIONAL_CONTRACT_INVALID')


def canonical(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def strict_json(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d: raise ValueError('duplicate')
            d[k]=v
        return d
    def bad(_): raise ValueError('constant')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)


def validate_suite(suite):
    require(type(suite) is dict and set(suite)=={'compiled_sha256','tests'})
    require(isinstance(suite['compiled_sha256'],str) and HEX.fullmatch(suite['compiled_sha256']))
    tests=suite['tests'];require(type(tests) is list and 1<=len(tests)<=MAX_TESTS)
    ids=set();paths=set()
    for t in tests:
        require(type(t) is dict and set(t)=={'case_id','method','path','assertions'})
        p=t['path'];i=t['case_id'];require(isinstance(i,str) and ID.fullmatch(i) and i not in ids)
        require(t['method']=='GET' and isinstance(p,str) and len(p)<=200 and PATH.fullmatch(p) and '//' not in p and not any(x in ('.','..') for x in p.split('/')) and p not in paths)
        ids.add(i);paths.add(p);a=t['assertions'];require(type(a) is list and 1<=len(a)<=5)
        require(all(type(x) is dict and set(x)=={'kind','expected'} and isinstance(x['kind'],str) and x['kind'] in KINDS for x in a))
        require(a[0]['kind']=='HTTP_STATUS_EQUALS' and sum(x['kind']=='HTTP_STATUS_EQUALS' for x in a)==1)
        for x in a:
            k,v=x['kind'],x['expected']
            if k=='HTTP_STATUS_EQUALS':require(type(v) is int and 200<=v<=599)
            elif k=='HTTP_BODY_SHA256_EQUALS':require(isinstance(v,str) and HEX.fullmatch(v))
            elif k=='JSON_EQUALS':require(len(canonical(v))<=512)
            else:require(isinstance(v,str) and bool(v.strip()) and len(canonical(v))<=512)
    require(len(canonical(suite).encode())<=40000)
    return suite


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','template','head'):self.hidden.append(tag)
    def handle_endtag(self,tag):
        if self.hidden and self.hidden[-1]==tag:self.hidden.pop()
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
    def text(self):return ' '.join(' '.join(self.parts).split())


class Deadline(Exception):pass

def alarm(_signum,_frame):raise Deadline()


def outcomes(test,status,body):
    result=[]
    for a in test['assertions']:
        k,v=a['kind'],a['expected'];error=None;matched=None
        try:
            if k=='HTTP_STATUS_EQUALS':matched=status==v
            elif k=='HTTP_BODY_SHA256_EQUALS':matched=hashlib.sha256(body).hexdigest()==v
            elif k=='BODY_TEXT_EQUALS':matched=body.decode('utf-8')==v
            elif k=='JSON_EQUALS':
                try:matched=canonical(strict_json(body.decode('utf-8')))==canonical(v)
                except UnicodeError:raise
                except (ValueError,RecursionError,OverflowError):error='JSON_PARSE_ERROR'
            elif k=='HTML_TEXT_CONTAINS':
                try:
                    parser=Text();parser.feed(body.decode('utf-8'));parser.close();matched=' '.join(v.split()) in parser.text()
                except UnicodeError:raise
                except (ValueError,RecursionError,AssertionError):error='HTML_PARSE_ERROR'
        except UnicodeError:error='BODY_DECODE_ERROR'
        result.append({'kind':k,'matched':matched,'error_code':error})
    return result


def verdict(rows):
    if any(a['matched'] is False for a in rows):return 'FAIL'
    if any(a['matched'] is None for a in rows):return 'INCONCLUSIVE'
    return 'PASS'


def execute_one(test,port):
    """Linux main-thread wall deadline covers connect, headers, body and parsing."""
    row={'case_id':test['case_id'],'status':'INCONCLUSIVE','request_attempted':True,'http_status':None,'assertions':[],'error_code':None}
    con=None;previous=signal.signal(signal.SIGALRM,alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL,TIMEOUT)
        con=http.client.HTTPConnection('127.0.0.1',port,timeout=TIMEOUT)
        con.request('GET',test['path'],headers={'Connection':'close','Accept-Encoding':'identity','User-Agent':'ProjectIQ-9BB'})
        r=con.getresponse();row['http_status']=r.status
        if r.getheader('Content-Encoding','identity').strip().lower()!='identity':row['error_code']='UNSUPPORTED_CONTENT_ENCODING';return row
        body=r.read(MAX_BYTES+1)
        if len(body)>MAX_BYTES:row['error_code']='RESPONSE_TOO_LARGE';return row
        if r.length is not None and r.length!=0:row['error_code']='HTTP_TRANSPORT_ERROR';return row
        row['assertions']=outcomes(test,r.status,body);row['status']=verdict(row['assertions']);return row
    except (Deadline,TimeoutError):row['error_code']='REQUEST_TIMEOUT';return row
    except (OSError,http.client.HTTPException,ValueError):row['error_code']='HTTP_TRANSPORT_ERROR';return row
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if con is not None:con.close()


def execute_suite(suite,port,available):
    validate_suite(suite);require(type(port) is int and 1024<=port<=65535)
    rows=[]
    for t in suite['tests']:
        if not available():rows.append({'case_id':t['case_id'],'status':'NOT_RUN','request_attempted':False,'http_status':None,'assertions':[],'error_code':'APPLICATION_NOT_AVAILABLE'})
        else:rows.append(execute_one(t,port))
    return {'compiled_sha256':suite['compiled_sha256'],'cases':rows}


def validate_results(value,suite):
    """Narrow guest receipt; verify exact identities, shape and status consistency."""
    validate_suite(suite)
    require(type(value) is dict and set(value)=={'compiled_sha256','cases'} and value['compiled_sha256']==suite['compiled_sha256'])
    rows=value['cases'];require(type(rows) is list and len(rows)==len(suite['tests']))
    for row,t in zip(rows,suite['tests']):
        require(type(row) is dict and set(row)=={'case_id','status','request_attempted','http_status','assertions','error_code'} and row['case_id']==t['case_id'])
        s=row['http_status'];require(s is None or type(s) is int and 100<=s<=599)
        a=row['assertions'];require(type(a) is list and type(row['request_attempted']) is bool)
        err=row['error_code'];require(err is None or isinstance(err,str) and err in ERRORS)
        if not row['request_attempted']:
            require(row['status']=='NOT_RUN' and s is None and not a and err=='APPLICATION_NOT_AVAILABLE');continue
        if err is not None:
            require(row['status']=='INCONCLUSIVE' and not a and err!='APPLICATION_NOT_AVAILABLE');continue
        require(s is not None and len(a)==len(t['assertions']))
        for actual,expected in zip(a,t['assertions']):
            require(type(actual) is dict and set(actual)=={'kind','matched','error_code'} and actual['kind']==expected['kind'])
            match=actual['matched'];e=actual['error_code'];require(type(match) is bool and e is None or match is None and isinstance(e,str) and e in {'BODY_DECODE_ERROR','JSON_PARSE_ERROR','HTML_PARSE_ERROR'})
            k=expected['kind']
            if k=='HTTP_STATUS_EQUALS':require(match is (s==expected['expected']))
            if match is None:require(e in {'BODY_TEXT_EQUALS':{'BODY_DECODE_ERROR'},'JSON_EQUALS':{'BODY_DECODE_ERROR','JSON_PARSE_ERROR'},'HTML_TEXT_CONTAINS':{'BODY_DECODE_ERROR','HTML_PARSE_ERROR'}}.get(k,set()))
        require(row['status']==verdict(a))
    return value
