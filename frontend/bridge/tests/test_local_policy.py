import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_policy import Admission, BridgeBusy, authorized, request_mode, available_modes, demo_args, final_states, job_agent_actions

class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.vm = Path(self.tmp.name)/'vm.json'; self.vm.write_text('{}')
        self.token = 'x'*48
        self.headers={'Host':'127.0.0.1:8765','Origin':'http://localhost:5173','X-ProjectIQ-Token':self.token}
        self.req={'repository_url':'https://github.com/example/app','ref':'a'*40,'executioner':{'mode':'TRUSTED_DEMO','trust_demo_source':True,'confirm_disposable_state':True,'suites':[]}}
    def check(self, headers=None, **kw):
        return authorized(self.headers if headers is None else headers, port=8765, origin='http://localhost:5173',token=self.token,**kw)
    def mode(self, req=None, payload=None, enabled=True, vm=True):
        return request_mode(self.req if req is None else req,payload or {},vm_config=self.vm if vm else None,demo_enabled=enabled)
    def test_valid(self): self.assertTrue(self.check())
    def test_wrong_token(self): self.assertFalse(self.check({**self.headers,'X-ProjectIQ-Token':'y'*48}))
    def test_missing_token(self): self.assertFalse(self.check({k:v for k,v in self.headers.items() if k!='X-ProjectIQ-Token'}))
    def test_cross_origin(self): self.assertFalse(self.check({**self.headers,'Origin':'https://example.com'}))
    def test_rebinding(self): self.assertFalse(self.check({**self.headers,'Host':'example.com:8765'}))
    def test_preflight(self): self.assertTrue(self.check({k:v for k,v in self.headers.items() if k!='X-ProjectIQ-Token'},preflight=True))
    def test_preflight_wrong_origin(self): self.assertFalse(self.check({**self.headers,'Origin':'null'},preflight=True))
    def test_nonbrowser_token(self): self.assertTrue(self.check({k:v for k,v in self.headers.items() if k!='Origin'}))
    def test_admission_release(self):
        with Admission() as a:
            pass
    def test_admission(self):
        a=Admission()
        with a:
            with self.assertRaises(BridgeBusy): a.acquire()
        with a: pass
    def test_exception_releases(self):
        a=Admission()
        with self.assertRaises(RuntimeError):
            with a: raise RuntimeError()
        with a: pass
    def test_capabilities(self):
        self.assertEqual(available_modes(None,True),['STATIC']); self.assertNotIn('TRUSTED_DEMO',available_modes(self.vm,False)); self.assertIn('TRUSTED_DEMO',available_modes(self.vm,True))
    def test_demo(self):
        self.assertEqual(self.mode(),('TRUSTED_DEMO',False)); self.assertEqual(demo_args(self.req['executioner'],self.vm),['--trust-demo-source','--demo-vm-config',str(self.vm),'--confirm-disposable-state'])
    def test_demo_readonly(self):
        self.req['executioner']['confirm_disposable_state']=False
        self.assertNotIn('--confirm-disposable-state',demo_args(self.req['executioner'],self.vm))
    def test_disabled(self):
        with self.assertRaisesRegex(ValueError,'NOT_ENABLED'): self.mode(enabled=False)
    def test_no_vm(self):
        with self.assertRaisesRegex(ValueError,'NOT_ENABLED'): self.mode(vm=False)
    def test_trust_required(self):
        self.req['executioner']['trust_demo_source']=False
        with self.assertRaisesRegex(ValueError,'EXPLICIT_DEMO_TRUST_REQUIRED'): self.mode()
    def test_string_boolean_refused(self):
        self.req['executioner']['trust_demo_source']='true'
        with self.assertRaisesRegex(ValueError,'INVALID_DEMO_CONFIRMATION'): self.mode()
    def test_pinned(self):
        self.req['ref']='main'
        with self.assertRaisesRegex(ValueError,'PINNED'): self.mode()
    def test_import_in_demo(self):
        with self.assertRaisesRegex(ValueError,'DEMO_CANNOT'): self.mode(payload={'executioner_import':{'name':'a.json'}})
    def test_suites_in_demo(self):
        self.req['executioner']['suites']=[{'kind':'crud'}]
        with self.assertRaisesRegex(ValueError,'DEMO_CANNOT'): self.mode()
    def test_static(self): self.assertEqual(self.mode({'executioner':None},enabled=False,vm=False),('STATIC',False))
    def test_reviewed(self):
        self.req['executioner']={'mode':'REVIEWED'}
        self.assertEqual(self.mode(payload={'acknowledge_imported':True}),('REVIEWED',True))
        with self.assertRaisesRegex(ValueError,'ACKNOWLEDGEMENT'): self.mode()
    def test_import_without_vm(self):
        self.req['executioner']={'mode':'REVIEWED'}
        self.assertEqual(self.mode(payload={'acknowledge_imported':True,'executioner_import':{'name':'a'}},vm=False),('REVIEWED',True))
    def test_no_repo_runtime(self):
        self.req['repository_url']=None
        with self.assertRaisesRegex(ValueError,'REPOSITORY_REQUIRED'): self.mode()
    def test_status(self):
        result={'assessment':{},'rubric':{},'workflow_status':'INCOMPLETE_ASSESSMENT','rubric_exit_code':0,'agents':{a:{'status':'COMPLETED','evidence_check':'MATCHED'} for a in ('summarizer','integrity','research','executioner')}}
        result['agents']['research']['status']='NOT_RUN'; result['agents']['integrity']['status']='PARTIAL'
        self.assertEqual(final_states(result),{'summarizer':'done','integrity':'partial','research':'skipped','executioner':'done','format':'skipped','rubric':'partial'})
        self.assertEqual(job_agent_actions(result,composed=True,imported=False,mode='REVIEWED')['executioner'],'COLLECTED_THIS_RUN')
        self.assertEqual(job_agent_actions(result,composed=True,imported=True,mode='REVIEWED')['executioner'],'IMPORTED_FILE')
        self.assertEqual(job_agent_actions(result,composed=True,imported=False,mode='RESUME')['executioner'],'RESUME_THIS_RUN')
        result['rubric_exit_code']=2; self.assertEqual(final_states(result)['rubric'],'failed')
    def test_invalid_result(self):
        with self.assertRaisesRegex(ValueError,'INVALID_UNIFIED_RESULT'): final_states({})
if __name__=='__main__': unittest.main()
