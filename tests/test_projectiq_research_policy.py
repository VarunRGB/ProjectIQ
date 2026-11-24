"""Regression tests for native research gates, no-inference absence and legacy migration."""
from pathlib import Path
import json,tempfile,unittest,copy
from unittest.mock import Mock,patch
from projectiq_core.collector import collect,make_submission,json_bytes
from projectiq_core.phase2.evidence import build_evidence,digest
from projectiq_core.phase2.research_policy import ABSENT,PRESENT,UNKNOWN,research_state,research_record
from projectiq_core.phase2.scoring import assess,empty_decisions
from projectiq_core.phase2.suggest import propose,prepare,plan_batches
from projectiq_core.phase2.contracts import Decision,DecisionSet,Rubric
from projectiq_core.orchestrator import configure_rubric,evaluate
from test_projectiq_phase2 import raw_fixture,criterion,rubric,SHA
from test_projectiq_phase2c import REPORT,load
from test_projectiq_phase2e import cited_research_fixture,reviewed_raw,mock_model,read

REPO='https://github.com/example/project'

def absent_raw():
    raw=raw_fixture()['research'];r=raw['research_report'];p=r['phase_1']
    p.update(document_citation_mentions=0,literature_review_citation_mentions=0,reference_match_records=0,matched_reference_entries=0,hyperlinks_found=1)
    r['reference_overview']=[]
    raw.update(papers=[],hyperlinks=[{'url':REPO}],document_citations=[],literature_citations=[],citation_reference_matches=[],notes=['Literature Review section was not found.','References section was not found.'])
    return raw

class ResearchPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.count=0
        self.r=rubric(criterion('research',group='research',allowed_evidence=['REPORT_EXCERPT','RESEARCH_RECORD'],required_evidence=['REPORT_EXCERPT','RESEARCH_RECORD']))
    def bundle(self,raw=None,text=REPORT,mode='LIVE',ack=True,loader=None,include=True):
        self.count+=1;report=self.root/'report.txt';report.write_text(text,encoding='utf8')
        sub,data=make_submission(report,'Demo','demo',REPO,SHA);out=self.root/f'run{self.count}'
        sources={'integrity':lambda p,o:raw_fixture()['integrity']}
        if include:sources['research']=lambda p,o:raw if raw is not None else absent_raw()
        collect(submission=sub,report_bytes=data,out=out,mode=mode,sources=sources,progress=lambda _:None)
        self.path=out/'evaluation.json'
        return build_evidence(self.path,acknowledge_imported=ack,loader=loader)
    def test_absence_zero_no_llm(self):
        b=self.bundle();self.assertEqual(research_state(b),ABSENT);a=assess(b,self.r)
        self.assertEqual(a.proposed_points,0);self.assertEqual(a.assessed_weight_percent,100);self.assertEqual(a.criteria[0].status,'OBJECTIVE_PROVISIONAL');self.assertEqual(a.criteria[0].objective_details['method'],'RESEARCH_ABSENCE')
        self.assertEqual(a.criteria[0].evidence_details[0]['state'],ABSENT)
        llm=Mock();d=propose(b,self.r,llm);llm.chat_json.assert_not_called();self.assertEqual(d.decisions,[]);self.assertEqual(plan_batches(b,self.r,existing=d),[])
    def test_absence_repo_link_ignored(self):
        self.assertEqual(research_state(self.bundle(text=REPORT+'\n'+REPO)),ABSENT)
    def test_external_link_inconclusive(self):
        raw=absent_raw();raw['hyperlinks']=[{'url':'https://example.org/paper'}];b=self.bundle(raw);self.assertEqual(research_state(b),UNKNOWN);self.assertIsNone(assess(b,self.r).proposed_points)
    def test_inline_citation_conflicts(self):
        self.assertEqual(research_state(self.bundle(text=REPORT+'\nPrior work [1] provides context.')),UNKNOWN)
    def test_missed_heading_conflicts(self):
        self.assertEqual(research_state(self.bundle(text=REPORT+'\nWorks Cited\nA study on tasks.')),UNKNOWN)
    def test_literature_exists_but_entries_missing(self):
        raw=absent_raw();raw['research_report']['phase_1']['literature_review_found']=True
        self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_heading_only_inconclusive(self):
        raw=absent_raw();raw['research_report']['phase_1']['references_found']=True
        self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_partial_native_counts_inconclusive(self):
        raw=absent_raw();del raw['research_report']['phase_1']['document_citation_mentions'];self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_native_paper_count_disagrees(self):
        raw=absent_raw();raw['papers']=[{'title':'Source'}];self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_unavailable_report(self):
        b=self.bundle(loader=lambda p:'');self.assertEqual(research_state(b),UNKNOWN);self.assertIsNone(assess(b,self.r).proposed_points)
    def test_truncated_report(self):
        with patch('projectiq_core.phase2.evidence.MAX_TEXT',25):b=self.bundle()
        self.assertEqual(research_state(b),UNKNOWN)
    def test_missing_agent(self):
        b=self.bundle(include=False);self.assertEqual(research_state(b),UNKNOWN);llm=Mock();propose(b,self.r,llm);llm.chat_json.assert_not_called()
    def test_unacknowledged_import(self):
        self.assertEqual(research_state(self.bundle(mode='IMPORTED',ack=False)),UNKNOWN)
    def test_receipt_mismatch(self):
        self.bundle();(self.path.parent/'native/research.json').write_text('{}');b=build_evidence(self.path);self.assertEqual(research_state(b),UNKNOWN)
    def test_model_positive_on_absence_rejected(self):
        b=self.bundle();d=Decision(criterion_id='research',source='MODEL',action='SCORE',level_id='half',evidence_ids=[research_record(b).id],rationale='References exist')
        with self.assertRaisesRegex(ValueError,'RESEARCH_ABSENCE'):assess(b,self.r,DecisionSet(evidence_sha256=digest(b),rubric_sha256=digest(self.r),decisions=[d]))
    def test_teacher_can_confirm_zero_or_defer(self):
        b=self.bundle();d=Decision(criterion_id='research',source='TEACHER',action='SCORE',level_id='zero',evidence_ids=[research_record(b).id],rationale='Reviewed report and confirmed absence.')
        ds=DecisionSet(evidence_sha256=digest(b),rubric_sha256=digest(self.r),decisions=[d]);self.assertEqual(assess(b,self.r,ds).reviewed_points,0)
        ds.decisions=[Decision(criterion_id='research',source='TEACHER',action='DEFER',rationale='Check scan.')];self.assertIsNone(assess(b,self.r,ds).proposed_points)
    def test_excluded_redistribution(self):
        b=self.bundle();r=rubric(criterion('quality'),criterion('research',app='EXCLUDED',group='research'));a=assess(b,r);self.assertEqual(a.criteria[1].max_points,0);self.assertEqual(a.criteria[0].max_points,100)
    def positive(self):return self.bundle(cited_research_fixture(),REPORT+'\n8. Literature Review\nTask Systems Study [1] supports the workflow.\n9. References\n[1] Task Systems Study, 2020.')
    def test_sources_present_native_mandatory(self):
        b=self.positive();self.assertEqual(research_state(b),PRESENT);p=prepare(b,self.r);request=json.loads(p[5]);c=request['criteria'][0];self.assertIn('RESEARCH_RECORD',c['evidence_slots']);self.assertIn('research_inventory',c);self.assertEqual(c['research_inventory']['phase_1']['reference_entries'],1);self.assertIsNone(c['research_inventory']['recency']['recent_reliable_percent']);self.assertTrue(all('Task Systems Study' in e['text'] or e['kind']=='REPORT_EXCERPT' for e in request['evidence_by_id'].values()));self.assertNotIn('Problem statement',str(request['evidence_by_id']))
    def test_report_only_custom_model_refused(self):
        b=self.positive();r=rubric(criterion('research',group='research'));eid=next(e.id for e in b.items if e.kind=='REPORT_EXCERPT');d=Decision(criterion_id='research',source='MODEL',action='SCORE',level_id='half',evidence_ids=[eid],rationale='The application manages tasks.')
        with self.assertRaisesRegex(ValueError,'RESEARCH_REFERENCE_EVIDENCE_REQUIRED'):assess(b,r,DecisionSet(evidence_sha256=digest(b),rubric_sha256=digest(r),decisions=[d]))
    def test_offtopic_feedback_rejected(self):
        b=self.positive();ids=[next(e.id for e in b.items if e.kind=='REPORT_EXCERPT' and 'Literature Review' in e.label),next(e.id for e in b.items if e.data.get('record_type')=='reference')];d=Decision(criterion_id='research',source='MODEL',action='SCORE',level_id='half',evidence_ids=ids,rationale='The project has a central task list and a prototype dashboard.')
        with self.assertRaisesRegex(ValueError,'RESEARCH_FEEDBACK_OFF_TOPIC'):assess(b,self.r,DecisionSet(evidence_sha256=digest(b),rubric_sha256=digest(self.r),decisions=[d]))
    def test_integrity_advisory_never_changes_absence_grade(self):
        b=self.bundle();b.observations['integrity']['reference_overlap']=99.0;self.assertEqual(assess(b,self.r).proposed_points,0)
    def test_legacy_migration_discards_wrong_research_only(self):
        report=self.root/'report.txt';report.write_text(REPORT,encoding='utf8');sub,data=make_submission(report,'Demo','demo',REPO,SHA);raw=reviewed_raw();raw['research']=absent_raw();src=self.root/'historical';collect(submission=sub,report_bytes=data,out=src,mode='IMPORTED',sources={a:lambda p,o,a=a:raw[a] for a in raw},progress=lambda _:None)
        oldb=build_evidence(src/'evaluation.json',acknowledge_imported=True,legacy_research=True)
        # Construct historical policy and the accepted-but-wrong research score.
        r=configure_rubric(load(),research_required=True);data=r.model_dump(mode='json');c=next(x for x in data['criteria'] if x['id']=='research');c['required_evidence']=['REPORT_EXCERPT'];r=Rubric.model_validate(data)
        report_id=next(e.id for e in oldb.items if e.kind=='REPORT_EXCERPT');static_id=next(e.id for e in oldb.items if e.kind=='STATIC_SOURCE' and e.data.get('severity')=='HIGH')
        ds=[]
        for cid,level in [('problem_scope','strong'),('report_quality','strong'),('technical_design','adequate'),('implementation_quality','adequate'),('testing_strategy','developing'),('research','adequate')]:
            refs=[report_id,static_id] if cid=='implementation_quality' else [report_id]
            ds.append(Decision(criterion_id=cid,source='MODEL' if cid=='research' else 'TEACHER',action='SCORE',level_id=level,evidence_ids=refs,rationale='Historical judgment.'))
        old=DecisionSet(evidence_sha256=digest(oldb),rubric_sha256=digest(r),decisions=ds);prev=self.root/'old';prev.mkdir();(prev/'rubric.json').write_bytes(json_bytes(r.model_dump(mode='json')));(prev/'decisions.json').write_bytes(json_bytes(old.model_dump(mode='json')))
        out=self.root/'new';self.assertEqual(evaluate(out=out,rubric=configure_rubric(r,research_required=True),evaluation=src/'evaluation.json',previous_assessment=prev,acknowledge_imported=True),0)
        result=read(out/'result.json');self.assertEqual(result['assessment']['proposed_points'],61.0);self.assertEqual(result['assessment']['assessed_weight_percent'],100);self.assertEqual(len(result['decision_reuse']['preserved_criterion_ids']),5);self.assertEqual(result['decision_reuse']['dropped_decisions'],{'research':'RESEARCH_POLICY_UPGRADED'})
        self.assertEqual(set(result['agent_actions'].values()),{'REUSED'});self.assertEqual(result['assessment']['criteria'][-1]['earned_points'],0)
    def test_positive_conflicting_native_count(self):
        raw=cited_research_fixture();raw['research_report']['phase_1']['document_citation_mentions']=2
        self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_positive_conflicting_heading(self):
        raw=cited_research_fixture();raw['research_report']['phase_1']['references_found']=False
        self.assertEqual(research_state(self.bundle(raw)),UNKNOWN)
    def test_partial_scan_is_not_usable(self):
        from projectiq_core.phase2.research_policy import extraction_usable
        from types import SimpleNamespace as NS
        with patch('pypdf.PdfReader',return_value=NS(pages=[NS(extract_text=lambda:'Some text'),NS(extract_text=lambda:'')])):
            self.assertFalse(extraction_usable(Path('report.pdf'),'Some text','COMPLETE'))
    def test_native_legacy_rebuild_matches_baseline(self):
        b=self.bundle();old=build_evidence(self.path,legacy_research=True);self.assertNotEqual(digest(b),digest(old));self.assertNotIn('assessment',old.observations['research']);self.assertEqual([e.id for e in b.items if e.agent!='research'],[e.id for e in old.items if e.agent!='research'])

if __name__=='__main__':unittest.main()
