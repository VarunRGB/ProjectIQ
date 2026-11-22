"""4D-C loopback/DNS differential tests; no browser or VM launched."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from projectiq_executioner.phase4 import guest_browser_ui as ui

CMD=['/usr/bin/google-chrome-stable','--headless=new','--disable-gpu',
     '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
     '--timeout=20000','--user-data-dir=/workspace/quota/phase4dc-browser-profile',
     '--dump-dom','http://127.0.0.1:8765/index.html']

class LoopbackDifferentialTests(unittest.TestCase):
    def test_app_uid_http_and_dns_ab_controls(self):
        calls=[]
        def fake(args,**kw):
            calls.append((args,kw))
            if args[0]=='/usr/bin/python3':
                self.assertTrue(kw['project_code'])
                self.assertNotIn('browser_profile', kw)
                self.assertIn('127.0.0.1',args[-1])
                self.assertIn('8765',args[-1])
                compile(args[-1], '<guest-python-http>', 'exec')
                return SimpleNamespace(returncode=0,stdout=ui._LOOPBACK_SENTINEL,stderr=b'')
            return SimpleNamespace(returncode=0,stdout=(b'<html><head><title>ProjectIQ D4 Fullstack Pilot</title></head><body><div id="root"><main><h1>ProjectIQ D4 pilot</h1><p>SQLite verified</p></main></div></body></html>'),stderr=b'')
        record=ui._loopback_differential(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        self.assertEqual(record['app_uid_http'],'HTTP_200_EXPECTED_HTML')
        self.assertEqual(record['chrome_without_dns_rule']['result'],'DOM_MATCHED')
        argv,opts=calls[1]
        self.assertNotIn(ui._DNS_RULE,argv)
        self.assertEqual(argv[-1],ui._SMOKE_LOCAL_URL)
        self.assertIn('--dump-dom',argv)
        self.assertIn('--headless=new',argv)
        self.assertNotIn('--no-sandbox',argv)
        self.assertEqual(opts['browser_profile'],True)
        self.assertEqual(opts['timeout'],26)
        self.assertIn('--timeout=12000',argv)
        self.assertTrue(any('phase4dc-smoke-no-dns-rule' in a for a in argv))
        ui._validate_loopback_differential(record)

    def test_unavailable_loopback_is_not_accidental_pass(self):
        def fake(args,**kw):
            if args[0]=='/usr/bin/python3':return SimpleNamespace(returncode=1,stdout=b'',stderr=b'private error')
            return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        value=ui._loopback_differential(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        self.assertEqual(value['app_uid_http'],'HTTP_UNAVAILABLE')
        self.assertEqual(value['chrome_without_dns_rule']['result'],'EMPTY_STDOUT')
        self.assertNotIn('private error',str(value))

    def test_bad_command_refuses_browser_launch(self):
        calls=[]
        def fake(*args,**kw):calls.append(1)
        for bad in (CMD[:-1]+['https://example.org'],CMD+['--no-sandbox'],
                    [x for x in CMD if x != ui._DNS_RULE]):
            value=ui._chrome_without_dns_override(fake,bad,Path('/workspace/quota'),Path('/workspace'))
            self.assertEqual(value['result'],'LAUNCH_REJECTED')
        self.assertFalse(calls)

    def test_schema_rejects_extra_raw_text_and_false_types(self):
        def fake(args,**kw):return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        record=ui._loopback_differential(fake,CMD,Path('/workspace/quota'),Path('/workspace'))
        for bad in ({**record,'raw_html':'private'},
                    {**record,'app_uid_http':'private'},
                    {**record,'chrome_without_dns_rule':{**record['chrome_without_dns_rule'],'stdout_bytes':'0'}}):
            with self.subTest(bad=bad),self.assertRaises(ui.BrowserUiError):
                ui._validate_loopback_differential(bad)

    def test_never_promotes_inconclusive_to_pass(self):
        source={'kind':'github','repository':'Fixture/Approved','commit_sha':'a'*40}
        bundle={'schema_version':'0.1','source':source,'adapter':'node_fullstack_offline_v1',
            'catalog_sha256':'b'*64,'page_path':'/index.html',
            'browser_binary':'/usr/bin/google-chrome-stable',
            'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                      {'tag':'p','exact_text':'SQLite verified'}]}
        report=ui._empty(bundle,issue='BROWSER_EMPTY_STDOUT_DUMP_DOM')
        report['loopback_differential']={
            'classification':'OBSERVATIONAL_NOT_SECURITY_ATTESTATION',
            'app_uid_http':'HTTP_200_EXPECTED_HTML',
            'chrome_without_dns_rule':{'result':'EXPECTED','stdout_bytes':112,
                 'exit_code':0,'stderr_flags':[]}}
        ui.validate_browser_ui_report(bundle,report)
        report['status']='PASS'
        report['issue_code']=None
        report['checks']=[{'tag':'h1','matched':True},{'tag':'p','matched':True}]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(bundle,report)

if __name__=='__main__':unittest.main()
