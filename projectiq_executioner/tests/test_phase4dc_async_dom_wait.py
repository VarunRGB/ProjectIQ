"""4D-C regression: only the real, incomplete React DOM triggers bounded CDP.

No Chrome/QEMU/student code invoked. The pre-existing CDP poll remains the
sole bounded wait; the guest's reviewed exact-text DOM checks stay unchanged.
"""
from __future__ import annotations
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_ui as ui

SOURCE={'kind':'github','repository':'Example/FullStack','commit_sha':'a'*40}
CHECKS=[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
        {'tag':'p','exact_text':'SQLite verified'}]
BUNDLE={'schema_version':'0.1','source':SOURCE,
        'adapter':'node_fullstack_offline_v1','catalog_sha256':'b'*64,
        'page_path':'/index.html','checks':CHECKS,
        'browser_binary':'/usr/bin/google-chrome-stable'}


def html(p='Checking backend...', h1='ProjectIQ D4 pilot', *, script=True):
    asset='<script type="module" src="/assets/index-AbC123.js"></script>' if script else ''
    return (f'<!doctype html><html><head><title>ProjectIQ D4 Fullstack Pilot</title>'
            f'{asset}</head><body><div id="root"><main><h1>{h1}</h1>'
            f'<p>{p}</p></main></div></body></html>').encode('utf-8')


class PendingState(unittest.TestCase):
    def test_initial_exact_fingerprint_triggers(self):
        self.assertTrue(ui._p4dc_pending_backend_dom(html(), CHECKS))

    def test_terminal_states_not_retried(self):
        for p in ('SQLite verified','Backend unavailable','SQLite unavailable',
                  'Unexpected content',''):
            with self.subTest(p=p):
                self.assertFalse(ui._p4dc_pending_backend_dom(html(p), CHECKS))

    def test_wrong_heading_and_missing_assets_are_not_retried(self):
        self.assertFalse(ui._p4dc_pending_backend_dom(html(h1='Wrong heading'), CHECKS))
        self.assertFalse(ui._p4dc_pending_backend_dom(html(script=False), CHECKS))

    def test_unapproved_catalog_never_triggers(self):
        self.assertFalse(ui._p4dc_pending_backend_dom(html(),
            [CHECKS[0], {'tag':'p','exact_text':'Checking backend...'}]))
        self.assertFalse(ui._p4dc_pending_backend_dom(html(), list(reversed(CHECKS))))

    def test_bounded_utf8_or_non_bytes_never_triggers(self):
        self.assertFalse(ui._p4dc_pending_backend_dom(b'\xff\xfe', CHECKS))
        self.assertFalse(ui._p4dc_pending_backend_dom(b'', CHECKS))
        self.assertFalse(ui._p4dc_pending_backend_dom(b'x'*(ui.MAX_DOM+1), CHECKS))
        self.assertFalse(ui._p4dc_pending_backend_dom('Checking backend...', CHECKS))

    def test_script_string_not_a_fake_p_state(self):
        body=html('SQLite verified').replace(b'</body>',
                    b'<script>let x = "Checking backend..."</script></body>')
        self.assertFalse(ui._p4dc_pending_backend_dom(body, CHECKS))

    def test_error_page_does_not_trigger(self):
        body=html().replace(b'</body>',b'chrome-error://chromewebdata/</body>')
        self.assertFalse(ui._p4dc_pending_backend_dom(body, CHECKS))


class BrowserDispatch(unittest.TestCase):
    def execute(self, cli, cdp=None, *, mode=True):
        calls=[]
        def fake_process(cmd, **kwargs):
            calls.append((cmd,kwargs))
            if len(calls)==1:
                return SimpleNamespace(returncode=0,stdout=cli,stderr=b'')
            if len(calls)==2 and cdp is not None:
                return cdp
            self.fail('unexpected extra browser invocation')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'home'; home.mkdir()
            real=Path.is_file
            approved_binary = Path(BUNDLE['browser_binary'])
            resolved_binary = str(approved_binary.resolve())
            def is_file(path):
                return path == approved_binary or real(path)
            # On Windows the Linux binary resolves to a Windows-style path.
            # Extend only the *mocked test module's* trusted-path set, not source.
            with patch.object(Path,'is_file',is_file), \
                    patch.object(ui,'_BROWSER_PATHS',
                                 ui._BROWSER_PATHS | frozenset((resolved_binary,))), \
                    patch.object(ui.os,'access',return_value=True), \
                    patch.dict(os.environ,{'PROJECTIQ_QUOTA_HOME':str(home)}):
                report=ui.run_browser_ui(BUNDLE,expected_source=SOURCE,
                    expected_adapter='node_fullstack_offline_v1',
                    workspace=root,trusted_process=fake_process,cdp_mode=mode)
        ui.validate_browser_ui_report(BUNDLE,report)
        return report,calls

    def test_initial_dom_cdps_to_exact_success(self):
        res,calls=self.execute(html(),SimpleNamespace(
            returncode=0,stdout=html('SQLite verified'),stderr=b''))
        self.assertEqual(res['status'],'PASS')
        self.assertEqual(res['render_engine'],'CDP_PIPE')
        self.assertEqual([x['matched'] for x in res['checks']],[True,True])
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][0],
            ['/usr/bin/python3','-I','/home/projectiq/guest_browser_cdp.py'])
        self.assertTrue(calls[1][1]['browser_profile'])
        self.assertTrue(calls[1][1]['project_code'])
        self.assertEqual(calls[1][1]['timeout'],43)
        self.assertNotIn('--no-sandbox',calls[0][0])

    def test_initial_dom_cdps_but_backend_unavailable_is_fail(self):
        res,calls=self.execute(html(),SimpleNamespace(
            returncode=0,stdout=html('Backend unavailable'),stderr=b''))
        self.assertEqual(res['status'],'FAIL')
        self.assertEqual(res['page_fingerprint']['p_state'],'BACKEND_UNAVAILABLE')
        self.assertEqual([x['matched'] for x in res['checks']],[True,False])
        self.assertEqual(len(calls),2)

    def test_initial_dom_cdps_but_stays_pending_is_fail_not_pass(self):
        res,calls=self.execute(html(),SimpleNamespace(returncode=0,stdout=html(),stderr=b''))
        self.assertEqual(res['status'],'FAIL')
        self.assertEqual(res['page_fingerprint']['p_state'],'INITIAL')
        self.assertEqual(len(calls),2)

    def test_initial_dom_cdp_error_is_inconclusive_not_pass(self):
        res,calls=self.execute(html(),SimpleNamespace(
            returncode=119,stdout=b'',stderr=b'CDP_REPLY_TIMEOUT\n'))
        self.assertEqual(res['status'],'INCONCLUSIVE')
        self.assertEqual(len(calls),2)
        self.assertTrue(all(x['matched'] is None for x in res['checks']))

    def test_final_dom_cli_alone_succeeds(self):
        res,calls=self.execute(html('SQLite verified'))
        self.assertEqual(res['status'],'PASS')
        self.assertEqual(len(calls),1)

    def test_backend_error_cli_alone_is_fail(self):
        res,calls=self.execute(html('Backend unavailable'))
        self.assertEqual(res['status'],'FAIL')
        self.assertEqual(res['page_fingerprint']['p_state'],'BACKEND_UNAVAILABLE')
        self.assertEqual(len(calls),1)

    def test_legacy_non_cdp_mode_remains_fail(self):
        res,calls=self.execute(html(),mode=False)
        self.assertEqual(res['status'],'FAIL')
        self.assertEqual(res['page_fingerprint']['p_state'],'INITIAL')
        self.assertEqual(len(calls),1)

    def test_empty_cli_still_uses_original_cdp_fallback(self):
        res,calls=self.execute(b'',SimpleNamespace(
            returncode=0,stdout=html('SQLite verified'),stderr=b''))
        self.assertEqual(res['status'],'PASS')
        self.assertEqual(len(calls),2)


class ExistingCdpBoundedPoll(unittest.TestCase):
    def test_private_cdp_collect_waits_through_initial_before_returning_exact_state(self):
        from projectiq_executioner.phase4 import guest_browser_cdp as cdp
        class FakeSession:
            navigation_error = None
            main_frame = None
            evaluations = 0
            def call(self, method, params=None, *, session=None, deadline):
                if method == 'Target.createTarget':
                    return {'targetId': 'reviewed-only'}
                if method == 'Target.attachToTarget':
                    return {'sessionId': 'private'}
                if method == 'Page.navigate':
                    return {'frameId':'reviewed-frame'}
                if method == 'Runtime.evaluate':
                    self.evaluations += 1
                    state = 'Checking backend...' if self.evaluations == 1 else 'SQLite verified'
                    return {'result':{'type':'string','value':html(state).decode('utf-8')}}
                return {}
        session = FakeSession()
        with patch.object(cdp.time, 'sleep', return_value=None):
            rendered = cdp._collect(999999999.0, session)
        self.assertEqual(session.evaluations,2)
        self.assertEqual(ui.dom_checks(rendered.decode('utf-8'),CHECKS),
                         [{'tag':'h1','matched':True},{'tag':'p','matched':True}])


if __name__=='__main__':
    unittest.main()
