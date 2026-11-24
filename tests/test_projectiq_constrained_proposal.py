"""Regression tests of the actual dynamic response schema, using no live LLM."""
import json
import unittest
from unittest.mock import Mock
from pydantic import ValidationError
from test_projectiq_proposal_fix import fixture
from projectiq_core.phase2.suggest import propose
from projectiq_core.phase2.contracts import Rubric, Decision, DecisionSet
from projectiq_core.phase2.scoring import assess, empty_decisions

class ConstrainedProposal(unittest.TestCase):
    def setUp(self):
        self.b,self.r=fixture();self.log={}
    def preview(self, ids=('first',)):
        propose(self.b,self.r,None,criterion_ids=list(ids),preview=True,diagnostics=self.log)
    def run_wire(self, obj, ids=('first',), **kw):
        llm=Mock()
        def chat(system,prompt,schema,**kwargs):
            return schema.model_validate_json(json.dumps(obj))
        llm.chat_json.side_effect=chat
        return propose(self.b,self.r,llm,criterion_ids=list(ids),diagnostics=self.log,**kw)
    def row(self, choice='L2', evidence=None):
        return {'choice':choice,'evidence':{'REPORT_EXCERPT':(evidence or ['E1'])[0]} if evidence!=[] else {},'additional_evidence_ids':[],'rationale':'Report states the objective clearly.'}
    def test_schema_requires_choice_not_nullable(self):
        self.preview();s=self.log['response_schema'];row=s['$defs']['Proposal_C1'];self.assertEqual(row['properties']['choice']['enum'],['L1','L2','D']);self.assertIn('choice',row['required']);self.assertEqual(s['required'],['C1'])
    def test_exact_null_failure_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({'C1':self.row(None)})
    def test_missing_choice_rejected(self):
        row=self.row();del row['choice']
        with self.assertRaises(ValidationError):self.run_wire({'C1':row})
    def test_unknown_level_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({'C1':self.row('L99')})
    def test_unknown_evidence_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({'C1':self.row(evidence=['E99'])})
    def test_missing_criterion_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({})
    def test_extra_criterion_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({'C1':self.row(),'C2':self.row()})
    def test_valid_score_calculated(self):
        d=self.run_wire({'C1':self.row()});a=assess(self.b,self.r,d);self.assertEqual(a.assessed_subtotal_points,50);self.assertEqual(a.assessed_max_points,50);self.assertEqual(a.assessed_weight_percent,50);self.assertEqual(a.criteria[0].level_id,'full');self.assertIsNone(a.proposed_points)
    def test_full_score_is_proposed_not_reviewed(self):
        d=self.run_wire({'C1':self.row(),'C2':self.row('L1')},ids=('first','second'));a=assess(self.b,self.r,d);self.assertEqual(a.proposed_points,50);self.assertIsNone(a.reviewed_points);self.assertEqual(a.status,'READY_FOR_TEACHER_REVIEW')
    def test_explicit_defer(self):
        d=self.run_wire({'C1':self.row('D')});self.assertEqual(d.decisions[0].action,'DEFER');self.assertEqual(self.log['criteria']['first']['code'],'MODEL_DEFERRED')
    def test_defer_considered_slots_are_not_supporting_citations(self):
        d=self.run_wire({'C1':self.row('D')});self.assertEqual(self.log['criteria']['first']['code'],'MODEL_DEFERRED');self.assertEqual(d.decisions[0].action,'DEFER');self.assertEqual(d.decisions[0].evidence_ids,[])
    def test_empty_score_rejected(self):
        with self.assertRaises(ValidationError):self.run_wire({'C1':self.row(evidence=[])})
    def test_custom_level_named_D_not_confused_with_defer(self):
        raw=self.r.model_dump(mode='json');raw['criteria'][0]['levels'][1]['id']='D';self.r=Rubric.model_validate(raw)
        d=self.run_wire({'C1':self.row()});self.assertEqual(d.decisions[0].level_id,'D');self.assertEqual(d.decisions[0].action,'SCORE')
    def test_criterion_specific_level_enums(self):
        raw=self.r.model_dump(mode='json');raw['criteria'][1]['levels'].insert(1,dict(id='half',label='Half',fraction=.5,descriptor='Partial.'));self.r=Rubric.model_validate(raw)
        self.preview(('first','second'));defs=self.log['response_schema']['$defs'];self.assertEqual(defs['Proposal_C1']['properties']['choice']['enum'],['L1','L2','D']);self.assertEqual(defs['Proposal_C2']['properties']['choice']['enum'],['L1','L2','L3','D'])
    def test_retry_preserves_teacher(self):
        base=empty_decisions(self.b,self.r);base=DecisionSet(evidence_sha256=base.evidence_sha256,rubric_sha256=base.rubric_sha256,decisions=[Decision(criterion_id='first',source='MODEL',action='DEFER',rationale='previous'),Decision(criterion_id='second',source='TEACHER',action='SCORE',level_id='full',evidence_ids=[self.b.items[0].id],rationale='Teacher reviewed.')]);d=self.run_wire({'C1':self.row()},ids=('first','second'),existing=base,retry_deferred=True);self.assertEqual(assess(self.b,self.r,d).proposed_points,100);self.assertIn(base.decisions[1],d.decisions)

if __name__=='__main__':unittest.main()
