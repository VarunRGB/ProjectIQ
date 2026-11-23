"""Phase 6 offline adversarial and cross-stage acceptance tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from projectiq_executioner.phase6.contracts import EvidenceError, read_json, source_key, stable_hash
from projectiq_executioner.phase6.evidence import normalize_plan, normalize_quality, normalize_run, normalize_vm
from projectiq_executioner.phase6.pipeline import build
from projectiq_executioner.phase6.report import validate_report, markdown
from projectiq_executioner.phase6.dependencies import read_edges
from projectiq_executioner.phase6.reruns import analyze_reruns

SHA='a'*40;DIGEST='b'*64
SRC={'kind':'github','repository':'example/sample','commit_sha':SHA}

def vm(*,clean='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',source=None,status='STARTUP_VERIFIED'):
    return {'source':source or SRC,'adapter':'php_mysql_v1','status':status,
     'execution':'PERFORMED_IN_DISPOSABLE_VM','cleanup':clean,
     'backend':'QEMU_DISPOSABLE_QCOW2_VM','security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
     'phase4c_status':'VALIDATED'}

def run(*,status='TESTS_FAILED',result='FAIL',stage='reviewed_vm_api_testing',src=None,
        test='TC-AAAAAAAAAAAAAAAA',digest=DIGEST):
    rows=[{'test_id':test,'result':result,'http_status':500 if result=='FAIL' else 200}]
    summary={'total':1,'passed':int(result=='PASS'),'failed':int(result=='FAIL'),'inconclusive':int(result=='INCONCLUSIVE')}
    return {'schema_version':'0.1','phase':4,'stage':stage,'source':src or SRC,
      'adapter':'php_mysql_v1','status':status,'execution':'PERFORMED_IN_DISPOSABLE_VM',
      'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED','security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
      'results':rows,'summary':summary,'issue_code':None,'catalog_sha256':digest}

def plan(*,mode='READY_FOR_PHASE4'):
    ready = mode == 'READY_FOR_PHASE4'
    inventory={k:[] for k in ('page_sources','existing_tests','program_entrypoints',
             'database_schemas','features','proposals','source_contracts')}
    return {'schema_version':'1.1','phase':3,'stage':'deterministic_test_generation',
      'status':'GENERATED','source':SRC,
      'generator':{'name':'projectiq_phase3_rules','version':'0.2','llm_used':False,'student_code_executed':False},
      'execution_context':{'adapter':'php_mysql_v1', 'operator_approval':'NOT_EVALUATED',
         'source_snapshot_verified':True,'phase2_candidate_status':'CANDIDATE_NEEDS_VM_VERIFICATION'},
      'inventory':inventory,'issues':[],
      'summary':{'total':1,'ready_for_phase4':int(ready),'needs_oracle':int(not ready),
        'unsupported_by_runner':0,'categories':{'HTTP_READINESS':1},
        'inventory_counts':{key:0 for key in sorted(inventory)}},
      'test_cases':[{'id':'TC-AAAAAAAAAAAAAAAA','title':'Readiness endpoint',
        'category':'HTTP_READINESS','status':mode,
        'target':{'method':'GET','path':'/public/login.html','label':None},
        'evidence':[{'source':'phase2_candidate','path':'probe_path',
                     'line':None,'reason':'Reviewed startup probe'}],
        'required_adapter':'php_mysql_v1','execution':'NOT_PERFORMED',
        'blockers':[] if ready else ['ORACLE_NOT_ESTABLISHED'],
        'oracle':{'status':'GROUNDED' if ready else 'NOT_ESTABLISHED',
                  'basis':'PHASE2_ADAPTER_READINESS_CONTRACT' if ready else None},
        'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':200}] if ready else []}]}

def quality(*,alerts=1):
    return {'schema_version':'0.3','stage':'phase5_code_quality',
     'source':{'kind':'github_pinned','repository':SRC['repository'],'commit_sha':SHA},
     'quality':{'status':'PROVISIONAL'},
     'security':{'findings':[{'code':'TRACKED_SENSITIVE_FILE','path':'config/.env.production','line':None,'confidence':'HIGH'} for _ in range(alerts)]},
     'findings':[{'code':'HIGH_CYCLOMATIC'}]}

def analyze(**kw):
    return build(**kw)

class EvidenceValidation(unittest.TestCase):
    def test_good_source(self):self.assertEqual(source_key(SRC),('github','example/sample',SHA))
    def test_missing_sha(self):
        with self.assertRaises(EvidenceError):source_key({'kind':'github','repository':'a/b'})
    def test_local_sha_forbidden(self):
        with self.assertRaises(EvidenceError):source_key({'kind':'local','repository':'x','commit_sha':SHA})
    def test_plan_ready(self):self.assertTrue(normalize_plan(plan())['cases']['TC-AAAAAAAAAAAAAAAA']['grounded'])
    def test_plan_unready(self):self.assertFalse(normalize_plan(plan(mode='NEEDS_ORACLE'))['cases']['TC-AAAAAAAAAAAAAAAA']['grounded'])
    def test_plan_forged(self):
        p=plan();p['test_cases'][0]['assertions']=[]
        with self.assertRaises(EvidenceError):normalize_plan(p)
    def test_plan_duplicate(self):
        p=plan();p['test_cases']*=2
        with self.assertRaises(EvidenceError):normalize_plan(p)
    def test_result_valid(self):self.assertEqual(normalize_run(run(),label='RUN001')['rows'][0]['outcome'],'FAIL')
    def test_result_mismatch(self):
        r=run();r['summary']['passed']=1
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_result_forged_pass(self):
        r=run(status='TESTS_PASSED',result='FAIL')
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_result_duplicate_ids(self):
        r=run();r['results']*=2;r['summary']['total']=2;r['summary']['failed']=2
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_result_http_bad(self):
        r=run();r['results'][0]['http_status']='500'
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_result_no_actual_execution(self):
        r=run();r['execution']='NOT_PERFORMED'
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_stage_unknown_not_converted(self):
        r=run(stage='unknown_stage')
        self.assertFalse(normalize_run(r,label='RUN001')['supported'])
    def test_stage_status_mismatch(self):
        r=run(status='LOGIN_PASSED')
        with self.assertRaises(EvidenceError):normalize_run(r,label='RUN001')
    def test_vm_source_mismatch(self):
        other=vm(source={'kind':'github','repository':'other/one','commit_sha':SHA})
        with self.assertRaises(EvidenceError):normalize_run(run(),label='RUN001',vm=normalize_vm(other))
    def test_vm_cleanup_mismatch(self):
        with self.assertRaises(EvidenceError):normalize_run(run(),label='RUN001',vm=normalize_vm(vm(clean='NOT_VERIFIED')))
    def test_normal_vm(self):self.assertTrue(normalize_vm(vm())['ready'])
    def test_missing_vm_backend(self):
        v=vm();v['backend']='OTHER'
        self.assertFalse(normalize_vm(v)['ready'])
    def test_phase5_source_translation(self):self.assertEqual(normalize_quality(quality())['source'],source_key(SRC))
    def test_phase5_secret_unsafe_path_redacted(self):
        q=quality();q['security']['findings'][0]['path']='../../etc/passwd'
        self.assertFalse(normalize_quality(q)['security_alerts'])
    def test_phase5_invalid_stage(self):
        q=quality();q['stage']='not-phase5'
        with self.assertRaises(EvidenceError):normalize_quality(q)
    def test_reject_duplicates_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'x.json';f.write_text('{"source":1,"source":2}')
            with self.assertRaises(EvidenceError):read_json(f)
    def test_reject_nonfinite(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'x.json';f.write_text('{"x":NaN}')
            with self.assertRaises(EvidenceError):read_json(f)
    def test_read_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'x.json';f.write_text('{"x":1}')
            v,h,n=read_json(f);self.assertEqual(v['x'],1);self.assertEqual(n,7);self.assertEqual(len(h),64)

class DiagnosisAcceptance(unittest.TestCase):
    def good(self,**kw):
        return build(plans=[(plan(),'PLAN')],run_inputs=[(run(**kw),'RUN001',vm())])
    def test_application_mismatch_evidence(self):
        r=self.good();self.assertEqual(r['status'],'DIAGNOSED')
        self.assertTrue(any(d['code']=='TEST_ASSERTION_MISMATCH' for d in r['diagnoses']))
        self.assertTrue(all(d['student_defect']=='NOT_ESTABLISHED' for d in r['diagnoses']))
    def test_valid_report_contract(self):validate_report(self.good())
    def test_no_code_execution(self):
        r=self.good();self.assertEqual(r['execution'],'NOT_PERFORMED');self.assertEqual(r['vm_launches'],0)
    def test_vm_count(self):self.assertEqual(self.good()['summary']['vm_verified_runs'],1)
    def test_fail_event_grounded(self):self.assertEqual(self.good()['events'][0]['oracle'],'GROUNDED')
    def test_without_vm_not_high_confidence(self):
        r=build(run_inputs=[(run(),'RUN001',None)])
        self.assertEqual(r['status'],'PARTIAL')
        self.assertTrue(all(d['confidence']!='HIGH' for d in r['diagnoses'] if d['code']=='TEST_ASSERTION_MISMATCH'))
    def test_no_false_student_fault(self):
        r=self.good();self.assertTrue(all(e['student_defect']=='NOT_ESTABLISHED' for e in r['events']))
    def test_no_source_crossover(self):
        r2=run(src={'kind':'github','repository':'other/other','commit_sha':SHA})
        with self.assertRaises(EvidenceError):build(plans=[(plan(),'PLAN')],run_inputs=[(r2,'RUN001',None)])
    def test_cleanup_hold(self):
        r=run();r['status']='CLEANUP_FAILED';r['cleanup']='NOT_VERIFIED'
        result=build(run_inputs=[(r,'RUN001',vm(clean='NOT_VERIFIED'))])
        self.assertEqual(result['status'],'SECURITY_HOLD')
        self.assertEqual(result['diagnoses'][0]['code'],'VM_CLEANUP_UNVERIFIED')
    def test_missing_oracle(self):
        r=build(plans=[(plan(mode='NEEDS_ORACLE'),'PLAN')])
        self.assertTrue(any(x['code']=='INVALID_TEST_ORACLE' for x in r['diagnoses']))
    def test_only_plan(self):
        r=build(plans=[(plan(),'PLAN')])
        self.assertEqual(r['status'],'PARTIAL')
        self.assertTrue(any(x['code']=='NO_EXECUTION_EVIDENCE' for x in r['diagnoses']))
    def test_only_vm_failed(self):
        v=vm(status='DENIED')
        r=build(vm_standalone=(v,'VM'))
        self.assertTrue(any(x['code']=='VM_EXECUTION_UNVERIFIED' for x in r['diagnoses']))
    def test_security_independent(self):
        r=build(quality_input=(quality(),'QUALITY'))
        self.assertTrue(any(x['code']=='PHASE5_SECRET_INDICATOR' for x in r['diagnoses']))
        self.assertFalse(any(x['code']=='TEST_ASSERTION_MISMATCH' for x in r['diagnoses']))
    def test_security_not_causal(self):
        r=build(quality_input=(quality(),'QUALITY'))
        self.assertTrue(all(x['causal_status']=='UNPROVEN' for x in r['diagnoses']))
    def test_no_raw_secret_leak(self):
        q=quality();q['security']['findings'][0]['message']='ACTUAL-SUPER-SECRET'
        r=build(quality_input=(q,'QUALITY'))
        self.assertNotIn('ACTUAL-SUPER-SECRET',json.dumps(r)+markdown(r))
    def test_report_md(self):self.assertIn('Student defect attribution',markdown(self.good()))
    def test_not_grading(self):
        r=self.good();self.assertNotIn('score',r)
    def test_standalone_source(self):self.assertEqual(build(vm_standalone=(vm(),'VM'))['source']['commit_sha'],SHA)
    def test_summary(self):
        r=self.good();self.assertEqual(r['summary']['fails'],1)
    def test_no_content_echo(self):
        rr=run();rr['untrusted_log']='SOME_SECRET_VERY_LONG'
        r=build(run_inputs=[(rr,'RUN001',vm())]);self.assertNotIn('SOME_SECRET_VERY_LONG',json.dumps(r))
    def test_tamper_title_rejected(self):
        r=self.good();r['diagnoses'][0]['title']='Raw secret from user file'
        with self.assertRaises(EvidenceError):validate_report(r)
    def test_tamper_student_attribution(self):
        r=self.good();r['diagnoses'][0]['student_defect']='CONFIRMED'
        with self.assertRaises(EvidenceError):validate_report(r)

class DependencyAndRerun(unittest.TestCase):
    def edges(self):
        return {'schema_version':'0.1','source':SRC,'edges':[{'test_id':'B','requires':'A'}]}
    def test_valid_edges(self):self.assertEqual(read_edges(self.edges(),expected_source=source_key(SRC),allowed_ids={'A','B'}),{'B':{'A'}})
    def test_cycle_rejected(self):
        e=self.edges();e['edges'].append({'test_id':'A','requires':'B'})
        with self.assertRaises(EvidenceError):read_edges(e,expected_source=source_key(SRC),allowed_ids={'A','B'})
    def test_unknown_id_rejected(self):
        with self.assertRaises(EvidenceError):read_edges(self.edges(),expected_source=source_key(SRC),allowed_ids={'B'})
    def test_dependency_only_when_blocked(self):
        r=run(test='A');r['results'].append({'test_id':'B','result':'BLOCKED','http_status':None});r['summary']['total']=2
        doc=build(run_inputs=[(r,'RUN001',vm())],dependency_input=(self.edges(),'DEPENDENCIES'))
        self.assertEqual(len(doc['dependency_groups']),1)
        self.assertTrue(any(x['code']=='DEPENDENT_TEST_BLOCKED' for x in doc['diagnoses']))
    def test_no_group_if_dependent_failed(self):
        r=run(test='A');r['results'].append({'test_id':'B','result':'FAIL','http_status':500});r['summary']['total']=2;r['summary']['failed']=2
        doc=build(run_inputs=[(r,'RUN001',vm())],dependency_input=(self.edges(),'DEPENDENCIES'))
        self.assertEqual(len(doc['dependency_groups']),0)
    def test_two_runs_different_outcomes(self):
        a=run();b=run(status='TESTS_PASSED',result='PASS')
        got=build(run_inputs=[(a,'RUN001',vm()),(b,'RUN002',vm())])
        self.assertEqual(got['summary']['comparisons'],1)
        self.assertTrue(any(x['code']=='RERUN_OUTCOME_INCONSISTENT' for x in got['diagnoses']))
    def test_changed_oracle_not_comparable(self):
        a=run();b=run(status='TESTS_PASSED',result='PASS',digest='f'*64)
        got=build(run_inputs=[(a,'RUN001',vm()),(b,'RUN002',vm())])
        self.assertEqual(got['summary']['comparisons'],0)
    def test_without_vm_not_comparable(self):
        a=run();b=run(status='TESTS_PASSED',result='PASS')
        got=build(run_inputs=[(a,'RUN001',None),(b,'RUN002',None)])
        self.assertEqual(got['summary']['comparisons'],0)
    def test_identical_digests_not_reruns(self):
        a=run()
        got=build(run_inputs=[(a,'RUN001',vm()),(copy.deepcopy(a),'RUN002',vm())])
        self.assertEqual(got['summary']['comparisons'],0)
    def test_consistent_runs(self):
        a=run();b=run();b['summary']['extra']='harmless'
        got=build(run_inputs=[(a,'RUN001',vm()),(b,'RUN002',vm())])
        self.assertEqual(got['summary']['comparisons'],1)
        self.assertFalse(any(x['code']=='RERUN_OUTCOME_INCONSISTENT' for x in got['diagnoses']))

if __name__=='__main__':unittest.main()
