"""4D-C regression. All mocked/no Chrome, QEMU or student code execution.

Real Windows/WHPX and sealed-browser-image acceptance is a separate gate.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4.browser_ui_contracts import (
    BrowserReviewError, compile_reviewed_browser, validate_reviewed_browser)
from projectiq_executioner.phase4.browser_ui_stage import stage
from projectiq_executioner.phase4.guest_browser_ui import (
    BrowserUiError, dom_checks, run_browser_ui,
    validate_browser_ui_report, validate_guest_browser_bundle)
from projectiq_executioner.phase4.oracle_contracts import digest

SRC = {'kind':'github','repository':'Example/FullStack', 'commit_sha':'a'*40}


def inputs():
    plan={'source':dict(SRC), 'execution_context':{'adapter':'node_fullstack_offline_v1'}}
    candidate={'source':dict(SRC), 'schema_version':'0.3','stage':'vm_execution_candidate',
               'status':'CANDIDATE_NEEDS_VM_VERIFICATION','worker_provider':'qemu_qcow2_v1',
               'adapter':'node_fullstack_offline_v1',
               'service_roots':{'frontend':'frontend','backend':'backend'}}
    catalog={'schema_version':'0.1','source':dict(SRC),'page_path':'/index.html',
             'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                       {'tag':'p','exact_text':'SQLite verified'}]}
    ident=SRC['repository']+'@'+SRC['commit_sha']
    cfg={'approved_snapshots':[ident],
         'base_sha256':'b'*64,
         'browser_runtime':{'binary':'/usr/bin/google-chrome-stable',
                            'base_sha256':'b'*64},
         'approved_browser_ui':[ident+'#'+digest(catalog)]}
    return plan,candidate,catalog,cfg


def guest_bundle():
    p,c,cat,cfg=inputs()
    return {'schema_version':'0.1','source':p['source'], 'adapter':c['adapter'],
            'catalog_sha256':digest(cat),'page_path':'/index.html',
            'checks':cat['checks'],'browser_binary':'/usr/bin/google-chrome-stable'}


def positive_report(bundle):
    return {'schema_version':'0.1','stage':'guest_browser_ui','source':bundle['source'],
            'adapter':bundle['adapter'],'catalog_sha256':bundle['catalog_sha256'],
            'execution':'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
            'status':'PASS','issue_code':None,
            'checks':[{'tag':item['tag'],'matched':True} for item in bundle['checks']]}


class GuestContracts(unittest.TestCase):
    def setUp(self):self.bundle=guest_bundle()

    def test_valid_guest_bundle(self):
        validate_guest_browser_bundle(self.bundle,expected_source=SRC)

    def test_source_mismatch_denied(self):
        with self.assertRaises(BrowserUiError):
            validate_guest_browser_bundle(self.bundle,expected_source={**SRC,'commit_sha':'c'*40})

    def test_untrusted_browser_path_denied(self):
        self.bundle['browser_binary']='/home/projectiq/checkout/browser'
        with self.assertRaises(BrowserUiError):validate_guest_browser_bundle(self.bundle)

    def test_external_url_denied(self):
        self.bundle['page_path']='https://example.com'
        with self.assertRaises(BrowserUiError):validate_guest_browser_bundle(self.bundle)

    def test_unbounded_html_and_dynamic_code_denied(self):
        for text in ('$(shell)', 'a'*121, 'url; rm -rf', 'x\ny'):
            with self.subTest(text=text[:15]):
                v=deepcopy(self.bundle)
                v['checks'][1]['exact_text']=text
                with self.assertRaises(BrowserUiError):validate_guest_browser_bundle(v)

    def test_duplicate_tags_denied(self):
        self.bundle['checks'][1]['tag']='h1'
        with self.assertRaises(BrowserUiError):validate_guest_browser_bundle(self.bundle)

    def test_parser_matches_rendered_react_dom(self):
        html='<!doctype html><html><body><main><h1>ProjectIQ D4 pilot</h1><p>SQLite verified</p></main></body></html>'
        self.assertEqual(dom_checks(html,self.bundle['checks']),[
            {'tag':'h1','matched':True},{'tag':'p','matched':True}])

    def test_initial_loading_text_fails(self):
        html='<h1>ProjectIQ D4 pilot</h1><p>Checking backend...</p>'
        self.assertEqual(dom_checks(html,self.bundle['checks'])[1]['matched'],False)

    def test_script_string_cannot_satisfy_dom(self):
        html='<script>const x="SQLite verified";</script><h1>ProjectIQ D4 pilot</h1><p>Backend unavailable</p>'
        self.assertEqual(dom_checks(html,self.bundle['checks'])[1]['matched'],False)

    def test_false_pass_rejected(self):
        report=positive_report(self.bundle)
        report['checks'][1]['matched']=False
        with self.assertRaises(BrowserUiError):validate_browser_ui_report(self.bundle,report)

    def test_missing_result_rejected(self):
        report=positive_report(self.bundle);del report['checks'][1]
        with self.assertRaises(BrowserUiError):validate_browser_ui_report(self.bundle,report)

    def test_wrong_digest_rejected(self):
        report=positive_report(self.bundle);report['catalog_sha256']='0'*64
        with self.assertRaises(BrowserUiError):validate_browser_ui_report(self.bundle,report)

    def test_valid_inconclusive_report(self):
        report=positive_report(self.bundle)
        report.update(status='INCONCLUSIVE',issue_code='BROWSER_RUNTIME_UNAVAILABLE')
        for item in report['checks']:item['matched']=None
        validate_browser_ui_report(self.bundle,report)

    def test_untrusted_inconclusive_reason_rejected(self):
        report=positive_report(self.bundle)
        report.update(status='INCONCLUSIVE',issue_code='RAW_BROWSER_EXCEPTION:secret')
        for item in report['checks']:item['matched']=None
        with self.assertRaises(BrowserUiError):validate_browser_ui_report(self.bundle,report)

    def test_missing_runtime_is_inconclusive_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            res=run_browser_ui(self.bundle,expected_source=SRC,
                               expected_adapter='node_fullstack_offline_v1',
                               workspace=Path(tmp),trusted_process=lambda *a,**kw:None)
        self.assertEqual(res['status'],'INCONCLUSIVE')
        validate_browser_ui_report(self.bundle,res)

    def test_exec_under_existing_app_cgroup_no_sandbox_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'app_home';home.mkdir()
            calls=[]
            def fake(cmd,**kw):
                calls.append((cmd,kw))
                return SimpleNamespace(returncode=0,stdout=b'<h1>ProjectIQ D4 pilot</h1><p>SQLite verified</p>')
            original_is_file = Path.is_file
            original_resolve = Path.resolve

            def is_file(path):
                return (
                    path.as_posix() == '/usr/bin/google-chrome-stable'
                    or original_is_file(path)
                )

            def resolve(path, *args, **kwargs):
                if path.as_posix() == '/usr/bin/google-chrome-stable':
                    return PurePosixPath('/opt/google/chrome/chrome')
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, 'is_file', is_file),\
                 patch.object(Path, 'resolve', resolve),\
                 patch('projectiq_executioner.phase4.guest_browser_ui.os.access',return_value=True),\
                 patch.dict(os.environ,{'PROJECTIQ_QUOTA_HOME':str(home)}):
                result=run_browser_ui(self.bundle,expected_source=SRC,
                    expected_adapter='node_fullstack_offline_v1',workspace=root,
                    trusted_process=fake)
            self.assertEqual(result['status'],'PASS')
            self.assertEqual(len(calls),1)
            self.assertIs(calls[0][1]['project_code'],True)
            self.assertIn('--dump-dom',calls[0][0])
            self.assertNotIn('--no-sandbox',calls[0][0])
            self.assertTrue(all(not part.startswith('https://') for part in calls[0][0]))


class HostApprovals(unittest.TestCase):
    def setUp(self):
        self.plan,self.candidate,self.catalog,self.config=inputs()
        patcher=patch('projectiq_executioner.phase4.browser_ui_contracts.validate_test_plan')
        patcher.start();self.addCleanup(patcher.stop)

    def compile(self):
        return compile_reviewed_browser(self.plan,self.catalog,self.candidate,self.config)

    def test_exact_approved_review(self):
        bundle=self.compile()
        self.assertEqual(bundle['browser']['catalog_sha256'],digest(self.catalog))
        self.assertEqual(validate_reviewed_browser(bundle,self.candidate,self.config),bundle['browser'])

    def test_changed_expected_text_invalidates_approval(self):
        self.catalog['checks'][1]['exact_text']='Backend unavailable'
        with self.assertRaises(BrowserReviewError):self.compile()

    def test_missing_approval_denied(self):
        self.config['approved_browser_ui']=[]
        with self.assertRaises(BrowserReviewError):self.compile()

    def test_image_mismatch_denied(self):
        self.config['browser_runtime']['base_sha256']='c'*64
        with self.assertRaises(BrowserReviewError):self.compile()

    def test_unapproved_source_denied(self):
        self.config['approved_snapshots']=[]
        with self.assertRaises(BrowserReviewError):self.compile()

    def test_wrong_adapter_denied(self):
        self.candidate['adapter']='php_mysql_v1'
        with self.assertRaises(BrowserReviewError):self.compile()

    def test_changed_guest_bundle_denied_at_vm_boundary(self):
        bundle=self.compile();bundle['browser']['page_path']='/other.html'
        with self.assertRaises(BrowserReviewError):validate_reviewed_browser(bundle,self.candidate,self.config)

    def test_staging_never_approves(self):
        cat=stage(self.plan,heading='ProjectIQ D4 pilot',loaded_text='SQLite verified')
        self.assertEqual(cat,self.catalog)
        self.assertNotIn('approval',cat)



class ControllerReports(unittest.TestCase):
    def setUp(self):
        self.plan,self.candidate,self.catalog,self.config=inputs()
        self.patcher=patch('projectiq_executioner.phase4.browser_ui_contracts.validate_test_plan')
        self.patcher.start();self.addCleanup(self.patcher.stop)

    def _vm(self, status='PASS'):
        bundle=guest_bundle()
        result=positive_report(bundle)
        if status=='INCONCLUSIVE':
            result['status']='INCONCLUSIVE'
            result['issue_code']='BROWSER_RUNTIME_UNAVAILABLE'
            for x in result['checks']:x['matched']=None
        return {'status':'STARTUP_VERIFIED','execution':'PERFORMED_IN_DISPOSABLE_VM',
                'cleanup':'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED',
                'managed_runtime':'SIGNED_LEASED_WORKSPACE_REMOVED',
                'backend':'QEMU_DISPOSABLE_QCOW2_VM',
                'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
                'source':dict(SRC),'adapter':'node_fullstack_offline_v1',
                'phase4dc_status':'VALIDATED','phase4dc_browser_report':result}

    def test_preflight_does_not_launch(self):
        from projectiq_executioner.phase4.browser_ui_vm_run import preflight
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'preflight'
            report=preflight(plan=self.plan,catalog=self.catalog,candidate=self.candidate,
                             config=self.config,output_dir=out)
            self.assertEqual(report['status'],'APPROVED_NOT_EXECUTED')
            self.assertEqual(report['browser_installed_in_guest'],'NOT_YET_VERIFIED')
            self.assertTrue((out/'d4c_preflight.json').is_file())

    def _run(self, vm):
        from projectiq_executioner.phase4.browser_ui_vm_run import run
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.execute_vm_candidate',return_value=vm):
                report=run(plan=self.plan,catalog=self.catalog,candidate=self.candidate,
                           config=self.config,output_dir=Path(tmp)/'run')
        return report

    def test_verified_vm_with_dom_pass(self):
        self.assertEqual(self._run(self._vm())['status'],'BROWSER_PASSED')

    def test_missing_chrome_is_not_a_pass(self):
        self.assertEqual(self._run(self._vm('INCONCLUSIVE'))['status'],'BROWSER_INCONCLUSIVE')

    def test_cleanup_failure_overrides_browser_success(self):
        vm=self._vm();vm['cleanup']='NOT_VERIFIED'
        self.assertEqual(self._run(vm)['status'],'CLEANUP_FAILED')

    def test_bad_backend_is_not_a_pass(self):
        vm=self._vm();vm['backend']='DOCKER'
        self.assertEqual(self._run(vm)['status'],'BROWSER_VM_NOT_VERIFIED')

    def test_missing_guest_report_is_not_a_pass(self):
        vm=self._vm();vm.pop('phase4dc_browser_report')
        self.assertEqual(self._run(vm)['status'],'BROWSER_VM_NOT_VERIFIED')


if __name__=='__main__':
    unittest.main()
