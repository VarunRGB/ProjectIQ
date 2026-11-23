"""Phase 7 offline acceptance: actual Phase 6 reports + adversarial benchmark labels."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase6.pipeline import build
from projectiq_executioner.phase6.report import validate_report
from projectiq_executioner.phase7.contracts import (BenchmarkError,DEFAULT_POLICY,
    load_json, validate_dataset, relpath, safe_path)
from projectiq_executioner.phase7.engine import (run, evaluate_case, spearman, ranks)
from projectiq_executioner.phase7.__main__ import main
from projectiq_executioner.phase7.report import markdown

SHA_A='a'*40
SHA_B='b'*40

def source(repo='example/sample', sha=SHA_A):
    return {'kind':'github','repository':repo,'commit_sha':sha}


def evidence(outcome='FAIL', http=500, repo='example/sample', sha=SHA_A,
             verified=True, cleanup=None, stage='reviewed_vm_api_testing'):
    src=source(repo,sha)
    vm={'source':src,'adapter':'php_mysql_v1','status':'STARTUP_VERIFIED',
        'execution':'PERFORMED_IN_DISPOSABLE_VM',
        'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
        'backend':'QEMU_DISPOSABLE_QCOW2_VM',
        'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'phase4c_status':'VALIDATED'}
    if cleanup:vm['cleanup']=cleanup
    run_report={'schema_version':'0.1','phase':4,'stage':stage,'source':src,'adapter':'php_mysql_v1',
        'status':{'PASS':'TESTS_PASSED','FAIL':'TESTS_FAILED','INCONCLUSIVE':'TESTS_INCONCLUSIVE'}[outcome],
        'execution':'PERFORMED_IN_DISPOSABLE_VM','cleanup':vm['cleanup'],
        'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'results':[{'test_id':'TC-AAAAAAAAAAAAAAAA','result':outcome,'http_status':http}],
        'summary':{'total':1,'passed':int(outcome=='PASS'),'failed':int(outcome=='FAIL'),
                   'inconclusive':int(outcome=='INCONCLUSIVE')},'issue_code':None,
        'catalog_sha256':'b'*64}
    if not verified:return build(run_inputs=[(run_report,'RUN001',None)])
    return build(run_inputs=[(run_report,'RUN001',vm)])


def manifest_for(case):
    return {'schema_version':'0.1','stage':'phase7_benchmark_dataset',
            'benchmark_id':'PHASE7_UNIT', 'cases':[case],
            'policy':{**DEFAULT_POLICY,'min_cases':1,'min_repositories':1,'min_cohorts':1,
                'min_application_defects':0,'min_event_labels':1,
                'min_known_causes':0,'min_calibration_pairs':0}}


def build_case(folder, *, id='CASE1', outcome='FAIL', http=500, cohort=None,
               expected=None, repo='example/sample', expected_code=None, known_cause=None,
               verified=True, report=None):
    report=report if report is not None else evidence(outcome,http,repo,verified=verified)
    validate_report(report)
    p=folder/(id+'.json')
    raw=json.dumps(report,sort_keys=True).encode()
    p.write_bytes(raw)
    if expected_code is None:
        codes=sorted({d['code'] for d in report['diagnoses']})
    else:codes=expected_code
    return {'id':id,'cohort':cohort or ('APPLICATION_DEFECT' if outcome=='FAIL' else 'CLEAN'),
            'source':source(repo),
            'phase6':{'path':p.name,'sha256':hashlib.sha256(raw).hexdigest()},
            'ground_truth':{'reviewed':True,'reviewer_id':'REVIEWER1',
                'reviewed_at':'2026-11-24','basis':'CONTROLLED_INJECTION',
                'expected_report_status':report['status'],
                'expected_codes':codes,
                'expected_events':[{'run':'RUN001','test_id':'TC-AAAAAAAAAAAAAAAA',
                    'outcome':expected or outcome,'known_cause':known_cause}],
                'expected_comparisons':[],'maintainability_rating':None}}


class BenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def make(self,**kwargs):
        c=build_case(self.root,**kwargs)
        m=manifest_for(c)
        return c,m

    def test_real_phase6_generated_failure(self):
        c,m=self.make(known_cause='APPLICATION_RUNTIME_ERROR')
        r=run(m,self.root)
        self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['metrics']['event_accuracy'],1.0)
        self.assertEqual(r['metrics']['root_cause_top3_hit_rate'],1.0)
        self.assertEqual(r['metrics']['diagnostic_code_recall'],1.0)
        self.assertEqual(r['metrics']['false_student_attributions'],0)

    def test_real_phase6_generated_clean(self):
        _,m=self.make(outcome='PASS',http=200,cohort='CLEAN')
        r=run(m,self.root)
        self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['results'][0]['observed_codes'],[])
        self.assertEqual(r['metrics']['false_failures'],0)

    def test_inconclusive_not_a_failure(self):
        _,m=self.make(outcome='INCONCLUSIVE',cohort='INCONCLUSIVE')
        self.assertEqual(run(m,self.root)['metrics']['false_failures'],0)

    def test_false_pass_detection(self):
        _,m=self.make(outcome='PASS',http=200,expected='FAIL',cohort='APPLICATION_DEFECT')
        r=run(m,self.root)
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['metrics']['false_passes'],1)

    def test_false_failure_detection(self):
        _,m=self.make(outcome='FAIL',expected='PASS',cohort='CLEAN')
        self.assertEqual(run(m,self.root)['metrics']['false_failures'],1)

    def test_missing_events_not_counted_correct(self):
        _,m=self.make()
        m['cases'][0]['ground_truth']['expected_events'][0]['test_id']='TC-NOTSEEN'
        r=run(m,self.root)
        self.assertEqual(r['metrics']['missing_expected_events'],1)
        self.assertEqual(r['metrics']['event_accuracy'],0)

    def test_missing_diagnosis(self):
        _,m=self.make(outcome='PASS',http=200)
        m['cases'][0]['ground_truth']['expected_codes']=['TEST_ASSERTION_MISMATCH']
        r=run(m,self.root)
        self.assertEqual(r['metrics']['missing_diagnostic_codes'],1)
        self.assertEqual(r['status'],'LIMITED')

    def test_extra_diagnosis(self):
        _,m=self.make(expected_code=[])
        r=run(m,self.root)
        self.assertEqual(r['metrics']['unexpected_diagnostic_codes'],1)
        self.assertEqual(r['status'],'LIMITED')

    def test_unknown_cause_is_a_miss(self):
        _,m=self.make(known_cause='SOMETHING_NOT_IN_PHASE6')
        r=run(m,self.root)
        self.assertEqual(r['metrics']['root_cause_top3_hit_rate'],0)

    def test_bounded_hypothesis_count(self):
        c,m=self.make()
        r=run(m,self.root)
        self.assertEqual(r['metrics']['root_cause_labeled_failures'],0)
        self.assertIsNone(r['metrics']['root_cause_top3_hit_rate'])

    def test_root_cause_missing_hypothesis(self):
        _,m=self.make(outcome='PASS',http=200,known_cause=None)
        m['cases'][0]['ground_truth']['expected_events'][0]['outcome']='FAIL'
        m['cases'][0]['ground_truth']['expected_events'][0]['known_cause']='APPLICATION_RUNTIME_ERROR'
        self.assertEqual(run(m,self.root)['metrics']['root_cause_top3_hit_rate'],0)

    def test_validated_report_schema(self):
        _,m=self.make()
        self.assertEqual(run(m,self.root)['results'][0]['result'],'EVALUATED')

    def test_bad_sha_rejected(self):
        _,m=self.make()
        m['cases'][0]['phase6']['sha256']='0'*64
        r=run(m,self.root)
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['results'][0]['error'],'ARTIFACT_SHA_MISMATCH')

    def test_source_mismatch_rejected(self):
        _,m=self.make()
        m['cases'][0]['source']=source('other/project')
        self.assertEqual(run(m,self.root)['results'][0]['error'],'CROSS_ARTIFACT_SOURCE_MISMATCH')

    def test_phase6_tampered_report_rejected(self):
        c,m=self.make()
        p=self.root/c['phase6']['path']
        doc=json.loads(p.read_text())
        doc['summary']['diagnoses']=999
        b=json.dumps(doc).encode();p.write_bytes(b)
        c['phase6']['sha256']=hashlib.sha256(b).hexdigest()
        self.assertEqual(run(m,self.root)['results'][0]['error'],'INVALID_PHASE6_REPORT')

    def test_unreviewed_ground_truth_blocked(self):
        _,m=self.make()
        m['cases'][0]['ground_truth']['reviewed']=False
        with self.assertRaises(BenchmarkError) as x:validate_dataset(m)
        self.assertEqual(x.exception.code,'GROUND_TRUTH_NOT_REVIEWED')

    def test_duplicate_ground_truth_case_ids(self):
        _,m=self.make()
        m['cases'].append(deepcopy(m['cases'][0]))
        with self.assertRaises(BenchmarkError):validate_dataset(m)

    def test_duplicate_artifact_rejected(self):
        _,m=self.make()
        other=deepcopy(m['cases'][0]);other['id']='CASE2'
        m['cases'].append(other)
        r=run(m,self.root)
        self.assertEqual(r['metrics']['rejected'],1)
        self.assertEqual(r['results'][1]['error'],'DUPLICATE_BENCHMARK_ARTIFACT')

    def test_report_paths_cannot_traverse(self):
        _,m=self.make()
        for candidate in ('../outside.json','/tmp/source.json','foo\\bar.json','C:/secret.json','foo//bar'):
            with self.subTest(candidate=candidate):
                changed=deepcopy(m);changed['cases'][0]['phase6']['path']=candidate
                with self.assertRaises(BenchmarkError):validate_dataset(changed)

    def test_data_symlink_rejected(self):
        c,m=self.make()
        p=self.root/'link.json'
        try:p.symlink_to(self.root/c['phase6']['path'])
        except (OSError,NotImplementedError):self.skipTest('Symlink unavailable')
        m['cases'][0]['phase6']['path']='link.json'
        self.assertEqual(run(m,self.root)['results'][0]['error'],'UNSAFE_DATASET_PATH')

    def test_receipt_sha_match(self):
        c,m=self.make()
        p=self.root/c['phase6']['path']
        d=json.loads(p.read_text())
        inp=self.root/'input.json';inp.write_text('{"ok":true}')
        d['evidence_receipts']=[{'id':'RUN001','role':'RUN',
                                 'sha256':hashlib.sha256(inp.read_bytes()).hexdigest(),
                                 'bytes':inp.stat().st_size}]
        raw=json.dumps(d).encode();p.write_bytes(raw)
        c['phase6']['sha256']=hashlib.sha256(raw).hexdigest()
        c['receipt_files']=[{'id':'RUN001','path':'input.json'}]
        self.assertEqual(run(m,self.root)['metrics']['input_receipts_crosschecked'],1)

    def test_receipt_mismatch_rejected(self):
        c,m=self.make()
        inp=self.root/'input.json';inp.write_text('{"ok":true}')
        c['receipt_files']=[{'id':'RUN001','path':'input.json'}]
        self.assertEqual(run(m,self.root)['results'][0]['error'],'EVIDENCE_RECEIPT_MISSING')

    def test_report_determinism(self):
        _,m=self.make()
        self.assertEqual(run(m,self.root),run(m,self.root))

    def test_markdown_renders_without_raw_path(self):
        _,m=self.make()
        t=markdown(run(m,self.root))
        self.assertIn('Phase 7 Benchmark',t)
        self.assertNotIn(str(self.root),t)

    def test_cli_roundtrip(self):
        _,m=self.make()
        f=self.root/'manifest.json';f.write_text(json.dumps(m))
        out=self.root/'fresh_output'
        self.assertEqual(main(['--manifest',str(f),'--out',str(out),'--check-determinism']),0)
        d=json.loads((out/'phase7_benchmark.json').read_text())
        self.assertEqual(d['deterministic_replay'],'MATCHED')
        self.assertEqual(d['vm_launches'],0)
        self.assertEqual(main(['--manifest',str(f),'--out',str(out)]),2)

    def test_cli_refuses_existing_output_without_writing(self):
        _,m=self.make()
        f=self.root/'manifest.json';f.write_text(json.dumps(m))
        out=self.root/'exists';out.mkdir()
        (out/'keep.txt').write_text('unchanged')
        self.assertEqual(main(['--manifest',str(f),'--out',str(out)]),2)
        self.assertEqual((out/'keep.txt').read_text(),'unchanged')

    def test_quality_calibration_math(self):
        self.assertEqual(spearman([1,2,3],[3,2,1]),-1)
        self.assertEqual(spearman([1,2,3],[1,2,3]),1)
        self.assertIsNone(spearman([3,3,3],[3,2,1]))
        self.assertEqual(ranks([3,1,1]),[3.0,1.5,1.5])

    def test_quality_calibration_excludes_scoreless(self):
        from projectiq_executioner.phase7.engine import _aggregate
        r={'id':'A','result':'EVALUATED','cohort':'STATIC_ONLY',
           'source':source(),'phase5_sha256':'a'*64,
           'expected_codes':[],'observed_codes':[], 'missing_codes':[],'unexpected_codes':[],
           'events':[],'comparisons':[],'known_causes':0,'top3_hits':0,'status_correct':True,
           'false_attributions':0,'verified_input_receipts':0,'total_report_receipts':0,
           'quality':{'status':'INSUFFICIENT_COVERAGE','score':None,'reviewer_maintainability':72}}
        status,m=_aggregate([r],{**DEFAULT_POLICY,'min_cases':0,'min_repositories':0,
            'min_cohorts':0,'min_application_defects':0,'min_event_labels':0,
            'min_known_causes':0,'min_calibration_pairs':0},1)
        self.assertEqual(m['score_calibration']['pairs'],0)
        self.assertIsNone(m['score_calibration']['mae'])

    def test_quality_calibration_deduplicates(self):
        from projectiq_executioner.phase7.engine import _aggregate
        base={'result':'EVALUATED','cohort':'STATIC_ONLY','source':source(),
              'phase5_sha256':'a'*64,'expected_codes':[],'observed_codes':[],
              'missing_codes':[],'unexpected_codes':[],'events':[],'comparisons':[],
              'known_causes':0,'top3_hits':0,'status_correct':True,'false_attributions':0,
              'verified_input_receipts':0,'total_report_receipts':0,
              'quality':{'status':'PROVISIONAL','score':70,'reviewer_maintainability':75}}
        _,m=_aggregate([{**base,'id':'A'},{**base,'id':'B'}],{**DEFAULT_POLICY,
              'min_cases':0,'min_repositories':0,'min_cohorts':0,'min_application_defects':0,
              'min_event_labels':0,'min_known_causes':0,'min_calibration_pairs':0},2)
        self.assertEqual(m['score_calibration']['pairs'],1)
        self.assertEqual(m['score_calibration']['mae'],5)

    def test_cross_report_score_conflict_fails(self):
        from projectiq_executioner.phase7.engine import _aggregate
        base={'result':'EVALUATED','cohort':'STATIC_ONLY','source':source(),
              'phase5_sha256':'a'*64,'expected_codes':[],'observed_codes':[],
              'missing_codes':[],'unexpected_codes':[],'events':[],'comparisons':[],
              'known_causes':0,'top3_hits':0,'status_correct':True,'false_attributions':0,
              'verified_input_receipts':0,'total_report_receipts':0,
              'quality':{'status':'PROVISIONAL','score':70,'reviewer_maintainability':75}}
        b=deepcopy(base);b['quality']['reviewer_maintainability']=55
        status,m=_aggregate([base,b],{**DEFAULT_POLICY,'min_cases':0,'min_repositories':0,
            'min_cohorts':0,'min_application_defects':0,'min_event_labels':0,
            'min_known_causes':0,'min_calibration_pairs':0},2)
        self.assertEqual(status,'FAIL')
        self.assertEqual(m['score_calibration']['duplicate_label_conflicts'],1)

    def test_case_counts_are_bounded(self):
        _,m=self.make()
        m['cases']*=129
        with self.assertRaises(BenchmarkError):validate_dataset(m)

    def test_release_policy_cannot_tolerate_false_passes(self):
        _,m=self.make()
        m['policy']['max_false_passes']=1
        with self.assertRaises(BenchmarkError) as ex:validate_dataset(m)
        self.assertEqual(ex.exception.code,'UNSAFE_RELEASE_POLICY')

    def test_release_policy_cannot_tolerate_student_blame(self):
        _,m=self.make()
        m['policy']['max_false_attributions']=1
        with self.assertRaises(BenchmarkError) as ex:validate_dataset(m)
        self.assertEqual(ex.exception.code,'UNSAFE_RELEASE_POLICY')

    def test_invalid_policy_negative_rejected(self):
        _,m=self.make()
        m['policy']['min_cases']=-1
        with self.assertRaises(BenchmarkError):validate_dataset(m)

    def test_ground_truth_cannot_reference_unknown_diagnosis(self):
        _,m=self.make()
        m['cases'][0]['ground_truth']['expected_codes']=['INVENTED_FAILURE_CODE']
        with self.assertRaises(BenchmarkError):validate_dataset(m)

    def test_bogus_rating_requires_phase5(self):
        _,m=self.make()
        m['cases'][0]['ground_truth']['maintainability_rating']=90
        with self.assertRaises(BenchmarkError):validate_dataset(m)

    def test_duplicate_phase6_event_rejected(self):
        c,m=self.make()
        p=self.root/c['phase6']['path']
        d=json.loads(p.read_text());d['events'].append(deepcopy(d['events'][0]))
        d['summary']['tests_observed']+=1;d['summary']['fails']+=1
        b=json.dumps(d).encode();p.write_bytes(b)
        c['phase6']['sha256']=hashlib.sha256(b).hexdigest()
        self.assertEqual(run(m,self.root)['results'][0]['error'],'DUPLICATE_PHASE6_EVENT')

    def test_status_mismatch_is_limited(self):
        _,m=self.make()
        m['cases'][0]['ground_truth']['expected_report_status']='PARTIAL'
        self.assertEqual(run(m,self.root)['status'],'LIMITED')

    def test_unverified_report_does_not_grant_vm_verification(self):
        _,m=self.make(verified=False)
        r=run(m,self.root)
        self.assertEqual(r['results'][0]['phase6_status'],'PARTIAL')

    def test_unreviewed_template_cannot_be_benchmarked(self):
        from projectiq_executioner.phase7.prepare import main as prep
        sub=self.root/'runs';sub.mkdir()
        p=sub/'phase6_diagnosis.json'
        p.write_text(json.dumps(evidence()))
        dest=sub/'template.json'
        self.assertEqual(prep(['--root',str(sub),'--out',str(dest)]),0)
        templ=json.loads(dest.read_text())
        self.assertFalse(templ['cases'][0]['ground_truth']['reviewed'])
        with self.assertRaises(BenchmarkError):validate_dataset(templ)

    def test_dataset_isolation_no_vm(self):
        _,m=self.make()
        r=run(m,self.root)
        self.assertEqual(r['execution'],'NOT_PERFORMED')
        self.assertEqual(r['vm_launches'],0)


if __name__=='__main__':unittest.main()
