"""Offline contract and integration-boundary tests (no model, network or VM)."""
import json
import tempfile
import unittest
from pathlib import Path
from hashlib import sha256
from unittest.mock import patch
from projectiq_core.contracts import AIAnalysis, Evaluation, Receipt
from projectiq_core.adapters import normalize, AdapterError
from projectiq_core.collector import collect, make_submission, load_json, live_sources, validate_identity
from projectiq_core.__main__ import main

SHA='a'*40
REPO='https://github.com/example/project'
RECEIPT=Receipt(path='native/result.json',sha256='b'*64,origin='IMPORTED')

def samples():
    # Synthetic, contract-shaped inputs; NOT actual student evaluations.
    return {
        'summarizer':{'name':'report.txt','summary':{'summary':'Example','key_points':[]},'criteria':[{'criterion_id':'clarity','score':8,'evidence_quote':'Example'}], 'confidence':{'score':82},'weighted_score':8},
        'integrity':{'document':'report.txt','references':['paper.pdf'],'words_checked':100,'overall_percent':12.5,'percent_in_chunks':2,'matches':[],'chunks':[]},
        'research':{'source':'report.txt','research_report':{'headline':'Example','phase_1':{'reference_entries':2},'phase_2':{'checked_references':1},'phase_3':{'assessed_references':2},'phase_4':{'recent_reliable_percent':50}}},
        'executioner':{'stage':'executioner_evaluation','submission_id':'demo','source':{'kind':'github','repository':'example/project','commit_sha':SHA},'effective_mode':'STATIC','status':'COMPLETED_WITH_LIMITATIONS','project_intelligence':{'server_endpoint_count':10},'suites':[], 'test_case_matrix':{'counts':{'PASSED':0},'reviewed_check_counts':{'PASSED':0}},'code_quality':{'quality':{'status':'PROVISIONAL_LIMITED','score':100}}},
    }

def metric(result,name):return next(m.value for m in result.metrics if m.id==name)

class Phase1(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.report=self.root/'report.txt';self.report.write_text('Example report.',encoding='utf-8')
        self.sub,self.data=make_submission(self.report,'Example','demo',REPO,SHA)
        self.raw=samples()
    def source(self,a):return lambda r,o:self.raw[a]
    def run_collect(self, sources=None,mode='IMPORTED'):
        return collect(submission=self.sub,report_bytes=self.data,out=self.root/'run',mode=mode,sources=sources if sources is not None else {a:self.source(a) for a in self.raw},progress=lambda _:None)
    def test_four_agents_and_receipts(self):
        r=self.run_collect();self.assertEqual(set(r.agents),set(self.raw));self.assertIsNone(r.final_assessment)
        path=self.root/'run/evaluation.json';self.assertEqual(Evaluation.model_validate_json(path.read_text()),r)
        for a,res in r.agents.items():
            b=(self.root/'run'/res.native.path).read_bytes();self.assertEqual(sha256(b).hexdigest(),res.native.sha256);self.assertEqual(json.loads(b),self.raw[a])
    def test_missing_agents(self):
        r=self.run_collect({'summarizer':self.source('summarizer')});self.assertEqual(r.agents['research'].status,'NOT_RUN');self.assertIsNone(r.agents['integrity'].ai_detection.estimated_percent)
    def test_failure_isolation(self):
        def broken(*args):raise RuntimeError('private token should not appear')
        r=self.run_collect({'integrity':broken,'executioner':self.source('executioner')});self.assertEqual(r.agents['integrity'].status,'FAILED');self.assertEqual(r.agents['executioner'].status,'PARTIAL');self.assertNotIn('private token',r.model_dump_json())
    def test_no_references_not_zero_similarity(self):
        d=self.raw['integrity'];d['references']=[];d['overall_percent']=0
        self.assertIsNone(metric(normalize('integrity',d,RECEIPT),'reference_overlap'))
    def test_real_zero_similarity_retained(self):
        d=self.raw['integrity'];d['overall_percent']=0
        self.assertEqual(metric(normalize('integrity',d,RECEIPT),'reference_overlap'),0)
    def test_future_ai_detection(self):
        d=self.raw['integrity'];d['ai_detection']={'status':'COMPLETED','estimated_percent':0,'interpretation':'Estimated share of analyzed words flagged by detector'}
        self.assertEqual(normalize('integrity',d,RECEIPT).ai_detection.estimated_percent,0)
    def test_invalid_future_ai_isolated(self):
        d=self.raw['integrity'];d['ai_detection']={'status':'COMPLETED','estimated_percent':200}
        r=normalize('integrity',d,RECEIPT);self.assertEqual(r.ai_detection.status,'FAILED');self.assertEqual(metric(r,'reference_overlap'),12.5)
    def test_ai_not_available_by_default(self):
        self.assertEqual(normalize('integrity',self.raw['integrity'],RECEIPT).ai_detection.status,'NOT_AVAILABLE')
    def test_ai_cannot_invent_percentage(self):
        with self.assertRaises(ValueError):AIAnalysis(status='NOT_AVAILABLE',estimated_percent=0)
        with self.assertRaises(ValueError):AIAnalysis(status='COMPLETED',estimated_percent=101,interpretation='x')
        with self.assertRaises(ValueError):AIAnalysis(status='COMPLETED',estimated_percent=12)
    def test_score_scope(self):
        r=normalize('summarizer',self.raw['summarizer'],RECEIPT);self.assertEqual(r.status,'PARTIAL');self.assertEqual(metric(r,'report_writing_score'),8)
    def test_limited_maintainability_withheld(self):
        self.assertIsNone(metric(normalize('executioner',self.raw['executioner'],RECEIPT),'maintainability'))
    def test_vm_cleanup_required(self):
        d=self.raw['executioner'];d['suites']=[{'state':'PASS','vm_verified':True,'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'},{'state':'PASS','vm_verified':True,'cleanup':None},{'state':'PASS','vm_verified':False,'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'}]
        self.assertEqual(metric(normalize('executioner',d,RECEIPT),'vm_suites_passed'),1)
    def test_security_hold_preserved(self):
        d=self.raw['executioner'];d['status']='SECURITY_HOLD';r=normalize('executioner',d,RECEIPT);self.assertEqual((r.status,r.native_status),('REVIEW_REQUIRED','SECURITY_HOLD'))
    def test_wrong_sha_rejected(self):
        self.raw['executioner']['source']['commit_sha']='b'*40;r=self.run_collect();self.assertEqual(r.agents['executioner'].error_code,'COMMIT_MISMATCH')
    def test_wrong_report_rejected(self):
        self.raw['research']['source']='other.pdf';self.assertEqual(self.run_collect().agents['research'].error_code,'REPORT_FILENAME_MISMATCH')
    def test_wrong_repo_rejected(self):
        self.raw['executioner']['source']['repository']='other/project';self.assertEqual(self.run_collect().agents['executioner'].error_code,'REPOSITORY_IDENTITY_MISMATCH')
    def test_partial_research_not_silently_complete(self):
        self.raw['research']['research_report']=None;self.assertEqual(self.run_collect().agents['research'].status,'FAILED')
    def test_legacy_integrity_notes_not_parsed(self):
        with self.assertRaises(AdapterError):normalize('integrity',{'notes':['12% overlap']},RECEIPT)
    def test_invalid_number(self):
        self.raw['integrity']['overall_percent']=True
        with self.assertRaises(AdapterError):normalize('integrity',self.raw['integrity'],RECEIPT)
    def test_duplicate_json_key(self):
        p=self.root/'bad.json';p.write_text('{"a":1,"a":2}')
        with self.assertRaises(ValueError):load_json(p)
    def test_nonfinite_json(self):
        p=self.root/'bad.json';p.write_text('{"a":NaN}')
        with self.assertRaises(ValueError):load_json(p)
    def test_immutable_output(self):
        self.run_collect()
        with self.assertRaises(FileExistsError):self.run_collect()
    def test_live_guard(self):
        self.raw['executioner']['effective_mode']='REVIEWED'
        with self.assertRaises(AdapterError):validate_identity('executioner',self.raw['executioner'],self.sub)
    def test_live_static_request(self):
        import types,sys
        seen={}
        def fake(request,*,output_dir):seen.update(request);return self.raw['executioner']
        with patch.dict(sys.modules,{'projectiq_executioner.orchestration':types.SimpleNamespace(evaluate=fake)}),patch.dict('os.environ',{}):
            funcs=live_sources(self.sub,agents=['executioner']);self.run_collect(funcs,'LIVE')
        self.assertEqual(seen['mode'],'STATIC');self.assertEqual(seen['max_vm_launches'],0)
    def test_live_integrity_without_reference_folder(self):
        import types,sys
        seen={}
        def fake(report,corpus):
            seen['corpus']=corpus
            self.assertTrue(corpus.is_dir())
            self.assertEqual(list(corpus.iterdir()),[])
            self.raw['integrity'].update(references=[],words_checked=0,overall_percent=0,percent_in_chunks=0,ai_notes=['Native AI-writing check attempted.'])
            return types.SimpleNamespace(to_dict=lambda:self.raw['integrity'])
        with patch.dict(sys.modules,{'projectiq_integrity.document_check':types.SimpleNamespace(check_document=fake)}),patch.dict('os.environ',{}):
            funcs=live_sources(self.sub,agents=['integrity'])
            self.assertIn('integrity',funcs)
            result=self.run_collect(funcs,'LIVE').agents['integrity']
        self.assertEqual(seen['corpus'],self.root/'run/empty_reference_corpus')
        self.assertEqual(result.status,'PARTIAL')
        self.assertIsNone(metric(result,'reference_overlap'))
        self.assertIsNone(result.ai_detection.estimated_percent)
        self.assertEqual(result.details['ai_notes'],['Native AI-writing check attempted.'])
    def test_live_integrity_with_supplied_reference_folder(self):
        import types,sys
        corpus=self.root/'references';corpus.mkdir()
        def fake(report,path):
            self.assertEqual(path,corpus)
            return types.SimpleNamespace(to_dict=lambda:self.raw['integrity'])
        with patch.dict(sys.modules,{'projectiq_integrity.document_check':types.SimpleNamespace(check_document=fake)}),patch.dict('os.environ',{}):
            result=self.run_collect(live_sources(self.sub,references=corpus,agents=['integrity']),'LIVE').agents['integrity']
        self.assertEqual(metric(result,'reference_overlap'),12.5)
        self.assertFalse((self.root/'run/empty_reference_corpus').exists())
    def test_cli_import(self):
        p=self.root/'sum.json';p.write_text(json.dumps(self.raw['summarizer']))
        self.assertEqual(main(['bundle','--report',str(self.report),'--title','Example','--submission-id','demo','--out',str(self.root/'cli'),'--summarizer',str(p)]),0)
        self.assertTrue((self.root/'cli/evaluation.json').exists())
    def test_snapshot_digest_mismatch(self):
        with self.assertRaises(ValueError):collect(submission=self.sub,report_bytes=b'changed',out=self.root/'run',mode='IMPORTED',sources={})

if __name__=='__main__':unittest.main()
