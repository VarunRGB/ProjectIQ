"""Phase 4D-D1/D2 offline contract, privacy and CDP-action regression tests.

No Chrome, QEMU, sockets, SQL writes or real account logins are performed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import browser_login_contracts as c
from projectiq_executioner.phase4 import guest_browser_login as guest
from projectiq_executioner.phase4 import guest_browser_login_cdp as helper
from projectiq_executioner.phase4.oracle_contracts import digest


def inputs():
    source = dict(c.PINNED_SOURCE)
    plan = {'source': source, 'execution_context': {'adapter': 'php_mysql_v1'}}
    candidate = {'source': dict(source), 'adapter': 'php_mysql_v1',
                 'stage': 'vm_execution_candidate', 'schema_version': '0.3',
                 'status': 'CANDIDATE_NEEDS_VM_VERIFICATION',
                 'worker_provider': 'qemu_qcow2_v1'}
    catalog = c.login_catalog(source)
    identity = source['repository'] + '@' + source['commit_sha']
    cfg = {'base_sha256': 'a' * 64,
           'approved_snapshots': [identity],
           'browser_resource_profile': 'reviewed_browser_384_512_v1',
           'browser_runtime': {'binary': '/usr/bin/google-chrome-stable',
                               'base_sha256': 'a' * 64},
           'approved_browser_logins': [identity + '#' + digest(catalog)]}
    bundle = {'schema_version': '0.1', 'source': dict(source),
              'adapter': 'php_mysql_v1', 'catalog_sha256': digest(catalog),
              'flow': c.FLOW, 'start_path': c.START_PATH,
              'target_path': c.TARGET_PATH, 'checks': list(c.CHECKS),
              'browser_binary': '/usr/bin/google-chrome-stable'}
    return plan, candidate, catalog, cfg, bundle


def report_for(bundle, states, status=None, issue=None):
    if status is None:
        status = 'PASS' if all(states) else 'FAIL'
    if status == 'FAIL' and issue is None:
        issue = 'LOGIN_FLOW_DID_NOT_MATCH'
    return {'schema_version': '0.1', 'stage': 'guest_browser_login',
            'source': bundle['source'], 'adapter': bundle['adapter'],
            'catalog_sha256': bundle['catalog_sha256'],
            'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
            'status': status, 'issue_code': issue,
            'checks': [{'id': k, 'matched': v} for k, v in zip(c.CHECKS, states)]}


class Review(unittest.TestCase):
    def setUp(self):
        self.plan, self.candidate, self.catalog, self.cfg, self.bundle = inputs()
        p = patch.object(c, 'validate_test_plan', return_value=None)
        p.start(); self.addCleanup(p.stop)

    def compile(self):
        return c.compile_reviewed_login(self.plan, self.candidate,
                                        self.catalog, self.cfg)

    def test_01_pinned_fixture_and_catalog(self):
        self.assertEqual(self.catalog['source'], c.PINNED_SOURCE)
        self.assertEqual(self.catalog['checks'], list(c.CHECKS))
        self.assertEqual(self.catalog['start_path'], '/public/login.html')

    def test_02_approval_compiles_and_revalidates(self):
        compiled = self.compile()
        self.assertEqual(c.validate_reviewed_login(compiled, self.candidate,
                                                   self.cfg), compiled['login'])

    def test_03_missing_snapshot_fails_closed(self):
        self.cfg['approved_snapshots'] = []
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_04_missing_catalog_grant_fails_closed(self):
        self.cfg['approved_browser_logins'] = []
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_05_wrong_resource_profile_fails_closed(self):
        self.cfg['browser_resource_profile'] = 'standard_128_192_v1'
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_06_wrong_source_sha_fails_closed(self):
        self.plan['source']['commit_sha'] = 'f' * 40
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_07_wrong_repository_fails_closed(self):
        self.candidate['source']['repository'] = 'Other/Repo'
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_08_wrong_adapter_fails_closed(self):
        self.candidate['adapter'] = 'node_fullstack_offline_v1'
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_09_outbound_path_not_operator_editable(self):
        self.catalog['start_path'] = 'https://example.com/'
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_10_check_order_not_operator_editable(self):
        self.catalog['checks'].reverse()
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_11_catalog_credentials_not_permitted(self):
        self.catalog['password'] = 'do-not-accept'
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_12_runtime_base_mismatch_fails_closed(self):
        self.cfg['browser_runtime']['base_sha256'] = 'b' * 64
        with self.assertRaises(c.LoginReviewError): self.compile()

    def test_13_added_proof_fields_fails_closed(self):
        proof = self.compile(); proof['actions'] = ['arbitrary']
        with self.assertRaises(c.LoginReviewError):
            c.validate_reviewed_login(proof, self.candidate, self.cfg)

    def test_14_mutated_proof_fails_closed(self):
        proof = self.compile(); proof['login']['flow'] = 'custom'
        with self.assertRaises(c.LoginReviewError):
            c.validate_reviewed_login(proof, self.candidate, self.cfg)

    def test_15_bundle_credential_and_js_fields_rejected(self):
        self.bundle['javascript'] = 'fetch("https://evil.example")'
        with self.assertRaises(c.LoginReviewError):
            c.validate_login_bundle(self.bundle)


class Reports(unittest.TestCase):
    def setUp(self):
        self.bundle = inputs()[-1]

    def test_16_positive_all_true(self):
        c.validate_login_report(self.bundle, report_for(self.bundle, [True]*4))

    def test_17_false_positive_rejected(self):
        r = report_for(self.bundle, [True, True, True, False], status='PASS')
        with self.assertRaises(c.LoginReviewError): c.validate_login_report(self.bundle, r)

    def test_18_unrecognized_field_rejected(self):
        r = report_for(self.bundle, [True]*4)
        r['raw_cookie'] = 'session=secret'
        with self.assertRaises(c.LoginReviewError): c.validate_login_report(self.bundle, r)

    def test_19_wrong_source_rejected(self):
        r = report_for(self.bundle, [True]*4)
        r['source'] = {'kind':'github','repository':'Wrong/Repo','commit_sha':'a'*40}
        with self.assertRaises(c.LoginReviewError): c.validate_login_report(self.bundle, r)

    def test_20_inconclusive_all_none(self):
        r = report_for(self.bundle, [None]*4, status='INCONCLUSIVE',
                       issue='LOGIN_CDP_UNAVAILABLE')
        c.validate_login_report(self.bundle, r)

    def test_21_inconclusive_cannot_contain_success_flags(self):
        r = report_for(self.bundle, [True, None, None, None], status='INCONCLUSIVE',
                       issue='LOGIN_CDP_UNAVAILABLE')
        with self.assertRaises(c.LoginReviewError): c.validate_login_report(self.bundle, r)

    def test_22_duplicate_or_out_of_order_checks_denied(self):
        r = report_for(self.bundle, [True]*4)
        r['checks'][1]['id'] = r['checks'][0]['id']
        with self.assertRaises(c.LoginReviewError): c.validate_login_report(self.bundle, r)


class Dispatch(unittest.TestCase):
    def setUp(self): self.bundle = inputs()[-1]

    def test_23_fixed_trusted_helper_only(self):
        commands = []
        def fake(cmd, **kw):
            commands.append((cmd, kw))
            body = {'status':'PASS', 'issue_code':None,
                    'checks':[{'id':k,'matched':True} for k in c.CHECKS]}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body).encode('ascii'))
        with tempfile.TemporaryDirectory() as tmp:
            r = guest.run_login(self.bundle, expected_source=c.PINNED_SOURCE,
                                workspace=Path(tmp), trusted_process=fake)
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual(commands[0][0], ['/usr/bin/python3','-I',
                                           '/home/projectiq/guest_browser_login_cdp.py'])
        self.assertTrue(commands[0][1]['project_code'])
        self.assertTrue(commands[0][1]['browser_profile'])
        self.assertEqual(len(commands), 1)

    def test_24_subprocess_nonzero_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = guest.run_login(self.bundle, expected_source=c.PINNED_SOURCE,
                 workspace=Path(tmp), trusted_process=lambda *a,**kw:SimpleNamespace(returncode=119, stdout=b''))
        self.assertEqual(r['status'],'INCONCLUSIVE')
        c.validate_login_report(self.bundle, r)

    def test_25_untrusted_helper_json_is_rejected(self):
        payload = {'status':'PASS','issue_code':None,'checks':[], 'password':'x'}
        with tempfile.TemporaryDirectory() as tmp:
            r = guest.run_login(self.bundle, expected_source=c.PINNED_SOURCE,
                workspace=Path(tmp), trusted_process=lambda *a,**kw:SimpleNamespace(
                    returncode=0, stdout=json.dumps(payload).encode()))
        self.assertEqual(r['issue_code'],'LOGIN_HELPER_INVALID_REPORT')

    def test_26_transport_exception_is_inconclusive(self):
        def broken(*a,**k): raise OSError('sensitive transport message')
        with tempfile.TemporaryDirectory() as tmp:
            r = guest.run_login(self.bundle, expected_source=c.PINNED_SOURCE,
                                workspace=Path(tmp), trusted_process=broken)
        self.assertNotIn('sensitive', json.dumps(r))
        self.assertEqual(r['issue_code'],'LOGIN_HELPER_UNAVAILABLE')


class FakeCdp:
    def __init__(self, states=(True,True,True,True)):
        self.states=states; self.calls=[]; self.session_id=None; self.navigation_error=None
    def call(self, method, params=None, *, session=None, deadline=None):
        self.calls.append((method, params, session))
        if method == 'Target.createTarget':return {'targetId':'new-target'}
        if method == 'Target.attachToTarget':return {'sessionId':'one-session'}
        if method == 'Page.navigate':return {'frameId':'one-frame'}
        if method == 'Runtime.evaluate':
            expression = params['expression']
            if expression == helper._FORM_READY: success=self.states[0]
            elif expression == helper._UNAUTHENTICATED_DENIAL:success=self.states[1]
            elif expression == helper._DASHBOARD:success=self.states[2]
            elif expression == helper._AUTHENTICATED_TASKS:success=self.states[3]
            else:success=True  # focus and fixed click
            return {'result':{'type':'boolean', 'value':bool(success)}}
        return {}


class FixedCdpWorkflow(unittest.TestCase):
    def test_27_full_action_path_and_no_external_navigation(self):
        fake=FakeCdp()
        with patch.object(helper.time,'sleep',return_value=None):
            r=helper.run_flow(fake, helper.time.monotonic()+2)
        self.assertEqual(r['status'],'PASS')
        pages=[params['url'] for m,params,_ in fake.calls if m=='Page.navigate']
        self.assertEqual(pages, ['http://127.0.0.1:8765/public/login.html'])
        inserted=[params['text'] for m,params,_ in fake.calls if m=='Input.insertText']
        self.assertEqual(len(inserted),2)
        self.assertNotIn(inserted[0],json.dumps(r))
        self.assertNotIn(inserted[1],json.dumps(r))
        self.assertFalse(any(m=='Page.captureScreenshot' for m,_,_ in fake.calls))

    def test_28_denied_pre_auth_stops_before_credentials(self):
        fake=FakeCdp(states=(True,False,True,True))
        with patch.object(helper.time,'sleep',return_value=None):
            r=helper.run_flow(fake,helper.time.monotonic()+2)
        self.assertEqual(r['status'],'FAIL')
        self.assertFalse(any(m=='Input.insertText' for m,_,_ in fake.calls))

    def test_29_form_missing_stops_before_api_and_credentials(self):
        fake=FakeCdp(states=(False,True,True,True))
        r=helper.run_flow(fake,helper.time.monotonic()-1)
        self.assertEqual(r['status'],'FAIL')
        self.assertFalse(any(m=='Input.insertText' for m,_,_ in fake.calls))

    def test_30_dashboard_without_api_access_cannot_pass(self):
        fake=FakeCdp(states=(True,True,True,False))
        with patch.object(helper.time,'sleep',return_value=None):
            r=helper.run_flow(fake,helper.time.monotonic()+2)
        self.assertEqual(r['status'],'FAIL')
        self.assertEqual(r['checks'][-1]['matched'],False)

    def test_31_wrong_dashboard_fails(self):
        fake=FakeCdp(states=(True,True,False,True))
        with patch.object(helper.time,'sleep',return_value=None):
            r=helper.run_flow(fake,helper.time.monotonic()+0.01)
        self.assertEqual(r['status'],'FAIL')
        self.assertFalse(r['checks'][2]['matched'])

    def test_32_helper_results_are_boolean_only(self):
        r=helper._checks_result([True]*4)
        self.assertEqual(set(r),{'status','issue_code','checks'})
        self.assertTrue(all(type(x['matched']) is bool for x in r['checks']))

if __name__ == '__main__':
    unittest.main()
