"""Offline fixed-code diagnostics regression for Phase 4D-D; never runs Chrome/VM."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, Mock

from projectiq_executioner.phase4 import (
    guest_browser_login_cdp as helper,
    guest_browser_login as guest,
    browser_login_contracts as contract,
)


SOURCE = dict(contract.PINNED_SOURCE)
BUNDLE = {'schema_version': '0.1', 'source': SOURCE, 'adapter': 'php_mysql_v1',
          'catalog_sha256': 'a' * 64, 'flow': contract.FLOW,
          'start_path': contract.START_PATH, 'target_path': contract.TARGET_PATH,
          'checks': list(contract.CHECKS),
          'browser_binary': '/usr/bin/google-chrome-stable'}


def fake_proc(code=119, stderr=b''):
    return types.SimpleNamespace(returncode=code, stderr=stderr, stdout=b'')


def test_report(issue):
    return {
        'schema_version': '0.1', 'stage': 'guest_browser_login',
        'source': SOURCE, 'adapter': 'php_mysql_v1',
        'catalog_sha256': 'a' * 64,
        'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
        'status': 'INCONCLUSIVE', 'issue_code': issue,
        'checks': [{'id': k, 'matched': None} for k in contract.CHECKS],
    }


class FakePath:
    def __init__(self, v): self.v = str(v)
    def is_dir(self): return self.v.startswith('/srv/projectiq-workspace/')
    def resolve(self): return self
    def is_relative_to(self, p):
        return self.v.startswith(str(p).rstrip('/') + '/')
    def __truediv__(self, other): return type(self)(self.v + '/' + str(other))
    def __str__(self): return self.v


class Diagnostics(unittest.TestCase):
    def test_01_fixed_enums_agree_on_all_three_boundaries(self):
        self.assertEqual(helper._DIAG_STAGES, guest._DIAG_STAGES)
        self.assertEqual(helper._DIAG_REASONS, guest._DIAG_REASONS)
        self.assertEqual(helper._DIAG_STAGES, contract._DIAG_STAGES)
        self.assertEqual(helper._DIAG_REASONS, contract._DIAG_REASONS)
        self.assertGreater(len(helper._DIAG_STAGES), 10)

    def test_02_exact_fixed_marker_parsed(self):
        code = guest._sanitized_cdp_diagnostic(
            b'PIQ4DD_DIAG TARGET_CREATE CDP_REPLY_TIMEOUT\n')
        self.assertEqual(code, 'LOGIN_DIAG_TARGET_CREATE__CDP_REPLY_TIMEOUT')

    def test_03_other_stderr_rejected(self):
        for raw in (b'password=user123', b'PIQ4DD_DIAG TARGET_CREATE CDP_REPLY_TIMEOUT\nextra',
                    b'PIQ4DD_DIAG TARGET_CREATE UNKNOWN\n',
                    b'PIQ4DD_DIAG UNKNOWN CDP_REPLY_TIMEOUT\n',
                    b'PIQ4DD_DIAG TARGET_CREATE CDP_REPLY_TIMEOUT\r\n',
                    b'X' * 101, b'\xff', 'not bytes', None):
            with self.subTest(raw=repr(raw)[:60]):
                self.assertIsNone(guest._sanitized_cdp_diagnostic(raw))

    def test_04_guest_reports_bounded_stage_without_stderr(self):
        p = fake_proc(stderr=b'PIQ4DD_DIAG PAGE_NAVIGATE CDP_PIPE_CLOSED\n')
        out = guest.run_login(BUNDLE, expected_source=SOURCE,
                              workspace=Path('.'), trusted_process=lambda *a, **kw: p)
        self.assertEqual(out['issue_code'], 'LOGIN_DIAG_PAGE_NAVIGATE__CDP_PIPE_CLOSED')
        self.assertEqual(out['status'], 'INCONCLUSIVE')
        self.assertNotIn('stderr', json.dumps(out))
        contract.validate_login_report(BUNDLE, out)

    def test_05_non_whitelisted_stderr_falls_back(self):
        p = fake_proc(stderr=b'sensitive stack trace; password=user123\n')
        out = guest.run_login(BUNDLE, expected_source=SOURCE,
                              workspace=Path('.'), trusted_process=lambda *a, **kw: p)
        self.assertEqual(out['issue_code'], 'LOGIN_DIAG_NO_MARKER_EXIT_119')
        self.assertNotIn('user123', json.dumps(out))

    def test_06_existing_inconclusive_codes_still_accepted(self):
        for issue in ('LOGIN_CDP_UNAVAILABLE', 'LOGIN_HELPER_UNAVAILABLE',
                      'LOGIN_BROWSER_UNAVAILABLE', 'LOGIN_HELPER_INVALID_REPORT',
                      'LOGIN_DIAG_NO_MARKER_EXIT_118', 'LOGIN_DIAG_NO_MARKER_EXIT_119'):
            contract.validate_login_report(BUNDLE, test_report(issue))

    def test_07_new_inconclusive_codes_accepted(self):
        for stage, reason in [('ENVIRONMENT', 'ENV_REJECTED'),
                              ('CHROME_START', 'OS_FAILURE'),
                              ('FORM_WAIT', 'CDP_REPLY_TIMEOUT'),
                              ('AUTH_TASKS', 'CDP_PIPE_CLOSED')]:
            contract.validate_login_report(BUNDLE, test_report(
                f'LOGIN_DIAG_{stage}__{reason}'))

    def test_08_invalid_issue_codes_rejected(self):
        for issue in ('LOGIN_DIAG_ENVIRONMENT__XYZ',
                      'LOGIN_DIAG_NONEXISTENT__ENV_REJECTED',
                      'LOGIN_DIAG_CHROME_START__OS_FAILURE_MORE',
                      'LOGIN_DIAG_CHROME_START__OS_FAILURE\n',
                      'LOGIN_DIAG_CHROME_START__OS_FAILURE, password=user123',
                      'LOGIN_DIAG_CHROME_START__', None, 11):
            with self.subTest(issue=str(issue)[:50]):
                with self.assertRaises(contract.LoginReviewError):
                    contract.validate_login_report(BUNDLE, test_report(issue))

    def test_09_diag_cannot_certify_pass(self):
        out = test_report('LOGIN_DIAG_FORM_WAIT__CDP_REPLY_TIMEOUT')
        out['status'] = 'PASS'
        for item in out['checks']: item['matched'] = True
        with self.assertRaises(contract.LoginReviewError):
            contract.validate_login_report(BUNDLE, out)

    def test_10_diag_cannot_certify_fail_with_indeterminate_checks(self):
        out = test_report('LOGIN_DIAG_FORM_WAIT__CDP_REPLY_TIMEOUT')
        out['status'] = 'FAIL'
        with self.assertRaises(contract.LoginReviewError):
            contract.validate_login_report(BUNDLE, out)

    def test_11_unsafe_field_never_accepted(self):
        out = test_report('LOGIN_DIAG_FORM_WAIT__CDP_REPLY_TIMEOUT')
        out['cookie'] = 'abc'
        with self.assertRaises(contract.LoginReviewError):
            contract.validate_login_report(BUNDLE, out)

    def test_12_known_cdp_error_classified(self):
        class CdpFailure(Exception): pass
        self.assertEqual(helper._failure_category(CdpFailure('CDP_REPLY_TIMEOUT')),
                         'CDP_REPLY_TIMEOUT')
        self.assertEqual(helper._failure_category(CdpFailure('secret-cookie=abc')),
                         'CDP_UNKNOWN_FAILURE')

    def test_13_os_and_timeout_types_classified(self):
        self.assertEqual(helper._failure_category(OSError('secret')), 'OS_FAILURE')
        self.assertEqual(helper._failure_category(TimeoutError('secret')), 'TIMEOUT_FAILURE')
        self.assertEqual(helper._failure_category(ValueError('secret')), 'VALUE_FAILURE')
        self.assertEqual(helper._failure_category(RuntimeError('secret')), 'INTERNAL_FAILURE')

    def test_14_emission_contains_only_fixed_words(self):
        chunks = []
        with patch.object(helper.os, 'write', side_effect=lambda fd, buf: chunks.append((fd, buf))):
            with patch.object(helper, '_DIAG_STAGE', 'TARGET_CREATE'):
                helper._emit_diagnostic('CDP_REPLY_TIMEOUT')
                helper._emit_diagnostic('raw-danger=password')
        self.assertEqual(chunks[0], (2, b'PIQ4DD_DIAG TARGET_CREATE CDP_REPLY_TIMEOUT\n'))
        self.assertEqual(chunks[1], (2, b'PIQ4DD_DIAG TARGET_CREATE INTERNAL_FAILURE\n'))

    def test_15_unknown_stage_downgrades(self):
        chunks = []
        with patch.object(helper.os, 'write', side_effect=lambda fd, buf: chunks.append(buf)):
            with patch.object(helper, '_DIAG_STAGE', 'danger'):
                helper._emit_diagnostic('OS_FAILURE')
        self.assertEqual(chunks, [b'PIQ4DD_DIAG INTERNAL OS_FAILURE\n'])

    def test_16_env_rejection_emits_code_118_without_chrome(self):
        chunks = []
        with (patch.dict(os.environ, {'HOME': '/tmp/invalid',
                                     'PROJECTIQ_BROWSER_PROFILE': '1'}, clear=True),
              patch.object(helper.sys, 'platform', 'linux'),
              patch.object(helper.os, 'geteuid', return_value=1234, create=True),
              patch.object(helper.sys, 'argv', ['guest_browser_login_cdp.py']),
              patch.object(helper, 'Path', FakePath),
              patch.object(helper.os, 'write', side_effect=lambda fd, buf: chunks.append(buf))):
            code = helper.main()
        self.assertEqual(code, 118)
        self.assertEqual(chunks, [b'PIQ4DD_DIAG ENVIRONMENT ENV_REJECTED\n'])

    def test_17_chrome_start_error_reports_fixed_code(self):
        fake_pipe = types.ModuleType('guest_browser_cdp')
        fake_pipe._start_chrome = Mock(side_effect=OSError('secret text'))
        chunks = []
        with (patch.dict(os.environ, {'HOME': '/srv/projectiq-workspace/app_home',
                                     'PROJECTIQ_BROWSER_PROFILE': '1'}, clear=True),
              patch.object(helper.sys, 'platform', 'linux'),
              patch.object(helper.os, 'geteuid', return_value=1234, create=True),
              patch.object(helper.sys, 'argv', ['guest_browser_login_cdp.py']),
              patch.object(helper, 'Path', FakePath),
              patch.object(helper.os, 'write', side_effect=lambda fd, buf: chunks.append(buf)),
              patch.object(helper, '_load_trusted_cdp_module', return_value=fake_pipe)):
            code = helper.main()
        self.assertEqual(code, 119)
        self.assertEqual(chunks, [b'PIQ4DD_DIAG CHROME_START OS_FAILURE\n'] * 2)
        fake_pipe._start_chrome.assert_called_once()
        self.assertNotIn(b'secret', b''.join(chunks))

    def test_18_first_cdp_operation_has_stage(self):
        class CdpFailure(Exception): pass
        class FailingCdp:
            def call(self, *a, **kw): raise CdpFailure('CDP_REPLY_TIMEOUT')
        with self.assertRaises(CdpFailure):
            helper.run_flow(FailingCdp(), 999999999999)
        self.assertEqual(helper._DIAG_STAGE, 'TARGET_CREATE')

    def test_19_no_mutation_to_fixed_workflow_or_security_inputs(self):
        s = Path(helper.__file__).read_text('utf-8')
        self.assertIn("quota_text = os.environ.get('HOME', '')", s)
        self.assertIn("not quota.resolve().is_relative_to(Path('/srv/projectiq-workspace'))", s)
        self.assertIn('pipe._start_chrome(profile)', s)
        self.assertNotIn('no-sandbox', s)
        self.assertIn("_START_URL = 'http://127.0.0.1:8765/public/login.html'", s)
        self.assertIn("_SAMPLE_PASSWORD = 'user123'", s)

    def test_20_absent_returncode_marker_is_not_auto_passed(self):
        out = guest.run_login(BUNDLE, expected_source=SOURCE, workspace=Path('.'),
            trusted_process=lambda *a, **kw: fake_proc(code=118, stderr=b''))
        self.assertEqual(out['status'], 'INCONCLUSIVE')
        self.assertEqual(out['issue_code'], 'LOGIN_DIAG_NO_MARKER_EXIT_118')
        contract.validate_login_report(BUNDLE, out)

    def test_21_status_0_ignores_stderr_marker(self):
        payload = {'status': 'PASS', 'issue_code': None, 'checks': [
            {'id': key, 'matched': True} for key in contract.CHECKS]}
        proc = types.SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode('ascii'),
                                     stderr=b'PIQ4DD_DIAG TARGET_CREATE CDP_REPLY_TIMEOUT\n')
        out = guest.run_login(BUNDLE, expected_source=SOURCE, workspace=Path('.'),
                              trusted_process=lambda *a, **kw: proc)
        self.assertEqual(out['status'], 'PASS')
        contract.validate_login_report(BUNDLE, out)


if __name__ == '__main__':
    unittest.main()
