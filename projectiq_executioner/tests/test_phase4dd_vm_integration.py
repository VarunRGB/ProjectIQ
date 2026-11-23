"""4D-D VM wiring acceptance: entirely offline/mocked (NO QEMU or browser)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import browser_login_vm_run as runner
from projectiq_executioner.phase4 import browser_login_contracts as contracts
from projectiq_executioner.tests.test_phase4dd_login_foundation import inputs, report_for


def fake_vm(bundle, *, status='PASS', checks=None):
    vm={'status':'STARTUP_VERIFIED','execution':'PERFORMED_IN_DISPOSABLE_VM',
        'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
        'managed_runtime':'SIGNED_LEASED_WORKSPACE_REMOVED',
        'backend':'QEMU_DISPOSABLE_QCOW2_VM',
        'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
        'source':bundle['source'],'adapter':'php_mysql_v1',
        'phase4dd_status':'VALIDATED'}
    if status=='PASS':
        vm['phase4dd_login_report']=report_for(bundle,[True]*4)
    elif status=='FAIL':
        vm['phase4dd_login_report']=report_for(bundle,[True,True,False,False])
    elif status=='INCONCLUSIVE':
        vm['phase4dd_login_report']=report_for(bundle,[None]*4,
                       status='INCONCLUSIVE',issue='LOGIN_CDP_UNAVAILABLE')
    return vm


class LoginRunner(unittest.TestCase):
    def setUp(self):
        self.plan,self.candidate,self.catalog,self.config,self.bundle=inputs()
        p=patch.object(contracts,'validate_test_plan',return_value=None)
        p.start();self.addCleanup(p.stop)

    def test_preflight_no_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'fresh'
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       side_effect=AssertionError('never launch VM')):
                r=runner.preflight(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=out)
            self.assertEqual(r['status'],'APPROVED_NOT_EXECUTED')
            self.assertTrue((out/'d4d_preflight.json').is_file())
            self.assertFalse((out/'d4d_run.json').exists())

    def test_preflight_requires_new_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(contracts.LoginReviewError):
                runner.preflight(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp))

    def test_run_uses_only_login_mode(self):
        captured={}
        def execute(candidate,config,out,**kw):
            captured.update(kw)
            return fake_vm(self.bundle)
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       side_effect=execute):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_PASSED')
        self.assertEqual(len(r['checks']),4)
        self.assertTrue(all(x['matched'] for x in r['checks']))
        self.assertIs(captured['reviewed_fixture'],True)
        self.assertEqual(captured['login_bundle']['login'],self.bundle)
        self.assertNotIn('browser_bundle',captured)
        self.assertNotIn('auth_bundle',captured)

    def test_false_guest_pass_rejected(self):
        vm=fake_vm(self.bundle)
        vm['phase4dd_login_report']['checks'][2]['matched']=False
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=vm):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_VM_NOT_VERIFIED')
        self.assertIsNone(r['checks'])

    def test_valid_but_negative_login_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=fake_vm(self.bundle,status='FAIL')):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_FAILED')
        self.assertEqual([x['matched'] for x in r['checks']],[True,True,False,False])

    def test_guest_unavailable_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=fake_vm(self.bundle,status='INCONCLUSIVE')):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_INCONCLUSIVE')

    def test_cleanup_failure_takes_precedence(self):
        vm=fake_vm(self.bundle)
        vm['cleanup']='NOT_VERIFIED'
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=vm):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'CLEANUP_FAILED')
        self.assertIsNone(r['checks'])

    def test_guest_source_mismatch_never_passes(self):
        vm=fake_vm(self.bundle);vm['source']={'repository':'Other/Repo'}
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=vm):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_VM_NOT_VERIFIED')

    def test_missing_host_validation_never_passes(self):
        vm=fake_vm(self.bundle);vm['phase4dd_status']='INVALID_GUEST_REPORT'
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       return_value=vm):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['status'],'LOGIN_VM_NOT_VERIFIED')

    def test_exception_fails_closed_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       side_effect=RuntimeError('sensitive credentials')):
                r=runner.run(plan=self.plan,candidate=self.candidate,
                    catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')
        self.assertEqual(r['issue_code'],'VM_WORKER_EXCEPTION')
        self.assertNotIn('sensitive',json.dumps(r))

    def test_outside_approval_denied_before_vm(self):
        self.config['approved_browser_logins']=[]
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',
                       side_effect=AssertionError('VM must not run')):
                with self.assertRaises(contracts.LoginReviewError):
                    runner.run(plan=self.plan,candidate=self.candidate,
                        catalog=self.catalog,config=self.config,output_dir=Path(tmp)/'fresh')


class IntegrationInstalled(unittest.TestCase):
    def test_fixed_launcher_and_resource_policy_present(self):
        from projectiq_executioner.phase2.vm_worker import guest_agent
        self.assertIn("'/home/projectiq/guest_browser_login_cdp.py'",
                      guest_agent.APP_LIMIT_LAUNCHER)
        self.assertIn('guest_browser_login_cdp.py',
                      guest_agent._project_command.__code__.co_consts.__str__())

    def test_guest_policy_rejects_unapproved_login_profile(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import (
            _reviewed_browser_pids_for_request,GuestFailure)
        source=dict(contracts.PINNED_SOURCE)
        bundle=inputs()[-1]
        meta={'adapter':'php_mysql_v1','phase4dd_source':source,
              'phase4dd_login_bundle':bundle,'phase4dc_browser_resource_pids':384}
        self.assertEqual(_reviewed_browser_pids_for_request(meta),384)
        for change in (lambda x:x.update(phase4dc_browser_resource_pids=128),
                       lambda x:x.update(adapter='static_html_v1'),
                       lambda x:x['phase4dd_login_bundle'].update(start_path='/evil'),
                       lambda x:x['phase4dd_login_bundle'].update(source={'x':'y'}),
                       lambda x:x.update(phase4dc_browser_bundle={'x':'y'})):
            broken=deepcopy(meta);change(broken)
            with self.subTest(broken=broken.keys()):
                with self.assertRaises(GuestFailure):
                    _reviewed_browser_pids_for_request(broken)
        self.assertEqual(_reviewed_browser_pids_for_request({'adapter':'static_html_v1'}),128)


if __name__=='__main__':unittest.main()
