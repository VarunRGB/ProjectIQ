"""Offline fail-closed checks for the bounded 4D-D stdout marker transport.

Does not launch Chrome or a VM. Runs on Windows and Linux.
"""
from __future__ import annotations
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_login as guest
from projectiq_executioner.phase4 import guest_browser_login_cdp as helper


SOURCE = dict(guest.PINNED_SOURCE)
BUNDLE = {'schema_version':'0.1', 'source':SOURCE,
          'adapter':'php_mysql_v1', 'catalog_sha256':'a'*64,
          'flow':'minicollab_user_login_session_v1',
          'start_path':'/public/login.html',
          'target_path':'/public/user_dashboard.html',
          'checks':list(guest.CHECKS),
          'browser_binary':'/usr/bin/google-chrome-stable'}


def execute(code, stdout=b'', stderr=b''):
    proc = types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    return guest.run_login(BUNDLE, expected_source=SOURCE, workspace=Path('.'),
                           trusted_process=lambda *a, **kw: proc)


class StdoutMarker(unittest.TestCase):
    def capture(self, reason='CDP_REPLY_TIMEOUT', stage='FORM_WAIT'):
        chunks=[]
        with (patch.object(helper,'_DIAG_STAGE',stage),
              patch.object(helper.os,'write',side_effect=lambda fd, b: chunks.append((fd,bytes(b))) or len(b))):
            helper._emit_stdout_diagnostic(reason)
        return chunks

    def test_01_marker_uses_stdout_only(self):
        self.assertEqual(self.capture(),[(1,b'PIQ4DD_DIAG FORM_WAIT CDP_REPLY_TIMEOUT\n')])

    def test_02_marker_is_bounded(self):
        self.assertLessEqual(len(self.capture()[0][1]),100)

    def test_03_dangerous_reason_is_sanitized(self):
        items=self.capture('password=user123')
        self.assertEqual(items,[(1,b'PIQ4DD_DIAG FORM_WAIT INTERNAL_FAILURE\n')])
        self.assertNotIn(b'user123',items[0][1])

    def test_04_dangerous_stage_is_sanitized(self):
        self.assertEqual(self.capture(stage='password=user123'),
                         [(1,b'PIQ4DD_DIAG INTERNAL CDP_REPLY_TIMEOUT\n')])

    def test_05_stdout_marker_recovers_stage_from_exit_119(self):
        report=execute(119,b'PIQ4DD_DIAG FORM_WAIT CDP_REPLY_TIMEOUT\n')
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_FORM_WAIT__CDP_REPLY_TIMEOUT')
        self.assertTrue(all(c['matched'] is None for c in report['checks']))

    def test_06_fallback_to_existing_stderr_marker(self):
        report=execute(119,b'',b'PIQ4DD_DIAG TARGET_CREATE OS_FAILURE\n')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_TARGET_CREATE__OS_FAILURE')

    def test_07_invalid_stdout_never_escapes(self):
        report=execute(119,b'secret=password=user123')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_NO_MARKER_EXIT_119')
        self.assertNotIn('user123',json.dumps(report))

    def test_08_composite_marker_is_not_accepted(self):
        report=execute(119,b'PIQ4DD_DIAG FORM_WAIT CDP_REPLY_TIMEOUT\npassword=user123\n')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_NO_MARKER_EXIT_119')

    def test_09_unknown_stage_is_denied(self):
        report=execute(119,b'PIQ4DD_DIAG SECRET CDP_REPLY_TIMEOUT\n')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_NO_MARKER_EXIT_119')

    def test_10_known_code_no_false_pass(self):
        report=execute(119,b'PIQ4DD_DIAG AUTH_TASKS INTERNAL_FAILURE\n')
        self.assertNotEqual(report['status'],'PASS')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_AUTH_TASKS__INTERNAL_FAILURE')

    def test_11_exit_zero_diagnostic_not_pass(self):
        report=execute(0,b'PIQ4DD_DIAG FORM_WAIT CDP_REPLY_TIMEOUT\n')
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertEqual(report['issue_code'],'LOGIN_HELPER_INVALID_REPORT')

    def test_12_helper_failure_still_returns_119(self):
        s=Path(helper.__file__).read_text('utf8')
        self.assertIn('_emit_stdout_diagnostic(reason)\n        return 119',s)
        self.assertIn('_emit_stdout_diagnostic(\'RESULT_OVERSIZE\')\n            return 119',s)

    def test_13_unchanged_sandbox_and_workspace_gates(self):
        s=Path(helper.__file__).read_text('utf8')
        self.assertIn("quota_text = os.environ.get('HOME', '')",s)
        self.assertIn(".is_relative_to(Path('/srv/projectiq-workspace'))",s)
        self.assertNotIn('--no-sandbox',s)

    def test_14_whitelists_equal(self):
        self.assertEqual(helper._DIAG_STAGES, guest._DIAG_STAGES)
        self.assertEqual(helper._DIAG_REASONS, guest._DIAG_REASONS)

    def test_15_stderr_extraneous_cannot_override_valid_stdout(self):
        report=execute(119,b'PIQ4DD_DIAG FORM_WAIT CDP_REPLY_TIMEOUT\n',b'secret')
        self.assertEqual(report['issue_code'],'LOGIN_DIAG_FORM_WAIT__CDP_REPLY_TIMEOUT')

if __name__=='__main__':
    unittest.main()
