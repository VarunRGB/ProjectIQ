"""L9 focused offline regressions. No network, Ollama or VM execution."""
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from projectiq_core.collector import collect, make_submission
from projectiq_core.phase2.evidence import build_evidence, digest
from projectiq_core.phase2.hardening import (grounded_summary, report_facts, report_items,
    prerequisite, validate)
from projectiq_core.orchestrator import DEFAULT_RUBRIC
from projectiq_core.phase2.contracts import Rubric, Feedback
from projectiq_research.engineering_support import supporting_concerns
from test_projectiq_phase2 import raw_fixture

class TestL9(unittest.TestCase):
    def bundle(self, text):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name); report=root/'report.txt'; report.write_text(text,encoding='utf-8')
        sub,data=make_submission(report,'QA control','L9-CONTROL','https://github.com/example/project','a'*40)
        raw=raw_fixture(); collect(submission=sub,report_bytes=data,out=root/'saved',mode='LIVE',
            sources={a:lambda p,o,a=a:raw[a] for a in raw},progress=lambda _:None)
        return build_evidence(root/'saved/evaluation.json',loader=lambda p:p.read_text(encoding='utf-8'))
    def criterion(self,cid):
        r=Rubric.model_validate_json(Path(DEFAULT_RUBRIC).read_text(encoding='utf-8'))
        return next(c for c in r.criteria if c.id==cid)
    def test_sparse_but_real_design_and_negative_case(self):
        b=self.bundle('Library lending prototype\n\n1. Problem and objectives\nA small library tracks loans.\n\n2. Design\nThe report proposes book, member and loan tables. A loan links one book to one member.\n\n3. Proposed testing\nCheck loan creation, return and rejection of an unavailable book. No tests have been executed.')
        facts=report_facts(report_items(b)); self.assertTrue(facts['design_description']);self.assertTrue(facts['negative_checks']);self.assertTrue(facts['limitations'])
        self.assertIsNone(prerequisite(b,self.criterion('technical_design')))
        c=self.criterion('testing_strategy');level=next(l for l in c.levels if l.id=='adequate')
        refs=[e for e in report_items(b) if e.id in facts['test_scenarios']]
        fb=Feedback(strength='A scenario is described.',limitation='The strategy is incomplete, lacking explicit negative checks.',improvement='Specify more test inputs.',observed_failure=False)
        with self.assertRaisesRegex(ValueError,'NEGATIVE_CHECK_CONTRADICTION'):
            validate(b,c,level,refs,'Some scenarios are documented.',fb)
        fb=fb.model_copy(update={'limitation':'Negative cases are described, but exact input data is not specified.','improvement':'Add more negative checks and reproducible test inputs.'})
        validate(b,c,level,refs,'The loan rejection is a negative test.',fb)
    def test_limitation_contradiction(self):
        b=self.bundle('1. Testing\nCheck login with invalid credentials; requests must be rejected. No tests have been executed.')
        c=self.criterion('testing_strategy');level=next(l for l in c.levels if l.id=='adequate')
        refs=report_items(b);fb=Feedback(strength='A test case is proposed.',limitation='The plan is lacking explicit limitations.',improvement='Add reproducibility details.',observed_failure=False)
        with self.assertRaisesRegex(ValueError,'LIMITATION_CONTRADICTION'):validate(b,c,level,refs,'A test is documented.',fb)
    def test_empty_design_still_deferred(self):
        b=self.bundle('1. Design\nA system was proposed.\n2. Validation\nNo tests have been executed.')
        self.assertIsNotNone(prerequisite(b,self.criterion('technical_design')))
        self.assertIsNotNone(prerequisite(b,self.criterion('testing_strategy')))
    def test_summary_boundary_survives_many_sections(self):
        text='\n\n'.join(f'{i}. Design\nThe component stores a distinct record with identifier number {i}.' for i in range(1,11))+'\n\n11. Validation\nNo tests have been executed.'
        s=grounded_summary(text);self.assertIn('No tests have been executed.',s['summary']+' '.join(s['key_points']))
        self.assertLessEqual(len(s['key_points']),4)
        s=grounded_summary('Library lending prototype\n\n1. Proposed testing\nCheck loan creation, return and rejection of an unavailable book. No tests have been executed.')
        self.assertIn('rejection of an unavailable book',s['summary']+' '.join(s['key_points']))

    def test_support_triple_gate(self):
        d=NS(text='1. Design\nServer checks administrator permissions before updates.\nLiterature Review\nThe source [1] investigates access control.\nReferences\n[1] A title.')
        e=NS(literature_review=NS(found=True,start_line=3,end_line=4),references=NS(found=True,start_line=5,end_line=6))
        self.assertIn('authorization',supporting_concerns(d,e,'Access control in web systems',['Source [1] investigates access control.']))
        self.assertFalse(supporting_concerns(d,e,'Molecular structure of DNA',['Source [1] investigates access control.']))
        self.assertFalse(supporting_concerns(d,e,'Access control in web systems',[]))
        d.text=d.text.replace('Server checks administrator permissions before updates.','A simple application is described.')
        self.assertFalse(supporting_concerns(d,e,'Access control in web systems',['Source [1] investigates access control.']))
    def test_bad_section_offsets_fail_closed(self):
        d=NS(text='The server uses role-based access.');e=NS(references=NS(found=True,start_line=None,end_line=None),literature_review=NS(found=False))
        self.assertFalse(supporting_concerns(d,e,'Access control',['Access control [1]']))
    def test_checkpoint_after_interruption(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        report=root/'report.txt';report.write_text('A report about a small project.',encoding='utf-8')
        sub,data=make_submission(report,'QA recovery','L9-CHECKPOINT',None,None)
        raw=raw_fixture()['summarizer']
        # native fixture names are tied to the submission, not a foreign report.
        raw={**raw,'name':report.name}
        def cancelled(*args):raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):collect(submission=sub,report_bytes=data,out=root/'saved',mode='LIVE',sources={'summarizer':lambda *_:raw,'integrity':cancelled},progress=lambda _:None)
        self.assertTrue((root/'saved/collection_cancelled.json').is_file())
        b=build_evidence(root/'saved/evaluation.json',loader=lambda p:p.read_text(encoding='utf-8'))
        self.assertEqual(b.agent_checks['summarizer'],'MATCHED');self.assertEqual(b.agent_checks['integrity'],'FAILED')
