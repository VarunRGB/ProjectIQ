"""Regression: browser UI failure metadata is bounded and cannot claim PASS."""
import copy
import unittest
from projectiq_executioner.phase4.guest_browser_ui import (
    _dom_fingerprint, validate_browser_ui_report, BrowserUiError)

SRC = {'kind':'github','repository':'Example/Reviewed','commit_sha':'a'*40}
BUNDLE = {'schema_version':'0.1','source':SRC, 'adapter':'node_fullstack_offline_v1',
          'catalog_sha256':'b'*64, 'page_path':'/index.html',
          'browser_binary':'/usr/bin/google-chrome-stable',
          'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}]}

def report(status, checks, issue):
    return {'schema_version':'0.1','stage':'guest_browser_ui',
            'source':SRC,'adapter':'node_fullstack_offline_v1',
            'catalog_sha256':'b'*64, 'execution':'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
            'status':status,'issue_code':issue,
            'checks':[{'tag':tag,'matched':matched} for tag,matched in checks]}


class TestDomFingerprint(unittest.TestCase):
    def test_expected_rendered_dom_structure(self):
        html = ('<!doctype html><html><head><meta charset="utf-8">'
                '<title>ProjectIQ D4 Fullstack Pilot</title></head><body>'
                '<div id="root"><main><h1>ProjectIQ D4 pilot</h1>'
                '<p>SQLite verified</p></main></div>'
                '<script src="/assets/index-A1.js"></script></body></html>')
        x = _dom_fingerprint(html)
        self.assertTrue(x['h1_expected'])
        self.assertEqual(x['p_state'], 'EXPECTED')
        self.assertTrue(x['vite_js_asset_present'])
        self.assertTrue(x['title_expected'])
        self.assertEqual(x['h1_count'], 1)
        self.assertEqual(x['p_count'], 1)

    def test_blank_react_root_is_detected(self):
        html = ('<html><head><title>ProjectIQ D4 Fullstack Pilot</title></head>'
                '<body><div id="root"></div><script src="/assets/index-A.js">'
                '</script></body></html>')
        x = _dom_fingerprint(html)
        self.assertTrue(x['root_present'])
        self.assertTrue(x['vite_js_asset_present'])
        self.assertFalse(x['main_present'])
        self.assertEqual(x['h1_count'], 0)
        self.assertEqual(x['p_state'], 'ABSENT')

    def test_failed_backend_and_initial_states(self):
        self.assertEqual(_dom_fingerprint('<p>Checking backend...</p>')['p_state'], 'INITIAL')
        self.assertEqual(_dom_fingerprint('<p>Backend unavailable</p>')['p_state'], 'BACKEND_UNAVAILABLE')
        self.assertEqual(_dom_fingerprint('<p>SQLite unavailable</p>')['p_state'], 'SQLITE_UNAVAILABLE')
        self.assertEqual(_dom_fingerprint('<p>Other user data</p>')['p_state'], 'OTHER')

    def test_chrome_error_marker_no_exposure(self):
        x = _dom_fingerprint('<html>chrome-error://chromewebdata</html>')
        self.assertTrue(x['chrome_error_marker'])
        self.assertNotIn('chromewebdata', str(x))

    def test_fail_accepts_strict_fingerprint(self):
        x = report('FAIL',[('h1',False),('p',False)],'RENDERED_DOM_DID_NOT_MATCH')
        x['page_fingerprint'] = _dom_fingerprint('<div id="root"></div>')
        validate_browser_ui_report(BUNDLE,x)

    def test_diagnostics_cannot_claim_pass(self):
        x = report('PASS',[('h1',True),('p',True)],None)
        x['page_fingerprint'] = _dom_fingerprint('<div></div>')
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE,x)

    def test_diagnostics_must_not_attach_to_inconclusive(self):
        x = report('INCONCLUSIVE',[('h1',None),('p',None)],'BROWSER_COMMAND_TIMEOUT')
        x['page_fingerprint'] = _dom_fingerprint('<div></div>')
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE,x)

    def test_rejects_raw_dom_added_to_fingerprint(self):
        x = report('FAIL',[('h1',False),('p',False)],'RENDERED_DOM_DID_NOT_MATCH')
        x['page_fingerprint'] = _dom_fingerprint('<div></div>')
        x['page_fingerprint']['raw_html'] = '<secret>do not leak</secret>'
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE,x)

    def test_rejects_invalid_type_and_raw_p_state(self):
        x = report('FAIL',[('h1',False),('p',False)],'RENDERED_DOM_DID_NOT_MATCH')
        x['page_fingerprint'] = _dom_fingerprint('<div></div>')
        x['page_fingerprint']['p_state'] = 'Secret user data'
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE,x)

    def test_no_page_fingerprint_still_works(self):
        x = report('FAIL',[('h1',False),('p',False)],'RENDERED_DOM_DID_NOT_MATCH')
        validate_browser_ui_report(BUNDLE,x)


if __name__ == '__main__':
    unittest.main()
