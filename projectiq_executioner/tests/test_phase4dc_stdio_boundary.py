"""Reviewed pilot: fixed stdout-path probes never treat empty DOM as FAIL."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from projectiq_executioner.phase4 import guest_browser_ui as ui

SRC = {'kind':'github','repository':'Fixture/Approved','commit_sha':'a'*40}
BUNDLE = {'schema_version':'0.1','source':SRC,'adapter':'node_fullstack_offline_v1',
          'catalog_sha256':'b'*64,'page_path':'/index.html',
          'browser_binary':'/usr/bin/google-chrome-stable',
          'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}]}

class BoundaryTests(unittest.TestCase):
    def test_fixed_stdout_and_version_both_work(self):
        calls=[]
        def run(cmd, **kw):
            calls.append((cmd,kw))
            if len(calls)==1:
                return SimpleNamespace(returncode=0,stdout=ui._STDOUT_MARKER,
                                       stderr=ui._STDERR_MARKER)
            return SimpleNamespace(returncode=0,stdout=b'Google Chrome 143.0\n',stderr=b'')
        result=ui._empty_capture_issue(run,Path('/srv/projectiq-workspace'),
                                       Path('/usr/bin/google-chrome-stable'))
        self.assertEqual(result,'BROWSER_EMPTY_STDOUT_DUMP_DOM')
        self.assertEqual(len(calls),2)
        self.assertTrue(all(x[1]['project_code'] is True for x in calls))
        self.assertTrue(all(x[1]['timeout'] <= 12 for x in calls))
        self.assertNotIn('--no-sandbox',str(calls))

    def test_scope_drops_stdout(self):
        def run(cmd, **kw):
            return SimpleNamespace(returncode=0, stdout=b'',stderr=ui._STDERR_MARKER)
        self.assertEqual(ui._empty_capture_issue(run, Path('/x'),Path('/usr/bin/google-chrome-stable')),
                         'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED')

    def test_version_missing_stdout(self):
        count=0
        def run(cmd, **kw):
            nonlocal count
            count+=1
            if count==1:
                return SimpleNamespace(returncode=0,stdout=ui._STDOUT_MARKER,
                                       stderr=ui._STDERR_MARKER)
            return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        self.assertEqual(ui._empty_capture_issue(run,Path('/x'),Path('/usr/bin/google-chrome-stable')),
                         'BROWSER_EMPTY_STDOUT_CHROME_VERSION_UNVERIFIED')

    def test_launch_fail_closed(self):
        def run(*args, **kw): raise RuntimeError('opaque unsafe context')
        self.assertEqual(ui._empty_capture_issue(run,Path('/x'),Path('/usr/bin/google-chrome-stable')),
                         'BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED')

    def test_new_outcomes_valid_inconclusive_only(self):
        for code in ('BROWSER_EMPTY_STDOUT_SCOPE_UNVERIFIED',
                     'BROWSER_EMPTY_STDOUT_CHROME_VERSION_UNVERIFIED',
                     'BROWSER_EMPTY_STDOUT_DUMP_DOM'):
            report=ui._empty(BUNDLE, issue=code)
            ui.validate_browser_ui_report(BUNDLE,report)
            report['status']='PASS'
            report['checks']=[{'tag':'h1','matched':True},{'tag':'p','matched':True}]
            with self.assertRaises(ui.BrowserUiError):
                ui.validate_browser_ui_report(BUNDLE,report)

if __name__=='__main__': unittest.main()
