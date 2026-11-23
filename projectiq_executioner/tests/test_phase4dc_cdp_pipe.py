"""Focused security and protocol tests for private Chrome CDP fallback."""
import json
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase4.guest_browser_ui import dom_checks


class FakeCdp:
    def __init__(self, *, fail_nav=False, html=None):
        self.actions = []
        self.fail_nav = fail_nav
        self.navigation_error = None
        self.main_frame = None
        self.html = html or ('<!doctype html><html><body><main><h1>ProjectIQ D4 pilot</h1>'
                             '<p>SQLite verified</p></main></body></html>')

    def call(self, method, params=None, *, session=None, deadline):
        self.actions.append((method, params, session))
        if method == 'Target.createTarget':
            return {'targetId': 'page123'}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'sessionABC'}
        if method == 'Page.navigate':
            return {'errorText': 'net::ERR_CONNECTION_REFUSED'} if self.fail_nav else {'frameId': 'frame1'}
        if method == 'Runtime.evaluate':
            return {'result': {'type': 'string', 'value': self.html}}
        return {}


class ChromeCdpPipeTests(unittest.TestCase):
    def test_exact_local_url_and_no_remote_debugging_port(self):
        self.assertEqual(cdp.PAGE, 'http://127.0.0.1:8765/index.html')
        self.assertLessEqual(cdp.MAX_DOM, 128000)
        self.assertLessEqual(cdp.DEADLINE_SECONDS, 30)
        import inspect
        source = inspect.getsource(cdp._start_chrome)
        self.assertIn('--remote-debugging-pipe', source)
        self.assertNotIn('--remote-debugging-port', source)
        self.assertNotIn("'--no-sandbox'", source)
        self.assertIn('close_fds=False', source)
        self.assertIn('start_new_session=True', source)

    def test_protocol_requires_one_page_target(self):
        fake = FakeCdp()
        html = cdp._collect(999999999.0, fake)
        self.assertIn(b'SQLite verified', html)
        self.assertIn(('Target.createTarget', {'url': 'about:blank'}, None), fake.actions)
        self.assertIn(('Target.attachToTarget', {'targetId': 'page123', 'flatten': True}, None), fake.actions)
        self.assertIn(('Page.navigate', {'url': cdp.PAGE}, 'sessionABC'), fake.actions)
        self.assertIn(('Runtime.enable', None, 'sessionABC'), fake.actions)
        checks = [{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                  {'tag':'p','exact_text':'SQLite verified'}]
        self.assertEqual(dom_checks(html.decode('utf-8'), checks),
                         [{'tag':'h1','matched':True},{'tag':'p','matched':True}])

    def test_protocol_fail_closed_on_navigation_error(self):
        with self.assertRaisesRegex(cdp.CdpFailure, 'CDP_NET_ERR_CONNECTION_REFUSED'):
            cdp._collect(999999999.0, FakeCdp(fail_nav=True))

    def test_protocol_rejects_multiple_page_targets(self):
        fake = FakeCdp()
        def two_pages(method, params=None, *, session=None, deadline):
            if method == 'Target.createTarget':
                return {'targetId': None}
            return fake.call(method, params, session=session, deadline=deadline)
        fake.call = two_pages
        with self.assertRaisesRegex(cdp.CdpFailure, 'CDP_NO_PAGE_TARGET'):
            cdp._collect(999999999.0, fake)

    def test_protocol_rejects_large_document(self):
        with self.assertRaisesRegex(cdp.CdpFailure, 'CDP_DOM_TOO_LARGE'):
            cdp._collect(999999999.0, FakeCdp(html='x'*(cdp.MAX_DOM+1)))

    def test_parser_rejects_invalid_reply(self):
        import os
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'{not valid}\0')
            client = cdp.PipeCdp(write_fd, read_fd)
            # Mock readiness only; the real pipe read and JSON parser still run.
            with patch.object(cdp.select, 'select', return_value=([read_fd], [], [])):
                with self.assertRaisesRegex(cdp.CdpFailure, 'CDP_INVALID_JSON'):
                    client._receive(999999999.0)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_fixed_source_path_protected_by_exact_policy(self):
        from projectiq_executioner.phase2.vm_worker import guest_agent
        import inspect
        src = inspect.getsource(guest_agent._project_command)
        self.assertIn("'/home/projectiq/guest_browser_cdp.py'", src)
        self.assertIn("not _APP_CGROUP_ACTIVE", src)
        launcher = guest_agent.APP_LIMIT_LAUNCHER
        self.assertIn("'/home/projectiq/guest_browser_cdp.py'", launcher)
        self.assertIn('sys.argv[1:]', launcher)


class BrowserCdpFallbackTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {
            'schema_version': '0.1',
            'source': {'kind':'github','repository':'Fixture/Approved','commit_sha':'a'*40},
            'adapter':'node_fullstack_offline_v1','catalog_sha256':'b'*64,
            'page_path':'/index.html', 'browser_binary':'/usr/bin/google-chrome-stable',
            'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                      {'tag':'p','exact_text':'SQLite verified'}]}

    def run_case(self, *, alt_exit=0, alt_stdout=None, alt_stderr=b''):
        import tempfile
        import os
        from pathlib import PurePosixPath
        from types import SimpleNamespace
        from projectiq_executioner.phase4 import guest_browser_ui as ui
        expected = (b'<!doctype html><html><body><main><h1>ProjectIQ D4 pilot</h1>'
                    b'<p>SQLite verified</p></main></body></html>')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            home=root/'apphome';home.mkdir()
            calls=[]
            def fake(args, **kw):
                calls.append((args,kw))
                if len(calls) == 1:
                    return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
                return SimpleNamespace(returncode=alt_exit,
                                       stdout=expected if alt_stdout is None else alt_stdout,
                                       stderr=alt_stderr)
            original_is_file=Path.is_file
            original_resolve=Path.resolve
            def is_file(path):
                return path.as_posix()=='/usr/bin/google-chrome-stable' or original_is_file(path)
            def resolve(path,*args,**kw):
                if path.as_posix()=='/usr/bin/google-chrome-stable':
                    return PurePosixPath('/opt/google/chrome/chrome')
                return original_resolve(path,*args,**kw)
            from unittest.mock import patch
            with patch.object(Path,'is_file',is_file),\
                 patch.object(Path,'resolve',resolve),\
                 patch('projectiq_executioner.phase4.guest_browser_ui.os.access',return_value=True),\
                 patch.dict(os.environ,{'PROJECTIQ_QUOTA_HOME':str(home)}):
                report=ui.run_browser_ui(self.bundle, expected_source=self.bundle['source'],
                            expected_adapter=self.bundle['adapter'],workspace=root,
                            trusted_process=fake,cdp_mode=True)
            return report,calls

    def test_empty_cli_dom_recovers_via_private_cdp(self):
        from projectiq_executioner.phase4.guest_browser_ui import validate_browser_ui_report
        report,calls=self.run_case()
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['render_engine'],'CDP_PIPE')
        self.assertEqual([x['matched'] for x in report['checks']],[True,True])
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][0],['/usr/bin/python3','-I','/home/projectiq/guest_browser_cdp.py'])
        self.assertIs(calls[1][1]['browser_profile'],True)
        validate_browser_ui_report(self.bundle,report)

    def test_cdp_not_a_false_pass_on_missing_dynamic_dom(self):
        from projectiq_executioner.phase4.guest_browser_ui import validate_browser_ui_report
        report,_=self.run_case(alt_stdout=b'<h1>ProjectIQ D4 pilot</h1><p>Checking backend...</p>')
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual([x['matched'] for x in report['checks']],[True,False])
        validate_browser_ui_report(self.bundle,report)

    def test_cdp_admin_policy_block_is_inconclusive(self):
        from projectiq_executioner.phase4.guest_browser_ui import validate_browser_ui_report
        report,_=self.run_case(alt_exit=119,alt_stdout=b'',alt_stderr=b'CDP_ADMIN_POLICY_BLOCK\n')
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertEqual(report['issue_code'],'BROWSER_CDP_ADMIN_POLICY_BLOCK')
        validate_browser_ui_report(self.bundle,report)

    def test_cdp_permission_denial_not_a_pass(self):
        report,_=self.run_case(alt_exit=118,alt_stdout=b'',alt_stderr=b'')
        self.assertEqual(report['issue_code'],'BROWSER_CDP_HELPER_REJECTED')


if __name__ == '__main__':
    unittest.main()
