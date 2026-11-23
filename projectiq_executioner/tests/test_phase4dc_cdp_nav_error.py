"""4D-C Chromium navigation error classification; no raw URLs/logs escape."""
import unittest
from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase4 import guest_browser_ui as ui


class FakeNav:
    def __init__(self, error):
        self.navigation_error = None
        self.main_frame = None
        self.error = error

    def call(self, method, params=None, *, session=None, deadline):
        if method == 'Target.createTarget':
            return {'targetId': 'x'}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'y'}
        if method == 'Page.navigate':
            return {'errorText': self.error}
        return {}


class NavigationFailureTests(unittest.TestCase):
    def test_fixed_chromium_net_error_propagates(self):
        for error in ('net::ERR_INTERNET_DISCONNECTED',
                      'net::ERR_NETWORK_ACCESS_DENIED',
                      'net::ERR_NAME_NOT_RESOLVED',
                      'net::ERR_UNSAFE_PORT',
                      'net::ERR_CONNECTION_REFUSED'):
            with self.subTest(error=error):
                code = 'CDP_NET_' + error.removeprefix('net::')
                with self.assertRaisesRegex(cdp.CdpFailure, code):
                    cdp._collect(999999999.0, FakeNav(error))
                issue = ui._cdp_failure_issue(119, (code + '\n').encode('ascii'))
                self.assertEqual(issue, 'BROWSER_' + code)

    def test_document_network_failure_event_is_distinguished(self):
        handler = cdp.PipeCdp(1, 2)
        handler._observe({'method': 'Network.loadingFailed', 'params': {
            'type':'Document', 'errorText':'net::ERR_NETWORK_ACCESS_DENIED'}})
        self.assertEqual(handler.navigation_error,'CDP_NET_ERR_NETWORK_ACCESS_DENIED')
        handler2=cdp.PipeCdp(1,2)
        handler2._observe({'method':'Network.loadingFailed', 'params': {
            'type':'Script', 'errorText':'net::ERR_NETWORK_ACCESS_DENIED'}})
        self.assertIsNone(handler2.navigation_error)

    def test_no_arbitrary_error_text_is_emitted(self):
        for error in ('net::ERR_'+'A'*200, 'SECRET_FROM_STUDENT',
                      'net::ERR_BAD-URL', 'net::ERR_BAD\nOTHER', None):
            self.assertEqual(cdp._navigation_error_code(error), 'CDP_NAVIGATION_ERROR')
        self.assertEqual(ui._cdp_failure_issue(119,b'SECRET_FROM_STUDENT\n'),
                         'BROWSER_CDP_UNAVAILABLE')

    def test_report_validator_accepts_codes_only_for_inconclusive(self):
        bundle={'schema_version':'0.1','source':{'kind':'github','repository':'Fixture/Reviewed','commit_sha':'a'*40},
                'adapter':'node_fullstack_offline_v1','catalog_sha256':'b'*64,
                'page_path':'/index.html','browser_binary':'/usr/bin/google-chrome-stable',
                'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                          {'tag':'p','exact_text':'SQLite verified'}]}
        for code in ('BROWSER_CDP_NET_ERR_UNSAFE_PORT','BROWSER_CDP_NET_ERR_NETWORK_ACCESS_DENIED'):
            ui.validate_browser_ui_report(bundle, ui._empty(bundle, issue=code))
        for invalid in ('BROWSER_CDP_NET_ERR_X\nSECRET','BROWSER_CDP_NET_ERR_','BROWSER_CDP_NET_ERR_'+'A'*99):
            with self.assertRaises(ui.BrowserUiError):
                ui.validate_browser_ui_report(bundle, ui._empty(bundle, issue=invalid))

if __name__=='__main__':
    unittest.main()
