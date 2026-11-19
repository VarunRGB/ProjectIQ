"""Phase 2D planning and integrated pipeline regression tests; offline only."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.models import RepositorySource
from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.phase2d.capabilities import build_capability_plan
from projectiq_executioner.phase2.phase2d.pipeline import plan_from_manifest, run_pipeline
from projectiq_executioner.phase2.vm_worker.contracts import VMPlanError, build_vm_plan

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
FSDL_SHA = 'f358b6edda071da6666e587a8f1db054965ba106'
FSDL_REPO = 'VarunRGB/FSDL'


def _fsdl():
    return {
        'schema_version': '1.0', 'phase': 1,
        'source': {'kind': 'github', 'repository': FSDL_REPO, 'commit_sha': FSDL_SHA},
        'services': [{'name': 'frontend', 'kind': 'frontend', 'root': '.',
                      'technology': 'HTML/JS frontend (inferred)', 'depends_on': []}],
        'pages': ['index.html'], 'routes': [], 'database_schema_files': [],
        'inventory': {}, 'environment_variables': [], 'findings': [],
    }


def _plans(p1):
    p2a = build_execution_plan(p1)
    p2b = create_preflight(p1, p2a)
    return p2a, p2b, build_vm_plan(p1, p2a, p2b)


class Phase2DCapabilityTest(unittest.TestCase):
    def test_fsdl_preserves_existing_pilot(self):
        p1 = _fsdl()
        a, b, c = _plans(p1)
        report = build_capability_plan(p1, a, b, c)
        self.assertEqual(report['status'], 'PHASE2C_PILOT_ADAPTER_AVAILABLE')
        self.assertEqual(report['existing_pilot_adapter'], 'static_html_v1')
        self.assertEqual(report['services'][0]['implementation_state'], 'PHASE2C_CURATED_PILOT_ADAPTER')
        self.assertEqual(report['execution'], 'NOT_PERFORMED')
        self.assertIn('DENIED', report['launch_authorization'])

    def test_mini_php_mysql_preserved(self):
        p1 = json.loads((FIXTURES / 'mini_task.json').read_text(encoding='utf-8'))
        a, b, c = _plans(p1)
        report = build_capability_plan(p1, a, b, c)
        self.assertEqual(report['existing_pilot_adapter'], 'php_mysql_v1')
        self.assertIn('mysql_server', [s['proposed_profile'] for s in report['services']])
        self.assertTrue(any(x['implementation_state'] == 'PHASE2C_CURATED_PILOT_ADAPTER'
                            for x in report['services']))

    def test_cloud_drop_keeps_cloud_and_node_python_blocked(self):
        p1 = json.loads((FIXTURES / 'cloud_drop.json').read_text(encoding='utf-8'))
        a, b, c = _plans(p1)
        report = build_capability_plan(p1, a, b, c)
        self.assertEqual(report['status'], 'NEEDS_PHASE2D_IMPLEMENTATION')
        self.assertIsNone(report['existing_pilot_adapter'])
        by_name = {s['name']: s for s in report['services']}
        self.assertEqual(by_name['frontend']['proposed_profile'], 'node_spa')
        self.assertEqual(by_name['backend']['proposed_profile'], 'python_web')
        self.assertEqual(by_name['sqlite']['implementation_state'],
                         'PHASE2D_DB_RESOURCE_NOT_IMPLEMENTED')
        self.assertEqual(by_name['amazon_s3']['implementation_state'], 'EXTERNAL_SERVICE_NOT_AUTHORIZED')
        self.assertIn('external_cloud', report['missing_profiles'])
        self.assertIn('sqlite_file', report['missing_profiles'])

    def test_nextjs_not_misrepresented_as_vite(self):
        p1 = json.loads((FIXTURES / 'moss_meadows.json').read_text(encoding='utf-8'))
        a, b, c = _plans(p1)
        report = build_capability_plan(p1, a, b, c)
        self.assertIn('node_ssr', report['missing_profiles'])
        self.assertIsNone(report['existing_pilot_adapter'])

    def test_reject_modified_phase2c_candidate(self):
        p1 = _fsdl()
        a, b, c = _plans(p1)
        for mutation in ('source', 'adapter', 'vm_limits', 'probe_path'):
            corrupted = copy.deepcopy(c)
            if mutation == 'source':
                corrupted['source']['commit_sha'] = '0' * 40
            elif mutation == 'vm_limits':
                corrupted['vm_limits']['ram_mib'] = 1
            else:
                corrupted[mutation] = 'UNTRUSTED_OVERRIDE'
            with self.subTest(mutation=mutation), self.assertRaises(VMPlanError):
                build_capability_plan(p1, a, b, corrupted)

    def test_plan_from_manifest_produces_four_nonexecuting_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            c, d = plan_from_manifest(_fsdl(), out)
            self.assertEqual(c['adapter'], 'static_html_v1')
            self.assertEqual(d['execution'], 'NOT_PERFORMED')
            self.assertEqual(set(p.name for p in out.iterdir()), {
                'phase2a_plan.json', 'phase2b_preflight.json',
                'phase2c_candidate.json', 'phase2d_capabilities.json'})

    def test_existing_manifest_is_plan_only_no_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / 'manifest.json'
            manifest_path.write_text(json.dumps(_fsdl()), encoding='utf-8')
            with patch('projectiq_executioner.phase2.phase2d.pipeline.execute_vm_candidate') as vm, \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.GitHubClient') as github:
                result = run_pipeline(manifest_path=manifest_path, output_dir=Path(tmp) / 'out')
            self.assertEqual(result['status'], 'PLANNED_NO_EXECUTION')
            self.assertEqual(result['execution'], 'NOT_PERFORMED')
            vm.assert_not_called()
            github.assert_not_called()

    def test_manifest_cannot_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VMPlanError):
                run_pipeline(manifest_path=Path(tmp) / 'p1.json',
                             vm_config=Path(tmp) / 'config.json',
                             run_reviewed_fixture=True, output_dir=Path(tmp) / 'out')

    def test_requires_explicit_sha_for_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            for ref in (None, 'main', FSDL_SHA[:-1]):
                with self.subTest(ref=ref), self.assertRaises(VMPlanError):
                    run_pipeline(repository_url='https://github.com/VarunRGB/FSDL',
                                 ref=ref, vm_config=Path(tmp) / 'config.json',
                                 run_reviewed_fixture=True, output_dir=Path(tmp) / 'out')

    def test_unapproved_sha_stops_before_source_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'vm.json'
            cfg.write_text(json.dumps({'base_image': 'ignored.qcow2', 'base_sha256': '0'*64,
                                       'approved_snapshots': [], 'accelerator':'whpx'}), encoding='utf-8')
            source = RepositorySource('github', FSDL_REPO,
                                      'https://github.com/VarunRGB/FSDL', FSDL_SHA, FSDL_SHA, FSDL_SHA)
            with patch('projectiq_executioner.phase2.phase2d.pipeline.GitHubClient') as factory:
                factory.return_value.resolve.return_value = source
                with self.assertRaisesRegex(VMPlanError, 'not approved'):
                    run_pipeline(repository_url=source.url, ref=FSDL_SHA,
                                 vm_config=cfg, run_reviewed_fixture=True,
                                 output_dir=root / 'out')
                factory.return_value.download.assert_not_called()

    def test_plan_mode_does_not_call_vm_even_with_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = RepositorySource('github', FSDL_REPO,
                                      'https://github.com/VarunRGB/FSDL', FSDL_SHA, FSDL_SHA, FSDL_SHA)
            fake_manifest = SimpleNamespace(to_dict=_fsdl)
            with patch('projectiq_executioner.phase2.phase2d.pipeline.GitHubClient') as factory, \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.extract_snapshot', return_value=root), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.analyze_directory', return_value=fake_manifest), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.write_reports'), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.execute_vm_candidate') as vm:
                factory.return_value.resolve.return_value = source
                result = run_pipeline(repository_url=source.url, ref=FSDL_SHA,
                                      output_dir=root / 'out')
                vm.assert_not_called()
                self.assertEqual(factory.return_value.download.call_count, 1)
            self.assertEqual(result['status'], 'PLANNED_NO_EXECUTION')

    def test_approved_fixture_reuses_existing_vm_execution_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'vm.json'
            cfg.write_text(json.dumps({'base_image': 'ignored.qcow2', 'base_sha256': '0'*64,
                                       'approved_snapshots': [FSDL_REPO + '@' + FSDL_SHA],
                                       'accelerator': 'whpx'}), encoding='utf-8')
            source = RepositorySource('github', FSDL_REPO,
                                      'https://github.com/VarunRGB/FSDL', FSDL_SHA, FSDL_SHA, FSDL_SHA)
            fake_manifest = SimpleNamespace(to_dict=_fsdl)
            vm_response = {'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
                           'cleanup': 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'}
            with patch('projectiq_executioner.phase2.phase2d.pipeline.GitHubClient') as factory, \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.extract_snapshot', return_value=root), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.analyze_directory', return_value=fake_manifest), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.write_reports'), \
                 patch('projectiq_executioner.phase2.phase2d.pipeline.execute_vm_candidate', return_value=vm_response) as vm:
                factory.return_value.resolve.return_value = source
                result = run_pipeline(repository_url=source.url, ref=FSDL_SHA,
                                      vm_config=cfg, run_reviewed_fixture=True,
                                      output_dir=root / 'out')
            self.assertEqual(result['status'], 'STARTUP_VERIFIED')
            self.assertEqual(result['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
            self.assertEqual(vm.call_count, 1)
            self.assertEqual(vm.call_args.kwargs['reviewed_fixture'], True)
            self.assertEqual(vm.call_args.args[0]['source']['commit_sha'], FSDL_SHA)

    def test_plan_reports_still_block_unsupported_repo_launch(self):
        p1 = json.loads((FIXTURES / 'cloud_drop.json').read_text('utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            c, d = plan_from_manifest(p1, out)
            self.assertEqual(c['status'], 'NEEDS_ADAPTER')
            self.assertEqual(d['status'], 'NEEDS_PHASE2D_IMPLEMENTATION')
            self.assertEqual(d['execution'], 'NOT_PERFORMED')


if __name__ == '__main__':
    unittest.main()
