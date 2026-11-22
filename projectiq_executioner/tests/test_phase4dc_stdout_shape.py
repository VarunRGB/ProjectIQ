"""Fail-only browser stdout-shape evidence; no raw page content can escape."""
import unittest
from projectiq_executioner.phase4 import guest_browser_ui as ui

SRC = {'kind':'github','repository':'Fixture/Approved','commit_sha':'a'*40}
BUNDLE = {'schema_version':'0.1','source':SRC,'adapter':'node_fullstack_offline_v1',
          'catalog_sha256':'b'*64,'page_path':'/index.html',
          'browser_binary':'/usr/bin/google-chrome-stable',
          'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}]}

class CaptureShapeTests(unittest.TestCase):
    def test_doctype_empty_document_is_detected(self):
        a = ui._capture_shape(b'<!DOCTYPE html><html lang="en"><head></head><body>\n</body></html>')
        self.assertEqual(a['kind'], 'EMPTY_HTML_DOCUMENT')
        self.assertTrue(a['body_empty'])
        self.assertEqual(a['prefix'], 'DOCTYPE')
        ui._validate_capture_shape(a)

    def test_plain_empty_document(self):
        a = ui._capture_shape(b'<html><head></head><body></body></html>')
        self.assertEqual(a['kind'], 'EMPTY_HTML_DOCUMENT')
        self.assertEqual(a['prefix'], 'HTML')

    def test_non_html_wrapped_output(self):
        a = ui._capture_shape(b'Running scope as unit: piq-app-abcdef.scope\n')
        self.assertEqual(a['kind'], 'WRAPPER_STDOUT')
        self.assertTrue(a['wrapper_marker'])
        self.assertNotIn('abcdef', str(a))

    def test_arbitrary_non_html_is_not_reported(self):
        a = ui._capture_shape(b'private data that must not be exposed')
        self.assertEqual(a['kind'], 'NON_HTML_STDOUT')
        self.assertNotIn('private', str(a))

    def test_chrome_internal_error(self):
        a = ui._capture_shape(b'<!DOCTYPE html><html><body>chrome-error://chromewebdata</body></html>')
        self.assertEqual(a['kind'], 'CHROME_ERROR_DOCUMENT')
        self.assertTrue(a['chrome_error_marker'])

    def test_expected_fixture_html_is_not_blank(self):
        a = ui._capture_shape(b'<!doctype html><html><body><div id="root"><h1>ProjectIQ D4 pilot</h1></div></body></html>')
        self.assertEqual(a['kind'], 'APPLICATION_HTML')
        self.assertFalse(a['body_empty'])

    def test_schema_refuses_excess_fields(self):
        a = ui._capture_shape(b'<html><body></body></html>')
        a['raw_page'] = 'secret'
        with self.assertRaises(ui.BrowserUiError):
            ui._validate_capture_shape(a)

    def test_schema_refuses_bool_as_numeric_and_negative(self):
        a = ui._capture_shape(b'')
        a['byte_count'] = True
        with self.assertRaises(ui.BrowserUiError):
            ui._validate_capture_shape(a)
        a['byte_count'] = -1
        with self.assertRaises(ui.BrowserUiError):
            ui._validate_capture_shape(a)

    def test_capture_evidence_is_fail_only(self):
        valid = {'schema_version':'0.1', 'stage':'guest_browser_ui',
            'source':SRC, 'adapter':BUNDLE['adapter'], 'catalog_sha256':BUNDLE['catalog_sha256'],
            'execution':'HEADLESS_BROWSER_IN_DISPOSABLE_VM', 'status':'FAIL',
            'issue_code':'RENDERED_DOM_DID_NOT_MATCH',
            'checks':[{'tag':'h1','matched':False},{'tag':'p','matched':False}],
            'page_fingerprint':ui._dom_fingerprint('<html><body></body></html>'),
            'navigation_probe':{'status':'UNAVAILABLE','chrome_blank_document':False,
                'static_title_present':False,'react_root_present':False,
                'built_asset_reference_present':False},
            'capture_shape':ui._capture_shape(b'<!doctype html><html><body></body></html>')}
        ui.validate_browser_ui_report(BUNDLE, valid)
        valid['status'] = 'PASS'
        valid['issue_code'] = None
        valid['checks'] = [{'tag':'h1','matched':True},{'tag':'p','matched':True}]
        with self.assertRaises(ui.BrowserUiError):
            ui.validate_browser_ui_report(BUNDLE, valid)

    def test_shape_not_produced_from_unbounded_dom(self):
        with self.assertRaises(ui.BrowserUiError):
            ui._capture_shape(b'a' * (ui.MAX_DOM+1))

if __name__ == '__main__':
    unittest.main()
