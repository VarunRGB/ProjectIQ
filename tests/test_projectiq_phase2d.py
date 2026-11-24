"""Phase 2D regression and calibration harness. Model calls are mocked here."""
import itertools,json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
from pydantic import ValidationError
from projectiq_core.phase2.contracts import Decision,DecisionSet,Feedback,Rubric
from projectiq_core.phase2.suggest import propose,recover_rejected,evidence_slots,Suggestions
from projectiq_core.phase2.scoring import assess,empty_decisions,reweight
from projectiq_core.phase2.intelligence import select_candidates
from projectiq_core.phase2.__main__ import main as phase2_main
from projectiq_core.phase2.calibration import CASES,IDS,run_calibration,calibration_bundle,calibration_rubric,main as calibrate_main
from test_projectiq_phase2c import demo_bundle,load,ev,ratio,rubric


def wire_row(c,choice='L3',**kw):
    return dict(choice=choice,evidence={k:v[0] for k,v in c['evidence_slots'].items()},additional_evidence_ids=[],feedback=dict(strength='Task ownership is documented.',limitation='Authorization guards require review.',improvement='Verify role checks with negative tests.',observed_failure=False))|kw


def mock_wire(system,prompt,schema,**kw):
    req=json.loads(prompt)
    return schema.model_validate({c['criterion_alias']:wire_row(c) for c in req['criteria']})


def bad_wire(cid='implementation_quality',ids=None):
    return Suggestions.model_validate({'decisions':[dict(criterion_id=cid,action='SCORE',level_id='adequate',evidence_ids=ids or ['E1'],rationale='Unverified judgment.',feedback=dict(strength='Some implementation.',limitation='Some concerns.',improvement='Improve implementation.',observed_failure=False))]})

class Coverage(unittest.TestCase):
    def setUp(self):self.b=demo_bundle();self.r=load();self.log={};self.cid='implementation_quality'
    def run_wire(self,fn):
        llm=Mock();llm.chat_json.side_effect=fn
        d=propose(self.b,self.r,llm,criterion_ids=[self.cid],diagnostics=self.log);return d,llm
    def test_report_and_static_both_structurally_required(self):
        d,_=self.run_wire(mock_wire);req=self.log['request'];c=req['criteria'][0];slots=c['evidence_slots']
        self.assertEqual(set(slots),{'STATIC_SOURCE','REPORT_EXCERPT'})
        for kind,aliases in slots.items():self.assertTrue(all(req['evidence_by_id'][a]['kind']==kind for a in aliases))
        self.assertEqual(self.log['criteria'][self.cid]['code'],'PROPOSAL_ACCEPTED')
        self.assertEqual({next(e.kind for e in self.b.items if e.id==i) for i in d.decisions[0].evidence_ids},{'STATIC_SOURCE','REPORT_EXCERPT'})
    def test_actual_previous_two_static_alias_shape_refused(self):
        def wrong(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];slots=c['evidence_slots'];row=wire_row(c);row['evidence']['REPORT_EXCERPT']=slots['STATIC_SOURCE'][0];return schema.model_validate({'C1':row})
        with self.assertRaises(ValidationError):self.run_wire(wrong)
    def test_missing_category_refused(self):
        def wrong(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];row=wire_row(c);del row['evidence']['REPORT_EXCERPT'];return schema.model_validate({'C1':row})
        with self.assertRaises(ValidationError):self.run_wire(wrong)
    def test_nonexistent_category_refused(self):
        def wrong(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];row=wire_row(c);row['evidence']['RESEARCH_RECORD']='E1';return schema.model_validate({'C1':row})
        with self.assertRaises(ValidationError):self.run_wire(wrong)
    def test_optional_aliases_dedup_without_adding_refs(self):
        def extra(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];row=wire_row(c);a=row['evidence']['STATIC_SOURCE'];row['additional_evidence_ids']=[a,a];return schema.model_validate({'C1':row})
        d,_=self.run_wire(extra);self.assertEqual(len(d.decisions[0].evidence_ids),2)
    def test_d_discards_considered_evidence(self):
        def defer(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];return schema.model_validate({'C1':wire_row(c,'D')})
        d,_=self.run_wire(defer);self.assertEqual(d.decisions[0].action,'DEFER');self.assertEqual(d.decisions[0].evidence_ids,[]);self.assertIsNone(d.decisions[0].feedback)
    def test_slot_partition_minimum_and_distinct_locators(self):
        # Exercise the actual partition logic with a mix of evidence kinds.
        candidates=[('R',self.b.items[0])]+[(f'S{i}',e) for i,e in enumerate([x for x in self.b.items if x.kind=='STATIC_SOURCE'][:5])]
        slots=evidence_slots(candidates,['STATIC_SOURCE','REPORT_EXCERPT'],4);self.assertEqual(len(slots),4)
        for chosen in itertools.product(*slots.values()):self.assertEqual(len(set(chosen)),4)
    def test_duplicate_native_locator_cannot_satisfy_minimum(self):
        e=self.b.items[0];e2=e.model_copy(update={'id':'duplicate'})
        with self.assertRaisesRegex(ValueError,'EVIDENCE_SLOT_COVERAGE_UNAVAILABLE'):evidence_slots([('E1',e),('E2',e2)],['REPORT_EXCERPT'],2)
    def test_invalid_alias_in_extras_refused(self):
        def wrong(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];return schema.model_validate({'C1':wire_row(c,additional_evidence_ids=['E999'])})
        with self.assertRaises(ValidationError):self.run_wire(wrong)
    def test_legacy_policy_with_multiple_kinds_uses_slots(self):
        raw=self.r.model_dump();next(c for c in raw['criteria'] if c['id']==self.cid)['feedback_required']=False
        r=Rubric.model_validate(raw);log={};propose(self.b,r,None,criterion_ids=[self.cid],preview=True,diagnostics=log)
        self.assertIn('evidence_slots',log['request']['criteria'][0])

class FeedbackAndRecovery(unittest.TestCase):
    def setUp(self):self.b=demo_bundle();self.r=load();self.cid='implementation_quality';self.log={}
    def rejected(self):
        llm=Mock();llm.chat_json.return_value=bad_wire();d=propose(self.b,self.r,llm,criterion_ids=[self.cid],diagnostics=self.log);self.assertEqual(self.log['criteria'][self.cid]['code'],'EVIDENCE_COVERAGE_NOT_MET');return d
    def test_balanced_implementation_context(self):
        c=next(c for c in self.r.criteria if c.id==self.cid)
        self.b.items.append(ev('endpoint','STATIC_SOURCE','/logic/admin.php','/logic/admin.php',{'record_type':'endpoints'}))
        _,best=select_candidates(self.b,c)
        self.assertTrue(any(e.kind=='REPORT_EXCERPT' and 'Implementation' in e.label for e in best))
        self.assertTrue(any(e.id=='feature' for e in best));self.assertTrue(any(e.id=='sec0' for e in best));self.assertFalse(any(e.id=='endpoint' for e in best[:4]))
    def test_testing_plan_prioritized(self):
        c=next(c for c in self.r.criteria if c.id=='testing_strategy');_,best=select_candidates(self.b,c);self.assertIn('Testing',best[0].label)
    def test_targeted_recovery_only_once_preserves_previous(self):
        d=self.rejected();llm=Mock();llm.chat_json.side_effect=mock_wire
        # Add a successful independent teacher decision.
        teacher=Decision(criterion_id='problem_scope',source='TEACHER',action='SCORE',level_id='adequate',evidence_ids=[self.b.items[0].id],rationale='Faculty reviewed scope.')
        d=DecisionSet(evidence_sha256=d.evidence_sha256,rubric_sha256=d.rubric_sha256,decisions=[*d.decisions,teacher]);new,failed=recover_rejected(self.b,self.r,llm,d,self.log)
        self.assertFalse(failed);self.assertIn(teacher,new.decisions);llm.chat_json.assert_called_once();req=json.loads(llm.chat_json.call_args.args[1]);self.assertEqual(len(req['criteria']),1);self.assertEqual(req['criteria'][0]['previous_validation_error'],'EVIDENCE_COVERAGE_NOT_MET');self.assertEqual(self.log['recovery_attempts'][0]['status'],'PROPOSAL_ACCEPTED')
    def test_second_rejection_stays_unassessed_no_loop(self):
        d=self.rejected();llm=Mock();llm.chat_json.return_value=bad_wire();new,failed=recover_rejected(self.b,self.r,llm,d,self.log);llm.chat_json.assert_called_once();self.assertFalse(failed);self.assertEqual(new.decisions[0].action,'DEFER')
        again,failed=recover_rejected(self.b,self.r,llm,new,self.log);self.assertEqual(again,new);llm.chat_json.assert_called_once()
    def test_recovery_transport_failure_preserves_original(self):
        d=self.rejected();llm=Mock();llm.chat_json.side_effect=RuntimeError('transport');new,failed=recover_rejected(self.b,self.r,llm,d,self.log);self.assertTrue(failed);self.assertEqual(new,d);self.assertEqual(self.log['recovery_attempts'][0]['status'],'RECOVERY_INFERENCE_FAILED')
    def test_no_retry_deliberate_deferral_or_precheck(self):
        d=empty_decisions(self.b,self.r);llm=Mock();log={'criteria':{'a':{'code':'MODEL_DEFERRED'},'b':{'code':'PRECHECK_COVERAGE_UNAVAILABLE'}}};new,failed=recover_rejected(self.b,self.r,llm,d,log);self.assertEqual(new,d);self.assertFalse(failed);llm.chat_json.assert_not_called()
    def test_deterministic_evidence_enrichment_no_new_selected_citations(self):
        llm=Mock();llm.chat_json.side_effect=mock_wire;d=propose(self.b,self.r,llm,criterion_ids=[self.cid]);a=assess(self.b,self.r,d);c=next(x for x in a.criteria if x.id==self.cid)
        self.assertEqual([x['evidence_id'] for x in c.evidence_details],c.evidence_ids)
        detail=next(x for x in c.evidence_details if x['kind']=='STATIC_SOURCE');self.assertEqual(detail['severity'],'HIGH');self.assertEqual(detail['finding_instances'],2);self.assertIn('source_locations',detail)
    def test_generic_feedback_flag_and_no_score_override(self):
        llm=Mock()
        def vague(system,prompt,schema,**kw):
            c=json.loads(prompt)['criteria'][0];return schema.model_validate({'C1':wire_row(c,feedback=dict(strength='Implementation is plausible.',limitation='Significant engineering gaps.',improvement='Address the identified issues.',observed_failure=False))})
        llm.chat_json.side_effect=vague;d=propose(self.b,self.r,llm,criterion_ids=[self.cid]);a=assess(self.b,self.r,d);c=next(x for x in a.criteria if x.id==self.cid);self.assertIn('IMPROVEMENT_NEEDS_SPECIFIC_ACTION',c.review_flags);self.assertEqual(c.level_id,'adequate')
    def test_objective_only_and_reweight_are_model_free(self):
        r=rubric(ratio());d=empty_decisions(self.b,r);raw=r.model_dump();raw['total_points']=40;new=Rubric.model_validate(raw);a=assess(self.b,new,reweight(self.b,r,new,d));self.assertEqual(a.proposed_points,40);self.assertEqual(len(a.criteria[0].evidence_details),1)

    def test_complete_hybrid_reweight_preserves_model_decisions(self):
        llm=Mock();llm.chat_json.side_effect=mock_wire;d=empty_decisions(self.b,self.r)
        from projectiq_core.phase2.suggest import plan_batches
        for ids in plan_batches(self.b,self.r,existing=d):d=propose(self.b,self.r,llm,criterion_ids=ids,existing=d)
        before=assess(self.b,self.r,d);self.assertEqual(before.assessed_weight_percent,100);calls=llm.chat_json.call_count
        raw=self.r.model_dump();raw['total_points']=60;new=Rubric.model_validate(raw)
        rebound=reweight(self.b,self.r,new,d);after=assess(self.b,new,rebound)
        self.assertEqual(after.assessed_weight_percent,100);self.assertEqual(d.decisions,rebound.decisions)
        self.assertAlmostEqual(after.proposed_points,before.proposed_points*.6,places=2);self.assertEqual(llm.chat_json.call_count,calls)
    def test_cli_auto_recovery_preserves_batch_and_finishes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rp=root/'rubric.json';rp.write_text(self.r.model_dump_json());out=root/'out';llm=Mock();calls=[]
            def chat(system,prompt,schema,**kw):
                req=json.loads(prompt);calls.append([c['criterion_id'] for c in req['criteria']])
                if len(calls)==2:
                    # Simulate a structurally valid response with an unsupported
                    # exploit claim, so only the implementation judgment is rejected.
                    values={c['criterion_alias']:wire_row(c) for c in req['criteria']}
                    values['C1']['feedback']['limitation']='A confirmed exploit exists.'
                    return schema.model_validate(values)
                return mock_wire(system,prompt,schema,**kw)
            llm.chat_json.side_effect=chat
            modules={'projectiq_summarizer.config':NS(Settings=lambda **kw:NS(num_ctx=8192,seed=42,ollama_url='http://localhost:11434')),'projectiq_summarizer.llm':NS(OllamaClient=lambda settings:llm)}
            with patch('projectiq_core.phase2.__main__.build_evidence',return_value=self.b),patch.dict(sys.modules,modules),patch('projectiq_core.resources.release_ollama_model',return_value='UNLOAD_VERIFIED') as release:
                rc=phase2_main(['propose-all','--evaluation',str(root/'unused'),'--rubric',str(rp),'--out',str(out)])
            self.assertEqual(rc,0);self.assertEqual(calls[-1],['implementation_quality']);self.assertEqual(len(calls),3);release.assert_called_once()
            a=json.loads((out/'assessment.json').read_text());self.assertEqual(a['assessed_weight_percent'],100)
            log=json.loads((out/'proposal_diagnostics.json').read_text());self.assertEqual(log['batches'][1]['recovery_attempts'][0]['status'],'PROPOSAL_ACCEPTED')

class Calibration(unittest.TestCase):
    def model(self,choices):
        llm=Mock();it=iter(choices)
        def chat(system,prompt,schema,**kw):
            choice=next(it);req=json.loads(prompt);return schema.model_validate({c['criterion_alias']:wire_row(c,choice) for c in req['criteria']})
        llm.chat_json.side_effect=chat;return llm
    def test_preview_no_inference(self):
        llm=Mock();result=run_calibration(llm,preview=True);self.assertEqual(result['status'],'PREVIEW_ONLY');self.assertEqual(set(result['cases']),set(CASES));llm.chat_json.assert_not_called()
    def test_mock_contrast_passes(self):
        llm=self.model(['L2','L3','L5']);r=run_calibration(llm);self.assertEqual(r['status'],'PASS');self.assertEqual(llm.chat_json.call_count,3)
    def test_constant_model_fails_calibration(self):
        r=run_calibration(self.model(['L3','L3','L3']));self.assertEqual(r['status'],'CALIBRATION_REVIEW_REQUIRED');self.assertTrue(all(not x['ordering_pass'] for x in r['checks']))
    def test_inverted_model_fails_calibration(self):
        r=run_calibration(self.model(['L5','L3','L1']));self.assertEqual(r['status'],'CALIBRATION_REVIEW_REQUIRED')
    def test_calibration_cli_preview_writes_report(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'out';self.assertEqual(calibrate_main(['--preview','--out',str(out)]),0);r=json.loads((out/'calibration.json').read_text());self.assertEqual(r['status'],'PREVIEW_ONLY')
    def test_calibration_is_same_rubric_and_no_runtime_claim(self):
        r=calibration_rubric();self.assertEqual({c.id for c in r.criteria},set(IDS));self.assertTrue(all(c.method=='JUDGMENT' for c in r.criteria))
        for name in CASES:
            b=calibration_bundle(name);self.assertTrue(all(e.kind=='REPORT_EXCERPT' for e in b.items));self.assertEqual(assess(b,r).assessed_weight_percent,0)

if __name__=='__main__':unittest.main()
