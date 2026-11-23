"""Phase 7 Phase 5 integration: validate actual v0.3 report contract."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from projectiq_executioner.phase5.contracts import validate_report as validate_p5
from projectiq_executioner.phase6.pipeline import build as build_p6
from projectiq_executioner.phase6.report import validate_report as validate_p6
from projectiq_executioner.phase7.engine import run
from projectiq_executioner.phase7.contracts import DEFAULT_POLICY


def make_phase5(repo='example/sample',sha='a'*40):
    s={'kind':'github_pinned','repository':repo,'commit_sha':sha}
    r={'schema_version':'0.3','stage':'phase5_code_quality','status':'PARTIAL','source':s,
    'analysis':{'method':'bounded_static_analysis','source_execution':'NOT_PERFORMED','tool_execution':'NOT_PERFORMED','lint_scope':'BUILTIN_LIMITED','big_o_scope':'CONSERVATIVE_SUBSET'},
    'summary':{'indexed_files':1,'source_candidates':1,'analyzed_files':1,'source_lines':120,'blank_lines':20,'comment_lines':0,'code_lines':100,'functions_found':0,'findings':0,'duplicate_groups':0,'production_files':1,'test_files':0,'production_code_lines':100,'test_code_lines':0},
    'coverage':{'supported_files':0,'unsupported_files':1,'skipped_files':0,'limited_function_files':1,'skipped_reasons':{},'source_ratio':1.0,
    'function_analysis_scope':'NOT_AVAILABLE','function_eligible_files':1,'function_scanned_files':0,'function_file_ratio':0.0,'function_body_ratio':0.0,'procedural_php_files':0,
    'big_o':{'estimated_functions':0,'unknown_functions':0,'estimated_ratio':0.0},'limits':{'functions_truncated':False,'findings_truncated':False,'duplications_truncated':False},'production_function_body_ratio':0.0,'ast_complexity_share':0.0,'duplicate_production_lines':0,'duplicate_production_ratio':0.0},
    'languages':{'Python':1},
    'files':[{'path':'main.py','language':'Python','status':'ANALYZED','reason':None,'lines':120,'code_lines':100,'blank_lines':20,'comment_lines':0,'function_count':0,'function_scope':'UNSUPPORTED','sha256':'c'*64,'scope':'PRODUCTION','procedural_decision_markers':0,'procedural_loop_markers':0}],
    'functions':[],'findings':[],'duplications':[],
    'security':{'status':'NO_INDICATORS_FOUND','findings':[],'scanned_source_files':1,'tracked_sensitive_files':0,'truncated':False,'scope':'BOUNDED_PATTERNS_NO_SECRET_VALUES_OR_VALIDITY_CHECK','scanned_additional_files':0},
    'quality':{'status':'PROVISIONAL_LIMITED','score':80,'dimensions':{'lint':80,'complexity':None,'duplication':80,'structure':80},'basis':'HEURISTIC_NOT_A_GRADE','reason_codes':['LIMITED_COMPLEXITY_ANALYSIS'],'scoring_version':'0.3','weights_used':{'lint':0.428571,'duplication':0.285714,'structure':0.285714},'confidence':'LOW'},
    'limitations':[]}
    validate_p5(r)
    return r


def artifacts(root, index=0, maintainability=75, score=80):
    sha=hex(index+10)[2:]*40
    sha=sha[:40]
    repo='example/sample'
    p5=make_phase5(repo,sha)
    p5['quality']['score']=score
    for k in ('lint','duplication','structure'):
        p5['quality']['dimensions'][k]=score
    validate_p5(p5)
    p6=build_p6(quality_input=(p5,'QUALITY'))
    validate_p6(p6)
    output=[]
    for role,report in (('phase5',p5),('phase6',p6)):
        raw=json.dumps(report,sort_keys=True).encode()
        path=root/f'{role}_{index}.json';path.write_bytes(raw)
        output.append({'path':path.name,'sha256':hashlib.sha256(raw).hexdigest()})
    return {'id':f'CAL_{index:03d}','cohort':'STATIC_ONLY','source':{'kind':'github','repository':repo,'commit_sha':sha},
            'phase5':output[0], 'phase6':output[1],
            'ground_truth':{'reviewed':True,'reviewer_id':'INDEPENDENT_R1','reviewed_at':'2026-11-24',
                'basis':'INDEPENDENT_REVIEW','expected_report_status':'PARTIAL',
                'expected_codes':['NO_EXECUTION_EVIDENCE'], 'expected_events':[],
                'expected_comparisons':[], 'maintainability_rating':maintainability}}


class CalibrationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def run_one(self,case):
        manifest={'schema_version':'0.1','stage':'phase7_benchmark_dataset',
                  'benchmark_id':'VALIDATE_PHASE5','cases':[case],
                  'policy':{**DEFAULT_POLICY,'min_cases':0,'min_repositories':0,'min_cohorts':0,
                            'min_application_defects':0,'min_event_labels':0,
                            'min_known_causes':0,'min_calibration_pairs':0}}
        return run(manifest,self.root)

    def test_real_phase5_quality_report_is_accepted(self):
        case=artifacts(self.root,0,75,80)
        results=self.run_one(case)
        self.assertEqual(results['metrics']['rejected'],0)
        self.assertEqual(results['metrics']['score_calibration']['pairs'],1)
        self.assertEqual(results['metrics']['score_calibration']['mae'],5)

    def test_integration_five_phase5_calibration_pairs(self):
        cs=[artifacts(self.root,i, 70+i*4, 75+i*4) for i in range(5)]
        manifest={'schema_version':'0.1','stage':'phase7_benchmark_dataset','benchmark_id':'VALIDATE_CALIBRATION',
                  'cases':cs, 'policy':{**DEFAULT_POLICY,'min_cases':0,'min_repositories':0,
                                     'min_cohorts':0,'min_application_defects':0,'min_event_labels':0,
                                     'min_known_causes':0,'min_calibration_pairs':5}}
        r=run(manifest,self.root)
        self.assertEqual(r['status'],'PASS')
        self.assertEqual(r['metrics']['score_calibration']['status'],'DESCRIPTIVE_ONLY')
        self.assertEqual(r['metrics']['score_calibration']['pairs'],5)
        self.assertEqual(r['metrics']['score_calibration']['mae'],5)
        self.assertEqual(r['metrics']['score_calibration']['spearman_rank_correlation'],1)

    def test_phase5_hash_tampering_rejected(self):
        c=artifacts(self.root)
        c['phase5']['sha256']='0'*64
        self.assertEqual(self.run_one(c)['results'][0]['error'],'ARTIFACT_SHA_MISMATCH')

    def test_phase5_cross_source_is_rejected(self):
        c=artifacts(self.root)
        p=self.root/c['phase5']['path'];doc=json.loads(p.read_text())
        doc['source']['commit_sha']='f'*40
        b=json.dumps(doc).encode();p.write_bytes(b)
        c['phase5']['sha256']=hashlib.sha256(b).hexdigest()
        self.assertEqual(self.run_one(c)['results'][0]['error'],'CROSS_ARTIFACT_SOURCE_MISMATCH')

    def test_phase5_invalid_weights_rejected(self):
        c=artifacts(self.root)
        p=self.root/c['phase5']['path'];doc=json.loads(p.read_text())
        doc['quality']['weights_used']['lint']=0.99
        b=json.dumps(doc).encode();p.write_bytes(b)
        c['phase5']['sha256']=hashlib.sha256(b).hexdigest()
        self.assertEqual(self.run_one(c)['results'][0]['error'],'INVALID_PHASE5_REPORT')

    def test_tampered_quality_indicator_rejected(self):
        c=artifacts(self.root)
        p=self.root/c['phase5']['path'];doc=json.loads(p.read_text())
        doc['quality']['score']=101
        b=json.dumps(doc).encode();p.write_bytes(b)
        c['phase5']['sha256']=hashlib.sha256(b).hexdigest()
        self.assertEqual(self.run_one(c)['results'][0]['error'],'INVALID_PHASE5_REPORT')

    def test_missing_phase5_evidence_rejected(self):
        c=artifacts(self.root)
        (self.root/c['phase5']['path']).unlink()
        self.assertEqual(self.run_one(c)['results'][0]['error'],'EVIDENCE_FILE_NOT_REGULAR')

    def test_maintainability_score_not_student_grade(self):
        c=artifacts(self.root)
        r=self.run_one(c)
        self.assertEqual(r['metrics']['score_calibration']['reference'],
                         'REVIEWER_MAINTAINABILITY_INDEX_NOT_STUDENT_GRADE')

if __name__=='__main__':unittest.main()
