"""Phase 4D-C PHP/MySQL login pilot. All tests offline; never run QEMU or PHP.

Tests target the exact MiniCollabApp /public/login.html DOM observed at the
pinned public source revision. This does not test authentication or DB writes.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import tempfile
import unittest

from projectiq_executioner.phase4 import guest_browser_ui as ui
from projectiq_executioner.phase4 import browser_ui_contracts as contracts
from projectiq_executioner.phase4 import browser_ui_stage as stage
from projectiq_executioner.phase4.oracle_contracts import digest
from projectiq_executioner.phase2.vm_worker import guest_agent as guest

SOURCE = {'kind':'github', 'repository':'VarunRGB/Mini-Task-Collaboration-App',
          'commit_sha':'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37'}
CHECKS = [{'tag':'h2','exact_text':'Login'},
          {'tag':'button','exact_text':'Login'}]


def fixtures():
    plan = {'source':dict(SOURCE),'execution_context':{'adapter':'php_mysql_v1'}}
    candidate = {'source':dict(SOURCE),'schema_version':'0.3',
                 'stage':'vm_execution_candidate',
                 'status':'CANDIDATE_NEEDS_VM_VERIFICATION',
                 'worker_provider':'qemu_qcow2_v1','adapter':'php_mysql_v1'}
    catalog = {'schema_version':'0.1','source':dict(SOURCE),
               'page_path':'/public/login.html', 'checks':deepcopy(CHECKS)}
    identity = SOURCE['repository'] + '@' + SOURCE['commit_sha']
    config = {'approved_snapshots':[identity], 'base_sha256':'a'*64,
              'browser_runtime':{'binary':'/usr/bin/google-chrome-stable',
                                 'base_sha256':'a'*64},
              'approved_browser_ui':[identity+'#'+digest(catalog)],
              'browser_resource_profile':'reviewed_browser_384_512_v1'}
    bundle = {'schema_version':'0.1','source':dict(SOURCE),
              'adapter':'php_mysql_v1','catalog_sha256':digest(catalog),
              'page_path':'/public/login.html',
              'checks':deepcopy(CHECKS),
              'browser_binary':'/usr/bin/google-chrome-stable'}
    return plan,candidate,catalog,config,bundle


def php_dom(button='Login', h2='Login'):
    return (f'<!doctype html><html><head><title>Login - Mini Collab App</title>'
            '<link rel="stylesheet" href="styles/login.css"></head><body>'
            f'<h2>{h2}</h2><form id="loginForm"><button type="submit">{button}</button>'
            '</form><script src="js/login.js"></script></body></html>').encode()


class HostReview(unittest.TestCase):
    def setUp(self):
        self.plan,self.candidate,self.catalog,self.config,self.bundle = fixtures()
        self.patcher = patch.object(contracts, 'validate_test_plan')
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def compile(self):
        return contracts.compile_reviewed_browser(self.plan,self.catalog,
                                                 self.candidate,self.config)

    def test_approved_source_and_exact_catalog_pass(self):
        reviewed = self.compile()
        self.assertEqual(reviewed['browser'],self.bundle)
        self.assertEqual(contracts.validate_reviewed_browser(reviewed,self.candidate,self.config),self.bundle)

    def test_missing_catalog_approval_denied(self):
        self.config['approved_browser_ui']=[]
        with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_snapshot_approval_required(self):
        self.config['approved_snapshots']=[]
        with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_wrong_source_denied(self):
        self.candidate['source']['commit_sha']='b'*40
        with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_mismatched_adapter_denied(self):
        self.plan['execution_context']['adapter']='node_fullstack_offline_v1'
        with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_unauthorized_paths_denied(self):
        for path in ('/auth/login.php','/index.html','https://evil.test/',
                     '//evil.test','/public/../config/config.php'):
            with self.subTest(path=path):
                self.catalog['page_path']=path
                with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_weakened_php_oracle_denied_even_if_digest_approved(self):
        for checks in ([CHECKS[0]], [CHECKS[1]],
                       [{'tag':'h2','exact_text':'Wrong'},{'tag':'button','exact_text':'Login'}],
                       list(reversed(CHECKS))):
            with self.subTest(checks=checks):
                self.catalog['checks']=deepcopy(checks)
                identity=SOURCE['repository']+'@'+SOURCE['commit_sha']
                self.config['approved_browser_ui']=[identity+'#'+digest(self.catalog)]
                with self.assertRaises(contracts.BrowserReviewError):self.compile()

    def test_pinned_image_still_required(self):
        self.config['browser_runtime']['base_sha256']='b'*64
        with self.assertRaises(contracts.BrowserReviewError):self.compile()


class GuestContract(unittest.TestCase):
    def setUp(self):self.bundle=fixtures()[-1]

    def test_guest_validates_exact_php_bundle(self):
        ui.validate_guest_browser_bundle(self.bundle,expected_source=SOURCE,
                                          expected_adapter='php_mysql_v1')

    def test_wrong_text_or_path_denied(self):
        for key,value in (('page_path','/public/register.html'),
                          ('page_path','http://evil.test'),
                          ('browser_binary','/tmp/chrome')):
            with self.subTest(key=key):
                b=deepcopy(self.bundle);b[key]=value
                with self.assertRaises(ui.BrowserUiError):
                    ui.validate_guest_browser_bundle(b,expected_adapter='php_mysql_v1')
        b=deepcopy(self.bundle);b['checks'][0]['exact_text']='Anything'
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_guest_browser_bundle(b,expected_adapter='php_mysql_v1')

    def test_dom_parser_reads_real_login_tags_not_script_strings(self):
        self.assertEqual(ui.dom_checks(php_dom().decode(),CHECKS),[
            {'tag':'h2','matched':True},{'tag':'button','matched':True}])
        fake='<script>const x="Login";</script><h2>Wrong</h2><button>Wrong</button>'
        self.assertEqual([x['matched'] for x in ui.dom_checks(fake,CHECKS)], [False,False])

    def test_php_stage_cli_writes_only_unapproved_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan_path=root/'plan.json';plan_path.write_text(json.dumps(fixtures()[0]),'utf-8')
            out=root/'catalog.json'
            code=stage.main(['--plan',str(plan_path),'--php-login','--out',str(out)])
            self.assertEqual(code,0)
            proposed=json.loads(out.read_text('utf-8'))
            self.assertEqual(proposed['page_path'],'/public/login.html')
            self.assertEqual(proposed['checks'],CHECKS)
            self.assertEqual(set(proposed),{'schema_version','source','page_path','checks'})

    def test_inconclusive_cannot_claim_pass(self):
        b=self.bundle
        report=ui._empty(b,issue='BROWSER_EMPTY_STDOUT_DUMP_DOM')
        ui.validate_browser_ui_report(b,report)
        report['status']='PASS';report['issue_code']=None
        with self.assertRaises(ui.BrowserUiError):ui.validate_browser_ui_report(b,report)

    def test_new_pilot_stage_and_legacy_react_stage(self):
        plan=fixtures()[0]
        self.assertEqual(stage.stage_php_login(plan),{
            'schema_version':'0.1','source':SOURCE,'page_path':'/public/login.html',
            'checks':CHECKS})
        with self.assertRaises(ValueError):stage.stage_php_login({
            'source':SOURCE,'execution_context':{'adapter':'node_fullstack_offline_v1'}})
        react=stage.stage({'source':SOURCE,'execution_context':{
            'adapter':'node_fullstack_offline_v1'}},
            heading='ProjectIQ D4 pilot',loaded_text='SQLite verified')
        self.assertEqual(react['page_path'],'/index.html')


class Runtime(unittest.TestCase):
    def launch(self, stdout: bytes, *, cdp_mode=False):
        calls=[]
        def fake(cmd,**kwargs):
            calls.append((cmd,kwargs))
            return SimpleNamespace(returncode=0,stdout=stdout,stderr=b'')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); home=root/'home'; home.mkdir()
            # On Windows, /usr/bin/chrome resolves differently. Mock only test
            # filesystem existence/resolution; never change production allowlist.
            browser=Path('/usr/bin/google-chrome-stable')
            original_is_file=Path.is_file
            resolved=str(browser.resolve())
            def is_file(path):
                return path==browser or original_is_file(path)
            with patch.object(Path,'is_file',is_file),\
                 patch.object(ui,'_BROWSER_PATHS',ui._BROWSER_PATHS | frozenset((resolved,))),\
                 patch.object(ui.os,'access',return_value=True),\
                 patch.dict(os.environ,{'PROJECTIQ_QUOTA_HOME':str(home)}):
                report=ui.run_browser_ui(fixtures()[-1],expected_source=SOURCE,
                           expected_adapter='php_mysql_v1',workspace=root,
                           trusted_process=fake,cdp_mode=cdp_mode)
        return report,calls

    def test_php_chrome_is_exact_local_page_no_sandbox_bypass(self):
        report,calls=self.launch(php_dom())
        self.assertEqual(report['status'],'PASS')
        ui.validate_browser_ui_report(fixtures()[-1],report)
        self.assertEqual(len(calls),1)
        cmd,opts=calls[0]
        self.assertEqual(cmd[-1],'http://127.0.0.1:8765/public/login.html')
        self.assertIn('--dump-dom',cmd)
        self.assertNotIn('--no-sandbox',cmd)
        self.assertTrue(opts['project_code'])
        self.assertTrue(opts['browser_profile'])

    def test_wrong_dom_is_fail_not_pass(self):
        report,calls=self.launch(php_dom(button='Wrong'))
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual([x['matched'] for x in report['checks']],[True,False])
        self.assertNotIn('page_fingerprint',report) # React-only diagnostic
        self.assertEqual(len(calls),1)
        ui.validate_browser_ui_report(fixtures()[-1],report)

    def test_empty_capture_is_inconclusive_no_react_cdp(self):
        report,calls=self.launch(b'')
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertEqual(report['issue_code'],'BROWSER_EMPTY_STDOUT_DUMP_DOM')
        self.assertEqual(len(calls),1)
        ui.validate_browser_ui_report(fixtures()[-1],report)

    def test_cdp_mode_rejected_for_php(self):
        with self.assertRaises(ui.BrowserUiError):self.launch(php_dom(),cdp_mode=True)

    def test_launcher_fixed_url_and_normal_limits(self):
        self.assertIn("'http://127.0.0.1:8765/public/login.html'",guest.APP_LIMIT_LAUNCHER)
        self.assertIn('192',guest.APP_LIMIT_LAUNCHER)
        self.assertIn('512 if _browser_extended else 192',guest.APP_LIMIT_LAUNCHER)
        self.assertIn('resource.RLIMIT_CORE, 0',guest.APP_LIMIT_LAUNCHER)
        self.assertEqual(guest.APP_CGROUP_PIDS,128)

    def test_php_guest_server_browser_before_termination(self):
        _,_,_,_,bundle=fixtures()
        req={'adapter':'php_mysql_v1','probe_path':'/public/login.html',
             'schema_path':'sql/mini_collab_app.sql',
             'phase4dc_browser_bundle':bundle,'phase4dc_source':SOURCE}
        expected=ui._empty(bundle,status='PASS',issue=None)
        expected['checks']=[{'tag':x['tag'],'matched':True} for x in CHECKS]
        class Server:
            def __init__(self):self.stopped=False
            def terminate(self):self.stopped=True
            def wait(self, timeout=None):return 0
        server=Server()
        def verify_browser(*args,**kwargs):
            self.assertFalse(server.stopped)
            return expected
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); (base/'snapshot.zip').write_bytes(b'bounded')
            request=base/'request.json';request.write_text(json.dumps(req),'utf-8')
            checkout=base/'checkout';checkout.mkdir()
            with patch.object(guest,'safe_extract',return_value=checkout),\
                 patch.object(guest,'_share_tree'),\
                 patch.object(guest,'_provision_mysql',return_value=({},'DB_READY')),\
                 patch.object(guest,'_project_popen',return_value=server),\
                 patch.object(guest,'_probe',return_value=(200,'HTML_RESPONSE_OK')),\
                 patch.object(guest,'_run_phase4_on_live_app',return_value=None),\
                 patch.object(guest,'_run_phase4c_on_live_app',return_value=None),\
                 patch.object(guest,'_run_phase4d2_on_live_app',return_value=None),\
                 patch.object(guest,'_run_phase4dc_on_live_app',side_effect=verify_browser):
                result=guest.run(request,base_dir=base)
        self.assertEqual(result['phase4dc_browser_report'],expected)
        self.assertTrue(server.stopped)


if __name__=='__main__':unittest.main()
