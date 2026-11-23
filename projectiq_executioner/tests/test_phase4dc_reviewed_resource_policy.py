"""Offline invariants for the operator-reviewed Phase 4D-C browser resource policy.

No QEMU execution; real kernel enforcement remains an acceptance requirement.
"""
from __future__ import annotations
import ast
import tempfile
import types
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QEMU = ROOT / 'phase2/vm_worker/qemu.py'
GUEST = ROOT / 'phase2/vm_worker/guest_agent.py'
ORCH = ROOT / 'phase2/vm_worker/orchestrator.py'


def text(path):
    val = path.read_text(encoding='utf-8')
    ast.parse(val, filename=str(path))
    return val


class BrowserResourcePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.q, cls.g, cls.o = text(QEMU), text(GUEST), text(ORCH)

    def test_01_default_pids_remains_128_on_host(self):
        self.assertIn('APP_CGROUP_PIDS = 128', self.q)
        self.assertIn('TasksMax=128', self.q)

    def test_02_profile_requires_explicit_enum(self):
        self.assertIn('app_cgroup_pids not in (128, 384)', self.q)
        self.assertIn('app_cgroup_pids != 128 and not app_cgroup', self.q)
        self.assertIn('type(app_cgroup_pids) is not int', self.q)

    def test_03_only_one_tasksmax_anchor_replaced(self):
        self.assertIn("bootstrap.count('TasksMax=128\\n') != 1", self.q)
        self.assertIn("bootstrap.replace('TasksMax=128\\n', 'TasksMax=384\\n', 1)", self.q)
        self.assertIn("bootstrap.encode('utf-8')",self.q)

    def test_04_memory_unchanged(self):
        self.assertIn('MemoryMax=2147483648',self.q)
        self.assertIn('APP_CGROUP_MEMORY_BYTES = 2147483648',self.g)

    def test_05_guest_default_remains_128(self):
        self.assertIn('APP_CGROUP_PIDS = 128',self.g)
        self.assertIn('expected_pids: int = APP_CGROUP_PIDS',self.g)

    def test_06_guest_verifies_actual_kernel_limit(self):
        self.assertIn("node.joinpath('pids.max').read_text().strip() != str(expected_pids)",self.g)
        self.assertIn('expected_pids=requested_pids',self.g)

    def test_07_guest_rejects_invalid_resource_claims(self):
        # The original test asserted the old Node-only guard as a source string.
        # Verify the new dual-adapter guard using real controlled inputs.
        from projectiq_executioner.phase2.vm_worker import guest_agent
        choose = guest_agent._reviewed_browser_pids_for_request
        src = {'kind': 'github', 'repository': 'Example/Pinned', 'commit_sha': 'a' * 40}
        def request(adapter, page, checks):
            return {'adapter': adapter, 'phase4dc_browser_resource_pids': 384,
                    'phase4dc_source': dict(src),
                    'phase4dc_browser_bundle': {
                        'schema_version': '0.1', 'adapter': adapter,
                        'source': dict(src), 'page_path': page, 'checks': checks}}
        node = request('node_fullstack_offline_v1', '/index.html', [
            {'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'},
            {'tag': 'p', 'exact_text': 'SQLite verified'}])
        php = request('php_mysql_v1', '/public/login.html', [
            {'tag': 'h2', 'exact_text': 'Login'},
            {'tag': 'button', 'exact_text': 'Login'}])
        self.assertEqual(choose({'adapter': 'php_mysql_v1'}), 128)
        self.assertEqual(choose(node), 384)
        self.assertEqual(choose(php), 384)
        for bad in (
            {**php, 'phase4dc_browser_resource_pids': True},
            {**php, 'phase4dc_browser_resource_pids': 512},
            {**php, 'phase4dc_source': {**src, 'commit_sha': 'b' * 40}},
            {**php, 'phase4dc_browser_bundle': None},
            request('static_html_v1', '/public/login.html', php['phase4dc_browser_bundle']['checks']),
            request('php_mysql_v1', '/auth/login.php', php['phase4dc_browser_bundle']['checks']),
        ):
            with self.subTest(bad=repr(bad)[:70]):
                with self.assertRaises(guest_agent.GuestFailure):
                    choose(bad)

    def test_08_nonbrowser_nproc_unchanged(self):
        self.assertIn('if _browser_requested else 96',self.g)
        self.assertIn('APP_NPROC_HARD = 96',self.g)

    def test_09_browser_nproc_profile_only(self):
        self.assertIn("os.environ.get('PROJECTIQ_REVIEWED_BROWSER_384') == '1'",self.g)
        self.assertIn('512 if _browser_extended else 192',self.g)
        self.assertIn('_browser_extended and',self.g)
        self.assertIn('not _browser_requested or not _browser_valid',self.g)

    def test_10_marker_assigned_only_by_trusted_controller(self):
        self.assertIn('if browser_profile:\n        env_vars',self.g)
        self.assertIn('if _REVIEWED_BROWSER_RESOURCE_ACTIVE:',self.g)
        self.assertIn("env_vars['PROJECTIQ_REVIEWED_BROWSER_384'] = '1'",self.g)
        allowed = self.g.split('    allowed = {',1)[1].split('    env_vars =',1)[0]
        self.assertNotIn('PROJECTIQ_REVIEWED_BROWSER_384',allowed)

    def test_11_host_requires_verified_browser_proof(self):
        self.assertIn('browser_guest_bundle = validate_reviewed_browser',self.o)
        self.assertIn('browser_guest_bundle is not None and',self.o)
        self.assertIn("browser_profile_name == 'reviewed_browser_384_512_v1'",self.o)

    def test_12_ordinary_vms_standard(self):
        self.assertIn("config.get('browser_resource_profile', 'standard_128_192_v1')",self.o)
        self.assertIn('else 128)',self.o)

    def test_13_bootstrap_and_request_tied(self):
        self.assertIn("request['phase4dc_browser_resource_pids'] = browser_cgroup_pids",self.o)
        self.assertIn('app_cgroup_pids=browser_cgroup_pids',self.o)

    def test_14_no_reduced_isolation(self):
        self.assertNotIn("'--no-sandbox',", self.g)
        self.assertIn('APP_CGROUP_IO_BPS = 16777216',self.g)
        self.assertIn('RLIMIT_NOFILE, 512',self.g)

    def test_15_strict_profile_is_not_student_supplied(self):
        # Resource selection lives in the operator-controlled config, not
        # candidates or catalog fields supplied with student projects.
        self.assertIn("browser_profile_name = config.get('browser_resource_profile'",self.o)
        self.assertNotIn("candidate.get('browser_resource_profile'",self.o)

    def test_16_source_rejects_silent_unknown_browser_profile(self):
        self.assertIn("'PHASE4DC_BROWSER_RESOURCE_POLICY_DENIED'",self.o)
        self.assertIn("'standard_128_192_v1', 'reviewed_browser_384_512_v1'",self.o)


    def test_17_launcher_payload_compiles(self):
        tree=ast.parse(self.g)
        launcher=next(node.value for node in tree.body if isinstance(node,ast.Assign)
                      and any(isinstance(target,ast.Name) and target.id=='APP_LIMIT_LAUNCHER'
                              for target in node.targets))
        content=ast.literal_eval(launcher)
        ast.parse(content)
        self.assertIn('512 if _browser_extended else 192',content)
        self.assertIn('if (_browser_requested and not _browser_valid)',content)

    def test_18_real_cgroup_verification_matches_selected_limit(self):
        tree=ast.parse(self.g)
        node=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                  and x.name=='_evaluation_policy_matches')
        module=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
        namespace={
            'Path':Path,'APP_CGROUP_PIDS':128,
            'APP_CGROUP_MEMORY_BYTES':2147483648,
            'APP_CGROUP_IO_BPS':16777216,
            'APP_CGROUP_SLICE':'projectiq-eval.slice',
            'os':types.SimpleNamespace(major=lambda x:0,minor=lambda x:x),
        }
        exec(compile(module,filename=str(GUEST),mode='exec'),namespace)
        check=namespace['_evaluation_policy_matches']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            group='/user.slice/user-42.slice/user@42.service/projectiq.slice/projectiq-eval.slice'
            node=root/group.lstrip('/')
            node.mkdir(parents=True)
            (node/'memory.max').write_text('2147483648\n',encoding='ascii')
            (node/'pids.max').write_text('384\n',encoding='ascii')
            (node/'io.max').write_text('0:11 wbps=16777216\n',encoding='ascii')
            workspace=types.SimpleNamespace(stat=lambda:types.SimpleNamespace(st_dev=11))
            self.assertTrue(check(group,workspace,uid=42,cgroup_root=root,expected_pids=384))
            self.assertFalse(check(group,workspace,uid=42,cgroup_root=root,expected_pids=128))
            self.assertFalse(check(group,workspace,uid=42,cgroup_root=root,expected_pids=True))
            self.assertFalse(check(group,workspace,uid=42,cgroup_root=root,expected_pids=999))
            (node/'memory.max').write_text('0\n',encoding='ascii')
            self.assertFalse(check(group,workspace,uid=42,cgroup_root=root,expected_pids=384))

    def test_19_standard_default_is_enforced(self):
        tree=ast.parse(self.g)
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                and x.name=='_evaluation_policy_matches')
        kw={a.arg:a.default for a in []}
        self.assertIn('expected_pids', [x.arg for x in fn.args.args+fn.args.kwonlyargs])
        self.assertEqual('APP_CGROUP_PIDS', ast.unparse(fn.args.kw_defaults[-1]))


if __name__ == '__main__':
    unittest.main()
