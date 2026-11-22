"""4D-A: real response-to-request data passing, fail closed contracts and VM wiring."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4.guest_workflow import (
    DependentWorkflowError, validate_guest_workflow_bundle,
    validate_guest_workflow_report, run_dependent_workflow,
)

SOURCE = {'kind': 'github', 'repository': 'Fixture/SyntheticTaskApp', 'commit_sha': 'a'*40}


def bundle():
    return {
        'schema_version': '0.1', 'source': dict(SOURCE), 'adapter': 'php_mysql_v1',
        'workflow_catalog_sha256': 'b'*64, 'fixture_catalog_sha256': 'c'*64,
        'selector': {'field': 'title', 'value': 'Operator fixture task'},
        'capture_field': 'id', 'body_field': 'id', 'seed_id': 901,
        'steps': [
            {'role': 'DISCOVER', 'test_id': 'TC-'+'A'*16,
             'method': 'GET', 'path': '/logic/get_tasks.php'},
            {'role': 'DELETE_CAPTURED', 'test_id': 'TC-'+'B'*16,
             'method': 'POST', 'path': '/logic/delete_task.php'},
            {'role': 'VERIFY_ABSENT', 'test_id': 'TC-'+'A'*16,
             'method': 'GET', 'path': '/logic/get_tasks.php'},
        ],
        'expected_delete': {'status': 200, 'json_subset': {'success': True}},
    }


def client(script):
    calls = []
    def send(method, path, body, *, port):
        n = len(calls)
        calls.append((method, path, body, port))
        return script[n]
    return send, calls


class GuestValidation(unittest.TestCase):
    def test_valid_bounded_workflow(self):
        validate_guest_workflow_bundle(bundle(), expected_source=SOURCE,
                                       expected_adapter='php_mysql_v1')

    def test_wrong_source_or_adapter(self):
        for mutation in ('source', 'adapter'):
            b = bundle()
            if mutation == 'source': b['source']['commit_sha'] = 'd'*40
            else: b['adapter'] = 'node_fullstack_offline_v1'
            with self.subTest(mutation=mutation), self.assertRaises(DependentWorkflowError):
                validate_guest_workflow_bundle(b, expected_source=SOURCE,
                                               expected_adapter='php_mysql_v1')

    def test_unapproved_steps_urls_and_methods_rejected(self):
        for mutation in ('external', 'traversal', 'scheme', 'post_wrong', 'extra', 'role'):
            b = bundle()
            step = b['steps'][1]
            if mutation == 'external': step['path'] = '//remote'
            elif mutation == 'traversal': step['path'] = '/logic/../admin'
            elif mutation == 'scheme': step['path'] = 'https://example.org/'
            elif mutation == 'post_wrong': step['method'] = 'DELETE'
            elif mutation == 'extra': step['shell'] = 'rm -rf /'
            else: step['role'] = 'ARBITRARY_CODE'
            with self.subTest(mutation=mutation), self.assertRaises(DependentWorkflowError):
                validate_guest_workflow_bundle(b)

    def test_secret_selector_and_unbounded_id_rejected(self):
        for field, value in [('selector', {'field': 'password', 'value':'anything'}),
                             ('seed_id', 0), ('seed_id', True), ('body_field', 'admin')]:
            b=bundle();b[field]=value
            with self.subTest(field=field,value=str(value)), self.assertRaises(DependentWorkflowError):
                validate_guest_workflow_bundle(b)

    def test_both_get_steps_must_be_identical(self):
        b=bundle();b['steps'][2]['path'] = '/logic/get_user_tasks.php'
        with self.assertRaises(DependentWorkflowError): validate_guest_workflow_bundle(b)


class GuestExecution(unittest.TestCase):
    def _run(self, script):
        send,calls=client(script)
        report=run_dependent_workflow(bundle(), expected_source=SOURCE,
                                      expected_adapter='php_mysql_v1', http_client=send)
        validate_guest_workflow_report(bundle(), report)
        return report,calls

    def test_pass_captures_actual_returned_id_and_deletes_then_verifies_absent(self):
        rows=[{'id': 31, 'title':'Different task'},
              {'id': 901, 'title':'Operator fixture task'}]
        report,calls=self._run([(200,rows),(200,{'success':True}),(200,[rows[0]])])
        self.assertEqual(report['status'],'PASS')
        self.assertEqual([x[0] for x in calls],['GET','POST','GET'])
        self.assertEqual(calls[1][2], {'id':901})
        self.assertEqual(calls[0][1],calls[2][1])
        self.assertNotIn('Operator fixture task',json.dumps(report))
        self.assertNotIn('901',json.dumps(report))
        self.assertIsNone(report['issue_code'])

    def test_php_pdo_decimal_string_id_is_normalized_to_integer(self):
        report,calls=self._run([(200,[{'id':'901','title':'Operator fixture task'}]),
                                (200,{'success':True}),(200,[])])
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(calls[1][2],{'id':901})

    def test_ambiguous_pdo_id_or_boolean_never_mutates(self):
        for value in ('0901',True,'9e2',-901,'901.0'):
            with self.subTest(value=value):
                report,calls=self._run([(200,[{'id':value,'title':'Operator fixture task'}])])
                self.assertEqual(report['status'],'FAIL')
                self.assertEqual(len(calls),1)

    def test_missing_seed_never_mutates(self):
        report,calls=self._run([(200,[])])
        self.assertEqual((report['status'],len(calls)),('FAIL',1))
        self.assertEqual(report['observations']['discovery_matches'],0)

    def test_duplicate_task_never_mutates(self):
        row={'id':901,'title':'Operator fixture task'}
        report,calls=self._run([(200,[row,row])])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(len(calls),1)

    def test_bad_seed_id_never_mutates(self):
        report,calls=self._run([(200,[{'id':900,'title':'Operator fixture task'}])])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['observations']['captured_matches_seed'],False)
        self.assertEqual(len(calls),1)

    def test_wrong_http_status_never_mutates(self):
        report,calls=self._run([(404,None)])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(len(calls),1)

    def test_delete_false_must_not_claim_success_or_issue_verify(self):
        report,calls=self._run([(200,[{'id':901,'title':'Operator fixture task'}]),
                                (200,{'success':False})])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(len(calls),2)

    def test_after_delete_task_still_present_is_failure(self):
        row={'id':901,'title':'Operator fixture task'}
        report,calls=self._run([(200,[row]),(200,{'success':True}),(200,[row])])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(len(calls),3)

    def test_unknown_json_is_inconclusive(self):
        report,calls=self._run([(200,{'not':'array'})])
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertEqual(len(calls),1)

    def test_http_error_or_timeout_is_inconclusive(self):
        def send(*a,**kw):raise TimeoutError('internal sensitive response')
        report=run_dependent_workflow(bundle(),expected_source=SOURCE,
            expected_adapter='php_mysql_v1',http_client=send)
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertNotIn('internal sensitive response',json.dumps(report))

    def test_forged_guest_pass_rejected(self):
        b=bundle()
        report,_=self._run([(200,[{'id':901,'title':'Operator fixture task'}]),
                            (200,{'success':True}),(200,[])])
        mutations={
            'count':lambda x:x['observations'].__setitem__('remaining_matches',1),
            'observed_id':lambda x:x['observations'].__setitem__('captured_matches_seed',False),
            'source':lambda x:x['source'].__setitem__('commit_sha','0'*40),
            'digest':lambda x:x.__setitem__('workflow_catalog_sha256','0'*64),
            'extra':lambda x:x.__setitem__('raw_response',{'secret':'oops'}),
            'status':lambda x:x.__setitem__('status','FAIL'),
        }
        for name,alter in mutations.items():
            bad=deepcopy(report);alter(bad)
            with self.subTest(name=name),self.assertRaises(DependentWorkflowError):
                validate_guest_workflow_report(b,bad)


class OperatorReview(unittest.TestCase):
    def _inputs(self):
        from projectiq_executioner.phase4.oracle_contracts import digest
        from projectiq_executioner.tests.test_phase4cd1_db_fixtures import _inputs
        plan,candidate,fixtures,config=_inputs()
        plan['test_cases']=[
            {'id':'TC-'+'A'*16,'category':'HTTP_ROUTE','status':'NEEDS_ORACLE',
             'target':{'method':'GET','path':'/logic/get_tasks.php'}},
            {'id':'TC-'+'B'*16,'category':'HTTP_ROUTE','status':'NEEDS_ORACLE',
             'target':{'method':'POST','path':'/logic/delete_task.php'}},
        ]
        wf={'schema_version':'0.1','source':dict(plan['source']),
            'selector':{'field':'title','value':'ProjectIQ fixture task'},
            'capture_field':'id','body_field':'id',
            'steps':[{'role':'DISCOVER','test_id':'TC-'+'A'*16},
                     {'role':'DELETE_CAPTURED','test_id':'TC-'+'B'*16},
                     {'role':'VERIFY_ABSENT','test_id':'TC-'+'A'*16}],
            'expected_delete':{'status':200,'json_subset':{'success':True}}}
        config['approved_dependent_workflows']=[plan['source']['repository']+'@'+
            plan['source']['commit_sha']+'#'+digest(wf)]
        return plan,fixtures,wf,candidate,config

    def _compiled(self, modify=None):
        from projectiq_executioner.phase4.dependent_workflow_contracts import (
            compile_reviewed_dependent_workflow)
        p=self._inputs()
        if modify: modify(p)
        with patch('projectiq_executioner.phase4.dependent_workflow_contracts.validate_test_plan'), \
             patch('projectiq_executioner.phase4.db_fixture_contracts.validate_test_plan'):
            result=compile_reviewed_dependent_workflow(p[0],p[1],p[2],p[3],p[4])
        return result,p

    def test_reviewed_compilation_reuses_d1_fixture_and_real_phase3_routes(self):
        result,p=self._compiled()
        self.assertEqual(result['workflow']['steps'][1]['path'],'/logic/delete_task.php')
        self.assertEqual(result['workflow']['seed_id'],901)
        self.assertEqual(result['workflow']['fixture_catalog_sha256'],
                         result['fixture']['fixture_catalog_sha256'])

    def test_source_or_workflow_mutation_invalidates_approval(self):
        from projectiq_executioner.phase4.dependent_workflow_contracts import (
            DependentWorkflowReviewError)
        for which in ('expected','selector','route','approval','fixture'):
            p=self._inputs()
            if which=='expected':p[2]['expected_delete']['json_subset']['success']=False
            elif which=='selector':p[2]['selector']['value']='Different'
            elif which=='route':p[0]['test_cases'][1]['target']['method']='GET'
            elif which=='approval':p[4]['approved_dependent_workflows']=[]
            else:p[4]['approved_database_fixtures']=[]
            with self.subTest(which=which), \
                 patch('projectiq_executioner.phase4.dependent_workflow_contracts.validate_test_plan'), \
                 patch('projectiq_executioner.phase4.db_fixture_contracts.validate_test_plan'), \
                 self.assertRaises(DependentWorkflowReviewError):
                from projectiq_executioner.phase4.dependent_workflow_contracts import compile_reviewed_dependent_workflow
                compile_reviewed_dependent_workflow(p[0],p[1],p[2],p[3],p[4])

    def test_phase2_boundary_recompiles_and_rejects_tampered_guest_payload(self):
        from projectiq_executioner.phase4.dependent_workflow_contracts import (
            DependentWorkflowReviewError,validate_reviewed_dependent_workflow)
        compiled,p=self._compiled()
        with patch('projectiq_executioner.phase4.dependent_workflow_contracts.validate_test_plan'), \
             patch('projectiq_executioner.phase4.db_fixture_contracts.validate_test_plan'):
            result=validate_reviewed_dependent_workflow(compiled,p[3],p[4])
            self.assertEqual(result['workflow'],compiled['workflow'])
            bad=deepcopy(compiled)
            bad['workflow']['seed_id']=902
            with self.assertRaises(DependentWorkflowReviewError):
                validate_reviewed_dependent_workflow(bad,p[3],p[4])

    def test_preflight_must_not_launch_vm(self):
        from tempfile import TemporaryDirectory
        from projectiq_executioner.phase4.dependent_workflow_vm_run import preflight
        p=self._inputs()
        from projectiq_executioner.phase4.dependent_workflow_vm_run import (
            compile_reviewed_dependent_workflow)
        compiled,_=self._compiled()
        with TemporaryDirectory() as t, \
             patch('projectiq_executioner.phase4.dependent_workflow_vm_run.compile_reviewed_dependent_workflow',return_value=compiled):
            out=Path(t)/'preflight'
            report=preflight(plan=p[0],fixture_catalog=p[1],workflow_catalog=p[2],
                             candidate=p[3],config=p[4],output_dir=out)
            self.assertEqual(report['status'],'APPROVED_NOT_EXECUTED')
            self.assertTrue((out/'d4a_preflight.json').is_file())



class OfflineStage(unittest.TestCase):
    def test_uses_real_phase3_test_ids_and_never_auto_approves(self):
        from projectiq_executioner.phase4.dependent_workflow_stage import stage
        p,fixtures,_,candidate,config=OperatorReview()._inputs()
        wf=stage(plan=p, fixtures=fixtures,
                 read_path='/logic/get_tasks.php',delete_path='/logic/delete_task.php')
        self.assertEqual(wf['steps'][1]['test_id'],'TC-'+'B'*16)
        self.assertEqual(wf['selector']['value'],'ProjectIQ fixture task')
        self.assertNotIn('approved',wf)

    def test_missing_target_and_source_mismatch_fail(self):
        from projectiq_executioner.phase4.dependent_workflow_stage import stage
        p,fixtures,_,candidate,config=OperatorReview()._inputs()
        with self.assertRaises(ValueError):
            stage(plan=p,fixtures=fixtures,read_path='/nope',
                  delete_path='/logic/delete_task.php')
        bad=deepcopy(fixtures);bad['source']['commit_sha']='b'*40
        with self.assertRaises(ValueError):
            stage(plan=p,fixtures=bad,read_path='/logic/get_tasks.php',
                  delete_path='/logic/delete_task.php')


class WorkflowHostReport(unittest.TestCase):
    def _run(self,*,state='PASS',cleanup=True):
        from tempfile import TemporaryDirectory
        from projectiq_executioner.phase4.dependent_workflow_vm_run import run
        p,fixtures,wf,candidate,config=OperatorReview()._inputs()
        compiled,_=OperatorReview()._compiled()
        send,calls=client([
            (200,[{'id':901,'title':'ProjectIQ fixture task'}]),
            (200,{'success': True}),
            (200,[])])
        response=run_dependent_workflow(compiled['workflow'],
            expected_source=candidate['source'],expected_adapter='php_mysql_v1',
            http_client=send)
        if state=='FAIL':
            response=deepcopy(response)
            response['status']='FAIL'
            response['issue_code']='CAPTURED_ID_STILL_PRESENT'
            response['observations']['remaining_matches']=1
        vm={'status':'STARTUP_VERIFIED','execution':'PERFORMED_IN_DISPOSABLE_VM',
            'cleanup':('VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' if cleanup else 'NOT_VERIFIED'),
            'backend':'QEMU_DISPOSABLE_QCOW2_VM',
            'source':candidate['source'], 'adapter':candidate['adapter'],
            'managed_runtime':'SIGNED_LEASED_WORKSPACE_REMOVED',
            'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
            'phase4da_status':'VALIDATED',
            'phase4da_fixture_report':{
                'schema_version':'0.1','status':'FIXTURES_SEEDED',
                'fixture_catalog_sha256':compiled['fixture']['fixture_catalog_sha256'],
                'table_count':1,'seeded_rows':1,
                'database_scope':'DISPOSABLE_GUEST_DATABASE_ONLY'},
            'phase4da_workflow_report':response}
        with TemporaryDirectory() as t, \
             patch('projectiq_executioner.phase4.dependent_workflow_vm_run.compile_reviewed_dependent_workflow',return_value=compiled), \
             patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',return_value=vm):
            out=Path(t)/'vm'
            report=run(plan=p,fixture_catalog=fixtures,workflow_catalog=wf,
                       candidate=candidate,config=config,output_dir=out)
            self.assertEqual(json.loads((out/'d4a_run.json').read_text('utf8')),report)
            return report

    def test_valid_vm_and_guest_observation_promotes_pass(self):
        self.assertEqual(self._run()['status'],'WORKFLOW_PASSED')

    def test_valid_functional_failure_not_startup_failure(self):
        self.assertEqual(self._run(state='FAIL')['status'],'WORKFLOW_FAILED')

    def test_cleanup_failure_takes_precedence(self):
        self.assertEqual(self._run(cleanup=False)['status'],'CLEANUP_FAILED')



class IntegrationBoundary(unittest.TestCase):
    def test_qemu_copy_allowlist_has_new_guest_helper(self):
        from projectiq_executioner.phase2.vm_worker.qemu import SSHControl
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import Mock
        with TemporaryDirectory() as folder:
            path=Path(folder)/'guest_workflow.py';path.write_bytes(b'trusted')
            ssh=SimpleNamespace(ssh=Mock(return_value=SimpleNamespace(returncode=0)))
            SSHControl.copy(ssh,path,'guest_workflow.py')
            ssh.ssh.assert_called_once()
            with self.assertRaises(Exception):
                SSHControl.copy(ssh,path,'arbitrary_script.sh')

    def test_vm_boundary_recompiles_new_review_and_stays_mutually_exclusive(self):
        import inspect
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        from projectiq_executioner.phase2.vm_worker import guest_agent
        body=inspect.getsource(execute_vm_candidate)
        guest=inspect.getsource(guest_agent)
        for marker in (
            'validate_reviewed_dependent_workflow',
            'workflow_bundle', 'phase4da_workflow_bundle',
            'phase4da_status', 'phase4da_fixture_report', 'phase4da_workflow_report',
            "'guest_workflow.py'", "'guest_db_fixture.py'",
        ):
            self.assertIn(marker,body)
        self.assertIn('_run_phase4da_on_live_app',guest)
        self.assertIn('phase4da_workflow_report',guest)


if __name__=='__main__':unittest.main()
