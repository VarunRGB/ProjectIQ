"""Cross-stage and negative controls using Phase 4 report shapes."""
import json
import unittest
from projectiq_executioner.phase6.contracts import EvidenceError
from projectiq_executioner.phase6.pipeline import build
from projectiq_executioner.phase6.report import validate_report,markdown
from projectiq_executioner.tests.test_phase6_core import SRC,DIGEST,vm,run,quality


def stage_run(stage,status,checks=None,source=None):
    r={'schema_version':'0.1','phase':4,'stage':stage,'source':source or SRC,
      'adapter':'php_mysql_v1','catalog_sha256':DIGEST,'status':status,
      'execution':'PERFORMED_IN_DISPOSABLE_VM','cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
      'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
      'issue_code':None}
    if checks is not None:r['checks']=checks
    return r


def stage_vm(stage):
    keys={'reviewed_browser_login':'phase4dd_status','reviewed_browser_ui':'phase4dc_status',
      'reviewed_vm_database_postconditions':'phase4d3_status','reviewed_authenticated_crud':'phase4db_status',
      'reviewed_vm_dependent_api_workflow':'phase4da_status',
      'reviewed_database_fixture_seeding':'phase4d1_status'}
    v=vm();v.pop('phase4c_status',None)
    v[keys[stage]]='VALIDATED'
    return v

class CrossStageAcceptance(unittest.TestCase):
    def test_browser_login_pass(self):
        r=stage_run('reviewed_browser_login','LOGIN_PASSED',checks=[{'id':'login_form_ready','matched':True}])
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_login'))])
        self.assertEqual(result['status'],'DIAGNOSED');self.assertEqual(result['summary']['passes'],1)
    def test_browser_login_fail(self):
        r=stage_run('reviewed_browser_login','LOGIN_FAILED',checks=[{'id':'login_form_ready','matched':False}])
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_login'))])
        self.assertTrue(any(d['code']=='BROWSER_CHECK_FAILURE' for d in result['diagnoses']))
        self.assertEqual(result['root_cause_hypotheses'][0]['candidate_codes'][0],'AUTHENTICATION_CREDENTIAL_OR_STATE')
    def test_browser_login_inconclusive(self):
        r=stage_run('reviewed_browser_login','LOGIN_INCONCLUSIVE',checks=[{'id':'login_form_ready','matched':None}])
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_login'))])
        self.assertTrue(any(d['code']=='TEST_INCONCLUSIVE' for d in result['diagnoses']))
    def test_browser_false_pass_is_rejected(self):
        r=stage_run('reviewed_browser_login','LOGIN_PASSED',checks=[{'id':'login_form_ready','matched':False}])
        with self.assertRaises(EvidenceError):build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_login'))])
    def test_browser_ui_fail(self):
        r=stage_run('reviewed_browser_ui','BROWSER_FAILED',checks=[{'tag':'visible_label','matched':False}])
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_ui'))])
        self.assertTrue(any(d['code']=='BROWSER_CHECK_FAILURE' for d in result['diagnoses']))
    def test_browser_unvalidated_vm_not_counted(self):
        r=stage_run('reviewed_browser_ui','BROWSER_FAILED',checks=[{'tag':'visible_label','matched':False}])
        got=build(run_inputs=[(r,'RUN001',vm())])
        self.assertEqual(got['summary']['vm_verified_runs'],0)
        self.assertEqual(got['status'],'PARTIAL')
    def test_browser_duplicate_check_rejected(self):
        r=stage_run('reviewed_browser_ui','BROWSER_FAILED',checks=[{'tag':'abc','matched':False},{'tag':'abc','matched':False}])
        with self.assertRaises(EvidenceError):build(run_inputs=[(r,'RUN001',stage_vm('reviewed_browser_ui'))])
    def test_db_failed(self):
        r=stage_run('reviewed_vm_database_postconditions','POSTCONDITIONS_FAILED')
        r['results']=[{'id':'DB1','result':'FAIL','before_matches':0,'after_matches':0}]
        r['summary']={'total':1,'passed':0,'failed':1,'inconclusive':0}
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_vm_database_postconditions'))])
        self.assertTrue(any(d['code']=='DATABASE_POSTCONDITION_FAILURE' for d in result['diagnoses']))
        self.assertEqual(result['summary']['fails'],1)
    def test_db_auth_failure_not_db_defect(self):
        r=stage_run('reviewed_vm_database_postconditions','POSTCONDITIONS_FAILED')
        r['results']=[{'id':'DB1','result':'PASS'}]
        r['summary']={'total':1,'passed':1,'failed':0,'inconclusive':0}
        r['auth_summary']={'total':4,'passed':3,'failed':1,'inconclusive':0}
        result=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_vm_database_postconditions'))])
        self.assertFalse(any(d['code']=='DATABASE_POSTCONDITION_FAILURE' for d in result['diagnoses']))
        self.assertTrue(any(d['code']=='TEST_INCONCLUSIVE' for d in result['diagnoses']))
    def test_crud_failed_aggregate(self):
        r=stage_run('reviewed_authenticated_crud','CRUD_FAILED')
        got=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_authenticated_crud'))])
        self.assertTrue(any(d['code']=='TEST_ASSERTION_MISMATCH' for d in got['diagnoses']))
        self.assertTrue(all(d['student_defect']=='NOT_ESTABLISHED' for d in got['diagnoses']))
    def test_crud_unverified(self):
        r=stage_run('reviewed_authenticated_crud','CRUD_VM_NOT_VERIFIED');r['execution']='NOT_VERIFIED';r['cleanup']='NOT_VERIFIED'
        got=build(run_inputs=[(r,'RUN001',None)])
        self.assertEqual(got['status'],'SECURITY_HOLD')
    def test_dependent_workflow(self):
        r=stage_run('reviewed_vm_dependent_api_workflow','WORKFLOW_FAILED')
        got=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_vm_dependent_api_workflow'))])
        self.assertTrue(any(d['code']=='TEST_ASSERTION_MISMATCH' for d in got['diagnoses']))
    def test_isolation_stage_is_not_overinterpreted(self):
        r=stage_run('reviewed_vm_scenario_isolation','SCENARIO_ISOLATION_VERIFIED')
        r.pop('cleanup')
        got=build(run_inputs=[(r,'RUN001',None)])
        self.assertEqual(got['status'],'PARTIAL');self.assertTrue(any(d['code']=='UNSUPPORTED_STAGE' for d in got['diagnoses']))
    def test_cross_repo_aggregate_not_overinterpreted(self):
        r=stage_run('reviewed_cross_repository_functional_pilot','FUNCTIONAL_FAILURE_DETECTED')
        got=build(run_inputs=[(r,'RUN001',None)])
        self.assertTrue(any(d['code']=='UNSUPPORTED_STAGE' for d in got['diagnoses']))
        self.assertFalse(any(d['code']=='TEST_ASSERTION_MISMATCH' for d in got['diagnoses']))
    def test_startup_vm_bad_cleanup(self):
        v=vm(clean='NOT_VERIFIED')
        r=build(vm_standalone=(v,'VM'))
        self.assertEqual(r['status'],'SECURITY_HOLD')
    def test_security_hold_suppresses_hypotheses(self):
        r=stage_run('reviewed_browser_login','CLEANUP_FAILED')
        r['cleanup']='NOT_VERIFIED';r['execution']='NOT_VERIFIED'
        got=build(run_inputs=[(r,'RUN001',None)])
        self.assertEqual(got['root_cause_hypotheses'],[])
    def test_quality_with_static_context(self):
        q=quality();q['findings']=[{'path':'src/main.py','line':2,'code':'HIGH_CYCLOMATIC','severity':'MEDIUM'}]
        got=build(quality_input=(q,'QUALITY'))
        self.assertEqual(len(got['static_context']),1)
        self.assertIn('src/main.py',markdown(got))
    def test_quality_security_partial(self):
        q=quality();q['security']['truncated']=True
        got=build(quality_input=(q,'QUALITY'))
        self.assertTrue(any(d['code']=='PHASE5_SECURITY_SCAN_INCOMPLETE' for d in got['diagnoses']))
    def test_quality_secret_not_linked_to_401(self):
        r=run();r['results'][0]['http_status']=401
        got=build(run_inputs=[(r,'RUN001',vm())],quality_input=(quality(),'QUALITY'))
        self.assertFalse(any(d['code']=='MISSING_CONFIGURATION_EVIDENCE' for d in got['diagnoses']))
        self.assertTrue(all(d['student_defect']=='NOT_ESTABLISHED' for d in got['diagnoses']))
    def test_db_auth_failure_with_failed_row_not_blame_db(self):
        r=stage_run('reviewed_vm_database_postconditions','POSTCONDITIONS_FAILED')
        r['results']=[{'id':'DB1','result':'FAIL'}]
        r['summary']={'total':1,'passed':0,'failed':1,'inconclusive':0}
        r['auth_summary']={'total':4,'passed':2,'failed':2,'inconclusive':0}
        got=build(run_inputs=[(r,'RUN001',stage_vm('reviewed_vm_database_postconditions'))])
        self.assertFalse(any(d['code']=='DATABASE_POSTCONDITION_FAILURE' for d in got['diagnoses']))
    def test_phase4_attestation_tamper_rejected(self):
        r=run();r['security_attestation']='ATTESTED_FOR_ANY_STUDENT'
        with self.assertRaises(EvidenceError):build(run_inputs=[(r,'RUN001',vm())])
    def test_report_without_expected_attestation_rejected(self):
        r=run();r.pop('security_attestation')
        with self.assertRaises(EvidenceError):build(run_inputs=[(r,'RUN001',None)])
    def test_401_unproven_hypotheses(self):
        r=run();r['results'][0]['http_status']=401
        got=build(run_inputs=[(r,'RUN001',vm())])
        self.assertIn('REVIEWED_FIXTURE_MISMATCH',got['root_cause_hypotheses'][0]['candidate_codes'])
        self.assertFalse(got['root_cause_hypotheses'][0]['cause_established'])
    def test_500_unproven_hypotheses(self):
        got=build(run_inputs=[(run(),'RUN001',vm())])
        self.assertIn('APPLICATION_RUNTIME_ERROR',got['root_cause_hypotheses'][0]['candidate_codes'])
    def test_unknown_http_doesnt_invent(self):
        r=run();r['results'][0]['http_status']=418
        got=build(run_inputs=[(r,'RUN001',vm())])
        self.assertIn('REVIEWED_EXPECTATION_MISMATCH',got['root_cause_hypotheses'][0]['candidate_codes'])
    def test_status_dont_prove_security_attestation(self):
        got=build(run_inputs=[(run(),'RUN001',vm())])
        self.assertEqual(got['security_attestation'],'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
    def test_provenance_receipt(self):
        receipts=[{'id':'RUN001','role':'RUN','sha256':'b'*64,'bytes':123}]
        got=build(run_inputs=[(run(),'RUN001',vm())],receipts=receipts)
        validate_report(got);self.assertIn('b'*64,markdown(got))

if __name__=='__main__':unittest.main()
