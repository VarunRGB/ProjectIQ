"""Phase 2E workflow regressions. Real saved receipts and scoring; mocked external agents/model."""
from __future__ import annotations
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from projectiq_core.__main__ import main
from projectiq_core.collector import collect, make_submission, load_json
from projectiq_core.contracts import Evaluation
from projectiq_core.orchestrator import (DEFAULT_RUBRIC, configure_rubric, evaluate,
    research_needed, selected_agents, reuse_decisions)
from projectiq_core.phase2.contracts import Rubric, Decision, DecisionSet
from projectiq_core.phase2.evidence import build_evidence, digest
from projectiq_core.phase2.scoring import assess
from projectiq_core.phase2.__main__ import main as phase2_main
from test_projectiq_phase2 import raw_fixture, criterion, rubric, SHA
from test_projectiq_phase2c import CHECKS, CLEAN, REPORT, load, ratio, demo_bundle


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def default():
    return Rubric.model_validate(load_json(DEFAULT_RUBRIC))


def write(p, d):
    p.write_text(json.dumps(d), encoding='utf-8')


def reviewed_raw():
    raw = raw_fixture()
    run = raw['executioner']
    run['effective_mode'] = 'REVIEWED'
    run['suites'] = []
    run['test_case_matrix']['reviewed_only_checks'] = []
    for suite, check in CHECKS:
        run['suites'].append({'kind':suite,'state':'PASS','vm_verified':True,'cleanup':CLEAN,
                             'observations':[{'id':check,'result':'PASS','status_code':200}]})
        run['test_case_matrix']['reviewed_only_checks'].append({'suite':suite,'check_id':check,'outcome':'PASSED'})
    run['code_quality']['findings'] = [{'code':'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','severity':'HIGH',
        'confidence':'MEDIUM','path':'logic/admin.php','line':5,'message':'No local guard recognized',
        'recommendation':'Review authorization.'}]
    return raw


def cited_research_fixture():
    # Synthetic full native shape; these sources are test records, not real papers.
    raw=raw_fixture()['research'];r=raw['research_report'];p=r['phase_1']
    p.update(reference_entries=1,references_found=True,literature_review_found=True,document_citation_mentions=1,literature_review_citation_mentions=1,reference_match_records=1,matched_reference_entries=1,hyperlinks_found=0)
    r['reference_overview']=[{'reference_number':1,'title':'Task Systems Study','verification_status':'verified','relevance_classification':'methodologically_relevant','confidence':'medium','publication_year':2020,'year_source':'submitted_unverified','cited_in_literature_review':True}]
    raw.update(papers=[{'title':'Task Systems Study'}],hyperlinks=[],document_citations=[{'text':'[1]'}],literature_citations=[{'text':'[1]'}],citation_reference_matches=[{'reference_number':1}],notes=[])
    return raw


def mock_model():
    llm = Mock()
    def wire(system, prompt, schema, **kw):
        req = json.loads(prompt)
        result = {}
        for c in req['criteria']:
            row = {'choice':'L3','evidence':{k:v[0] for k,v in c['evidence_slots'].items()},'additional_evidence_ids':[]}
            if c['feedback_required']:
                row['feedback'] = {'strength':'Documented account and task workflows.', 'limitation':'Some implementation details require review.',
                    'improvement':'Document concrete role restrictions and expected results.', 'observed_failure':False}
            else:
                row['rationale'] = 'Evidence documents task workflows and role restrictions.'
            if c['criterion_id']=='research':
                row['feedback']={'strength':'The report cites Task Systems Study as a methodological source.', 'limitation':'Publication metadata is not verified.', 'improvement':'Verify the source and explain how the citation supports the design.', 'observed_failure':False}
                if not c['feedback_required']:
                    row.pop('feedback');row['rationale']='Task Systems Study is cited; verify its publication metadata.'
            result[c['criterion_alias']] = row
        return schema.model_validate(result)
    llm.chat_json.side_effect = wire
    return llm


class Config(unittest.TestCase):
    def test_generic_six_groups_no_project_specific_ids(self):
        r = default()
        self.assertEqual(r.group_weights,dict(problem=15,report=15,design=20,implementation=20,validation=20,research=10))
        self.assertFalse(research_needed(r))
        self.assertTrue(all(not c.test_ids and not c.requirement_map for c in r.criteria))
    def test_research_enabled_requires_agent_evidence(self):
        r = configure_rubric(default(),research_required=True)
        self.assertTrue(research_needed(r));c=next(c for c in r.criteria if c.id=='research')
        self.assertEqual(c.applicability,'REQUIRED');self.assertEqual(set(c.required_evidence),{'REPORT_EXCERPT','RESEARCH_RECORD'})
    def test_config_group_weights_and_total(self):
        r = configure_rubric(default(),weights={'problem':30,'report':5},criterion_weights={'testing_strategy':30},total_points=60)
        self.assertEqual(r.total_points,60);self.assertEqual(r.group_weights['problem'],30)
        self.assertEqual(next(c for c in r.criteria if c.id=='testing_strategy').weight,30)
        a = assess(demo_bundle(),r);g={x['id']:x for x in a.groups}
        self.assertEqual(g['research']['max_points'],0)
        self.assertEqual(g['problem']['effective_weight_percent'],31.58)
    def test_research_redistribution(self):
        a=assess(demo_bundle(),default());g={x['id']:x for x in a.groups};self.assertEqual(g['problem']['effective_weight_percent'],16.67)
        a=assess(demo_bundle(),configure_rubric(default(),research_required=True));g={x['id']:x for x in a.groups}
        self.assertEqual(g['problem']['effective_weight_percent'],15);self.assertEqual(g['research']['effective_weight_percent'],10)
        self.assertIn('research',a.unresolved_criterion_ids)
    def test_flat_weights(self):
        r=configure_rubric(rubric(criterion('a'),criterion('b')),weights={'a':3});a=assess(demo_bundle(),r)
        self.assertEqual(a.criteria[0].effective_weight_percent,75)
    def test_reject_invalid_weights(self):
        for value in (0,-1,True,'10',float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):configure_rubric(default(),weights={'problem':value})
        with self.assertRaises(ValueError):configure_rubric(default(),weights={'bogus':10})
        with self.assertRaises(ValueError):configure_rubric(default(),total_points=0)
    def test_agent_selection(self):
        r=default();self.assertEqual(selected_agents(r,repository_url='https://github.com/example/project'),('summarizer','integrity','executioner'))
        self.assertEqual(selected_agents(r,repository_url=None),('summarizer','integrity'))
        self.assertEqual(selected_agents(configure_rubric(r,research_required=True),repository_url=None),('summarizer','integrity','research'))
    def test_research_off_conflict_refused(self):
        r=rubric(criterion('other',required_evidence=['RESEARCH_RECORD'],allowed_evidence=['RESEARCH_RECORD']))
        with self.assertRaisesRegex(ValueError,'ACTIVE_CRITERION_REQUIRES_RESEARCH'):configure_rubric(r,research_required=False)
    def test_research_on_without_criterion_refused(self):
        with self.assertRaisesRegex(ValueError,'NO_RESEARCH_CRITERION'):configure_rubric(rubric(criterion()),research_required=True)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.b=demo_bundle();self.r=default()
        eid=next(e.id for e in self.b.items if e.kind=='REPORT_EXCERPT')
        self.d=DecisionSet(evidence_sha256=digest(self.b),rubric_sha256=digest(self.r),decisions=[Decision(criterion_id='problem_scope',source='TEACHER',action='SCORE',level_id='strong',evidence_ids=[eid],rationale='Teacher reviewed report.')])
    def test_applicability_change_preserves_unaffected_teacher(self):
        r=configure_rubric(self.r,research_required=True);d,log=reuse_decisions(self.b,self.r,self.d,self.b,r)
        self.assertEqual(d.decisions,self.d.decisions);self.assertEqual(log['preserved_criterion_ids'],['problem_scope'])
    def test_changed_relevant_evidence_invalidates_even_uncited(self):
        b=self.b.model_copy(deep=True);e=next(e for e in b.items if e.kind=='REPORT_EXCERPT' and e.id not in self.d.decisions[0].evidence_ids);e.text+=' Extra detail.'
        d,log=reuse_decisions(self.b,self.r,self.d,b,self.r);self.assertFalse(d.decisions);self.assertEqual(log['dropped_decisions']['problem_scope'],'RELEVANT_EVIDENCE_CHANGED')
    def test_changed_policy_invalidates(self):
        raw=self.r.model_dump();raw['criteria'][0]['description']+=' New assessment requirement.';r=Rubric.model_validate(raw)
        d,log=reuse_decisions(self.b,self.r,self.d,self.b,r);self.assertFalse(d.decisions);self.assertEqual(log['dropped_decisions']['problem_scope'],'CRITERION_POLICY_CHANGED')
    def test_different_report_never_reused(self):
        b=self.b.model_copy(update={'report_sha256':'c'*64})
        with self.assertRaisesRegex(ValueError,'REUSE_SUBMISSION_OR_REPORT_CHANGED'):reuse_decisions(self.b,self.r,self.d,b,self.r)
    def test_different_repo_commit_never_reused(self):
        b=self.b.model_copy(update={'repository_commit':'c'*40})
        with self.assertRaisesRegex(ValueError,'REUSE_SUBMISSION_OR_REPORT_CHANGED'):reuse_decisions(self.b,self.r,self.d,b,self.r)
    def test_stale_digest_refused(self):
        d=self.d.model_copy(update={'rubric_sha256':'c'*64})
        with self.assertRaisesRegex(ValueError,'STALE_DECISIONS'):reuse_decisions(self.b,self.r,d,self.b,self.r)
    def test_excluded_judgment_removed(self):
        r=configure_rubric(self.r,research_required=True);eid=next(e.id for e in self.b.items if e.kind=='REPORT_EXCERPT')
        # use a custom report-only research policy for this isolated test
        raw=r.model_dump();c=next(c for c in raw['criteria'] if c['id']=='research');c['minimum_evidence']=1;c['required_evidence']=['REPORT_EXCERPT'];r=Rubric.model_validate(raw)
        decision=Decision(criterion_id='research',source='TEACHER',action='SCORE',level_id='adequate',evidence_ids=[eid],rationale='Reviewed.')
        d=DecisionSet(evidence_sha256=digest(self.b),rubric_sha256=digest(r),decisions=[decision])
        d2,log=reuse_decisions(self.b,r,d,self.b,configure_rubric(r,research_required=False));self.assertFalse(d2.decisions);self.assertEqual(log['dropped_decisions']['research'],'CRITERION_EXCLUDED_OR_REMOVED')
    def test_retry_only_model_deferrals(self):
        ds=[Decision(criterion_id='problem_scope',source='TEACHER',action='DEFER',rationale='Teacher requested more evidence.'),Decision(criterion_id='report_quality',source='MODEL',action='DEFER',rationale='Model deferred.')]
        d=DecisionSet(evidence_sha256=digest(self.b),rubric_sha256=digest(self.r),decisions=ds)
        d2,log=reuse_decisions(self.b,self.r,d,self.b,self.r,retry_deferred=True);self.assertEqual(d2.decisions,[ds[0]]);self.assertEqual(log['dropped_decisions']['report_quality'],'EXPLICIT_MODEL_DEFER_RETRY')


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.report=self.root/'report.txt';self.report.write_text(REPORT+'\n8. Literature Review\nTask Systems Study [1] supports the task workflow.\n9. References\n[1] Task Systems Study, 2020.',encoding='utf-8')
        self.raw=raw_fixture();self.raw['research']=cited_research_fixture();self.llm=mock_model();summary=Mock();summary.model_dump.return_value={'summary':'Demo summary','key_points':['Task management']};self.summary=Mock(return_value=(summary,1));self.legacy=Mock(side_effect=AssertionError('Legacy judge must never run'))
        self.integrity=Mock(return_value=NS(to_dict=lambda:self.raw['integrity']))
        self.research=Mock(return_value=NS(to_dict=lambda:self.raw['research']))
        self.executioner=Mock(return_value=self.raw['executioner'])
        self.modules={'projectiq_integrity.document_check':NS(check_document=self.integrity),
                      'projectiq_research.agent':NS(analyze_research_document=self.research),
                      'projectiq_executioner.orchestration':NS(evaluate=self.executioner),
                      'projectiq_summarizer.config':NS(Settings=lambda **kw:NS(num_ctx=8192,seed=42,ollama_url='http://localhost:11434',model='test')),
                      'projectiq_summarizer.llm':NS(OllamaClient=lambda s:self.llm),
                      'projectiq_summarizer.summariser':NS(summarise=self.summary),
                      'projectiq_summarizer.pipeline':NS(evaluate=self.legacy)}
        self.stack=patch.dict(sys.modules,self.modules);self.stack.start();self.addCleanup(self.stack.stop)
        self.release=Mock(return_value='UNLOAD_VERIFIED');p=patch('projectiq_core.resources.release_ollama_model',self.release);p.start();self.addCleanup(p.stop)
    def live(self,out,r=None,**kw):
        return evaluate(out=self.root/out,rubric=r or default(),report=self.report,title='Demo',submission_id='demo',repository_url='https://github.com/example/project',ref=SHA,**kw)
    def saved(self,name='source',research=True,legacy=True):
        sub,data=make_submission(self.report,'Demo','demo','https://github.com/example/project',SHA);raw=reviewed_raw();raw['research']=self.raw['research'];agents=['executioner','integrity']+(['research'] if research else [])+(['summarizer'] if legacy else [])
        out=self.root/name;collect(submission=sub,report_bytes=data,out=out,mode='IMPORTED',sources={a:lambda p,o,a=a:raw[a] for a in agents},progress=lambda _:None);return out/'evaluation.json'
    def assert_no_agents(self):
        for m in (self.integrity,self.research,self.executioner,self.summary,self.legacy):m.assert_not_called()
    def test_fresh_default_runs_summary_integrity_executioner_and_rubric(self):
        self.assertEqual(self.live('new'),0);r=read(self.root/'new/result.json')
        self.integrity.assert_called_once();self.executioner.assert_called_once();self.research.assert_not_called();self.summary.assert_not_called();self.legacy.assert_not_called()
        self.assertEqual(self.executioner.call_args.args[0]['mode'],'STATIC');self.assertEqual(self.executioner.call_args.args[0]['max_vm_launches'],0)
        self.assertGreater(self.llm.chat_json.call_count,0);self.assertEqual(self.llm.check.call_count,1);self.assertEqual(self.release.call_count,1)
        self.assertEqual(r['agents']['summarizer']['status'],'COMPLETED');self.assertEqual(r['agents']['research']['status'],'NOT_RUN');self.assertIn('functional_validation',r['assessment']['unresolved_criterion_ids']);self.assertIsNone(r['assessment']['proposed_points']);self.assertFalse(r['legacy_judge_executed'])
        self.assertEqual(r['workflow_status'],'INCOMPLETE_ASSESSMENT')
    def test_fresh_research_checkbox_enabled(self):
        self.assertEqual(self.live('new',configure_rubric(default(),research_required=True)),0)
        self.research.assert_called_once();self.summary.assert_not_called();self.legacy.assert_not_called();r=read(self.root/'new/result.json')
        self.assertTrue(r['research_required']);self.assertEqual(r['agents']['research']['evidence_check'],'MATCHED')
        c=next(c for c in r['assessment']['criteria'] if c['id']=='research');self.assertEqual(c['status'],'PROPOSED')
        self.assertEqual({e['kind'] for e in c['evidence_details']},{'REPORT_EXCERPT','RESEARCH_RECORD'})
    def test_mandatory_summary_only_never_legacy(self):
        self.assertEqual(self.live('new',objective_only=True),0)
        self.summary.assert_not_called();self.legacy.assert_not_called();self.llm.chat_json.assert_not_called();r=read(self.root/'new/result.json')
        self.assertEqual(r['agents']['summarizer']['native_status'],'SUMMARY_GENERATED');self.assertEqual(r['agents']['summarizer']['details']['summary_display_method'],'EXTRACTIVE_REPORT_CLAIMS')
        self.assertEqual(r['agents']['summarizer']['details']['model'],'grounded-extractive-L1')
    def test_saved_reviewed_full_hybrid_one_command(self):
        src=self.saved();self.assertEqual(evaluate(out=self.root/'new',rubric=load(),evaluation=src,acknowledge_imported=True),0)
        self.assert_no_agents();r=read(self.root/'new/result.json');self.assertEqual(r['assessment']['assessed_weight_percent'],100);self.assertEqual(r['workflow_status'],'COMPLETE');self.assertEqual(self.llm.chat_json.call_count,2)
        self.assertEqual(r['evidence_intelligence']['runtime']['by_scope']['REVIEWED_SCENARIO_ONLY']['passed'],8)
        self.assertNotIn('report_writing_score',[m['id'] for m in r['agents']['summarizer']['metrics']]);self.assertNotIn('criteria',r['agents']['summarizer']['details']);self.assertNotIn('recommendation',r['agents']['summarizer']['details'])
        self.assertEqual((self.root/'new/collection/evaluation.json').read_bytes(),src.read_bytes())
        for rel in r['artifacts'].values():self.assertTrue((self.root/'new'/rel).is_file())
    def test_actual_saved_reweight_zero_model(self):
        src=self.saved();r=load();evaluate(out=self.root/'first',rubric=r,evaluation=src,acknowledge_imported=True);self.llm.reset_mock();self.release.reset_mock()
        r2=configure_rubric(r,total_points=60,weights={'problem':25,'report':5})
        self.assertEqual(evaluate(out=self.root/'second',rubric=r2,evaluation=src,previous_assessment=self.root/'first/rubric',acknowledge_imported=True),0)
        self.assert_no_agents();self.llm.chat_json.assert_not_called();self.llm.check.assert_not_called();self.release.assert_not_called()
        a=read(self.root/'second/result.json');self.assertEqual(a['assessment']['max_points'],60);self.assertEqual(a['assessment']['assessed_weight_percent'],100);self.assertEqual(len(a['decision_reuse']['preserved_criterion_ids']),5)
        self.assertEqual(read(self.root/'first/rubric/decisions.json')['decisions'],read(self.root/'second/rubric/decisions.json')['decisions'])
    def test_existing_research_toggle_only_research_call(self):
        src=self.saved();r=load();evaluate(out=self.root/'first',rubric=r,evaluation=src,acknowledge_imported=True);self.llm.reset_mock();self.release.reset_mock()
        r2=configure_rubric(r,research_required=True);evaluate(out=self.root/'second',rubric=r2,evaluation=src,previous_assessment=self.root/'first/rubric',acknowledge_imported=True)
        self.assert_no_agents();self.llm.chat_json.assert_called_once();req=json.loads(self.llm.chat_json.call_args.args[1]);self.assertEqual([c['criterion_id'] for c in req['criteria']],['research']);a=read(self.root/'second/result.json');self.assertEqual(a['assessment']['assessed_weight_percent'],100)
        self.llm.reset_mock();evaluate(out=self.root/'third',rubric=r,evaluation=src,previous_assessment=self.root/'second/rubric',acknowledge_imported=True);self.llm.chat_json.assert_not_called();a=read(self.root/'third/result.json');self.assertEqual(a['assessment']['assessed_weight_percent'],100);self.assertIn('research',a['decision_reuse']['dropped_decisions'])
    def test_new_live_research_only_other_agents_preserved(self):
        self.live('first');self.integrity.reset_mock();self.executioner.reset_mock();self.summary.reset_mock();self.llm.reset_mock();self.release.reset_mock()
        r=configure_rubric(default(),research_required=True);src=self.root/'first/collection/evaluation.json';original=src.read_bytes()
        self.assertEqual(evaluate(out=self.root/'second',rubric=r,evaluation=src,previous_assessment=self.root/'first/rubric'),0)
        self.integrity.assert_not_called();self.executioner.assert_not_called();self.research.assert_called_once();self.summary.assert_not_called();self.legacy.assert_not_called()
        self.llm.chat_json.assert_called_once();req=json.loads(self.llm.chat_json.call_args.args[1]);self.assertEqual([c['criterion_id'] for c in req['criteria']],['research']);self.assertEqual(src.read_bytes(),original)
        self.assertEqual((self.root/'first/collection/native/integrity.json').read_bytes(),(self.root/'second/collection/native/integrity.json').read_bytes())
        a=read(self.root/'second/result.json');self.assertEqual(a['agent_actions']['research'],'COLLECTED');self.assertIn('problem_scope',a['decision_reuse']['preserved_criterion_ids'])
    def test_live_research_failure_keeps_existing_grades(self):
        self.live('first');self.integrity.reset_mock();self.executioner.reset_mock();self.llm.reset_mock()
        self.research.side_effect=RuntimeError('research unavailable')
        r=configure_rubric(default(),research_required=True);src=self.root/'first/collection/evaluation.json'
        rc=evaluate(out=self.root/'second',rubric=r,evaluation=src,previous_assessment=self.root/'first/rubric')
        self.assertEqual(rc,2);self.integrity.assert_not_called();self.executioner.assert_not_called();self.llm.chat_json.assert_not_called()
        a=read(self.root/'second/result.json');self.assertEqual(a['workflow_status'],'PARTIAL_ERROR');self.assertIn('research',a['agent_failures'])
        self.assertIn('research',a['assessment']['unresolved_criterion_ids']);self.assertIn('problem_scope',a['decision_reuse']['preserved_criterion_ids'])
    def test_frontend_ai_detection_contract_preserved(self):
        self.raw['integrity']['ai_detection']={'status':'COMPLETED','estimated_percent':12.5,'interpretation':'Advisory model estimate.'}
        self.live('new',objective_only=True);a=read(self.root/'new/result.json');self.assertEqual(a['agents']['integrity']['ai_detection']['estimated_percent'],12.5)
    def test_cli_config_weights_research_and_total(self):
        src=self.saved();weights=self.root/'weights.json';write(weights,{'problem':30,'report':5,'research':15})
        rc=main(['evaluate','--evaluation',str(src),'--weights',str(weights),'--total-points','60','--research-required','--acknowledge-imported','--objective-only','--out',str(self.root/'new')]);self.assertEqual(rc,0)
        a=read(self.root/'new/result.json');self.assertTrue(a['research_required']);self.assertEqual(a['assessment']['max_points'],60);self.assertEqual(a['rubric']['group_weights']['problem'],30);self.llm.check.assert_not_called();self.assert_no_agents()
    def test_missing_imported_research_requires_explicit_import(self):
        src=self.saved(research=False);r=configure_rubric(load(),research_required=True)
        with self.assertRaisesRegex(ValueError,'IMPORTED_RESEARCH_MISSING'):evaluate(out=self.root/'new',rubric=r,evaluation=src,acknowledge_imported=True)
        self.assert_no_agents();self.llm.chat_json.assert_not_called()
    def test_imported_research_supplement_preserves_other_receipts(self):
        src=self.saved(research=False);r=load();evaluate(out=self.root/'first',rubric=r,evaluation=src,acknowledge_imported=True);self.llm.reset_mock()
        artifact=self.root/'research.json';write(artifact,self.raw['research']);r2=configure_rubric(r,research_required=True)
        evaluate(out=self.root/'new',rubric=r2,evaluation=src,previous_assessment=self.root/'first/rubric',acknowledge_imported=True,research_artifact=artifact)
        self.assert_no_agents();self.llm.chat_json.assert_called_once();req=json.loads(self.llm.chat_json.call_args.args[1]);self.assertEqual([c['criterion_id'] for c in req['criteria']],['research'])
        a=read(self.root/'new/result.json');self.assertEqual(a['agent_actions']['research'],'IMPORTED');self.assertEqual(a['collection_mode'],'IMPORTED');self.assertEqual(a['assessment']['assessed_weight_percent'],100)
    def test_no_ack_does_not_turn_import_into_runtime_marks(self):
        src=self.saved();evaluate(out=self.root/'new',rubric=load(),evaluation=src,objective_only=True)
        r=read(self.root/'new/result.json');self.assertEqual(r['assessment']['assessed_weight_percent'],0);self.assertIsNone(r['assessment']['proposed_points']);self.llm.check.assert_not_called()
    def test_invalid_native_receipt_rejected(self):
        src=self.saved();(src.parent/'native/integrity.json').write_text('{}');
        with self.assertRaisesRegex(ValueError,'NATIVE_DIGEST_MISMATCH'):evaluate(out=self.root/'new',rubric=load(),evaluation=src,acknowledge_imported=True)
        self.llm.chat_json.assert_not_called()
    def test_failed_inference_partial_result(self):
        src=self.saved();self.llm.chat_json.side_effect=RuntimeError('synthetic model error');rc=evaluate(out=self.root/'new',rubric=load(),evaluation=src,acknowledge_imported=True)
        self.assertEqual(rc,1);r=read(self.root/'new/result.json');self.assertEqual(r['workflow_status'],'PARTIAL_ERROR');self.assertEqual(r['assessment']['assessed_weight_percent'],20);self.assertIsNone(r['assessment']['proposed_points']);self.release.assert_called_once()
    def test_failed_agent_visible_but_other_evidence_retained(self):
        self.integrity.side_effect=RuntimeError('synthetic error');self.assertEqual(self.live('new',objective_only=True),2);r=read(self.root/'new/result.json');self.assertEqual(r['workflow_status'],'PARTIAL_ERROR');self.assertIn('integrity',r['agent_failures']);self.assertEqual(r['agents']['integrity']['status'],'FAILED');self.assertEqual(r['agents']['executioner']['evidence_check'],'MATCHED')
    def test_cli_objective_preview_does_not_call_model(self):
        src=self.saved();rp=self.root/'rubric.json';write(rp,load().model_dump(mode='json'))
        rc=main(['evaluate','--evaluation',str(src),'--rubric',str(rp),'--acknowledge-imported','--preview','--out',str(self.root/'new')]);self.assertEqual(rc,0)
        self.assert_no_agents();self.llm.check.assert_not_called();r=read(self.root/'new/result.json');self.assertEqual(r['assessment']['assessed_weight_percent'],20);self.assertEqual(r['rubric_mode'],'PREVIEW')
    def test_collection_entrypoints_include_summary_by_default(self):
        for fn,name in ((main,'core'),(phase2_main,'phase2')):
            rc=fn(['collect','--report',str(self.report),'--title','Demo','--submission-id','demo','--repo','https://github.com/example/project','--ref',SHA,'--out',str(self.root/name)])
            self.assertEqual(rc,0);ev=read(self.root/name/'evaluation.json');self.assertEqual(ev['agents']['summarizer']['status'],'COMPLETED');self.assertEqual(ev['agents']['research']['status'],'NOT_RUN')
        self.summary.assert_not_called();self.legacy.assert_not_called();self.llm.check.assert_not_called();self.research.assert_not_called()
    def test_fresh_preview_still_produces_summary_without_rubric_inference(self):
        self.assertEqual(self.live('new',preview=True),0)
        self.summary.assert_not_called();self.legacy.assert_not_called();self.llm.chat_json.assert_not_called()
        r=read(self.root/'new/result.json');self.assertEqual(r['agents']['summarizer']['status'],'COMPLETED');self.assertEqual(r['rubric_mode'],'PREVIEW')
    def test_saved_missing_summary_stays_unavailable_no_hidden_inference(self):
        src=self.saved(legacy=False)
        self.assertEqual(evaluate(out=self.root/'new',rubric=load(),evaluation=src,acknowledge_imported=True,objective_only=True),0)
        self.assert_no_agents();self.llm.check.assert_not_called();r=read(self.root/'new/result.json')
        self.assertEqual(r['agent_actions']['summarizer'],'NOT_AVAILABLE');self.assertEqual(r['agents']['summarizer']['status'],'NOT_RUN')
    def test_summary_failure_preserves_other_agent_evidence(self):
        with patch('projectiq_core.phase2.hardening.grounded_summary',side_effect=RuntimeError('summary unavailable')):
            self.assertEqual(self.live('new',objective_only=True),2)
        r=read(self.root/'new/result.json');self.assertEqual(r['workflow_status'],'PARTIAL_ERROR');self.assertIn('summarizer',r['agent_failures']);self.assertEqual(r['agents']['executioner']['evidence_check'],'MATCHED');self.legacy.assert_not_called()
    def test_legacy_cli_flag_rejected_before_agents(self):
        rc=main(['collect','--report',str(self.report),'--title','Demo','--submission-id','demo','--samples','1','--out',str(self.root/'new')]);self.assertEqual(rc,1);self.assert_no_agents()


if __name__=='__main__':unittest.main()
