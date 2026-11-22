"""4D-B independent approval compiler for authenticated CRUD and DB witnesses.

All requests are discovered Phase 3 targets, all inputs are reviewed literals,
and the existing D2 authentication approval remains independently mandatory.
"""
from __future__ import annotations

from typing import Any, Mapping
from .oracle_contracts import digest
from .guest_crud import CrudError, validate_crud_bundle
from .reviewed_auth import compile_reviewed_auth_bundle, AuthReviewError

_ROLES = ('CREATE','READ_CREATED','UPDATE_CAPTURED','READ_UPDATED','DELETE_CAPTURED','VERIFY_DELETED')
_METHODS = ('POST','GET','POST','GET','POST','GET')

class CrudReviewError(ValueError):
    pass


def _deny(code):
    raise CrudReviewError(code)


def compile_reviewed_crud(plan: Mapping[str,Any], auth_catalog: Mapping[str,Any],
                          oracle_catalog: Mapping[str,Any], crud_catalog: Mapping[str,Any],
                          candidate: Mapping[str,Any], config: Mapping[str,Any]) -> dict:
    try:
        auth = compile_reviewed_auth_bundle(plan,auth_catalog,oracle_catalog,candidate,config)
    except (AuthReviewError,KeyError,ValueError,TypeError) as exc:
        raise CrudReviewError('CRUD_REQUIRES_APPROVED_D2_AUTH') from exc
    if candidate.get('adapter')!='php_mysql_v1' or not isinstance(candidate.get('schema_path'),str):
        _deny('CRUD_REQUIRES_PHP_MYSQL_ADAPTER')
    src=plan['source']
    if (not isinstance(crud_catalog,dict) or set(crud_catalog)!= {'schema_version','source','create','update','steps'}
            or crud_catalog.get('schema_version')!='0.1' or crud_catalog.get('source')!=src):
        _deny('CRUD_CATALOG_INVALID')
    raw=crud_catalog['steps']
    if not isinstance(raw,list) or len(raw)!=6:
        _deny('CRUD_SIX_STEPS_REQUIRED')
    target_ids={item['id']:item for item in plan['test_cases']}
    compiled=[]
    for record,role,method in zip(raw,_ROLES,_METHODS):
        if (not isinstance(record,dict) or set(record)!={'role','test_id'} or record['role']!=role
                or not isinstance(record['test_id'],str)):
            _deny('CRUD_CATALOG_STEP_INVALID')
        case=target_ids.get(record['test_id'])
        if (not isinstance(case,dict) or case.get('status')=='UNSUPPORTED_BY_RUNNER'
                or case.get('category') not in ('HTTP_ROUTE','HTTP_READINESS','UI_PAGE')
                or not isinstance(case.get('target'),dict)
                or case['target'].get('method')!=method
                or not isinstance(case['target'].get('path'),str)):
            _deny('CRUD_ENDPOINT_NOT_ESTABLISHED_IN_PHASE3')
        compiled.append({'role':role,'test_id':record['test_id'],'method':method,'path':case['target']['path']})
    identity=src['repository']+'@'+src['commit_sha']
    approvals=config.get('approved_crud_workflows')
    h=digest(crud_catalog)
    if (not isinstance(approvals,list) or len(approvals)>256
            or any(not isinstance(v,str) or len(v)>260 for v in approvals)
            or identity+'#'+h not in approvals):
        _deny('CRUD_DIGEST_NOT_OPERATOR_APPROVED')
    guest={'schema_version':'0.1','source':dict(src),'adapter':'php_mysql_v1',
           'workflow_catalog_sha256':h,
           'auth_catalog_sha256':auth['guest']['auth_catalog_sha256'],
           'oracle_catalog_sha256':auth['guest']['oracle_catalog_sha256'],
           'create':crud_catalog['create'],'update':crud_catalog['update'],'steps':compiled}
    try:
        validate_crud_bundle(guest,expected_source=src,expected_adapter=candidate['adapter'])
    except (CrudError,KeyError,TypeError,ValueError) as exc:
        raise CrudReviewError('CRUD_GUEST_CONTRACT_INVALID') from exc
    return {'auth':auth['guest'],'crud':guest,
            '_review':{'plan':dict(plan),'auth_catalog':dict(auth_catalog),
                       'oracle_catalog':dict(oracle_catalog),'crud_catalog':dict(crud_catalog)}}


def validate_reviewed_crud(bundle:Any,candidate:Mapping[str,Any],config:Mapping[str,Any]) -> dict:
    if (not isinstance(bundle,dict) or set(bundle)!={'auth','crud','_review'}
            or not isinstance(bundle['_review'],dict) or set(bundle['_review'])!=
            {'plan','auth_catalog','oracle_catalog','crud_catalog'}):
        _deny('CRUD_REVIEW_PROOF_REQUIRED')
    doc=bundle['_review']
    rebuilt=compile_reviewed_crud(doc['plan'],doc['auth_catalog'],doc['oracle_catalog'],
                                  doc['crud_catalog'],candidate,config)
    if rebuilt!=bundle:
        _deny('CRUD_BUNDLE_DIFFERS_FROM_REVIEW')
    return {'auth':bundle['auth'],'crud':bundle['crud']}
