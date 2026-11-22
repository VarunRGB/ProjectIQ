"""D4-C diagnostics contract: no raw browser stderr or source crosses VM boundary."""
from __future__ import annotations

import unittest
from copy import deepcopy

from projectiq_executioner.phase4.guest_browser_ui import (
    _browser_exit_issue, validate_browser_ui_report, BrowserUiError,
)


class BrowserDiagnosticsTests(unittest.TestCase):
    def test_sandbox_and_dependency_failures(self):
        self.assertEqual(_browser_exit_issue(1, b'No usable sandbox!'),
                         'BROWSER_SANDBOX_UNAVAILABLE')
        self.assertEqual(_browser_exit_issue(1, b'Failed to move to new namespace: Operation not permitted'),
                         'BROWSER_SANDBOX_UNAVAILABLE')
        self.assertEqual(_browser_exit_issue(127, b'error while loading shared libraries: libX.so'),
                         'BROWSER_SHARED_LIBRARY_UNAVAILABLE')

    def test_resource_and_launcher_failures(self):
        self.assertEqual(_browser_exit_issue(112, b''), 'BROWSER_APP_LAUNCHER_REJECTED')
        self.assertEqual(_browser_exit_issue(-9, b''), 'BROWSER_RESOURCE_LIMIT_OR_KILL')
        self.assertEqual(_browser_exit_issue(1, b'pthread_create: Resource temporarily unavailable'),
                         'BROWSER_PROCESS_LIMIT')

    def test_unknown_error_never_leaks_raw_text(self):
        raw = b"secret=PRIVATE_TEST_VALUE from student page"
        self.assertEqual(_browser_exit_issue(7, raw), 'BROWSER_EXIT_NONZERO')
        self.assertEqual(_browser_exit_issue(7, None), 'BROWSER_EXIT_NONZERO')

    def test_schema_accepts_sanitized_inconclusive_code(self):
        source = {'kind': 'github', 'repository': 'Example/Test', 'commit_sha': 'a' * 40}
        bundle = {'schema_version': '0.1', 'source': source,
                  'adapter': 'node_fullstack_offline_v1', 'catalog_sha256': 'b' * 64,
                  'page_path': '/index.html',
                  'checks': [{'tag': 'h1', 'exact_text': 'Hello'}],
                  'browser_binary': '/usr/bin/google-chrome-stable'}
        report = {'schema_version': '0.1', 'stage': 'guest_browser_ui',
                  'source': source, 'adapter': bundle['adapter'],
                  'catalog_sha256': bundle['catalog_sha256'],
                  'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
                  'status': 'INCONCLUSIVE', 'issue_code': 'BROWSER_SANDBOX_UNAVAILABLE',
                  'checks': [{'tag': 'h1', 'matched': None}]}
        validate_browser_ui_report(bundle, report)
        for attack in ['BROWSER_EXIT_NONZERO:secrets', 'No usable sandbox!', 'BROWSER_FAKE']:
            changed = deepcopy(report)
            changed['issue_code'] = attack
            with self.assertRaises(BrowserUiError):
                validate_browser_ui_report(bundle, changed)


if __name__ == '__main__':
    unittest.main()
