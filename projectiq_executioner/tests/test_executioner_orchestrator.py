"""Offline one-call Executioner acceptance: no GitHub network and no QEMU launch.

Uses synthetic, typed phase outputs to test workflow-level decisions. The
real Phase 1–6 modules also retain their own independent regression suites.
"""
from __future__ import annotations

import json
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from projectiq_executioner.orchestration import OperatorContext,evaluate
from projectiq_executioner.orchestration.contracts import (ATTESTATION,CLEAN,EXECUTED,
    OrchestrationError,read_json,validate_request,write_json)
from projectiq_executioner.orchestration import core,runners

SHA='c4f7965d16807fa05c3d7ffeee54f86fee4d0a37'
SRC={'kind':'github','repository':'VarunRGB/Mini-Task-Collaboration-App','commit_sha':SHA}
URL='https://github.com/VarunRGB/Mini-Task-Collaboration-App'
CAND={'schema_version':'0.3','stage':'vm_execution_candidate','source':SRC,
      'adapter':'php_mysql_v1','status':'CANDIDATE_NEEDS_VM_VERIFICATION'}


def request(mode='STATIC',**kwargs):
    r={'submission_id':'SUB-001','repository_url':URL,'ref':SHA,'mode':mode}
    r.update(kwargs)
    return r


def sample_plan():
    return {'source':dict(SRC),'status':'GENERATED','summary':{
        'total':1,'ready_for_phase4':1,'needs_oracle':0,'unsupported_by_runner':0},
        'issues':[],'test_cases':[{'id':'TC-0123456789ABCDEF','category':'HTTP_READINESS',
        'title':'GET / readiness','status':'READY_FOR_PHASE4',
        'target':{'method':'GET','path':'/'},
        'oracle':{'status':'GROUNDED','basis':'PHASE2_ADAPTER_READINESS_CONTRACT'}}]}


def quality_report():
    return {'source':{'kind':'github_pinned','repository':SRC['repository'],'commit_sha':SHA},
        'status':'ANALYZED','summary':{'findings':1,'analyzed_files':2,'source_candidates':2},
        'coverage':{'source_ratio':1.0,'function_file_ratio':0.5},
        'security':{'status':'NO_INDICATORS_FOUND','findings':[]},
        'quality':{'score':None,'confidence':'NONE','basis':'HEURISTIC_NOT_A_GRADE',
                   'status':'PROVISIONAL','reason_codes':[]},
        'findings':[{'severity':'LOW','code':'EXAMPLE','path':'a.php','line':1,
                     'recommendation':'Review bounded code pattern','confidence':'LOW'}],
        'functions':[],'duplications':[],'limitations':['Test limitation']}


def manifest_report():
    return {'source':dict(SRC),'project_name':'MiniCollab','architecture':'PHP_WEB',
            'architecture_confidence':'MEDIUM','languages':{'PHP':3},
            'technologies':[{'name':'PHP','category':'backend','confidence':'HIGH'}],
            'services':[],'features':[],'routes':[{'path':'/'}],
            'tests':[],'inventory':{'indexed_files':3,'file_role_counts':{}},
            'findings':[],'unknowns':['Architecture not executed']}


def phase6_report():
    return {'status':'PARTIAL','summary':{'runs':0,'diagnoses':1},
            'events':[],'diagnoses':[{'priority':'P3','code':'NO_EXECUTION_EVIDENCE',
                'category':'INSUFFICIENT_EVIDENCE','title':'No execution provided',
                'explanation':'No runtime witness','recommended_action':'Supply a reviewed VM run',
                'confidence':'HIGH','causal_status':'UNPROVEN',
                'student_defect':'NOT_ESTABLISHED','evidence':['PLAN'],'test_ids':[]}],
            'dependency_groups':[],'rerun_comparisons':[],'root_cause_hypotheses':[],
            'static_context':[],'security_context':[],'limitations':[]}


class Stubs:
    def __init__(self):
        self.run_calls=[];self.phases=[];self.fail_quality=False
        self.api_status='TESTS_PASSED';self.vm_cleanup=CLEAN
        self.vm_source=dict(SRC);self.vm_execution=EXECUTED
        self.approved=True;self.preflight_fail=set();self.record_run=True
        self.plan=sample_plan();self.quality=quality_report();self.manifest=manifest_report()
        self.current_suite=None

    @contextmanager
    def pinned(self,url,ref):
        self.phases.append('ingest')
        with tempfile.TemporaryDirectory() as d:
            yield dict(SRC),Path(d)

    def inspect(self,checkout,src,dest):
        self.phases.append('phase1')
        write_json(dest/'manifest.json',self.manifest)
        (dest/'manifest.md').write_text('# Phase 1\n',encoding='utf-8')
        return self.manifest

    def phase2(self,manifest,dest):
        self.phases.append('phase2')
        write_json(dest/'phase2c_candidate.json',CAND)
        return dict(CAND),{'status':'SUPPORTED_PILOT'}

    def phase3(self,manifest,candidate,checkout,dest):
        self.phases.append('phase3')
        write_json(dest/'test_plan.json',self.plan)
        return self.plan

    def phase5(self,checkout,source,dest):
        self.phases.append('phase5')
        if self.fail_quality:raise ValueError('synthetic')
        write_json(dest/'code_quality.json',self.quality)
        (dest/'code_quality.md').write_text('# Phase 5\n',encoding='utf-8')
        return self.quality

    def phase6(self,**kwargs):
        self.phases.append('phase6')
        report=phase6_report()
        report['status']='DIAGNOSED' if kwargs.get('runs') else 'PARTIAL'
        write_json(kwargs['dest']/'phase6_diagnosis.json',report)
        (kwargs['dest']/'phase6_diagnosis.md').write_text('# Phase 6\n',encoding='utf-8')
        return report

    def config(self,path):
        return {'approved_snapshots':[SRC['repository']+'@'+SHA] if self.approved else []}

    def preflight(self,kind,**kwargs):
        self.phases.append('preflight:'+kind)
        return {'approved':kind not in self.preflight_fail,
                'code':'CATALOG_NOT_APPROVED' if kind in self.preflight_fail else 'APPROVED'}

    def catalogs(self,spec,root):
        self.phases.append('catalogs:'+spec['kind'])
        return {k:{'schema_version':'0.1','tag':k,'kind':spec['kind']}
                for k in spec['catalogs']}

    def invoke(self,kind,**kwargs):
        self.run_calls.append(kind)
        dest=kwargs['output_dir']
        if dest.exists():raise AssertionError('Orchestrator must pass a fresh suite directory')
        name,vmfile=runners.output_files(kind)
        status=self.api_status
        statuses={'api':status,'browser_ui':'BROWSER_PASSED','readiness':'TESTS_PASSED',
                  'crud':'CRUD_PASSED','auth':'TESTS_PASSED'}
        st=statuses.get(kind,'TESTS_PASSED')
        vm={'source':dict(self.vm_source),'adapter':'php_mysql_v1',
            'backend':'QEMU_DISPOSABLE_QCOW2_VM','security_attestation':ATTESTATION,
            'execution':self.vm_execution,'cleanup':self.vm_cleanup,'status':'STARTUP_VERIFIED',
            **{k:'VALIDATED' for k in core.VM_PROOF_FIELDS.values()}}
        if vmfile:write_json(dest/vmfile,vm)
        data={'source':dict(SRC),'adapter':'php_mysql_v1','security_attestation':ATTESTATION,
              'stage':'reviewed_vm_api_testing' if kind=='api' else 'reviewed_browser_ui',
              'status':st,'cleanup':self.vm_cleanup,'execution':self.vm_execution,
              'results':[{'test_id':'TC-0123456789ABCDEF','result':'PASS','http_status':200}],
              'summary':{'total':1,'passed':1,'failed':0,'inconclusive':0},
              'limitations':[]}
        if kind=='browser_ui':data['checks']=[{'id':'CHECK1','result':'PASS','status':'PASS'}]
        if self.record_run:write_json(dest/name,data)
        return data


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir=Path(self.tmp.name)
        self.stub=Stubs()
        patches=[
           patch.object(core.adapters,'pinned_checkout',self.stub.pinned),
           patch.object(core.adapters,'inspect_source',self.stub.inspect),
           patch.object(core.adapters,'compile_phase2',self.stub.phase2),
           patch.object(core.adapters,'compile_phase3',self.stub.phase3),
           patch.object(core.adapters,'analyze_quality',self.stub.phase5),
           patch.object(core.adapters,'diagnose',self.stub.phase6),
           patch.object(core.adapters,'operator_vm_config',self.stub.config),
           patch.object(core.runners,'load_catalogs',self.stub.catalogs),
           patch.object(core.runners,'preflight',self.stub.preflight),
           patch.object(core.runners,'invoke',self.stub.invoke),
        ]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.operator=OperatorContext(vm_config=self.dir/'vm.json',review_root=self.dir)
        self.base_spec={'kind':'api','catalogs':{'oracle':'reviews/api_oracle.json'}}

    def go(self,mode='STATIC',name='assessment',**params):
        return evaluate(request(mode,**params),output_dir=self.dir/name,
                        operator=self.operator)

    def test_static_one_download_all_static_phases_no_vm(self):
        res=self.go()
        self.assertEqual(res['status'],'COMPLETED_WITH_LIMITATIONS')
        self.assertEqual(res['vm_attempts_scheduled'],0)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(self.stub.phases.count('ingest'),1)
        self.assertTrue((self.dir/'assessment'/'executioner_report.md').is_file())
        self.assertTrue((self.dir/'assessment'/'executioner_result.json').is_file())
        self.assertIn('phase5/code_quality.json',res['artifacts'])
        self.assertIn('Phase 5', (self.dir/'assessment'/'executioner_report.md').read_text())

    def test_static_missing_quality_keeps_phase1_and_plan(self):
        self.stub.fail_quality=True
        res=self.go()
        self.assertTrue(res['summary']['has_project_intelligence'])
        self.assertTrue(res['summary']['has_test_plan'])
        self.assertFalse(res['summary']['has_code_quality'])
        self.assertIn('QUALITY_STAGE_VALUEERROR',res['error_codes'])

    def test_reviewed_api_success_clean_vm(self):
        res=self.go('REVIEWED',suites=[self.base_spec],max_vm_launches=1)
        self.assertEqual(res['status'],'COMPLETED')
        self.assertEqual(res['suites'][0]['state'],'PASS')
        self.assertTrue(res['suites'][0]['vm_verified'])
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertEqual(res['vm_attempts_scheduled'],1)
        self.assertIn('phase4/api/api_run.json',res['artifacts'])

    def test_reviewed_genuine_failed_test_is_completed_evaluation(self):
        self.stub.api_status='TESTS_FAILED'
        res=self.go('REVIEWED',suites=[self.base_spec],max_vm_launches=1)
        self.assertEqual(res['status'],'COMPLETED')
        self.assertEqual(res['suites'][0]['state'],'FAIL')
        self.assertTrue(res['suites'][0]['vm_verified'])

    def test_cleanup_failure_holds_remaining_approved_suites(self):
        self.stub.vm_cleanup='NOT_VERIFIED'
        specs=[self.base_spec,{'kind':'browser_ui','catalogs':{'browser':'reviews/browser.json'}}]
        res=self.go('REVIEWED',suites=specs,max_vm_launches=4)
        self.assertEqual(res['status'],'SECURITY_HOLD')
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertEqual(res['suites'][1]['state'],'BLOCKED')

    def test_wrong_source_vm_halts_everything(self):
        self.stub.vm_source={**SRC,'commit_sha':'a'*40}
        res=self.go('REVIEWED',suites=[self.base_spec],max_vm_launches=1)
        self.assertEqual(res['status'],'SECURITY_HOLD')

    def test_bad_run_provenance_is_security_hold(self):
        original=self.stub.invoke
        def corrupted(kind,**kwargs):
            d=original(kind,**kwargs)
            d['source']={**SRC,'commit_sha':'f'*40}
            return d
        with patch.object(core.runners,'invoke',corrupted):
            res=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(res['status'],'SECURITY_HOLD')

    def test_no_operator_vm_config_returns_static_with_review(self):
        self.operator=OperatorContext()
        res=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(res['status'],'NEEDS_REVIEW')
        self.assertEqual(self.stub.run_calls,[])
        self.assertTrue(res['code_quality'])

    def test_not_allowlisted_returns_review_request_no_vm(self):
        self.stub.approved=False
        res=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(res['status'],'NEEDS_REVIEW')
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(res['review_requests'][0]['type'],'EXACT_SHA_OPERATOR_APPROVAL_REQUIRED')

    def test_different_unapproved_branch_ref_never_executes(self):
        res=self.go('REVIEWED',ref='main',suites=[self.base_spec])
        self.assertEqual(res['status'],'NEEDS_REVIEW')
        self.assertEqual(self.stub.run_calls,[])

    def test_bad_catalog_preflight_never_launches_vm(self):
        self.stub.preflight_fail={'api'}
        res=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(res['status'],'NEEDS_REVIEW')
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(res['suites'][0]['state'],'REVIEW_REQUIRED')

    def test_phase4_only_ready_readiness_on_reviewed_without_suite(self):
        res=self.go('REVIEWED',max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,['readiness'])
        self.assertEqual(res['suites'][0]['kind'],'readiness')

    def test_denied_qemu_preflight_is_review_not_functional_pass(self):
        # Reproduces an actual Windows worker denial with missing QEMU tools.
        def denied(kind,**kwargs):
            dest=kwargs['output_dir']
            run_name,vm_name=runners.output_files(kind)
            self.assertEqual(kind,'api')
            write_json(dest/vm_name,{
                'source':dict(SRC),'adapter':'php_mysql_v1',
                'backend':'QEMU_DISPOSABLE_QCOW2_VM',
                'security_attestation':ATTESTATION,
                'status':'DENIED','execution':'NOT_PERFORMED','cleanup':'NOT_STARTED',
                'reason':'QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED',
                'toolchain':{'qemu-system-x86_64':False,'qemu-img':False,
                             'ssh':True,'ssh-keygen':True}})
            report={'source':dict(SRC),'adapter':'php_mysql_v1',
                'security_attestation':ATTESTATION,'stage':'reviewed_vm_api_testing',
                'status':'VM_EXECUTION_NOT_VERIFIED','execution':'NOT_PERFORMED',
                'cleanup':'NOT_STARTED','results':[],
                'summary':{'total':0,'passed':0,'failed':0,'inconclusive':0}}
            write_json(dest/run_name,report)
            return report
        with patch.object(core.runners,'invoke',denied):
            result=self.go('REVIEWED',suites=[self.base_spec],max_vm_launches=1)
        self.assertEqual(result['status'],'NEEDS_REVIEW')
        self.assertEqual(result['suites'][0]['state'],'NOT_VERIFIED')
        self.assertFalse(result['suites'][0]['vm_verified'])
        self.assertEqual(result['suites'][0]['vm_preflight_reason'],
                         'QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED')
        self.assertEqual(next(x for x in result['workflow'] if x['stage']=='functional_testing')['status'],
                         'PARTIAL')
        self.assertIn('FUNCTIONAL_BEHAVIOR_NOT_EXECUTED',result['limitations'])
        self.assertIn('VM_HOST_TOOLCHAIN_OR_IMAGE_NOT_VERIFIED',result['limitations'])
        self.assertIn('VERIFY_VM_HOST_TOOLS_AND_PINNED_IMAGE',
                      [x['type'] for x in result['review_requests']])
        md=(self.dir/'assessment'/'executioner_report.md').read_text(encoding='utf-8')
        self.assertIn('VM preflight blocker',md)
        self.assertIn('Functional suites VM-verified',md)

    def test_vm_budget_prevents_unplanned_extra_launches(self):
        specs=[self.base_spec,{'kind':'browser_ui','catalogs':{'browser':'reviews/browser.json'}}]
        res=self.go('REVIEWED',suites=specs,max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertEqual(res['suites'][1]['reason'],'VM_ATTEMPT_BUDGET')

    def test_vm_budget_zero_no_execution(self):
        res=self.go('REVIEWED',suites=[self.base_spec],max_vm_launches=0)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(res['suites'][0]['state'],'BLOCKED')

    def test_mixed_suite_preflight_no_global_launch(self):
        self.stub.preflight_fail={'api'}
        specs=[self.base_spec,{'kind':'browser_ui','catalogs':{'browser':'reviews/browser.json'}}]
        res=self.go('REVIEWED',suites=specs,max_vm_launches=2)
        self.assertEqual(self.stub.run_calls,['browser_ui'])
        self.assertEqual(res['status'],'NEEDS_REVIEW')

    def test_unexpected_runner_error_triggers_hold(self):
        with patch.object(core.runners,'invoke',side_effect=RuntimeError('not_going_to_leak')):
            res=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(res['status'],'SECURITY_HOLD')
        self.assertNotIn('not_going_to_leak',json.dumps(res))

    def test_reused_output_directory_refused(self):
        self.go()
        with self.assertRaises(OrchestrationError):self.go()

    def test_report_does_not_embed_secret_catalog(self):
        old=self.stub.catalogs
        def secret(spec,root):
            result=old(spec,root)
            for x in result.values():x['password']='SUPERSECRETSTRING'
            return result
        with patch.object(core.runners,'load_catalogs',secret):
            res=self.go('REVIEWED',suites=[self.base_spec])
        text=(self.dir/'assessment'/'executioner_report.md').read_text()
        payload=(self.dir/'assessment'/'executioner_result.json').read_text()
        self.assertNotIn('SUPERSECRETSTRING',text)
        self.assertNotIn('SUPERSECRETSTRING',payload)

    def test_readiness_yes_alongside_selected_suite(self):
        res=self.go('REVIEWED',suites=[self.base_spec],run_readiness='YES',max_vm_launches=2)
        self.assertEqual(self.stub.run_calls,['api','readiness'])

    def test_partial_not_wrongly_called_full_success(self):
        self.stub.plan['summary']['ready_for_phase4']=0
        res=self.go('REVIEWED',run_readiness='AUTO')
        self.assertEqual(res['status'],'NEEDS_REVIEW')
        self.assertEqual(self.stub.run_calls,[])

    def test_internal_error_returns_safe_persistent_report(self):
        with patch.object(core.adapters,'pinned_checkout',side_effect=ValueError('private secret')):
            res=self.go()
        self.assertEqual(res['status'],'FAILED')
        self.assertNotIn('private secret',json.dumps(res))
        self.assertTrue((self.dir/'assessment'/'executioner_result.json').exists())

    def test_report_includes_case_details_and_findings(self):
        self.go('REVIEWED',suites=[self.base_spec])
        md=(self.dir/'assessment'/'executioner_report.md').read_text()
        for word in ('TC-0123456789ABCDEF','EXAMPLE','phase4/api/api_run.json','Phase 6'):
            self.assertIn(word,md)


class ContractTests(unittest.TestCase):
    def test_request_minimal(self):
        self.assertEqual(validate_request(request())['mode'],'STATIC')

    def test_invalid_modes(self):
        for mode in ('AUTO','FULL','DANGEROUS',''):
            with self.subTest(mode=mode),self.assertRaises(OrchestrationError):
                validate_request(request(mode))

    def test_unsafe_github(self):
        for u in ('http://github.com/A/B','https://example.com/A/B',
                  'https://github.com/A/B?ref=x','https://github.com/A/../B'):
            with self.subTest(u=u),self.assertRaises(OrchestrationError):
                validate_request(request(repository_url=u))

    def test_static_must_have_no_reviewed_suites(self):
        with self.assertRaises(OrchestrationError):
            validate_request(request(suites=[{'kind':'api','catalogs':{'oracle':'x.json'}}]))

    def test_invalid_catalog_paths(self):
        for path in ('../x.json','/etc/passwd','a\\b.json','a//b.json','a/./b.json'):
            with self.subTest(path=path),self.assertRaises(OrchestrationError):
                validate_request(request('REVIEWED',suites=[{'kind':'api','catalogs':{'oracle':path}}]))

    def test_duplicate_suite_rejected(self):
        s={'kind':'api','catalogs':{'oracle':'x.json'}}
        with self.assertRaises(OrchestrationError):
            validate_request(request('REVIEWED',suites=[s,s]))

    def test_catalog_keys_exact(self):
        s={'kind':'auth','catalogs':{'auth':'a.json'}}
        with self.assertRaises(OrchestrationError):
            validate_request(request('REVIEWED',suites=[s]))

    def test_budget_strict_and_bounded(self):
        for n in (-1,13,True,1.0,'3'):
            with self.subTest(n=n),self.assertRaises(OrchestrationError):
                validate_request(request(max_vm_launches=n))

    def test_resume_requires_previous_result(self):
        with self.assertRaises(OrchestrationError):
            validate_request(request('RESUME',resume_target='REVIEWED'))

    def test_resume_requires_target(self):
        with self.assertRaises(OrchestrationError):
            validate_request(request('RESUME',resume_from='x.json'))

    def test_resume_ref_must_be_sha(self):
        with self.assertRaises(OrchestrationError):
            validate_request(request('RESUME',ref='main',resume_from='x.json',resume_target='REVIEWED'))

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.json';p.write_text('{"a":1,"a":2}')
            with self.assertRaises(OrchestrationError):read_json(p)

    def test_nonfinite_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.json';p.write_text('{"a":NaN}')
            with self.assertRaises(OrchestrationError):read_json(p)

    def test_input_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);p=base/'original.json';p.write_text('{}')
            link=base/'linked.json'
            try:link.symlink_to(p)
            except OSError:self.skipTest('symlinks unavailable')
            with self.assertRaises(OrchestrationError):read_json(link)

    def test_no_arbitrary_test_oracle_autogeneration_in_workflow(self):
        import inspect
        s=inspect.getsource(core)
        self.assertNotIn('subprocess.',s)
        self.assertNotIn('shell=True',s)
        self.assertNotIn('os.system',s)

if __name__=='__main__':unittest.main()

# ---------------------------------------------------------
# Resume tests are outside the original class to exercise a second API call.
# Mock validators accept synthetic reports; production validators validate
# the complete Phase 3 v1.1 and Phase 5 v0.3 schemas instead.
import sys

class ResumeTests(unittest.TestCase):
    go = OrchestratorTests.go

    def setUp(self):
        OrchestratorTests.setUp(self)
        f1=types.ModuleType('projectiq_executioner.phase3.contracts')
        f1.validate_test_plan=lambda report: None
        f2=types.ModuleType('projectiq_executioner.phase5.contracts')
        f2.validate_report=lambda report: None
        mocks=patch.dict(sys.modules,{'projectiq_executioner.phase3.contracts':f1,
                                      'projectiq_executioner.phase5.contracts':f2})
        mocks.start();self.addCleanup(mocks.stop)

    def _resume(self,previous,*,target='STATIC',suite=None,name='second',**kwargs):
        options={'resume_from':str(self.dir/previous/'executioner_result.json'),
                 'resume_target':target}
        if suite is not None:options['suites']=[suite]
        options.update(kwargs)
        return self.go('RESUME',name=name,**options)

    def test_resume_static_does_not_download_or_scan_source_again(self):
        self.go(name='first')
        self.stub.phases.clear()
        r=self._resume('first')
        self.assertEqual(r['status'],'COMPLETED_WITH_LIMITATIONS')
        self.assertNotIn('ingest',self.stub.phases)
        self.assertNotIn('phase1',self.stub.phases)
        self.assertNotIn('phase5',self.stub.phases)
        self.assertIn('phase3/test_plan.json',r['artifacts'])

    def test_resume_reviewed_adds_new_test_without_new_download(self):
        self.go(name='first')
        self.stub.phases.clear()
        r=self._resume('first',target='REVIEWED',suite=self.base_spec)
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertEqual(r['vm_attempts_scheduled'],1)
        self.assertNotIn('ingest',self.stub.phases)

    def test_resume_reuses_verified_reviewed_suite_no_vm(self):
        self.go('REVIEWED',name='first',suites=[self.base_spec])
        self.stub.run_calls.clear()
        r=self._resume('first',target='REVIEWED',suite=self.base_spec)
        self.assertEqual(self.stub.run_calls,[])
        self.assertTrue(r['suites'][0]['reused_from_prior'])
        self.assertTrue(r['suites'][0]['vm_verified'])
        self.assertEqual(r['vm_attempts_scheduled'],0)

    def test_resume_zero_budget_reuses_verified_suite(self):
        self.go('REVIEWED',name='first',suites=[self.base_spec])
        self.stub.run_calls.clear()
        r=self._resume('first',target='REVIEWED',suite=self.base_spec,
                       max_vm_launches=0)
        self.assertEqual(self.stub.run_calls,[])
        self.assertTrue(r['suites'][0]['reused_from_prior'])
        self.assertTrue(r['suites'][0]['vm_verified'])
        self.assertEqual(r['vm_attempts_scheduled'],0)
        self.assertEqual(r['test_case_matrix']['counts']['PASSED'],1)

    def test_resume_zero_budget_with_changed_oracle_does_not_start_vm(self):
        self.go('REVIEWED',name='first',suites=[self.base_spec])
        self.stub.run_calls.clear()
        original=self.stub.catalogs
        def changed(spec,root):
            x=original(spec,root)
            for v in x.values():v['changed_catalog']=True
            return x
        with patch.object(core.runners,'load_catalogs',changed):
            r=self._resume('first',target='REVIEWED',suite=self.base_spec,
                           max_vm_launches=0)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['suites'][0]['state'],'BLOCKED')
        self.assertEqual(r['suites'][0]['reason'],'VM_ATTEMPT_BUDGET')
        self.assertEqual(r['test_case_matrix']['counts']['PASSED'],0)
        self.assertEqual(r['vm_attempts_scheduled'],0)

    def test_resume_zero_budget_cannot_launch_new_suite(self):
        self.go(name='first')
        self.stub.run_calls.clear()
        r=self._resume('first',target='REVIEWED',suite=self.base_spec,
                       max_vm_launches=0)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['suites'][0]['state'],'BLOCKED')
        self.assertEqual(r['vm_attempts_scheduled'],0)

    def test_resume_changed_catalog_reruns(self):
        self.go('REVIEWED',name='first',suites=[self.base_spec])
        self.stub.run_calls.clear()
        original=self.stub.catalogs
        def changed(spec,root):
            x=original(spec,root)
            for v in x.values():v['different_reviewed_oracle']=True
            return x
        with patch.object(core.runners,'load_catalogs',changed):
            r=self._resume('first',target='REVIEWED',suite=self.base_spec)
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertFalse(r['suites'][0]['reused_from_prior'])

    def test_resume_rejects_tampered_phase5_artifact(self):
        self.go(name='first')
        target=self.dir/'first'/'phase5'/'code_quality.json'
        target.write_text(target.read_text()+'\n')
        r=self._resume('first')
        self.assertEqual(r['status'],'FAILED')
        self.assertIn('ORCHESTRATION_RESUME_ARTIFACT_CHANGED',r['error_codes'])

    def test_resume_rejects_wrong_repository_identity(self):
        self.go(name='first')
        r=self.go('RESUME',name='second',resume_from=str(self.dir/'first'/'executioner_result.json'),
                  resume_target='STATIC',repository_url='https://github.com/Other/Another')
        self.assertEqual(r['status'],'FAILED')

    def test_resume_never_reuses_security_hold_as_clean(self):
        self.stub.vm_cleanup='NOT_VERIFIED'
        r=self.go('REVIEWED',name='first',suites=[self.base_spec])
        self.assertEqual(r['status'],'SECURITY_HOLD')
        self.stub.vm_cleanup=CLEAN
        self.stub.run_calls.clear()
        r2=self._resume('first',target='REVIEWED',suite=self.base_spec)
        self.assertEqual(r2['status'],'FAILED')
        self.assertEqual(self.stub.run_calls,[])

class OperatorRegistryTests(unittest.TestCase):
    go = OrchestratorTests.go
    def setUp(self):
        OrchestratorTests.setUp(self)
        self.registry=self.dir/'operator_registry.json'
        self.operator=OperatorContext(vm_config=self.dir/'vm.json',review_root=self.dir,
                                      review_registry=self.registry)
        self.spec={'kind':'api','catalogs':{'oracle':'reviews/api_oracle.json'}}

    def registry_data(self,profiles):
        write_json(self.registry,{'schema_version':'0.1','profiles':profiles})

    def test_operator_registry_auto_selects_exact_sha(self):
        self.registry_data([{'repository':SRC['repository'],'commit_sha':SHA,'suites':[self.spec]}])
        r=self.go('REVIEWED',max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,['api'])
        self.assertEqual(r['suites'][0]['kind'],'api')

    def test_registry_wrong_sha_does_not_select_other_project(self):
        self.registry_data([{'repository':SRC['repository'],'commit_sha':'a'*40,
                            'suites':[self.spec]}])
        r=self.go('REVIEWED',max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,['readiness'])

    def test_registry_cannot_authorize_unapproved_catalog(self):
        self.registry_data([{'repository':SRC['repository'],'commit_sha':SHA,'suites':[self.spec]}])
        self.stub.preflight_fail={'api'}
        r=self.go('REVIEWED',max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['status'],'NEEDS_REVIEW')

    def test_registry_duplicate_profiles_block_execution(self):
        item={'repository':SRC['repository'],'commit_sha':SHA,'suites':[self.spec]}
        self.registry_data([item,item])
        r=self.go('REVIEWED')
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['status'],'NEEDS_REVIEW')

    def test_registry_invalid_suite_kind_fails_closed(self):
        self.registry_data([{'repository':SRC['repository'],'commit_sha':SHA,
                            'suites':[{'kind':'arbitrary_vm_command','catalogs':{}}]}])
        r=self.go('REVIEWED')
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['status'],'NEEDS_REVIEW')

    def test_registry_not_used_in_static_mode(self):
        self.registry_data([{'repository':SRC['repository'],'commit_sha':SHA,'suites':[self.spec]}])
        r=self.go('STATIC')
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['vm_attempts_scheduled'],0)


class ExtendedSafetyAcceptance(unittest.TestCase):
    go=OrchestratorTests.go
    def setUp(self):
        OrchestratorTests.setUp(self)
        self.api={'kind':'api','catalogs':{'oracle':'reviews/api.json'}}
        self.browser={'kind':'browser_ui','catalogs':{'browser':'reviews/browser.json'}}

    def test_all_reviewed_runner_argument_adapters_present(self):
        from projectiq_executioner.orchestration.contracts import SUITE_CATALOGS
        self.assertEqual(set(runners._REGISTRY),set(SUITE_CATALOGS))
        for kind,fields in SUITE_CATALOGS.items():
            with self.subTest(kind=kind):
                cats={x:{} for x in fields}
                arguments=runners._args(kind,{},CAND,{},cats)
                self.assertIn('plan',arguments)
                self.assertIn('candidate',arguments)
                self.assertIn('config',arguments)
                self.assertTrue(runners.output_files(kind)[0].endswith('.json'))

    def test_phase2_problem_still_produces_phase3_discovery(self):
        with patch.object(core.adapters,'compile_phase2',side_effect=ValueError('broken')):
            r=self.go()
        self.assertEqual(r['status'],'COMPLETED_WITH_LIMITATIONS')
        self.assertTrue(r['test_plan'])
        self.assertIn('phase3/test_plan.json',r['artifacts'])
        self.assertEqual(self.stub.run_calls,[])

    def test_vm_missing_validated_stage_proof_cannot_pass(self):
        original=self.stub.invoke
        def wrong(kind,**kwargs):
            report=original(kind,**kwargs)
            path=kwargs['output_dir']/'api_vm_execution.json'
            vm=read_json(path)
            del vm['phase4c_status']
            path.unlink()
            write_json(path,vm)
            return report
        with patch.object(core.runners,'invoke',wrong):
            r=self.go('REVIEWED',suites=[self.api])
        self.assertEqual(r['status'],'SECURITY_HOLD')
        self.assertFalse(r['suites'][0]['vm_verified'])

    def test_vm_missing_entirely_holds_even_when_run_claims_pass(self):
        original=self.stub.invoke
        def missing(kind,**kwargs):
            r=original(kind,**kwargs)
            (kwargs['output_dir']/'api_vm_execution.json').unlink()
            return r
        with patch.object(core.runners,'invoke',missing):
            result=self.go('REVIEWED',suites=[self.api])
        self.assertEqual(result['status'],'SECURITY_HOLD')

    def test_preflight_all_catalogs_before_first_execution(self):
        original=self.stub.invoke
        def log_invoke(kind,**kwargs):
            self.stub.phases.append('VM:'+kind)
            return original(kind,**kwargs)
        with patch.object(core.runners,'invoke',log_invoke):
            self.go('REVIEWED',suites=[self.api,self.browser],max_vm_launches=2)
        self.assertLess(self.stub.phases.index('preflight:browser_ui'),
                        self.stub.phases.index('VM:api'))

    def test_scenario_isolation_budget_2_required(self):
        spec={'kind':'scenario_isolation','catalogs':{
            'auth':'a.json','oracle':'o.json','postconditions':'p.json','isolation':'i.json'}}
        r=self.go('REVIEWED',suites=[spec],max_vm_launches=1)
        self.assertEqual(self.stub.run_calls,[])
        self.assertEqual(r['suites'][0]['reason'],'VM_ATTEMPT_BUDGET')

    def test_valid_two_scenario_isolation_is_not_one_vm(self):
        spec={'kind':'scenario_isolation','catalogs':{
            'auth':'a.json','oracle':'o.json','postconditions':'p.json','isolation':'i.json'}}
        def two_scenarios(kind,**kwargs):
            out=kwargs['output_dir']
            cases=[]
            for ix in range(2):
                sid='SCENARIO'+str(ix+1)
                path='scenarios/'+sid+'/d3_vm_execution.json'
                vm={'source':SRC,'adapter':'php_mysql_v1',
                    'backend':'QEMU_DISPOSABLE_QCOW2_VM','security_attestation':ATTESTATION,
                    'status':'STARTUP_VERIFIED','execution':EXECUTED,'cleanup':CLEAN,
                    'managed_runtime':'SIGNED_LEASED_WORKSPACE_REMOVED',
                    'phase4d3_status':'VALIDATED','managed_run_lease_id':'lease'+str(ix)}
                write_json(out/path,vm)
                cases.append({'id':sid,'status':'RESET_VERIFIED','cleanup':CLEAN,
                              'artifacts':{'vm':path}})
            report={'source':SRC,'adapter':'php_mysql_v1','security_attestation':ATTESTATION,
                'status':'SCENARIO_ISOLATION_VERIFIED',
                'execution':'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS',
                'scenarios':cases,'summary':{'total':2,'passed':2,'failed':0}}
            write_json(out/'d4_run.json',report)
            return report
        with patch.object(core.runners,'invoke',two_scenarios):
            r=self.go('REVIEWED',suites=[spec],max_vm_launches=2)
        self.assertEqual(r['vm_attempts_scheduled'],2)
        self.assertEqual(r['status'],'COMPLETED_WITH_LIMITATIONS')
        self.assertEqual(r['suites'][0]['state'],'PASS')
        self.assertTrue(r['suites'][0]['vm_verified'])

    def test_known_invalid_report_no_false_pass(self):
        self.stub.api_status='GUEST_RESULTS_NOT_VERIFIED'
        r=self.go('REVIEWED',suites=[self.api])
        self.assertEqual(r['status'],'COMPLETED_WITH_LIMITATIONS')
        self.assertEqual(r['suites'][0]['state'],'NOT_VERIFIED')
        self.assertEqual(r['summary']['suites']['verified_passes'],0)


    def test_final_report_matrix_static_no_false_pass(self):
        r=self.go('STATIC')
        self.assertEqual(r['test_case_matrix']['counts']['NOT_TESTED'],1)
        self.assertEqual(r['test_case_matrix']['discovered_cases'][0]['reason'],
                         'STATIC_MODE_NO_EXECUTION')
        self.assertEqual(r['test_case_matrix']['counts']['PASSED'],0)
        self.assertIn('Consolidated test-case outcome matrix',
                      (self.dir/'assessment'/'executioner_report.md').read_text(encoding='utf-8'))

    def test_final_report_matrix_verified_pass_evidence(self):
        r=self.go('REVIEWED',suites=[self.base_spec])
        self.assertEqual(r['test_case_matrix']['counts']['PASSED'],1)
        row=r['test_case_matrix']['discovered_cases'][0]
        self.assertEqual(row['evidence'][0]['report_path'],'phase4/api/api_run.json')
        self.assertIn('PASSED',(self.dir/'assessment'/'executioner_report.md').read_text(encoding='utf-8'))
        self.assertEqual(r['summary']['test_case_counts']['PASSED'],1)



class CaseMatrixTests(unittest.TestCase):
    """Regression for faculty-visible test outcomes; no VM/network."""

    def sample(self):
        from projectiq_executioner.orchestration.matrix import build_test_case_matrix
        plan={'cases':[{'id':'TC-A','title':'Readiness','status':'READY_FOR_PHASE4',
                'category':'HTTP_READINESS','target':{'method':'GET','path':'/'},
                'assertions':[{'kind':'HTTP_STATUS_EQUALS','expected':200}]},
               {'id':'TC-B','title':'Registration','status':'NEEDS_ORACLE',
                'category':'HTTP_ROUTE','target':{'method':'POST','path':'/register'}},
               {'id':'TC-C','title':'Other API','status':'UNSUPPORTED_BY_RUNNER',
                'category':'NON_HTTP_API'}]}
        return build_test_case_matrix,plan

    def suite(self, *, state='PASS',verified=True, observations=None):
        return {'kind':'readiness','state':state,'vm_verified':verified,
                'report_path':'phase4/readiness/phase4_run.json',
                'vm_path':'phase4/readiness/vm_execution.json','cleanup':CLEAN,
                'observations':observations if observations is not None else [
                    {'test_id':'TC-A','result':'PASS','http_status':200}]}

    def test_no_runtime_static_is_not_tested(self):
        build,plan=self.sample();matrix=build(plan,[])
        self.assertEqual(matrix['counts'],{'PASSED':0,'FAILED':0,'INCONCLUSIVE':0,
            'BLOCKED':0,'NOT_TESTED':3})
        self.assertEqual(matrix['discovered_cases'][1]['reason'],'REVIEWED_ORACLE_MISSING')

    def test_readiness_linked_only_by_exact_test_id(self):
        build,plan=self.sample();matrix=build(plan,[self.suite()])
        self.assertEqual(matrix['counts']['PASSED'],1)
        self.assertEqual(matrix['counts']['NOT_TESTED'],2)
        self.assertEqual(matrix['discovered_cases'][0]['evidence'][0]['http_status'],200)
        self.assertIn('phase4/readiness/',matrix['discovered_cases'][0]['evidence'][0]['report_path'])

    def test_unverified_suite_never_passes(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(verified=False)])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'BLOCKED')
        self.assertEqual(matrix['counts']['PASSED'],0)

    def test_verified_suite_without_matching_id_does_not_pass_plan(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(observations=[
            {'test_id':'TC-X','result':'PASS','http_status':200}])])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'NOT_TESTED')
        self.assertEqual(matrix['reviewed_only_checks'][0]['check_id'],'TC-X')
        self.assertEqual(matrix['reviewed_only_checks'][0]['outcome'],'PASSED')

    def test_verified_fail_is_specific_not_all_suites(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(state='FAIL',observations=[
            {'test_id':'TC-A','result':'FAIL','http_status':500}])])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'FAILED')
        self.assertEqual(matrix['discovered_cases'][1]['outcome'],'NOT_TESTED')

    def test_conflicting_observations_are_inconclusive(self):
        build,plan=self.sample();s1=self.suite();s2=self.suite(state='FAIL',observations=[
            {'test_id':'TC-A','result':'FAIL','http_status':500}]);s2['kind']='api'
        matrix=build(plan,[s1,s2])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'INCONCLUSIVE')
        self.assertEqual(matrix['discovered_cases'][0]['reason'],
                         'MULTIPLE_DIFFERENT_OBSERVED_OUTCOMES')

    def test_suite_and_check_conflict_is_not_pass_or_fail(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(state='PASS',observations=[
            {'test_id':'TC-A','result':'FAIL'}])])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'INCONCLUSIVE')

    def test_missing_observation_is_not_inferred_from_suite_pass(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(observations=[])])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'NOT_TESTED')

    def test_unrecognized_check_result_is_inconclusive(self):
        build,plan=self.sample();matrix=build(plan,[self.suite(observations=[
            {'test_id':'TC-A','result':{'malformed':'BAD'}}])])
        self.assertEqual(matrix['discovered_cases'][0]['outcome'],'INCONCLUSIVE')

    def test_reviewed_non_phase3_checks_are_separate(self):
        build,plan=self.sample();matrix=build(plan,[{'kind':'browser_ui','state':'PASS',
            'vm_verified':True,'report_path':'phase4/browser_ui/d4c_run.json',
            'observations':[{'id':'DOM-1','status':'PASS'}]}])
        self.assertEqual(matrix['counts']['PASSED'],0)
        self.assertEqual(matrix['reviewed_check_counts']['PASSED'],1)

    def test_discovered_rows_bound_to_observation_count(self):
        build,plan=self.sample();matrix=build(plan,[self.suite()])
        self.assertEqual(len(matrix['discovered_cases']),len(plan['cases']))
        self.assertEqual(sum(matrix['counts'].values()),len(plan['cases']))



class CliMatrixTests(unittest.TestCase):
    def test_cli_prints_distinct_outcome_totals(self):
        from projectiq_executioner.orchestration import __main__ as cli
        from contextlib import redirect_stdout
        from io import StringIO
        sample={'status':'COMPLETED_WITH_LIMITATIONS','source':dict(SRC),
                'mode':'REVIEWED','effective_mode':'REVIEWED',
                'suites':[{'kind':'readiness'}], 'vm_attempts_scheduled':1,
                'test_case_matrix':{'counts':{'PASSED':1,'FAILED':0,'INCONCLUSIVE':0,
                                             'BLOCKED':0,'NOT_TESTED':10},
                                    'reviewed_only_checks':[]}}
        buf=StringIO()
        with patch.object(cli,'read_json',return_value=request('REVIEWED')), \
             patch.object(cli,'evaluate',return_value=sample), redirect_stdout(buf):
            status=cli.main(['--request','input.json','--out','out'])
        self.assertEqual(status,3)
        self.assertIn('1 PASSED',buf.getvalue())
        self.assertIn('10 NOT_TESTED',buf.getvalue())
        self.assertIn('VM attempts scheduled: 1',buf.getvalue())
