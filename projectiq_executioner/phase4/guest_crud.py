"""4D-B trusted guest-only, bounded authenticated CRUD and read-only MySQL witnesses.

No arbitrary scripts/SQL/URL/credentials come from the student source. The
reviewed data bundle selects existing Phase 3 endpoint IDs; the trusted runner
uses a guest-generated D2 session. Only bounded observations leave the VM.
"""
from __future__ import annotations

from http.client import HTTPException
import json
import re
from typing import Any, Mapping

_SHA = re.compile(r'[a-f0-9]{40}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'TC-[A-F0-9]{16}\Z')
_URL = re.compile(r'/[A-Za-z0-9_./-]{1,180}\Z')
_EMAIL = re.compile(r'piq-[a-f0-9]{24}@example\.invalid\Z')
_LABEL = re.compile(r'[A-Za-z0-9 _.-]{1,80}\Z')
_DATE = re.compile(r'20[2-9][0-9]-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\Z')
_ROLES = ('CREATE', 'READ_CREATED', 'UPDATE_CAPTURED', 'READ_UPDATED', 'DELETE_CAPTURED', 'VERIFY_DELETED')
_METHODS = ('POST', 'GET', 'POST', 'GET', 'POST', 'GET')
_OBSERVATIONS = ('before_count', 'create_http', 'create_success', 'read_http',
                 'read_matches', 'id_is_valid', 'created_db_count', 'created_db_fields_match',
                 'update_http', 'update_success', 'read_updated_http',
                 'updated_matches', 'updated_api_fields_match', 'updated_db_count',
                 'updated_db_fields_match', 'delete_http', 'delete_success',
                 'read_deleted_http', 'deleted_matches', 'deleted_db_count')
_COUNT = {'before_count','read_matches','created_db_count','updated_matches','updated_db_count','deleted_matches','deleted_db_count'}
_BOOLEAN = {'create_success','id_is_valid','created_db_fields_match','update_success',
            'updated_api_fields_match','updated_db_fields_match','delete_success'}


class CrudError(ValueError):
    pass


def _deny(code: str) -> None:
    raise CrudError(code)


def _fields(node):
    if (not isinstance(node, dict) or set(node) != {'title','deadline','priority'}
            or not isinstance(node['title'], str) or not _LABEL.fullmatch(node['title'])
            or not isinstance(node['deadline'], str) or not _DATE.fullmatch(node['deadline'])
            or node['priority'] not in ('Low','Medium','High')):
        _deny('CRUD_INVALID_FIELDS')


def validate_crud_bundle(bundle: Any, *, expected_source=None, expected_adapter='php_mysql_v1') -> None:
    if (not isinstance(bundle, dict) or set(bundle) != {
            'schema_version', 'source', 'adapter', 'workflow_catalog_sha256',
            'auth_catalog_sha256', 'oracle_catalog_sha256', 'create', 'update', 'steps'}
            or bundle.get('schema_version') != '0.1'
            or bundle.get('adapter') != 'php_mysql_v1'
            or bundle['adapter'] != expected_adapter):
        _deny('CRUD_INVALID_BUNDLE')
    src = bundle['source']
    if (not isinstance(src, dict) or set(src) != {'kind','repository','commit_sha'}
            or src.get('kind') != 'github'
            or not isinstance(src.get('repository'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}',src['repository'])
            or not isinstance(src.get('commit_sha'),str) or not _SHA.fullmatch(src['commit_sha'])
            or expected_source is not None and src != expected_source
            or any(not isinstance(bundle.get(k),str) or not _HASH.fullmatch(bundle[k])
                   for k in ('workflow_catalog_sha256','auth_catalog_sha256','oracle_catalog_sha256'))):
        _deny('CRUD_INVALID_SOURCE_OR_HASH')
    _fields(bundle['create'])
    _fields(bundle['update'])
    if bundle['create']['title'] == bundle['update']['title']:
        _deny('CRUD_UPDATE_MUST_CHANGE_TITLE')
    steps = bundle['steps']
    if not isinstance(steps,list) or len(steps) != len(_ROLES):
        _deny('CRUD_INVALID_STEP_COUNT')
    for step, role, method in zip(steps,_ROLES,_METHODS):
        if (not isinstance(step,dict) or set(step) != {'role','test_id','method','path'}
                or step['role'] != role or step['method'] != method
                or not isinstance(step['test_id'],str) or not _ID.fullmatch(step['test_id'])
                or not isinstance(step['path'],str) or not _URL.fullmatch(step['path'])
                or step['path'].startswith('//') or '..' in step['path'].split('/')):
            _deny('CRUD_INVALID_STEP')
    if steps[1]['path'] != steps[3]['path'] or steps[1]['path'] != steps[5]['path'] or steps[1]['test_id'] != steps[3]['test_id'] or steps[1]['test_id'] != steps[5]['test_id']:
        _deny('CRUD_READ_STEPS_MUST_MATCH')
    if len({steps[i]['test_id'] for i in (0,1,2,4)}) != 4:
        _deny('CRUD_FOUR_DISTINCT_OPERATIONS_REQUIRED')


def _id(value):
    if type(value) is int and 1 <= value <= 10**9:
        return value
    if isinstance(value,str) and 1 <= len(value) <= 10 and value.isascii() and value.isdecimal() and not value.startswith('0'):
        n = int(value)
        return n if 1 <= n <= 10**9 else None
    return None


def _pairs(items):
    result={}
    for key,value in items:
        if key in result: _deny('CRUD_DUPLICATE_JSON_KEY')
        result[key]=value
    return result


def _http(method, path, body, cookie, *, port):
    from guest_api import _auth_http
    status,raw,headers,issue=_auth_http(port,method,path,'JSON' if body is not None else 'NONE',body,cookie,4)
    if issue:
        _deny('CRUD_LOCAL_HTTP_UNAVAILABLE')
    if len(raw)>1_048_576: _deny('CRUD_RESPONSE_LIMIT')
    try:
        return status,json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
                                 parse_constant=lambda _: _deny('CRUD_NONFINITE_JSON'))
    except (UnicodeError,ValueError,TypeError):
        _deny('CRUD_RESPONSE_NOT_JSON')


# Fixed trusted PHP read-only witness. SQL values are all PDO parameters;
# neither the catalog nor the repository can supply a SQL identifier/command.
_PHP = r'''
error_reporting(0);
try {
 $d=json_decode(stream_get_contents(STDIN,4096),true,10,JSON_THROW_ON_ERROR);
 if (getenv('PROJECTIQ_DB_NAME')!=='projectiq_run') exit(11);
 if (!is_array($d)||!isset($d['stage'],$d['email'],$d['title'])||!is_string($d['email'])) exit(12);
 $pdo=new PDO('mysql:host=127.0.0.1;dbname=projectiq_run;charset=utf8mb4',getenv('PROJECTIQ_DB_USER'),getenv('PROJECTIQ_DB_PASSWORD'),
 [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_EMULATE_PREPARES=>false,PDO::ATTR_TIMEOUT=>5]);
 if ($d['stage']==='BEFORE') {
   $q=$pdo->prepare('SELECT tasks.id FROM tasks JOIN users ON tasks.user_id=users.id WHERE users.email=? AND tasks.title=? LIMIT 2');
   $q->execute([$d['email'],$d['title']]);
 } else {
   if (!isset($d['id'])||!is_int($d['id'])||$d['id']<1) exit(13);
   $q=$pdo->prepare('SELECT tasks.title,tasks.deadline,tasks.priority,tasks.status FROM tasks JOIN users ON tasks.user_id=users.id WHERE users.email=? AND tasks.id=? LIMIT 2');
   $q->execute([$d['email'],$d['id']]);
 }
 $rows=$q->fetchAll(PDO::FETCH_ASSOC);
 $match=count($rows)===1;
 if ($match && $d['stage']!=='BEFORE') {
   $r=$rows[0];
   $match=($r['title']===$d['title'] && $r['deadline']===$d['deadline'] && $r['priority']===$d['priority'] && $r['status']==='Pending');
 }
 echo json_encode(['count'=>min(count($rows),2),'fields_match'=>$match],JSON_THROW_ON_ERROR);
} catch (Throwable $e) { exit(14); }
'''


def _db(stage, email, identity, fields, *, env, trusted_process):
    if (stage not in ('BEFORE','CREATED','UPDATED','DELETED')
            or not isinstance(email,str) or not _EMAIL.fullmatch(email)
            or stage != 'BEFORE' and _id(identity) is None
            or not isinstance(env,dict) or env.get('PROJECTIQ_DB_NAME')!='projectiq_run'):
        _deny('CRUD_DB_SCOPE_OR_ID_INVALID')
    doc={'stage':stage,'email':email,'title':fields['title'],
         'deadline':fields['deadline'],'priority':fields['priority']}
    if stage!='BEFORE': doc['id']=identity
    proc=trusted_process(['/usr/bin/php','-r',_PHP],input=json.dumps(doc,sort_keys=True).encode('utf-8'),timeout=20,env=env)
    if proc.returncode != 0 or not isinstance(proc.stdout,bytes) or len(proc.stdout)>512:
        _deny('CRUD_DB_READ_UNAVAILABLE')
    try: value=json.loads(proc.stdout.decode('utf-8'),object_pairs_hook=_pairs)
    except (ValueError,UnicodeError,TypeError): _deny('CRUD_DB_REPORT_INVALID')
    if (not isinstance(value,dict) or set(value)!={'count','fields_match'}
            or type(value['count']) is not int or value['count'] not in (0,1,2)
            or type(value['fields_match']) is not bool or value['fields_match'] and value['count']!=1):
        _deny('CRUD_DB_REPORT_INVALID')
    return value['count'],value['fields_match']


def _rows(data):
    if (not isinstance(data,list) or len(data)>1024 or any(not isinstance(x,dict) or len(x)>40 for x in data)):
        _deny('CRUD_INVALID_LIST_RESPONSE')
    return data


def _matches(row, expected):
    return (isinstance(row,dict) and row.get('title')==expected['title']
            and row.get('deadline')==expected['deadline']
            and row.get('priority')==expected['priority'] and row.get('status')=='Pending')


def _empty(bundle,status,issue,observations):
    return {'schema_version':'0.1','stage':'guest_authenticated_crud',
            'source':bundle['source'],'adapter':bundle['adapter'],
            'workflow_catalog_sha256':bundle['workflow_catalog_sha256'],
            'auth_catalog_sha256':bundle['auth_catalog_sha256'],
            'oracle_catalog_sha256':bundle['oracle_catalog_sha256'],
            'execution':'GUEST_LOCALHOST_SESSION_AND_READONLY_DB',
            'status':status,'issue_code':issue,'observations':observations}


def inconclusive_crud_report(bundle):
    validate_crud_bundle(bundle,expected_source=bundle['source'],expected_adapter=bundle['adapter'])
    return _empty(bundle,'INCONCLUSIVE','AUTHENTICATION_NOT_VERIFIED',dict.fromkeys(_OBSERVATIONS))


def run_crud_flow(bundle, *, expected_source, expected_adapter, cookie, email, env=None,
                  trusted_process=None, port=8765, http_client=None, db_reader=None):
    """Guest-only 6-step CRUD: bind API-captured ID to read-only MySQL rows."""
    validate_crud_bundle(bundle,expected_source=expected_source,expected_adapter=expected_adapter)
    if (not isinstance(cookie,str) or not cookie or len(cookie)>1024
            or not isinstance(email,str) or not _EMAIL.fullmatch(email)
            or type(port) is not int or not 1 <= port <= 65535):
        _deny('CRUD_SESSION_NOT_TRUSTED')
    if http_client is None and (env is None or trusted_process is None):
        _deny('CRUD_GUEST_RUNTIME_REQUIRED')
    send=http_client or _http
    observe=db_reader or (lambda stage, e, i, f: _db(stage,e,i,f,env=env,trusted_process=trusted_process))
    obs=dict.fromkeys(_OBSERVATIONS)
    issue=None
    def fail(code):
        nonlocal issue
        issue=code
    def read(step):
        code,data=send('GET',step['path'],None,cookie,port=port)
        return code,_rows(data) if code==200 else None
    try:
        before,_=observe('BEFORE',email,None,bundle['create'])
        obs['before_count']=before
        if before!=0: fail('CRUD_PREEXISTING_ROW')
        if issue is None:
            code,data=send('POST',bundle['steps'][0]['path'],bundle['create'],cookie,port=port)
            obs['create_http']=code
            obs['create_success']=code==200 and type(data) is dict and data.get('success') is True
            if not obs['create_success']: fail('CRUD_CREATE_ASSERTION_FAILED')
        if issue is None:
            code,rows=read(bundle['steps'][1]);obs['read_http']=code
            if code!=200: fail('CRUD_READ_HTTP_FAILED')
            else:
                matches=[x for x in rows if x.get('title')==bundle['create']['title']]
                obs['read_matches']=min(len(matches),2)
                if len(matches)!=1: fail('CRUD_NEW_TASK_NOT_UNIQUE')
                else:
                    ident=_id(matches[0].get('id'))
                    obs['id_is_valid']=ident is not None
                    if ident is None or not _matches(matches[0],bundle['create']): fail('CRUD_READ_FIELDS_INVALID')
        if issue is None:
            count,fields=observe('CREATED',email,ident,bundle['create'])
            obs['created_db_count']=count;obs['created_db_fields_match']=fields
            if count!=1 or not fields: fail('CRUD_CREATE_DB_MISMATCH')
        if issue is None:
            payload={'task_id':ident,**bundle['update']}
            code,data=send('POST',bundle['steps'][2]['path'],payload,cookie,port=port)
            obs['update_http']=code
            obs['update_success']=code==200 and type(data) is dict and data.get('success') is True
            if not obs['update_success']: fail('CRUD_UPDATE_ASSERTION_FAILED')
        if issue is None:
            code,rows=read(bundle['steps'][3]);obs['read_updated_http']=code
            if code!=200: fail('CRUD_UPDATE_READ_HTTP_FAILED')
            else:
                matches=[x for x in rows if _id(x.get('id'))==ident]
                obs['updated_matches']=min(len(matches),2)
                obs['updated_api_fields_match']=len(matches)==1 and _matches(matches[0],bundle['update'])
                if not obs['updated_api_fields_match']: fail('CRUD_UPDATE_API_MISMATCH')
        if issue is None:
            count,fields=observe('UPDATED',email,ident,bundle['update'])
            obs['updated_db_count']=count;obs['updated_db_fields_match']=fields
            if count!=1 or not fields: fail('CRUD_UPDATE_DB_MISMATCH')
        if issue is None:
            code,data=send('POST',bundle['steps'][4]['path'],{'task_id':ident},cookie,port=port)
            obs['delete_http']=code
            obs['delete_success']=code==200 and type(data) is dict and data.get('success') is True
            if not obs['delete_success']: fail('CRUD_DELETE_ASSERTION_FAILED')
        if issue is None:
            code,rows=read(bundle['steps'][5]);obs['read_deleted_http']=code
            if code!=200: fail('CRUD_DELETE_READ_HTTP_FAILED')
            else:
                matches=[x for x in rows if _id(x.get('id'))==ident]
                obs['deleted_matches']=min(len(matches),2)
                if matches: fail('CRUD_DELETED_TASK_STILL_VISIBLE')
        if issue is None:
            count,_=observe('DELETED',email,ident,bundle['update'])
            obs['deleted_db_count']=count
            if count!=0: fail('CRUD_DELETE_DB_ROW_REMAINS')
    except (OSError,TimeoutError,HTTPException,CrudError,ValueError,TypeError,UnicodeError):
        return _empty(bundle,'INCONCLUSIVE','CRUD_OBSERVATION_UNAVAILABLE',obs)
    return _empty(bundle,'PASS' if issue is None else 'FAIL',issue,obs)


def validate_crud_report(bundle,report):
    validate_crud_bundle(bundle,expected_source=bundle['source'],expected_adapter=bundle['adapter'])
    if (not isinstance(report,dict) or set(report)!=set(_empty(bundle,'INCONCLUSIVE',None,{}))
            or any(report.get(k)!=bundle[k] for k in ('source','adapter','workflow_catalog_sha256','auth_catalog_sha256','oracle_catalog_sha256'))
            or report.get('schema_version')!='0.1' or report.get('stage')!='guest_authenticated_crud'
            or report.get('execution')!='GUEST_LOCALHOST_SESSION_AND_READONLY_DB'
            or report.get('status') not in ('PASS','FAIL','INCONCLUSIVE')
            or report.get('issue_code') is not None and (not isinstance(report['issue_code'],str) or len(report['issue_code'])>80)
            or not isinstance(report.get('observations'),dict) or set(report['observations'])!=set(_OBSERVATIONS)):
        _deny('CRUD_INVALID_REPORT')
    obs=report['observations']
    for k in _OBSERVATIONS:
        value=obs[k]
        if k in _COUNT and value is not None and (type(value) is not int or value not in (0,1,2)):
            _deny('CRUD_INVALID_COUNT')
        if k in _BOOLEAN and value is not None and type(value) is not bool:
            _deny('CRUD_INVALID_BOOLEAN')
        if k not in _COUNT | _BOOLEAN and value is not None and (type(value) is not int or not 100<=value<=599):
            _deny('CRUD_INVALID_HTTP')
    required={'before_count':0,'create_http':200,'create_success':True,'read_http':200,
              'read_matches':1,'id_is_valid':True,'created_db_count':1,
              'created_db_fields_match':True,'update_http':200,'update_success':True,
              'read_updated_http':200,'updated_matches':1,'updated_api_fields_match':True,
              'updated_db_count':1,'updated_db_fields_match':True,'delete_http':200,
              'delete_success':True,'read_deleted_http':200,'deleted_matches':0,'deleted_db_count':0}
    passed = obs == required
    if (report['status']=='PASS')!=passed or (report['status']=='PASS') != (report['issue_code'] is None):
        _deny('CRUD_REPORT_PASS_NOT_SUPPORTED')
    if report['status']=='INCONCLUSIVE' and report['issue_code'] not in {'AUTHENTICATION_NOT_VERIFIED','CRUD_OBSERVATION_UNAVAILABLE'}:
        _deny('CRUD_INCONCLUSIVE_REASON_INVALID')
    if report['status']=='FAIL' and (not isinstance(report['issue_code'],str) or not report['issue_code'].startswith('CRUD_')):
        _deny('CRUD_FAIL_REASON_INVALID')
