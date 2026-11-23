"""D4-C deep diagnostics: evidence only; no browser, VM or network required."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import guest_agent
from projectiq_executioner.phase4.guest_browser_ui import (
    _pid_snapshot, _stderr_flags, _launcher_limits, _diagnostics,
    _validate_diagnostics, validate_browser_ui_report, BrowserUiError,
)

SOURCE = {'kind': 'github', 'repository': 'Example/Fixture', 'commit_sha': 'a' * 40}
BUNDLE = {'schema_version': '0.1', 'source': SOURCE,
          'adapter': 'node_fullstack_offline_v1', 'catalog_sha256': 'b' * 64,
          'page_path': '/index.html', 'browser_binary': '/usr/bin/google-chrome-stable',
          'checks': [{'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'}]}


class DeepDiagnostics(unittest.TestCase):
    def _cg_path(self, root, name):
        base = root / 'user.slice' / 'user-1001.slice' / 'user@1001.service'
        return {
            'user_slice': base.parent,
            'user_manager': base,
            'projectiq': base / 'projectiq.slice',
            'evaluation': base / 'projectiq.slice' / 'projectiq-eval.slice'
        }[name]

    def test_hierarchy_reading_including_ancestor_and_local_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for layer in ('user_slice', 'user_manager', 'projectiq', 'evaluation'):
                node = self._cg_path(root, layer)
                node.mkdir(parents=True, exist_ok=True)
                (node / 'pids.max').write_text('80\n')
                (node / 'pids.current').write_text('26\n')
                (node / 'pids.peak').write_text('70\n')
                (node / 'pids.events').write_text('max 5\n')
                (node / 'pids.events.local').write_text('max 3\n')
            observed = _pid_snapshot(root=root, uid=1001)
            self.assertEqual(observed['evaluation']['max'], 80)
            self.assertEqual(observed['user_manager']['current'], 26)
            self.assertEqual(observed['projectiq']['events_local'], 3)
            self.assertEqual(observed['user_slice']['peak'], 70)
            self.assertIsNone(_pid_snapshot(root=root, uid=0)['evaluation']['current'])
            node = self._cg_path(root, 'evaluation')
            (node / 'pids.events').write_text('garbled secret\n')
            self.assertIsNone(_pid_snapshot(root=root, uid=1001)['evaluation']['events'])

    def test_launcher_marker_parses_numeric_only(self):
        marker = b'PIQ4DC_LIMITS nproc=192/192 scope=max current=12\n'
        self.assertEqual(_launcher_limits(marker), {
            'nproc_soft': 192, 'nproc_hard': 192,
            'scope_max': 'max', 'scope_current': 12})
        self.assertIsNone(_launcher_limits(b'private info\n')['nproc_soft'])
        self.assertIsNone(_launcher_limits(marker + marker)['nproc_soft'])
        self.assertIsNone(_launcher_limits(marker.replace(b'192', b'PASSWORD'))['nproc_soft'])

    def test_rare_errors_stay_within_allowlist(self):
        raw = b'pthread_create: Resource temporarily unavailable; secret=CANARY'
        self.assertEqual(_stderr_flags(raw), ['PTHREAD_CREATE', 'EAGAIN'])
        self.assertEqual(_stderr_flags(b'private-only CANARY'), [])
        self.assertNotIn('CANARY', repr(_stderr_flags(raw)))

    def test_diagnostics_schema_and_untrusted_text_never_leaks(self):
        empty = {name: {'max': None, 'current': None, 'peak': None,
                        'events': None, 'events_local': None}
                 for name in ('user_slice', 'user_manager', 'projectiq', 'evaluation')}
        d = _diagnostics(SimpleNamespace(returncode=1,
                         stderr=b'PIQ4DC_LIMITS nproc=192/192 scope=80 current=18\n'
                                b'pthread_create failed: Resource temporarily unavailable'
                                b'secret=CANARY'), empty, empty)
        _validate_diagnostics(d)
        self.assertEqual(d['exit_code'], 1)
        self.assertEqual(d['launcher']['nproc_soft'], 192)
        self.assertEqual(d['stderr_flags'], ['PTHREAD_CREATE', 'EAGAIN'])
        self.assertNotIn('CANARY', repr(d))
        report = {'schema_version': '0.1', 'stage': 'guest_browser_ui',
                  'source': SOURCE, 'adapter': BUNDLE['adapter'],
                  'catalog_sha256': BUNDLE['catalog_sha256'],
                  'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
                  'status': 'INCONCLUSIVE', 'issue_code': 'BROWSER_PROCESS_LIMIT',
                  'checks': [{'tag': 'h1', 'matched': None}], 'diagnostics': d}
        validate_browser_ui_report(BUNDLE, report)
        bad = copy.deepcopy(report)
        bad['diagnostics']['stderr_flags'] = ['CANARY']
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE, bad)
        bad = copy.deepcopy(report)
        bad['diagnostics']['cgroups']['evaluation']['after']['secret'] = 'CANARY'
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE, bad)
        bad = copy.deepcopy(report)
        bad['status'] = 'PASS'
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE, bad)
        bad = copy.deepcopy(report)
        bad['diagnostics']['launcher']['nproc_soft'] = True
        with self.assertRaises(BrowserUiError):
            validate_browser_ui_report(BUNDLE, bad)

    def test_launcher_diagnostic_only_for_reviewed_browser_profile(self):
        src = guest_agent.APP_LIMIT_LAUNCHER
        self.assertIn('if _browser_requested and _browser_valid:', src)
        self.assertIn('PIQ4DC_LIMITS nproc=', src)
        self.assertIn('resource.getrlimit(resource.RLIMIT_NPROC)', src)
        self.assertIn('os.execvpe(sys.argv[1], sys.argv[1:], os.environ)', src)
        self.assertIn('(512 if _browser_extended else 192) if _browser_requested else 96', src)
        self.assertIn('not _browser_requested or not _browser_valid', src)
        self.assertIn('projectiq-eval.slice', src)
        self.assertNotIn('--no-sandbox', '\n'.join(
            line for line in src.splitlines() if 'PIQ4DC_LIMITS' in line))

if __name__ == '__main__':
    unittest.main()
