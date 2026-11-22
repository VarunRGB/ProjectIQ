"""4D-B: authenticated CRUD state transitions, DB witness and review safety.

All tests offline. Separate real QEMU acceptance is mandatory.
"""
from __future__ import annotations
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
import json

from projectiq_executioner.phase4.guest_crud import (
    CrudError, validate_crud_bundle, validate_crud_report,
    inconclusive_crud_report, run_crud_flow, _pairs)

SOURCE={'kind':'github','repository':'Fixture/CrudDemo','commit_sha':'a'*40}


def sample():
    return {'schema_version':'0.1','source':dict(SOURCE),'adapter':'php_mysql_v1',
            'workflow_catalog_sha256':'b'*64,'auth_catalog_sha256':'c'*64,
            'oracle_catalog_sha256':'d'*64,
            'create':{'title':'ProjectIQ CRUD Task','deadline':'2027-03-15','priority':'Low'},
            'update':{'title':'ProjectIQ CRUD Updated','deadline':'2027-03-16','priority':'High'},
            'steps':[{'role':role,'method':method,'path':path,'test_id':'TC-'+('ABCDEF%010X'%i)}
                     for i,(role,method,path) in enumerate([
                        ('CREATE','POST','/logic/add_task.php'),
                        ('READ_CREATED','GET','/logic/get_tasks.php'),
                        ('UPDATE_CAPTURED','POST','/logic/edit_task.php'),
                        ('READ_UPDATED','GET','/logic/get_tasks.php'),
                        ('DELETE_CAPTURED','POST','/logic/delete_task_user.php'),
                        ('VERIFY_DELETED','GET','/logic/get_tasks.php')])],}


def good():
    b=sample()
    for i in [3,5]:b['steps'][i]['test_id']=b['steps'][1]['test_id']
    return b


def simulate(*, overrides=None):
    b=good()
    row0={'id':'731','title':b['create']['title'],'deadline':b['create']['deadline'],
          'priority':'Low','status':'Pending'}
    row1={'id':731,'title':b['update']['title'],'deadline':b['update']['deadline'],
          'priority':'High','status':'Pending'}
    outputs=[(200,{'success':True}),(200,[row0]),(200,{'success':True}),
             (200,[row1]),(200,{'success':True}),(200,[])]
    stages={'BEFORE':(0,False),'CREATED':(1,True),'UPDATED':(1,True),'DELETED':(0,False)}
    if overrides:
        for k,v in overrides.get('http',{}).items():outputs[k]=v
        stages.update(overrides.get('db',{}))
    requests=[]; reads=[]
    def client(method,path,body,cookie,*,port):
        requests.append((method,path,body,cookie,port))
        return outputs[len(requests)-1]
    def db(stage,email,identity,fields):
        reads.append((stage,identity))
        return stages[stage]
    report=run_crud_flow(b,expected_source=SOURCE,expected_adapter='php_mysql_v1',
                         cookie='PHPSESSID=x',email='piq-'+'1'*24+'@example.invalid',
                         http_client=client,db_reader=db)
    validate_crud_report(b,report)
    return report,requests,reads


class CrudGuestTests(TestCase):
    def test_valid_bundle(self):validate_crud_bundle(good(),expected_source=SOURCE)
    def test_passing_full_crud_and_db_observations(self):
        report,calls,reads=simulate()
        self.assertEqual(report['status'],'PASS')
        self.assertEqual([r[0] for r in calls],['POST','GET','POST','GET','POST','GET'])
        self.assertEqual(calls[2][2]['task_id'],731)
        self.assertEqual(calls[4][2],{'task_id':731})
        self.assertEqual([r[0] for r in reads],['BEFORE','CREATED','UPDATED','DELETED'])
        self.assertNotIn('PHPSESSID',json.dumps(report))
        self.assertNotIn('731',json.dumps(report))
    def test_missing_creation_ack_stops_before_next_mutation(self):
        r,requests,_=simulate(overrides={'http':{0:(200,{'success':False})}})
        self.assertEqual(r['status'],'FAIL');self.assertEqual(len(requests),1)
    def test_wrong_created_fields_stops_before_update(self):
        r,calls,_=simulate(overrides={'db':{'CREATED':(1,False)}})
        self.assertEqual(r['status'],'FAIL');self.assertEqual(len(calls),2)
    def test_wrong_updated_database_fields_prevents_delete(self):
        r,calls,_=simulate(overrides={'db':{'UPDATED':(1,False)}})
        self.assertEqual(r['status'],'FAIL');self.assertEqual(len(calls),4)
    def test_delete_success_without_db_delete_fails(self):
        r,calls,_=simulate(overrides={'db':{'DELETED':(1,False)}})
        self.assertEqual(r['status'],'FAIL');self.assertEqual(len(calls),6)
    def test_deleted_task_still_visible_fails(self):
        b=good();row={'id':731,'title':b['update']['title']}
        r,calls,_=simulate(overrides={'http':{5:(200,[row])}})
        self.assertEqual(r['status'],'FAIL');self.assertEqual(len(calls),6)
    def test_bad_http_inconclusive(self):
        def client(*args,**kwargs):raise TimeoutError('no socket')
        b=good()
        r=run_crud_flow(b,expected_source=SOURCE,expected_adapter='php_mysql_v1',
                        cookie='PHPSESSID=x',email='piq-'+'1'*24+'@example.invalid',
                        http_client=client,db_reader=lambda *a:(0,False))
        self.assertEqual(r['status'],'INCONCLUSIVE')
        validate_crud_report(b,r)
    def test_inconclusive_auth_prior_to_mutation(self):
        b=good();r=inconclusive_crud_report(b)
        validate_crud_report(b,r)
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(CrudError):_pairs([('success',True),('success',False)])
    def test_guest_bundle_rejects_unsafe_paths(self):
        for path in ('//remote','/logic/../delete','https://example.net/x'):
            with self.subTest(path=path):
                b=good();b['steps'][2]['path']=path
                with self.assertRaises(CrudError):validate_crud_bundle(b)
    def test_guest_bundle_rejects_extra_actions(self):
        b=good();b['steps'][0]['command']='curl external'
        with self.assertRaises(CrudError):validate_crud_bundle(b)
    def test_wrong_source_and_adapter_rejected(self):
        for name in ('source','adapter'):
            b=good()
            if name=='source':b['source']['commit_sha']='e'*40
            else:b['adapter']='node_fullstack_offline_v1'
            with self.assertRaises(CrudError):validate_crud_bundle(b,expected_source=SOURCE)
    def test_tampered_report_cannot_fake_pass(self):
        r,_,_=simulate();r['observations']['deleted_db_count']=1
        with self.assertRaises(CrudError):validate_crud_report(good(),r)
    def test_secret_never_in_report(self):
        r,_,_=simulate()
        self.assertNotIn('email',json.dumps(r));self.assertNotIn('password',json.dumps(r))

class CrudHostReviewTests(TestCase):
    def _case(self):
        from projectiq_executioner.phase4.oracle_contracts import digest
        b=good()
        plan={'source':dict(SOURCE),'test_cases':[
            {'id':step['test_id'],'category':'HTTP_ROUTE','status':'NEEDS_ORACLE',
             'target':{'method':step['method'],'path':step['path']}}
            for step in (b['steps'][0],b['steps'][1],b['steps'][2],b['steps'][4])
        ]}
        catalog={'schema_version':'0.1','source':dict(SOURCE),
                 'create':deepcopy(b['create']),'update':deepcopy(b['update']),
                 'steps':[{'role':x['role'],'test_id':x['test_id']} for x in b['steps']]}
        config={'approved_crud_workflows':[SOURCE['repository']+'@'+SOURCE['commit_sha']+'#'+digest(catalog)]}
        candidate={'source':dict(SOURCE),'adapter':'php_mysql_v1','schema_path':'sql/schema.sql'}
        auth={'auth_catalog_sha256':'c'*64,'oracle_catalog_sha256':'d'*64}
        return plan,catalog,config,candidate,auth

    def _compile(self, plan=None,catalog=None,config=None,candidate=None,auth=None):
        from projectiq_executioner.phase4.crud_workflow_contracts import compile_reviewed_crud
        defaults=self._case()
        plan=plan or defaults[0];catalog=catalog or defaults[1]
        config=config or defaults[2];candidate=candidate or defaults[3]
        auth=auth or defaults[4]
        with patch('projectiq_executioner.phase4.crud_workflow_contracts.compile_reviewed_auth_bundle',return_value={'guest':auth}):
            return compile_reviewed_crud(plan,{}, {},catalog,candidate,config)

    def test_valid_review(self):
        proof=self._compile()
        self.assertEqual(len(proof['crud']['steps']),6)
        self.assertEqual(proof['crud']['adapter'],'php_mysql_v1')

    def test_missing_crud_approval_denied(self):
        from projectiq_executioner.phase4.crud_workflow_contracts import CrudReviewError
        plan,cat,cfg,cand,auth=self._case()
        cfg['approved_crud_workflows']=[]
        with self.assertRaises(CrudReviewError): self._compile(plan,cat,cfg,cand,auth)

    def test_catalog_change_requires_new_approval(self):
        from projectiq_executioner.phase4.crud_workflow_contracts import CrudReviewError
        plan,cat,cfg,cand,auth=self._case()
        cat['update']['priority']='Medium'
        with self.assertRaises(CrudReviewError): self._compile(plan,cat,cfg,cand,auth)

    def test_mismatched_plan_target_denied(self):
        from projectiq_executioner.phase4.crud_workflow_contracts import CrudReviewError
        plan,cat,cfg,cand,auth=self._case()
        plan['test_cases'][0]['target']['method']='GET'
        with self.assertRaises(CrudReviewError): self._compile(plan,cat,cfg,cand,auth)

    def test_recompile_proof_detects_mutation(self):
        from projectiq_executioner.phase4.crud_workflow_contracts import (
            CrudReviewError,validate_reviewed_crud)
        plan,cat,cfg,cand,auth=self._case()
        proof=self._compile(plan,cat,cfg,cand,auth)
        proof['crud']['update']['priority']='Low'
        with patch('projectiq_executioner.phase4.crud_workflow_contracts.compile_reviewed_auth_bundle',return_value={'guest':auth}):
            with self.assertRaises(CrudReviewError):validate_reviewed_crud(proof,cand,cfg)


class SessionCallbackTests(TestCase):
    def _bundle(self):
        return {'source':dict(SOURCE),'adapter':'php_mysql_v1',
            'registration':{'body_kind':'FORM','fields':{'email':{'$generated':'email'},'password':{'$generated':'password'}},
                            'method':'POST','path':'/auth/register.php','expected':{'status':200,'json_subset':None}},
            'login':{'body_kind':'FORM','fields':{'email':{'$generated':'email'},'password':{'$generated':'password'}},
                     'method':'POST','path':'/auth/login.php','expected':{'status':200,'json_subset':{'success':True}}},
            'unauthenticated':{'body_kind':'NONE','body':None,'method':'GET','path':'/protected','assertions':[]},
            'authenticated':{'body_kind':'NONE','body':None,'method':'GET','path':'/protected','assertions':[]},
            'auth_catalog_sha256':'a'*64,'oracle_catalog_sha256':'b'*64}

    def test_callback_only_after_auth_success(self):
        from projectiq_executioner.phase4 import guest_api
        callbacks=[]
        steps=[]
        def send(*args):
            steps.append(args[2])
            return 200,b'{}',([('Set-Cookie','PHPSESSID=abc; Path=/')] if len(steps)==3 else []),None
        with patch.object(guest_api,'validate_auth_guest_bundle'),patch.object(guest_api,'_auth_http',side_effect=send),patch.object(guest_api,'_auth_checks',return_value=[{'kind':'HTTP_STATUS_EQUALS','result':'PASS'}]):
            result=guest_api.run_auth_flow(self._bundle(),expected_source=SOURCE,expected_adapter='php_mysql_v1',
                                           authenticated_callback=lambda cookie,email:callbacks.append((cookie,email)))
        self.assertEqual(result['summary']['passed'],4)
        self.assertEqual(len(callbacks),1)
        self.assertTrue(callbacks[0][1].startswith('piq-'))
        self.assertNotIn(callbacks[0][1],json.dumps(result))

    def test_callback_not_called_on_failed_step(self):
        from projectiq_executioner.phase4 import guest_api
        callbacks=[]
        with patch.object(guest_api,'validate_auth_guest_bundle'),patch.object(guest_api,'_auth_http',return_value=(200,b'{}',[],None)),patch.object(guest_api,'_auth_checks',return_value=[{'kind':'HTTP_STATUS_EQUALS','result':'FAIL'}]):
            guest_api.run_auth_flow(self._bundle(),expected_source=SOURCE,expected_adapter='php_mysql_v1',
                                    authenticated_callback=lambda cookie,email:callbacks.append(cookie))
        self.assertEqual(callbacks,[])
