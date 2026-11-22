"""4D-C fixed data/localhost Chrome smoke diagnostic, offline tests only."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from projectiq_executioner.phase2.vm_worker import guest_agent
from projectiq_executioner.phase4 import guest_browser_ui as ui

SRC={'kind':'github','repository':'Fixture/Approved','commit_sha':'a'*40}
BUNDLE={'schema_version':'0.1','source':SRC,'adapter':'node_fullstack_offline_v1',
        'catalog_sha256':'b'*64,'page_path':'/index.html',
        'browser_binary':'/usr/bin/google-chrome-stable',
        'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                  {'tag':'p','exact_text':'SQLite verified'}]}
CMD=['/usr/bin/google-chrome-stable','--headless=new','--disable-gpu',
     '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
     '--timeout=20000','--user-data-dir=/workspace/quota/phase4dc-browser-profile',
     '--dump-dom','http://127.0.0.1:8765/index.html']

class SmokeTest(unittest.TestCase):
    def test_exact_two_targets_keep_sandbox_and_scope(self):
        calls=[]
        def fake(argv,**kw):
            calls.append((list(argv),kw))
            if len(calls)==1:
                return SimpleNamespace(returncode=0,stderr=b'',stdout=b'<html><h1>PIQ4DC_SMOKE_OK</h1></html>')
            return SimpleNamespace(returncode=0,stderr=b'',stdout=(
                b'<html><head><title>ProjectIQ D4 Fullstack Pilot</title></head>'
                b'<div id="root"></div></html>'))
        result=ui._render_smoke(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        self.assertEqual(result['data']['result'],'EXPECTED')
        self.assertEqual(result['localhost']['result'],'EXPECTED')
        self.assertEqual([c[0][-1] for c in calls],[ui._SMOKE_DATA_URL, ui._SMOKE_LOCAL_URL])
        self.assertEqual(len({next(v for v in c[0] if v.startswith('--user-data-dir=')) for c in calls}),2)
        for args,opts in calls:
            self.assertTrue(opts['project_code'])
            self.assertTrue(opts['browser_profile'])
            self.assertEqual(opts['timeout'],22)
            self.assertIn('--timeout=8000',args)
            self.assertNotIn('--no-sandbox',args)
            self.assertNotIn('--single-process',args)
        ui._validate_render_smoke(result)

    def test_empty_then_nonzero_remains_inconclusive(self):
        def fake(argv, **kw):
            if argv[-1]==ui._SMOKE_DATA_URL:
                return SimpleNamespace(returncode=0,stderr=b'',stdout=b'')
            return SimpleNamespace(returncode=-5,stderr=b'pthread_create: Resource temporarily unavailable',stdout=b'')
        result=ui._render_smoke(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        self.assertEqual(result['data']['result'],'EMPTY_STDOUT')
        self.assertEqual(result['localhost']['result'],'NONZERO')
        self.assertEqual(result['localhost']['stderr_flags'],['PTHREAD_CREATE','EAGAIN'])
        report=ui._empty(BUNDLE,issue='BROWSER_EMPTY_STDOUT_DUMP_DOM')
        report['render_smoke']=result
        ui.validate_browser_ui_report(BUNDLE,report)
        report['status']='PASS'
        report['issue_code']=None
        report['checks']=[{'tag':'h1','matched':True},{'tag':'p','matched':True}]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(BUNDLE,report)

    def test_timeout_and_exception_do_not_leak_exception(self):
        def fake(*args,**kwargs):
            raise RuntimeError('APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT')
        result=ui._render_smoke(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        self.assertEqual(result['data']['result'],'TIMEOUT')
        self.assertEqual(result['localhost']['result'],'TIMEOUT')
        self.assertNotIn('APP_CGROUP_',str(result))
        ui._validate_render_smoke(result)

    def test_reject_unknown_browser_destination(self):
        with patch.object(guest_agent,'_APP_CGROUP_ACTIVE',True), patch.object(guest_agent.os,'getuid',return_value=1001,create=True):
            for dest in ('https://google.com','file:///tmp/student.html','data:text/html,unsafe'):
                with self.subTest(dest=dest),self.assertRaises(guest_agent.GuestFailure):
                    guest_agent._project_command(CMD[:-1]+[dest],browser_profile=True)
            for dest in (ui._SMOKE_DATA_URL,ui._SMOKE_LOCAL_URL):
                result=guest_agent._project_command(CMD[:-1]+[dest],browser_profile=True)
                self.assertIn('PROJECTIQ_BROWSER_PROFILE=1',result)
                self.assertIn('--slice=projectiq-eval.slice',result)

    def test_schema_rejects_raw_html_and_fake_types(self):
        def fake(argv,**kw):return SimpleNamespace(returncode=0,stderr=b'',stdout=b'')
        diag=ui._render_smoke(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        for bad in ({**diag,'raw_html':'private'},
                    {**diag,'data':{**diag['data'],'stdout_bytes':'0'}},
                    {**diag,'localhost':{**diag['localhost'],'stderr_flags':['UNKNOWN']}}):
            with self.subTest(bad=bad),self.assertRaises(ui.BrowserUiError):
                ui._validate_render_smoke(bad)

if __name__=='__main__': unittest.main()
