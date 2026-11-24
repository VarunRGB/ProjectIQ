"""Offline regressions for the local proposal retry and diagnostics path."""
import json
import unittest
from unittest.mock import Mock
from projectiq_core.phase2.suggest import propose, Suggestions
from projectiq_core.phase2.contracts import Evidence, EvidenceBundle, Rubric, Decision, DecisionSet
from projectiq_core.phase2.scoring import assess, empty_decisions


def fixture():
    levels=[dict(id='zero',label='Zero',fraction=0,descriptor='Evidence establishes no achievement.'),dict(id='full',label='Full',fraction=1,descriptor='Evidence supports full achievement.')]
    rubric=Rubric.model_validate(dict(id='demo',title='Demo',total_points=100,criteria=[dict(id=i,title=i,description='Assess stated objectives',group='report',weight=1,levels=levels,required_evidence=['REPORT_EXCERPT']) for i in ['first','second']]))
    bundle=EvidenceBundle(evaluation_sha256='a'*64,submission_id='demo',report_sha256='b'*64,report_extraction='COMPLETE',agent_checks={},items=[Evidence(id='E-'+'c'*24,agent='report',kind='REPORT_EXCERPT',label='Report',text='The stated objective is to manage tasks.',artifact_sha256='b'*64,locator='extracted_text:0:39',provenance='REPORT_SNAPSHOT',eligible_for_review=True)],observations={},limitations=[])
    return bundle,rubric


def response(cid='first',action='SCORE',level='full',ids=None):
    return dict(criterion_id=cid,action=action,level_id=level,evidence_ids=['E1'] if ids is None else ids,rationale='The report explicitly states its objective.')


class ProposalFix(unittest.TestCase):
    def run_response(self,*rows,**kwargs):
        b,r=fixture();llm=Mock();llm.chat_json.return_value=Suggestions.model_validate({'decisions':list(rows)});log={}
        d=propose(b,r,llm,criterion_ids=['first'],diagnostics=log,**kwargs)
        return b,r,d,log,llm
    def test_short_alias_resolves_to_exact_evidence(self):
        b,r,d,log,llm=self.run_response(response());self.assertEqual(d.decisions[0].evidence_ids,[b.items[0].id]);self.assertEqual(assess(b,r,d).assessed_weight_percent,50);self.assertEqual(log['criteria']['first']['code'],'PROPOSAL_ACCEPTED');self.assertEqual(log['model_response']['decisions'][0]['evidence_ids'],['E1']);self.assertEqual(llm.chat_json.call_count,1)
        prompt=json.loads(llm.chat_json.call_args.args[1]);self.assertNotIn('weight',prompt['criteria'][0]);self.assertIn('E1',prompt['evidence_by_id'])
    def test_unknown_alias_rejected_with_reason(self):
        _,_,d,log,_=self.run_response(response(ids=['E99']));self.assertEqual(d.decisions[0].action,'DEFER');self.assertEqual(log['criteria']['first']['code'],'EVIDENCE_NOT_IN_CRITERION_CANDIDATES')
    def test_invalid_level(self):
        _,_,d,log,_=self.run_response(response(level='Excellent'));self.assertEqual(d.decisions[0].action,'DEFER');self.assertEqual(log['criteria']['first']['code'],'UNKNOWN_RUBRIC_LEVEL')
    def test_explicit_defer_retains_rationale(self):
        _,_,d,log,_=self.run_response(response(action='DEFER',level=None,ids=[]));self.assertEqual(log['criteria']['first']['code'],'MODEL_DEFERRED');self.assertEqual(d.decisions[0].rationale,'The report explicitly states its objective.')
    def test_invalid_defer(self):
        _,_,d,log,_=self.run_response(response(action='DEFER'));self.assertEqual(log['criteria']['first']['code'],'INVALID_DEFER_SHAPE')
    def test_missing_and_unexpected(self):
        _,_,d,log,_=self.run_response(response(cid='second'));self.assertEqual(log['criteria']['first']['code'],'MISSING_MODEL_DECISION');self.assertEqual(log['unexpected_criterion_ids'],['second'])
    def test_duplicate_decisions(self):
        _,_,d,log,_=self.run_response(response(),response());self.assertEqual(log['criteria']['first']['code'],'DUPLICATE_MODEL_DECISION')
    def test_alias_and_full_id_cannot_double_count(self):
        b,_=fixture();_,_,d,log,_=self.run_response(response(ids=['E1',b.items[0].id]));self.assertEqual(log['criteria']['first']['code'],'DUPLICATE_EVIDENCE_REFERENCE')
    def test_preview_does_not_call_model_or_change_decisions(self):
        b,r=fixture();llm=Mock();log={};base=empty_decisions(b,r);d=propose(b,r,llm,criterion_ids=['first'],existing=base,preview=True,diagnostics=log);self.assertEqual(d,base);llm.chat_json.assert_not_called();self.assertEqual(log['mode'],'PREVIEW');self.assertEqual(log['criteria']['first']['code'],'PREPARED')
    def test_retry_only_selected_model_deferral(self):
        b,r=fixture();base=empty_decisions(b,r);old=DecisionSet(evidence_sha256=base.evidence_sha256,rubric_sha256=base.rubric_sha256,decisions=[Decision(criterion_id='first',source='MODEL',action='DEFER',rationale='old'),Decision(criterion_id='second',source='TEACHER',action='DEFER',rationale='teacher review')]);llm=Mock();llm.chat_json.return_value=Suggestions.model_validate({'decisions':[response()]});d=propose(b,r,llm,criterion_ids=['first','second'],existing=old,retry_deferred=True);byid={x.criterion_id:x for x in d.decisions};self.assertEqual(byid['first'].action,'SCORE');self.assertEqual(byid['second'],old.decisions[1]);self.assertEqual(len(d.decisions),2)
    def test_no_automatic_retry(self):
        b,r=fixture();base=empty_decisions(b,r);base.decisions=[Decision(criterion_id='first',source='MODEL',action='DEFER',rationale='old')];llm=Mock();self.assertEqual(propose(b,r,llm,criterion_ids=['first'],existing=base),base);llm.chat_json.assert_not_called()
    def test_missing_coverage_no_model(self):
        b,r=fixture();b.items=[];llm=Mock();log={};d=propose(b,r,llm,criterion_ids=['first'],diagnostics=log);llm.chat_json.assert_not_called();self.assertEqual(log['criteria']['first']['code'],'PRECHECK_COVERAGE_UNAVAILABLE');self.assertEqual(d.decisions[0].action,'DEFER')

if __name__=='__main__':unittest.main()
