"""4D-C tests: Chrome process concurrency, cgroup evidence and redaction."""
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase4.guest_browser_ui import (
    _pid_limit_events, run_browser_ui, validate_browser_ui_report)

SOURCE = {'kind':'github','repository':'Example/FullStack','commit_sha':'a'*40}
BUNDLE = {'schema_version':'0.1','source':SOURCE,'adapter':'node_fullstack_offline_v1',
          'catalog_sha256':'b'*64,'page_path':'/index.html',
          'checks':[{'tag':'h1','exact_text':'ProjectIQ D4 pilot'},
                    {'tag':'p','exact_text':'SQLite verified'}],
          'browser_binary':'/usr/bin/google-chrome-stable'}

class BrowserProcessBudgetTests(unittest.TestCase):
    def test_kernel_events_from_fixed_cgroup_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root/'user.slice'/'user-1001.slice'/'user@1001.service'/'projectiq.slice'/'projectiq-eval.slice'/'pids.events'
            f.parent.mkdir(parents=True)
            f.write_text('max 9\n',encoding='ascii')
            self.assertEqual(_pid_limit_events(root=root, uid=1001),9)
            self.assertIsNone(_pid_limit_events(root=root, uid=1002))
            f.write_text('max 9\nmax 10\n',encoding='ascii')
            self.assertIsNone(_pid_limit_events(root=root, uid=1001))
            self.assertIsNone(_pid_limit_events(root=root, uid=0))

    def run_mocked(self, event_counts, status):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); home=base/'app_home';home.mkdir()
            original_is_file=Path.is_file; original_resolve=Path.resolve
            invoked=[]
            def fake_is_file(p):
                return p.as_posix()=='/usr/bin/google-chrome-stable' or original_is_file(p)
            def fake_resolve(p,*args,**kw):
                if p.as_posix()=='/usr/bin/google-chrome-stable':
                    return PurePosixPath('/opt/google/chrome/chrome')
                return original_resolve(p,*args,**kw)
            def fake_process(cmd,**kw):
                invoked.append((cmd,kw))
                return SimpleNamespace(returncode=status, stderr=b'pthread_create: Resource temporarily unavailable',
                    stdout=b'<h1>ProjectIQ D4 pilot</h1><p>SQLite verified</p>' if status==0 else b'')
            with patch.object(Path,'is_file',fake_is_file), patch.object(Path,'resolve',fake_resolve), \
                 patch('projectiq_executioner.phase4.guest_browser_ui.os.access',return_value=True), \
                 patch('projectiq_executioner.phase4.guest_browser_ui._pid_limit_events',side_effect=event_counts), \
                 patch.dict(os.environ,{'PROJECTIQ_QUOTA_HOME':str(home)}):
                result=run_browser_ui(BUNDLE,expected_source=SOURCE,expected_adapter='node_fullstack_offline_v1',
                                      workspace=base,trusted_process=fake_process)
            validate_browser_ui_report(BUNDLE,result)
            self.assertEqual(len(invoked),1)
            cmd, kw=invoked[0]
            self.assertTrue(kw['project_code'])
            self.assertIn('--renderer-process-limit=1',cmd)
            self.assertIn('--num-raster-threads=1',cmd)
            self.assertNotIn('--no-sandbox',cmd)
            self.assertIn('http://127.0.0.1:8765/index.html',cmd)
            return result

    def test_pid_ceiling_hit_is_diagnosed(self):
        r=self.run_mocked([1,2],1)
        self.assertEqual(r['status'],'INCONCLUSIVE')
        self.assertEqual(r['issue_code'],'BROWSER_CGROUP_PIDS_LIMIT_HIT')
        self.assertTrue(all(c['matched'] is None for c in r['checks']))

    def test_no_pid_ceiling_event_preserves_generic_failure(self):
        r=self.run_mocked([1,1],1)
        self.assertEqual(r['issue_code'],'BROWSER_PROCESS_LIMIT')

    def test_pass_still_requires_dom_evidence(self):
        r=self.run_mocked([1,1],0)
        self.assertEqual(r['status'],'PASS')
        self.assertTrue(all(c['matched'] for c in r['checks']))

if __name__=='__main__':
    unittest.main()
