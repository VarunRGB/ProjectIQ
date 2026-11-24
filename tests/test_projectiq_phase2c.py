"""Offline Phase 2C acceptance: real contracts, synthetic runtime and mocked model only."""
import copy,json,tempfile,unittest,sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from test_projectiq_phase2 import criterion,rubric,LEVELS,SHA,raw_fixture
from test_projectiq_proposal_fix import fixture
from projectiq_core.phase2.contracts import Evidence, Rubric, Decision, DecisionSet, Feedback
from projectiq_core.phase2.evidence import section_chunks, build_evidence, digest
from projectiq_core.phase2.intelligence import select_candidates,runtime_inventory,runtime_summary,intelligence_report,consistency
from projectiq_core.phase2.scoring import assess,empty_decisions,reweight
from projectiq_core.phase2.suggest import propose,plan_batches,Suggestions
from projectiq_core.phase2.__main__ import main
from projectiq_core.collector import collect,make_submission

CLEAN='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
P=Path(__file__).resolve().parents[1]/'projectiq_core/phase2'
CHECKS=[('api','OC-3CB38E63233A37C1'),('auth','UNAUTHENTICATED'),('auth','REGISTRATION'),('auth','LOGIN'),('auth','AUTHENTICATED'),('db_postconditions','PC-ABDC90DD364F48F8'),('dependent_workflow','WORKFLOW'),('crud','CRUD_WORKFLOW')]
REPORT='''MINICOLLAB TEST FIXTURE\n1. Abstract\nThis is a task-management application.\n2. Problem statement\nUsers need to track tasks and deadlines. The objectives include registration and CRUD.\n3. Architecture\nPHP handlers and MySQL database store accounts and tasks.\n4. Implementation\nPrepared statements and password hashing are described.\n5. Testing\nTest the registration and CRUD workflows. Verify database state and reject unauthenticated access.\n6. Limitations\nAuthorization requires review. No full requirements coverage is claimed.\n7. Conclusion\nThe application is a prototype.'''


def ev(id,kind,label,text,data,eligible=True):
    return Evidence(id=id,agent='executioner',kind=kind,label=label,text=text,artifact_sha256='a'*64,locator='/records/'+id,provenance='IMPORTED_ACKNOWLEDGED',eligible_for_review=eligible,data=data)

def runtime(id,suite,check,outcome='PASSED',pointer=None,**kw):
    return ev(id,'TEST_OBSERVATION',check,f'{outcome} — {suite}',dict(scope='REVIEWED_SCENARIO_ONLY',suite=suite,check_id=check,outcome=outcome,commit_sha=SHA,observations=[pointer or '/suite/'+id])|kw)

def demo_bundle(failing=False):
    b,_=fixture();b.repository_commit=SHA;b.observations={'executioner':{'planned_counts':{'NOT_TESTED':10,'PASSED':1}}}
    b.items += [runtime('run'+str(i),s,c,'FAILED' if failing and i in (2,3,7) else 'PASSED') for i,(s,c) in enumerate(CHECKS)]
    b.items += [ev('space'+str(i),'STATIC_SOURCE','TRAILING_WHITESPACE','Trailing whitespace in source file.',{'code':'TRAILING_WHITESPACE','record_type':'quality','severity':'LOW'}) for i in range(12)]
    b.items += [ev('sec'+str(i),'STATIC_SOURCE','SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','No recognized local session guard; external guards not verified.',{'code':'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','record_type':'quality','severity':'HIGH','source_locations':[{'path':f'logic/{i}.php','line':5}]}) for i in range(2)]
    b.items += [ev('feature','STATIC_SOURCE','Registration','Account registration discovered',{'record_type':'features','origin':'source'})]
    for i,(a,z,t,heading) in enumerate(section_chunks(REPORT)):
        b.items.append(Evidence(id='report'+str(i),agent='report',kind='REPORT_EXCERPT',label=heading,text=t,artifact_sha256='b'*64,locator=f'extracted_text:{a}:{z}',provenance='REPORT_SNAPSHOT',eligible_for_review=True,data={'section':heading,'start':a,'end':z}))
    return b

def ratio(id='ratio',keys=None,group='validation',weight=1):
    return criterion(id=id,group=group,weight=weight,method='REVIEWED_RATIO',test_ids=keys or ['auth:LOGIN'],allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION'],levels=[])

def load(name='minicollab_hybrid'):
    return Rubric.model_validate_json((P/(name+'.rubric.json')).read_text())

class Intelligence(unittest.TestCase):
    def test_exact_section_spans(self):
        spans=list(section_chunks(REPORT));self.assertGreater(len(spans),5)
        for a,z,t,h in spans:self.assertEqual(REPORT[a:z],t)
        self.assertTrue(any('Testing' in h and 'database state' in t for a,z,t,h in spans))
    def test_paragraph_fallback_long(self):
        text=('Paragraph without headings. '*90+'\n\n')*4
        spans=list(section_chunks(text));self.assertEqual(''.join(t for _,_,t,_ in spans),text);self.assertTrue(all(len(t)<=1600 for _,_,t,_ in spans))
    def test_severity_over_whitespace_and_dedup(self):
        b=demo_bundle();c=next(c for c in load().criteria if c.id=='implementation_quality');allowed,best=select_candidates(b,c)
        self.assertEqual(best[0].id,'sec0');self.assertEqual(sum(e.label=='SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN' for e in best),1)
        self.assertTrue(any(e.id=='feature' for e in best));self.assertTrue(any(e.kind=='REPORT_EXCERPT' for e in best))
        log={};propose(b,load(),None,criterion_ids=[c.id],preview=True,diagnostics=log)
        finding=next(x for x in log['request']['evidence_by_id'].values() if x['label']=='SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN')
        self.assertEqual(finding['finding_instances'],2);self.assertEqual(finding['severity'],'HIGH');self.assertEqual(len(finding['source_locations']),2)
    def test_all_eight_runtime_observations_visible(self):
        b=demo_bundle();r=load('software_project_intelligent');log={};propose(b,r,None,criterion_ids=['functional_validation'],preview=True,diagnostics=log)
        self.assertEqual(sum(e['kind']=='TEST_OBSERVATION' for e in log['request']['evidence_by_id'].values()),8)
        rt=log['request']['criteria'][0]['runtime_inventory']['by_scope']['REVIEWED_SCENARIO_ONLY'];self.assertEqual(rt,dict(passed=8,failed=0,decisive=8,pass_rate=1.0))
        self.assertIsNone(log['request']['criteria'][0]['runtime_inventory']['requirements_coverage'])
    def test_more_than_twenty_runtime_counts_not_lost(self):
        b=demo_bundle();b.items += [runtime('more'+str(i),'api','check'+str(i)) for i in range(30)];r=load('software_project_intelligent');c=next(x for x in r.criteria if x.id=='functional_validation');_,best=select_candidates(b,c)
        self.assertLessEqual(len(best),20);self.assertEqual(runtime_summary(b)['by_scope']['REVIEWED_SCENARIO_ONLY']['passed'],38)
    def test_duplicate_conflicting_scenario_excluded(self):
        b=demo_bundle();b.items.append(runtime('conflict','auth','LOGIN','FAILED'));inv,ambiguous,_=runtime_inventory(b);self.assertNotIn('reviewed:auth:LOGIN',inv);self.assertIn('reviewed:auth:LOGIN',ambiguous);self.assertIsNone(assess(b,rubric(ratio())).proposed_points)
    def test_no_readiness_or_wrong_commit(self):
        b=demo_bundle();b.items += [runtime('readiness','readiness','READY'),runtime('wrong','auth','WRONG',commit_sha='b'*40)];inv,_,_=runtime_inventory(b);self.assertEqual(len(inv),8)
    def test_scope_separation(self):
        b=demo_bundle();b.items.append(ev('exact','TEST_OBSERVATION','Route','PASSED',{'scope':'EXACT_PLANNED_TEST','test_id':'T1','category':'HTTP_ROUTE','commit_sha':SHA,'outcome':'PASSED','observations':['/suite/run0']}));rt=runtime_summary(b);self.assertEqual(rt['by_scope']['EXACT_PLANNED_TEST']['passed'],1);self.assertEqual(rt['by_scope']['REVIEWED_SCENARIO_ONLY']['passed'],8);self.assertIn('exact:T1',rt['overlapping_keys'])
    def test_no_duplicate_group_reward(self):
        b=demo_bundle();r=rubric(ratio('a'),ratio('b'))
        with self.assertRaisesRegex(ValueError,'OVERLAPPING_TEST_RATIO_RULES'):assess(b,r)
    def test_overlapping_native_pointer_reward_refused(self):
        b=demo_bundle();b.items.append(runtime('alias','auth','OTHER',pointer='/suite/run3'));r=rubric(ratio('a'),ratio('b',['auth:OTHER']))
        with self.assertRaisesRegex(ValueError,'OVERLAPPING_TEST_RATIO_OBSERVATIONS'):assess(b,r)
    def test_minor_only_deduction_blocked(self):
        b=demo_bundle();r=load('software_project_intelligent');c=next(x for x in r.criteria if x.id=='implementation_quality');level=c.levels[2]
        with self.assertRaisesRegex(ValueError,'MINOR_FINDINGS_ONLY'):consistency(b,c,level,[next(x for x in b.items if x.id=='space0')],'Significant gaps due to formatting.')
    def test_unsupported_failure_claim_blocked(self):
        b=demo_bundle();r=load('software_project_intelligent');c=next(x for x in r.criteria if x.id=='functional_validation');refs=[x for x in b.items if x.id=='run5']
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_OBSERVED_FAILURE'):consistency(b,c,c.levels[1],refs,'Significant failing behavior evidenced by a passed scenario.')
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_OBSERVED_FAILURE'):consistency(b,c,c.levels[0],refs,'Some checks passed.')
    def test_actual_failure_claim_allowed(self):
        b=demo_bundle(True);c=next(x for x in load('software_project_intelligent').criteria if x.id=='functional_validation');refs=[x for x in b.items if x.id=='run3'];self.assertIsInstance(consistency(b,c,c.levels[0],refs,'Observed failures in login.'),list)
    def test_negated_failure_not_flagged(self):
        b=demo_bundle();c=next(x for x in load('software_project_intelligent').criteria if x.id=='functional_validation');refs=[x for x in b.items if x.id=='run3'];self.assertIsInstance(consistency(b,c,c.levels[2],refs,'No observed failures in selected scenarios.'),list)
    def test_review_flags_not_false_certainty(self):
        b=demo_bundle();c=next(x for x in load('software_project_intelligent').criteria if x.id=='implementation_quality');refs=[x for x in b.items if x.id=='sec0'];f=Feedback(strength='A handler exists.',limitation='A local guard was not recognized.',improvement='Review access controls.',observed_failure=False);flags=consistency(b,c,c.levels[2],refs,c.levels[2].descriptor,f);self.assertIn('RATIONALE_REPEATS_DESCRIPTOR',flags);self.assertIn('STATIC_FINDINGS_REQUIRE_CONTEXTUAL_REVIEW',flags)

class Hybrid(unittest.TestCase):
    def test_objective_calibration_strong_vs_weak(self):
        r=rubric(ratio(keys=['auth:LOGIN','auth:REGISTRATION','crud:CRUD_WORKFLOW']));self.assertEqual(assess(demo_bundle(),r).proposed_points,100);self.assertEqual(assess(demo_bundle(True),r).proposed_points,0)
    def test_scope_not_app_correctness(self):
        a=assess(demo_bundle(),rubric(ratio()));self.assertEqual(a.criteria[0].objective_details['scope'],'REVIEWED_SCENARIO_ONLY');self.assertIsNone(a.reviewed_points)
    def test_missing_scenario_not_zero(self):
        b=demo_bundle();b.items=[e for e in b.items if e.id!='run3'];a=assess(b,rubric(ratio()));self.assertIsNone(a.proposed_points);self.assertIsNone(a.criteria[0].earned_points)
    def test_acknowledgement_gate_objective(self):
        b=demo_bundle();b.items=[e.model_copy(update={'eligible_for_review':False}) if e.kind=='TEST_OBSERVATION' else e for e in b.items];self.assertIsNone(assess(b,rubric(ratio())).proposed_points)
    def test_coverage_distinct_from_failure(self):
        b=demo_bundle(True);r=rubric(criterion(method='REQUIREMENT_COVERAGE',requirement_map={'login':['reviewed:auth:LOGIN'],'missing':['reviewed:auth:MISSING']},allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION'],levels=[]));a=assess(b,r);self.assertEqual(a.proposed_points,50);self.assertEqual(a.criteria[0].objective_details['covered'],['login']);self.assertEqual(a.criteria[0].objective_details['unverified'],['missing'])
    def test_coverage_unavailable_when_truncated(self):
        b=demo_bundle();b.limitations=['Reviewed scenario evidence capped'];r=rubric(criterion(method='REQUIREMENT_COVERAGE',requirement_map={'login':['reviewed:auth:LOGIN']},allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION'],levels=[]));self.assertIsNone(assess(b,r).proposed_points)
    def test_namespaced_map_required(self):
        with self.assertRaises(ValueError):rubric(criterion(method='REQUIREMENT_COVERAGE',requirement_map={'login':['LOGIN']},allowed_evidence=['TEST_OBSERVATION'],required_evidence=['TEST_OBSERVATION'],levels=[]))
    def test_explicit_confirmation_required_for_reviewed_grade(self):
        b=demo_bundle();r=rubric(ratio());d=empty_decisions(b,r);d.decisions=[Decision(criterion_id='ratio',action='CONFIRM_TEST_RATIO',source='TEACHER',rationale='Reviewed scoped assertion and attribution.')];self.assertEqual(assess(b,r,d).reviewed_points,100)
    def test_hierarchical_group_and_child_weights(self):
        b=demo_bundle();r=load();a=assess(b,r);g={x['id']:x for x in a.groups};self.assertEqual(g['validation']['max_points'],22.22);self.assertEqual(a.assessed_weight_percent,20.0);self.assertEqual(a.assessed_subtotal_points,20.0);self.assertEqual(next(x for x in a.criteria if x.id=='research').status,'EXCLUDED')
    def test_exclusion_within_group(self):
        b=demo_bundle();r=load();raw=r.model_dump();next(c for c in raw['criteria'] if c['id']=='testing_strategy')['applicability']='EXCLUDED';r=Rubric.model_validate(raw);self.assertEqual(assess(b,r).assessed_weight_percent,22.22)
    def test_reweight_same_policy_no_model(self):
        b=demo_bundle();r=rubric(ratio());d=empty_decisions(b,r);raw=r.model_dump();raw['total_points']=60;raw['criteria'][0]['weight']=12;new=Rubric.model_validate(raw);rebound=reweight(b,r,new,d);self.assertEqual(assess(b,new,rebound).proposed_points,60)
    def test_reweight_rejects_policy_change(self):
        b=demo_bundle();r=rubric(ratio());raw=r.model_dump();raw['criteria'][0]['test_ids']=['auth:REGISTRATION'];new=Rubric.model_validate(raw)
        with self.assertRaisesRegex(ValueError,'REWEIGHT_POLICY_CHANGED'):reweight(b,r,new,empty_decisions(b,r))
    def test_reweight_rejects_stale_evidence(self):
        b=demo_bundle();r=rubric(ratio());d=empty_decisions(b,r);b.items.pop()
        with self.assertRaisesRegex(ValueError,'STALE_DECISIONS'):reweight(b,r,r,d)
    def test_feedback_required_by_wire(self):
        b=demo_bundle();r=load();llm=Mock();llm.chat_json.side_effect=lambda system,prompt,schema,**kw:schema.model_validate({'C1':{'choice':'L3','evidence_ids':['E1'],'rationale':'Report clear.'}})
        with self.assertRaises(ValueError):propose(b,r,llm,criterion_ids=['problem_scope'])
    def test_hybrid_full_mock_grade_two_batches(self):
        b=demo_bundle();r=load();d=empty_decisions(b,r);batches=plan_batches(b,r,existing=d);self.assertEqual(len(batches),2)
        llm=Mock()
        def wire(system,prompt,schema,**kw):
            req=json.loads(prompt);response={}
            for c in req['criteria']:
                aliases=[]
                for kind in c['required_evidence']:
                    aliases.append(next(a for a in c['candidate_evidence_ids'] if req['evidence_by_id'][a]['kind']==kind))
                for a in c['candidate_evidence_ids']:
                    if len(aliases)>=c['minimum_evidence']:break
                    if a not in aliases:aliases.append(a)
                response[c['criterion_alias']]={'choice':'L4','evidence':{k:v[0] for k,v in c['evidence_slots'].items()},'additional_evidence_ids':[],'feedback':{'strength':'Specific project objectives are documented.','limitation':'Some details need independent verification.','improvement':'Add explicit measurable criteria.','observed_failure':False}}
            return schema.model_validate(response)
        llm.chat_json.side_effect=wire
        for ids in batches:d=propose(b,r,llm,criterion_ids=ids,existing=d)
        a=assess(b,r,d);self.assertEqual(a.assessed_weight_percent,100);self.assertEqual(a.status,'READY_FOR_TEACHER_REVIEW');self.assertEqual(llm.chat_json.call_count,2);self.assertEqual(len(d.decisions),5);self.assertIsNone(a.reviewed_points)
    def test_objective_only_no_model_calls(self):
        b=demo_bundle();r=rubric(ratio());llm=Mock();d=propose(b,r,llm);llm.chat_json.assert_not_called();self.assertEqual(assess(b,r,d).proposed_points,100)
    def test_detailed_template_valid(self):
        r=load('minicollab_hybrid_detailed');self.assertEqual(len(r.criteria),20);self.assertEqual(sum(c.weight for c in r.criteria if c.group=='implementation'),100)

class NativeIntegration(unittest.TestCase):
    def test_real_receipt_normalization_synthetic_reviewed_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);report=root/'report.txt';report.write_text(REPORT,encoding='utf-8');sub,data=make_submission(report,'Demo','demo','https://github.com/example/project',SHA);raw=raw_fixture()['executioner'];raw['effective_mode']='REVIEWED';raw['suites']=[];raw['test_case_matrix']['reviewed_only_checks']=[]
            for suite,check in CHECKS:
                raw['suites'].append({'kind':suite,'state':'PASS','vm_verified':True,'cleanup':CLEAN,'observations':[{'id':check,'result':'PASS','status_code':200,'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':200,'actual':200}],'response_body':'SECRET BODY NOT COPIED'}]});raw['test_case_matrix']['reviewed_only_checks'].append({'suite':suite,'check_id':check,'outcome':'PASSED'})
            raw['code_quality']['findings']=[{'code':'SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN','severity':'HIGH','confidence':'MEDIUM','path':'logic/admin.php','line':5,'message':'No local guard recognized','recommendation':'Review server-side authorization.'}]
            out=root/'run';collect(submission=sub,report_bytes=data,out=out,mode='IMPORTED',sources={'executioner':lambda r,o:raw},progress=lambda _:None)
            b=build_evidence(out/'evaluation.json',acknowledge_imported=True,loader=lambda p:p.read_text(encoding='utf-8'));self.assertEqual(b.agent_checks['executioner'],'MATCHED');inv,_,_=runtime_inventory(b);self.assertEqual(len(inv),8);d=inv['reviewed:auth:LOGIN'].data;self.assertEqual(d['observation_details'][0]['status_code'],200);self.assertNotIn('SECRET BODY',json.dumps(d));f=next(e for e in b.items if e.label=='SEC_PHP_LOCAL_AUTH_GUARD_NOT_SEEN');self.assertEqual(f.data['severity'],'HIGH');self.assertEqual(f.data['source_locations'],[{'path':'logic/admin.php','line':5}]);self.assertEqual(assess(b,load()).assessed_weight_percent,20.0)

class CLIIntegration(unittest.TestCase):
    def run_cli(self,b,r,mode='propose-all',fail_second=False,preview=False):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rp=root/'rubric.json';rp.write_text(r.model_dump_json());out=root/'out';llm=Mock();calls=[]
            def wire(system,prompt,schema,**kw):
                calls.append(prompt)
                if fail_second and len(calls)==2:raise RuntimeError('synthetic inference failure')
                req=json.loads(prompt);result={}
                for c in req['criteria']:
                    ids=[next(a for a in c['candidate_evidence_ids'] if req['evidence_by_id'][a]['kind']==kind) for kind in c['required_evidence']]
                    for a in c['candidate_evidence_ids']:
                        if len(ids)>=c['minimum_evidence']:break
                        if a not in ids:ids.append(a)
                    result[c['criterion_alias']]={'choice':'L4','evidence':{k:v[0] for k,v in c['evidence_slots'].items()},'additional_evidence_ids':[],'feedback':{'strength':'Reviewed evidence identifies the application purpose.','limitation':'Some details remain uncertain.','improvement':'Document measurable acceptance conditions.','observed_failure':False}}
                return schema.model_validate(result)
            llm.chat_json.side_effect=wire
            modules={'projectiq_summarizer.config':NS(Settings=lambda **kw:NS(num_ctx=8192,seed=42,ollama_url='http://localhost:11434')),'projectiq_summarizer.llm':NS(OllamaClient=lambda settings:llm)}
            args=[mode,'--evaluation',str(root/'unused.json'),'--rubric',str(rp),'--out',str(out)]
            if preview:args.append('--preview')
            with patch('projectiq_core.phase2.__main__.build_evidence',return_value=b),patch.dict(sys.modules,modules),patch('projectiq_core.resources.release_ollama_model',return_value='UNLOAD_VERIFIED') as release:
                rc=main(args)
            return rc,json.loads((out/'assessment.json').read_text()),json.loads((out/'proposal_diagnostics.json').read_text()),llm,release
    def test_cli_preview_no_model(self):
        rc,a,d,llm,release=self.run_cli(demo_bundle(),load(),preview=True);self.assertEqual(rc,0);self.assertEqual(a['assessed_weight_percent'],20);self.assertEqual(len(d['batches']),2);llm.check.assert_not_called();release.assert_not_called()
    def test_cli_all_single_model_session(self):
        rc,a,d,llm,release=self.run_cli(demo_bundle(),load());self.assertEqual(rc,0);self.assertEqual(a['assessed_weight_percent'],100);llm.check.assert_called_once();release.assert_called_once();llm.http.close.assert_called_once();self.assertEqual(llm.chat_json.call_count,2)
    def test_cli_failed_second_batch_preserves_first(self):
        rc,a,d,llm,release=self.run_cli(demo_bundle(),load(),fail_second=True);self.assertEqual(rc,1);self.assertGreater(a['assessed_weight_percent'],20);self.assertLess(a['assessed_weight_percent'],100);self.assertEqual(d['status'],'INFERENCE_FAILED');release.assert_called_once()
    def test_cli_objective_only_no_model(self):
        rc,a,d,llm,release=self.run_cli(demo_bundle(),rubric(ratio()));self.assertEqual(rc,0);self.assertEqual(a['proposed_points'],100);llm.check.assert_not_called();release.assert_not_called()
    def test_required_failure_level_removed_from_wire(self):
        log={};propose(demo_bundle(),load('software_project_intelligent'),None,criterion_ids=['functional_validation'],preview=True,diagnostics=log);self.assertNotIn('L1',log['request']['criteria'][0]['levels'])
    def test_unsupported_quote_refused(self):
        b=demo_bundle();c=next(x for x in load().criteria if x.id=='problem_scope');f=Feedback(strength='The report states "invented quote not present anywhere".',limitation='Some details missing.',improvement='Add examples.',observed_failure=False)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_QUOTATION'):consistency(b,c,c.levels[2],[b.items[0]],'A report claim.',f)
    def test_static_exploit_claim_refused(self):
        b=demo_bundle();c=next(x for x in load().criteria if x.id=='implementation_quality');refs=[x for x in b.items if x.id=='sec0']
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_RUNTIME_EXPLOIT_CLAIM'):consistency(b,c,c.levels[2],refs,'A confirmed exploit exists.')

if __name__=='__main__':unittest.main()
