"""Focused offline regression for real-time Chrome DOM capture + fixed loopback evidence."""
import unittest
from unittest.mock import patch
from projectiq_executioner.phase4 import guest_browser_ui as ui

SRC = {'kind':'github','repository':'Example/Fixture','commit_sha':'a'*40}
BUNDLE = {'schema_version':'0.1', 'source':SRC, 'adapter':'node_fullstack_offline_v1',
          'catalog_sha256':'b'*64, 'page_path':'/index.html',
          'browser_binary':'/usr/bin/google-chrome-stable',
          'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}]}


class FakeResponse:
    status=200
    def getheader(self, name, default=None):
        return 'text/html; charset=utf-8' if name.lower()=='content-type' else default
    def read(self, limit):
        assert limit==8193
        return (b'<!doctype html><title>ProjectIQ D4 Fullstack Pilot</title>'
                b'<div id="root"></div><script src="/assets/index-f0123.js"></script>')

class FakeConnection:
    def __init__(self, host, port, timeout):
        assert (host,port,timeout)==('127.0.0.1',8765,4)
    def request(self, method, path):
        assert (method,path)==('GET','/index.html')
    def getresponse(self): return FakeResponse()
    def close(self): pass

class NavigationCaptureRegression(unittest.TestCase):
    def test_real_time_timeout_switch_in_source(self):
        from pathlib import Path
        src=Path(ui.__file__).read_text(encoding='utf-8')
        self.assertIn("'--timeout=20000'",src)
        self.assertNotIn("'--virtual-time-budget=15000'",src)
        self.assertIn("'--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'",src)
        self.assertIn("'--dump-dom'",src)
        self.assertNotIn("'--no-sandbox'", src[src.index('    cmd = [str(browser)'):src.index('    pids_before =')])

    def test_local_control_probes_only_fixed_loopback(self):
        with patch.object(ui.http.client,'HTTPConnection',FakeConnection):
            result=ui._fixed_local_page_probe('<html><head></head><body></body></html>')
        self.assertEqual(result['status'],'HTTP_200_EXPECTED_HTML')
        self.assertTrue(result['chrome_blank_document'])
        self.assertTrue(result['static_title_present'])
        self.assertTrue(result['react_root_present'])
        self.assertTrue(result['built_asset_reference_present'])
        ui._validate_navigation_probe(result)

    def test_nonblank_dom_does_not_claim_about_blank(self):
        with patch.object(ui.http.client,'HTTPConnection',FakeConnection):
            result=ui._fixed_local_page_probe('<html><body><p>Other</p></body></html>')
        self.assertFalse(result['chrome_blank_document'])

    def test_unavailable_probe_never_fakes_success(self):
        with patch.object(ui.http.client,'HTTPConnection',side_effect=OSError('secret')):
            result=ui._fixed_local_page_probe('')
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertNotIn('secret', str(result))
        ui._validate_navigation_probe(result)

    def test_probe_schema_rejects_raw_html(self):
        with patch.object(ui.http.client,'HTTPConnection',FakeConnection):
            result=ui._fixed_local_page_probe('')
        result['raw_html']='private'
        with self.assertRaises(ui.BrowserUiError):
            ui._validate_navigation_probe(result)

    def test_probe_schema_rejects_false_and_non_boolean(self):
        with patch.object(ui.http.client,'HTTPConnection',FakeConnection):
            result=ui._fixed_local_page_probe('')
        result['react_root_present']='true'
        with self.assertRaises(ui.BrowserUiError):
            ui._validate_navigation_probe(result)

    def test_navigation_probe_only_on_failed_checks(self):
        for state,issue,values,accept in [
            ('FAIL','RENDERED_DOM_DID_NOT_MATCH',[False,False],True),
            ('PASS',None,[True,True],False),
            ('INCONCLUSIVE','BROWSER_PROCESS_LIMIT',[None,None],False),
        ]:
            with self.subTest(state=state):
                report={'schema_version':'0.1','stage':'guest_browser_ui',
                        'source':SRC,'adapter':BUNDLE['adapter'],
                        'catalog_sha256':BUNDLE['catalog_sha256'],
                        'execution':'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
                        'status':state,'issue_code':issue,
                        'checks':[{'tag':b['tag'],'matched':v} for b,v in zip(BUNDLE['checks'],values)],
                        'navigation_probe':{'status':'UNAVAILABLE','chrome_blank_document':False,
                                            'static_title_present':False,'react_root_present':False,
                                            'built_asset_reference_present':False}}
                if accept:
                    report['page_fingerprint']=ui._dom_fingerprint('<html><head></head><body></body></html>')
                    ui.validate_browser_ui_report(BUNDLE,report)
                else:
                    with self.assertRaises(ui.BrowserUiError):
                        ui.validate_browser_ui_report(BUNDLE,report)

if __name__=='__main__': unittest.main()
