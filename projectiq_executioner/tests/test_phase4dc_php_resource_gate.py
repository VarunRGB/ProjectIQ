"""PHP resource-profile regression: offline, no QEMU/Chrome/PHP execution."""
from __future__ import annotations
import inspect
import unittest
from projectiq_executioner.phase2.vm_worker import guest_agent, orchestrator

SRC = {'kind': 'github', 'repository': 'VarunRGB/Mini-Task-Collaboration-App',
       'commit_sha': 'a'*40}
PHP_CHECKS = [{'tag': 'h2', 'exact_text': 'Login'},
              {'tag': 'button', 'exact_text': 'Login'}]


def req(adapter='php_mysql_v1', pids=384, **changes):
    b = {'schema_version': '0.1', 'adapter': adapter, 'source': SRC,
         'page_path': '/public/login.html' if adapter == 'php_mysql_v1' else '/index.html',
         'checks': PHP_CHECKS if adapter == 'php_mysql_v1' else [
             {'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'},
             {'tag': 'p', 'exact_text': 'SQLite verified'}]}
    data = {'adapter': adapter, 'phase4dc_browser_resource_pids': pids,
            'phase4dc_source': dict(SRC), 'phase4dc_browser_bundle': b}
    data.update(changes)
    return data


class ReviewedResourceSelection(unittest.TestCase):
    def choose(self, x):
        return guest_agent._reviewed_browser_pids_for_request(x)

    def test_01_normal_nonbrowser_defaults_to_128(self):
        self.assertEqual(self.choose({'adapter': 'php_mysql_v1'}), 128)

    def test_02_normal_browser_explicit_128_still_allowed(self):
        self.assertEqual(self.choose(req(pids=128)), 128)

    def test_03_reviewed_php_login_gets_384(self):
        self.assertEqual(self.choose(req()), 384)

    def test_04_fullstack_original_browser_still_gets_384(self):
        self.assertEqual(self.choose(req(adapter='node_fullstack_offline_v1')), 384)

    def test_05_missing_source_denied(self):
        x = req(); x.pop('phase4dc_source')
        with self.assertRaisesRegex(guest_agent.GuestFailure, 'RESOURCE_POLICY_DENIED'):
            self.choose(x)

    def test_06_source_mismatch_denied(self):
        x = req(); x['phase4dc_source'] = {**SRC, 'commit_sha': 'b'*40}
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_07_wrong_browser_adapter_denied(self):
        x = req(); x['phase4dc_browser_bundle']['adapter'] = 'node_fullstack_offline_v1'
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_08_missing_browser_denied(self):
        x = req(); x.pop('phase4dc_browser_bundle')
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_09_php_wrong_page_denied(self):
        for wrong in ('/index.html', '/auth/login.php', 'https://example.com/'):
            with self.subTest(path=wrong):
                x = req(); x['phase4dc_browser_bundle']['page_path'] = wrong
                with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_10_php_wrong_check_denied(self):
        x = req(); x['phase4dc_browser_bundle']['checks'][1]['exact_text'] = 'Continue'
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_11_php_missing_tag_denied(self):
        x = req(); x['phase4dc_browser_bundle']['checks'].pop()
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_12_other_adapter_denied(self):
        for adapter in ('python_web_offline_v1', 'static_html_v1', 'node_vite_offline_v1'):
            with self.subTest(adapter=adapter):
                with self.assertRaises(guest_agent.GuestFailure): self.choose(req(adapter=adapter))

    def test_13_invalid_task_budget_denied(self):
        for pids in (0, 96, 512, True, '384'):
            with self.subTest(pids=pids):
                with self.assertRaises(guest_agent.GuestFailure): self.choose(req(pids=pids))

    def test_14_wrong_schema_denied(self):
        x = req(); x['phase4dc_browser_bundle']['schema_version'] = 'unknown'
        with self.assertRaises(guest_agent.GuestFailure): self.choose(x)

    def test_15_guest_main_checks_before_kernel_cgroup(self):
        src = inspect.getsource(guest_agent.main)
        self.assertLess(src.index('_reviewed_browser_pids_for_request(meta)'),
                        src.index('_verify_evaluation_cgroup(app_base, expected_pids=requested_pids)'))

    def test_16_host_report_allowlists_exact_fixed_error_only(self):
        src = inspect.getsource(orchestrator.execute_vm_candidate)
        self.assertIn("'REVIEWED_BROWSER_RESOURCE_POLICY_DENIED',", src)
        self.assertIn("reported if reported in allowed_guest_errors else 'UNCLASSIFIED_GUEST_FAILURE'", src)

    def test_17_original_caps_and_browser_policy_unchanged(self):
        self.assertEqual(guest_agent.APP_CGROUP_PIDS, 128)
        self.assertEqual(guest_agent.APP_NPROC_HARD, 96)
        self.assertIn('512 if _browser_extended else 192', guest_agent.APP_LIMIT_LAUNCHER)
        self.assertIn('PROJECTIQ_EVAL_REQUIRED', guest_agent.APP_LIMIT_LAUNCHER)


if __name__ == '__main__':
    unittest.main()
