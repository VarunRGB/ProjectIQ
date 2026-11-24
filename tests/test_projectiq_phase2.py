"""Phase 2 offline safety/behavior tests; no model, network, or VM calls."""
from __future__ import annotations
import json,tempfile,unittest,copy,sys
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
from projectiq_core.collector import collect,make_submission,live_sources
from projectiq_core.adapters import normalize
from projectiq_core.contracts import Receipt
from projectiq_core.phase2.contracts import Rubric,Decision,DecisionSet
from projectiq_core.phase2.evidence import build_evidence,digest
from projectiq_core.phase2.scoring import assess,empty_decisions
from projectiq_core.phase2.suggest import propose,Suggestions
from projectiq_core.phase2.__main__ import main

SHA='a'*40
TEXT='MiniCollab uses PHP and MySQL. The system has a registration page. No bibliography was required. A test is planned, not executed.'
LEVELS=[{'id':'zero','label':'No achievement','fraction':0,'descriptor':'Evidence establishes no achievement.'},
        {'id':'half','label':'Partial','fraction':0.5,'descriptor':'Evidence establishes partial achievement.'},
        {'id':'full','label':'Complete','fraction':1,'descriptor':'Evidence establishes full achievement.'}]

def criterion(id='quality',weight=1,app='REQUIRED',**kw):
    return dict(id=id,title='Quality '+id,description='Assess report clarity and PHP architecture.',group='engineering',weight=weight,applicability=app,
                allowed_evidence=['REPORT_EXCERPT'],required_evidence=['REPORT_EXCERPT'],levels=copy.deepcopy(LEVELS))|kw

def rubric(*criteria,total=100):return Rubric.model_validate(dict(id='demo',title='Demo rubric',total_points=total,criteria=list(criteria)))

def case(id='TC-1',category='HTTP_ROUTE',outcome='NOT_TESTED'):
    target={'method':'GET','path':'/example','label':None};spec={'id':id,'category':category,'target':target,'status':'NEEDS_ORACLE','assertions':[]}
    return spec,dict(test_id=id,category=category,target=target,expected_assertions=[],scope='PHASE3_DISCOVERED_CASE',title='Test example',outcome=outcome,reason='STATIC_MODE_NO_EXECUTION')

def raw_fixture():
    spec,row=case()
    return {'summarizer':{'name':'report.txt','summary':{'summary':'MiniCollab project','key_points':['Uses PHP']},'criteria':[{'criterion_id':'clarity','score':8,'evidence_quote':'MiniCollab uses PHP and MySQL.'},{'criterion_id':'bad','score':8,'evidence_quote':'Made up sentence is absent.'}], 'confidence':{'score':53},'weighted_score':8.17,'recommendation':'ACCEPT'},
            'integrity':{'document':'report.txt','references':[],'words_checked':0,'overall_percent':0,'percent_in_chunks':0,'percent_by_category':{'verbatim':0.0,'paraphrase':0.0},'backends':{'embeddings':'no reference text'}},
            'research':{'source':'report.txt','research_report':{'phase_1':{'reference_entries':0,'references_found':False,'literature_review_found':False},'phase_2':{'checked_references':0},'phase_3':{'assessed_references':0},'phase_4':{'recent_reliable_percent':None,'recency_assessment':'insufficient_evidence','reliable_publication_year_count':0}}},
            'executioner':{'stage':'executioner_evaluation','submission_id':'demo','source':{'kind':'github','repository':'example/project','commit_sha':SHA},'effective_mode':'STATIC','status':'COMPLETED_WITH_LIMITATIONS','project_intelligence':{'server_endpoint_count':1,'features_discovered':1,'features':[{'name':'Registration','origin':'readme'}]},'test_plan':{'cases':[spec]},'suites':[], 'test_case_matrix':{'counts':{'PASSED':0,'NOT_TESTED':1},'reviewed_check_counts':{},'discovered_cases':[row]},'code_quality':{'quality':{'status':'PROVISIONAL_LIMITED','score':None}}}}

class Phase2(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.report=self.root/'report.txt';self.report.write_text(TEXT,encoding='utf-8');self.raw=raw_fixture()
        self.sub,self.data=make_submission(self.report,'Demo','demo','https://github.com/example/project',SHA)
    def run_case(self,mode='LIVE',agents=None,ack=False):
        out=self.root/'run';collect(submission=self.sub,report_bytes=self.data,out=out,mode=mode,sources={a:lambda r,o,a=a:self.raw[a] for a in (agents or self.raw)},progress=lambda _:None)
        return build_evidence(out/'evaluation.json',acknowledge_imported=ack,loader=lambda p:p.read_text(encoding='utf-8'))
    def report_id(self,b):return next(e.id for e in b.items if e.kind=='REPORT_EXCERPT')
    def decisions(self,b,r,*ds):return DecisionSet(evidence_sha256=digest(b),rubric_sha256=digest(r),decisions=list(ds))
    def score(self,b,cid='quality',source='TEACHER',level='full',ids=None):return Decision(criterion_id=cid,source=source,action='SCORE',level_id=level,evidence_ids=ids or [self.report_id(b)],rationale='Evidence reviewed for this criterion.')
    def test_all_receipts_matched(self):
        b=self.run_case();self.assertEqual(set(b.agent_checks.values()),{'MATCHED'});self.assertEqual(b.report_extraction,'COMPLETE')
    def test_exact_report_offsets(self):
        b=self.run_case();e=next(e for e in b.items if e.kind=='REPORT_EXCERPT');self.assertEqual(TEXT[e.data['start']:e.data['end']],e.text)
    def test_no_corpus_null_categories(self):
        i=self.run_case().observations['integrity'];self.assertIsNone(i['reference_overlap']);self.assertEqual(i['category_percentages'],{'verbatim':None,'paraphrase':None});self.assertEqual(i['embedding_status'],'NOT_APPLICABLE_NO_REFERENCE_CORPUS')
    def test_real_zero_preserved(self):
        self.raw['integrity'].update(references=['paper'],words_checked=10);i=self.run_case().observations['integrity'];self.assertEqual(i['reference_overlap'],0);self.assertEqual(i['category_percentages']['verbatim'],0)
    def test_no_research_does_not_create_marks(self):
        b=self.run_case();self.assertEqual(b.observations['research']['reference_entries'],0);self.assertIsNone(b.observations['research']['recent_reliable_percent']);self.assertIsNone(assess(b,rubric(criterion())).proposed_points)
    def test_legacy_quotes_advisory_and_checked(self):
        b=self.run_case();self.assertEqual([x['quote_found'] for x in b.observations['summarizer']['legacy_quote_checks']],[True,False]);self.assertTrue(all(not e.eligible_for_review for e in b.items if e.kind in ('LEGACY_JUDGEMENT','REPORT_SUMMARY','INTEGRITY_ADVISORY')))
    def test_native_tamper_withheld(self):
        b=self.run_case();p=self.root/'run/native/integrity.json';p.write_text('{}');b=build_evidence(self.root/'run/evaluation.json',loader=lambda p:TEXT);self.assertEqual(b.agent_checks['integrity'],'RECEIPT_OR_CONTRACT_MISMATCH');self.assertNotIn('integrity',b.observations)
    def test_envelope_tamper_withheld(self):
        self.run_case();p=self.root/'run/evaluation.json';d=json.loads(p.read_text());d['agents']['summarizer']['metrics'][0]['value']=10;p.write_text(json.dumps(d));b=build_evidence(p,loader=lambda p:TEXT);self.assertEqual(b.agent_checks['summarizer'],'RECEIPT_OR_CONTRACT_MISMATCH')
    def test_report_tamper_rejected(self):
        self.run_case();(self.root/'run/input/report.txt').write_text('changed');
        with self.assertRaisesRegex(ValueError,'REPORT_DIGEST_MISMATCH'):build_evidence(self.root/'run/evaluation.json',loader=lambda p:TEXT)
    def test_native_path_traversal_withheld(self):
        self.run_case();p=self.root/'run/evaluation.json';d=json.loads(p.read_text());d['agents']['research']['native']['path']='../report.txt';p.write_text(json.dumps(d));b=build_evidence(p,loader=lambda p:TEXT);self.assertEqual(b.agent_checks['research'],'RECEIPT_OR_CONTRACT_MISMATCH')
    def test_imported_requires_acknowledgement(self):
        b=self.run_case(mode='IMPORTED');self.assertFalse(any(e.eligible_for_review for e in b.items if e.agent=='executioner'));b=build_evidence(self.root/'run/evaluation.json',acknowledge_imported=True,loader=lambda p:TEXT);self.assertTrue(any(e.eligible_for_review for e in b.items if e.kind=='STATIC_SOURCE'))
    def test_missing_agent_preserved(self):
        b=self.run_case(agents=['summarizer']);self.assertEqual(b.agent_checks['executioner'],'NOT_RUN')
    def test_stable_evidence_digest(self):
        b=self.run_case();self.assertEqual(digest(b),digest(build_evidence(self.root/'run/evaluation.json',loader=lambda p:TEXT)))
    def test_unresolved_weight_not_redistributed(self):
        b=self.run_case();r=rubric(criterion('quality',40),criterion('other',60));a=assess(b,r,self.decisions(b,r,self.score(b)));self.assertIsNone(a.proposed_points);self.assertEqual(a.assessed_subtotal_points,40);self.assertEqual(a.assessed_max_points,40);self.assertEqual(a.assessed_weight_percent,40)
    def test_optional_exclusion_reweights(self):
        b=self.run_case();r=rubric(criterion('quality',40),criterion('research',60,'OPTIONAL'));ex=Decision(criterion_id='research',source='TEACHER',action='EXCLUDE',rationale='Not required for this assignment.');a=assess(b,r,self.decisions(b,r,self.score(b),ex));self.assertEqual(a.reviewed_points,100);self.assertEqual(a.criteria[1].status,'EXCLUDED')
    def test_optional_unresolved_not_excluded(self):
        b=self.run_case();r=rubric(criterion(),criterion('research',1,'OPTIONAL'));self.assertIsNone(assess(b,r,self.decisions(b,r,self.score(b))).proposed_points)
    def test_required_cannot_exclude(self):
        b=self.run_case();r=rubric(criterion());d=Decision(criterion_id='quality',source='TEACHER',action='EXCLUDE',rationale='Missing');
        with self.assertRaisesRegex(ValueError,'ONLY_TEACHER'):assess(b,r,self.decisions(b,r,d))
    def test_true_zero_with_evidence(self):
        b=self.run_case();r=rubric(criterion());a=assess(b,r,self.decisions(b,r,self.score(b,level='zero')));self.assertEqual(a.reviewed_points,0)
    def test_custom_points_and_weights(self):
        b=self.run_case();r=rubric(criterion('quality',2),criterion('other',3),total=50);a=assess(b,r,self.decisions(b,r,self.score(b,level='half'),self.score(b,'other')));self.assertEqual(a.reviewed_points,40)
    def test_model_is_proposed_not_reviewed(self):
        b=self.run_case();r=rubric(criterion());a=assess(b,r,self.decisions(b,r,self.score(b,source='MODEL')));self.assertEqual(a.proposed_points,100);self.assertIsNone(a.reviewed_points);self.assertEqual(a.status,'READY_FOR_TEACHER_REVIEW')
    def test_fake_evidence_rejected(self):
        b=self.run_case();r=rubric(criterion());
        with self.assertRaisesRegex(ValueError,'UNKNOWN_EVIDENCE'):assess(b,r,self.decisions(b,r,self.score(b,ids=['E-fake'])))
    def test_advisory_cannot_score(self):
        b=self.run_case();r=rubric(criterion());eid=next(e.id for e in b.items if e.kind=='LEGACY_JUDGEMENT');
        with self.assertRaisesRegex(ValueError,'INELIGIBLE_EVIDENCE'):assess(b,r,self.decisions(b,r,self.score(b,ids=[eid])))
    def test_evidence_requirements(self):
        b=self.run_case();r=rubric(criterion(allowed_evidence=['REPORT_EXCERPT','TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION']));
        with self.assertRaisesRegex(ValueError,'EVIDENCE_COVERAGE'):assess(b,r,self.decisions(b,r,self.score(b)))
    def test_stale_rubric_hash(self):
        b=self.run_case();r=rubric(criterion());d=self.decisions(b,r,self.score(b));new=rubric(criterion(weight=2));
        with self.assertRaisesRegex(ValueError,'STALE_DECISIONS'):assess(b,new,d)
    def test_duplicate_decisions(self):
        b=self.run_case();r=rubric(criterion());d=self.score(b);
        with self.assertRaises(ValueError):self.decisions(b,r,d,d)
    def test_rubric_invalid_weights(self):
        for weight in (0,-1,float('nan'),True):
            with self.subTest(weight=weight),self.assertRaises(ValueError):rubric(criterion(weight=weight))
    def test_levels_require_endpoints(self):
        with self.assertRaises(ValueError):rubric(criterion(levels=LEVELS[1:]))
    def test_test_ratio_missing_is_unresolved(self):
        b=self.run_case();r=self.ratio_rubric();a=assess(b,r);self.assertIsNone(a.proposed_points);self.assertIn('TC-1',a.criteria[0].reason)
    def ratio_rubric(self):return rubric(criterion(method='TEST_RATIO',test_ids=['TC-1'],allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION'],levels=[]))
    def reviewed(self,mode='REVIEWED',cleanup='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',result='PASS',category='HTTP_ROUTE',conflict=False):
        raw=self.raw['executioner'];raw['effective_mode']=mode;spec,row=case(category=category,outcome='PASSED' if result=='PASS' else 'FAILED');raw['test_plan']['cases']=[spec];raw['test_case_matrix']['discovered_cases']=[row];raw['suites']=[dict(kind='api',state=result,vm_verified=True,cleanup=cleanup,observations=[{'test_id':'TC-1','result':result}])]
        if conflict:raw['suites'].append(dict(kind='api2',state='FAIL',vm_verified=True,cleanup=cleanup,observations=[{'test_id':'TC-1','result':'FAIL'}]))
        return self.run_case(mode='IMPORTED',ack=True)
    def test_verified_ratio_proposed(self):
        b=self.reviewed();a=assess(b,self.ratio_rubric());self.assertEqual(a.proposed_points,100);self.assertIsNone(a.reviewed_points);self.assertEqual(a.criteria[0].status,'OBJECTIVE_PROVISIONAL')
    def test_failed_ratio_teacher_confirms(self):
        b=self.reviewed(result='FAIL');r=self.ratio_rubric();a=assess(b,r,self.decisions(b,r,Decision(criterion_id='quality',source='TEACHER',action='CONFIRM_TEST_RATIO',rationale='Reviewed failed assertion and attribution.')));self.assertEqual(a.reviewed_points,0)
    def test_bad_cleanup_withholds_ratio(self):self.assertIsNone(assess(self.reviewed(cleanup='UNKNOWN'),self.ratio_rubric()).proposed_points)
    def test_readiness_not_functional_ratio(self):self.assertIsNone(assess(self.reviewed(category='HTTP_READINESS'),self.ratio_rubric()).proposed_points)
    def test_conflicting_observations_withhold_ratio(self):self.assertIsNone(assess(self.reviewed(conflict=True),self.ratio_rubric()).proposed_points)
    def test_static_cannot_claim_execution(self):self.assertIsNone(assess(self.reviewed(mode='STATIC'),self.ratio_rubric()).proposed_points)
    def test_model_single_call_and_review_gate(self):
        b=self.run_case();r=rubric(criterion());llm=Mock();llm.chat_json.return_value=Suggestions.model_validate({'decisions':[{'criterion_id':'quality','action':'SCORE','level_id':'half','evidence_ids':[self.report_id(b)],'rationale':'Specific explanation in report.'}]});d=propose(b,r,llm);self.assertEqual(llm.chat_json.call_count,1);self.assertEqual(assess(b,r,d).proposed_points,50);self.assertIsNone(assess(b,r,d).reviewed_points)
    def test_model_hallucinated_id_deferred(self):
        b=self.run_case();r=rubric(criterion());llm=Mock();llm.chat_json.return_value=Suggestions.model_validate({'decisions':[{'criterion_id':'quality','action':'SCORE','level_id':'full','evidence_ids':['E-fake'],'rationale':'Invented'}]});d=propose(b,r,llm);self.assertEqual(d.decisions[0].action,'DEFER')
    def test_model_skips_missing_coverage(self):
        b=self.run_case();r=rubric(criterion(allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION']));llm=Mock();d=propose(b,r,llm);llm.chat_json.assert_not_called();self.assertEqual(d.decisions[0].action,'DEFER')
    def test_summary_only_adapter(self):
        result=normalize('summarizer',{'result_kind':'SUMMARY_ONLY','summary':{'summary':'MiniCollab','key_points':[]},'word_count':100},Receipt(path='native/summarizer.json',sha256='b'*64,origin='LIVE'));self.assertEqual(result.native_status,'SUMMARY_GENERATED');self.assertEqual([m.id for m in result.metrics],['word_count'])
    def test_summary_only_rejects_legacy_scores(self):
        with self.assertRaisesRegex(ValueError,'LEGACY_JUDGEMENT'):normalize('summarizer',{'result_kind':'SUMMARY_ONLY','summary':{'summary':'MiniCollab','key_points':[]},'weighted_score':99},Receipt(path='native/summarizer.json',sha256='b'*64,origin='LIVE'))
    def test_summary_only_never_calls_legacy_judge(self):
        llm=Mock();llm.check.return_value=None;summary=Mock();summary.model_dump.return_value={'summary':'Example','key_points':[]};summarise=Mock(return_value=(summary,1));legacy=Mock(side_effect=AssertionError('legacy judge must not run'))
        mods={'projectiq_summarizer.config':NS(Settings=lambda **kw:NS(model='test',ollama_url='http://localhost:11434')),'projectiq_summarizer.llm':NS(OllamaClient=lambda s:llm),'projectiq_summarizer.loaders':NS(load_text=lambda p:TEXT),'projectiq_summarizer.summariser':NS(summarise=summarise),'projectiq_summarizer.pipeline':NS(evaluate=legacy)}
        with patch.dict(sys.modules,mods),patch('projectiq_core.resources.release_ollama_model',return_value='UNLOAD_VERIFIED'):
            out=self.root/'mock';out.mkdir();raw=live_sources(self.sub,agents=['summarizer'],summary_only=True)['summarizer'](self.report,out)
        summarise.assert_called_once();legacy.assert_not_called();self.assertEqual(raw['result_kind'],'SUMMARY_ONLY');self.assertEqual(json.loads((out/'ollama_release.json').read_text())['status'],'UNLOAD_VERIFIED')
    def test_example_rubric_valid(self):
        p=Path(__file__).resolve().parents[1]/'projectiq_core/phase2/software_project.rubric.json';r=Rubric.model_validate_json(p.read_text());self.assertEqual(sum(c.weight for c in r.criteria),100)
    def test_schema_cli(self):
        self.assertEqual(main(['schemas','--out',str(self.root/'schemas')]),0);self.assertEqual(len(list((self.root/'schemas').glob('*.json'))),4)

if __name__=='__main__':unittest.main()
