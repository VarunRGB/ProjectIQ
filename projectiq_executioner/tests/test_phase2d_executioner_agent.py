"""No-network, no-VM unit tests for the one-call reviewed-fixture agent."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.phase2d import executioner_agent as agent

SHA = 'a' * 40
REPO = 'https://github.com/Teacher/StudentPortal'
IDENTITY = 'Teacher/StudentPortal@' + SHA


def _candidate(adapter='static_html_v1'):
    return {
        'source': {'kind': 'github', 'repository': 'Teacher/StudentPortal', 'commit_sha': SHA},
        'adapter': adapter,
        'status': 'CANDIDATE_NEEDS_VM_VERIFICATION' if adapter else 'NEEDS_ADAPTER',
    }


def _vm(**changes):
    obj = {
        'status': 'STARTUP_VERIFIED', 'execution': 'PERFORMED_IN_DISPOSABLE_VM',
        'cleanup': agent.CLEAN, 'security_attestation': agent.ATTESTATION,
        'guest_result': {'status': 'STARTUP_VERIFIED', 'http_status': 200},
        'adapter': 'static_html_v1',
        'source': _candidate()['source'],
        'backend': 'QEMU_DISPOSABLE_QCOW2_VM',
    }
    return {**obj, **changes}


class AgentFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'demo'
        self.config = self.root / 'vm_config.json'

    def _plan(self, adapter='static_html_v1'):
        candidate = _candidate(adapter)
        def run_pipeline(*, output_dir, **kwargs):
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            (Path(output_dir) / 'phase2c_candidate.json').write_text(
                json.dumps(candidate), encoding='utf-8')
            return {'source': candidate['source'], 'status': 'PLANNED_NO_EXECUTION'}
        return run_pipeline

    def _run(self, adapter='static_html_v1', approved=True, execute=True,
             preparation=None, run_value=None, extra_config=None):
        cfg = {'approved_snapshots': [IDENTITY] if approved else []}
        if extra_config is not None:
            cfg.update(extra_config)
        with patch.object(agent, 'run_pipeline', side_effect=self._plan(adapter)) as plan, \
             patch.object(agent, 'load_config', return_value=cfg), \
             patch.object(agent, 'execute_vm_candidate', return_value=(run_value or _vm())) as worker:
            result = agent.run_submission(
                repo_url=REPO, output_dir=self.out,
                vm_config=self.config if execute else None,
                execute=execute, preparation=preparation,
            )
        return result, plan, worker

    def test_plan_only_does_not_launch(self):
        r, _, vm = self._run(execute=False)
        self.assertEqual(r['status'], 'PLANNED_NO_EXECUTION')
        vm.assert_not_called()
        self.assertEqual(r['steps'][2]['status'], 'SKIPPED')

    def test_unsupported_stack_never_launches(self):
        r, _, vm = self._run(adapter=None)
        self.assertEqual(r['status'], 'NEEDS_ADAPTER')
        self.assertEqual(r['steps'][1]['status'], 'BLOCKED')
        vm.assert_not_called()

    def test_unapproved_sha_cannot_launch(self):
        r, _, vm = self._run(approved=False)
        self.assertEqual(r['status'], 'NEEDS_OPERATOR_APPROVAL')
        self.assertEqual(r['review_identity'], IDENTITY)
        self.assertEqual(r['execution'], 'NOT_PERFORMED')
        vm.assert_not_called()

    def test_static_success_single_call(self):
        r, _, vm = self._run()
        self.assertEqual(r['status'], 'STARTUP_VERIFIED')
        self.assertEqual(r['http_status'], 200)
        self.assertEqual(r['cleanup'], agent.CLEAN)
        self.assertEqual(len(r['steps']), 6)
        self.assertTrue(all(x['status'] == 'PASS' for x in r['steps']))
        self.assertEqual(r['security_attestation'], agent.ATTESTATION)
        vm.assert_called_once()
        self.assertTrue(vm.call_args.kwargs['reviewed_fixture'])
        self.assertEqual(vm.call_args.args[0]['source']['commit_sha'], SHA)
        self.assertTrue((self.out / 'orchestration_result.json').is_file())

    def test_php_reuses_existing_runtime(self):
        r, _, vm = self._run(adapter='php_mysql_v1', run_value=_vm(adapter='php_mysql_v1'))
        self.assertEqual(r['status'], 'STARTUP_VERIFIED')
        vm.assert_called_once()

    def test_worker_startup_failure_is_not_success(self):
        r, _, _ = self._run(run_value=_vm(status='DENIED'))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_worker_cleanup_failure_is_not_success(self):
        r, _, _ = self._run(run_value=_vm(cleanup='CLEANUP_FAILED'))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')
        self.assertEqual(r['steps'][-1]['status'], 'FAILED')

    def test_worker_http_is_required(self):
        r, _, _ = self._run(run_value=_vm(guest_result={'status': 'STARTUP_VERIFIED', 'http_status': 503}))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_worker_source_mismatch_fails_closed(self):
        r, _, _ = self._run(run_value=_vm(source={**_candidate()['source'], 'commit_sha': 'b' * 40}))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_worker_adapter_mismatch_fails_closed(self):
        r, _, _ = self._run(run_value=_vm(adapter='php_mysql_v1'))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_worker_guest_status_mismatch_fails_closed(self):
        r, _, _ = self._run(run_value=_vm(guest_result={'status':'FAILED', 'http_status':200}))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_worker_backend_mismatch_fails_closed(self):
        r, _, _ = self._run(run_value=_vm(backend='HOST_SUBPROCESS'))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')

    def test_attestation_cannot_be_promoted(self):
        r, _, _ = self._run(run_value=_vm(security_attestation='ATTESTED'))
        self.assertEqual(r['status'], 'EXECUTION_NOT_VERIFIED')
        self.assertEqual(r['security_attestation'], agent.ATTESTATION)

    def test_node_missing_cache_needs_preparation(self):
        with patch.object(agent, 'approved_node_vite_assets', side_effect=agent.OfflineAssetError('MISSING')):
            # NODE_ADAPTERS retains the reference bound at module import.
            with patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1': (
                    'node_vite_offline', lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}):
                r, _, vm = self._run(adapter='node_vite_offline_v1')
        self.assertEqual(r['status'], 'NEEDS_OFFLINE_DEPENDENCIES')
        self.assertEqual(r['failure_code'], 'PREPARATION_NOT_ENABLED')
        vm.assert_not_called()

    def test_one_call_node_auto_prepare_then_vm(self):
        def assets(config, identity):
            if identity not in config.get('node_vite_offline', {}):
                raise agent.OfflineAssetError('MISSING')
            return config['node_vite_offline'][identity]
        cfg = {'approved_snapshots': [IDENTITY]}
        prepared_cfg = {**cfg, 'node_vite_offline': {IDENTITY: {'test': True}}}
        with patch.dict(agent.NODE_ADAPTERS, {
                'node_vite_offline_v1': ('node_vite_offline', assets)}), \
             patch.object(agent, 'run_pipeline', side_effect=self._plan('node_vite_offline_v1')), \
             patch.object(agent, 'load_config', return_value=cfg), \
             patch.object(agent, '_prepare_assets', return_value=(prepared_cfg, 'PREPARED_AND_VERIFIED')) as prep, \
             patch.object(agent, 'execute_vm_candidate',
                          return_value=_vm(adapter='node_vite_offline_v1')) as worker:
            r = agent.run_submission(repo_url=REPO, output_dir=self.out,
                    vm_config=self.config,
                    preparation=agent.DependencyPreparation(enabled=True,
                        allow_trusted_network=True))
        self.assertEqual(r['status'], 'STARTUP_VERIFIED')
        self.assertEqual(r['dependency_state'], 'PREPARED_AND_VERIFIED')
        self.assertEqual(r['steps'][3]['status'], 'PASS')
        prep.assert_called_once()
        self.assertIs(worker.call_args.args[1], prepared_cfg)

    def test_cache_preparation_error_blocks_execution(self):
        with patch.dict(agent.NODE_ADAPTERS, {
                'node_vite_offline_v1': ('node_vite_offline',
                    lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}), \
             patch.object(agent, '_prepare_assets', side_effect=agent.OfflineAssetError('BAD_HASH')):
            r, _, vm = self._run(adapter='node_vite_offline_v1',
                    preparation=agent.DependencyPreparation(enabled=True, allow_trusted_network=True))
        self.assertEqual(r['status'], 'ORCHESTRATION_ERROR')
        self.assertEqual(r['failure_code'], 'OFFLINE_DEPENDENCY_VALIDATION_FAILED')
        vm.assert_not_called()

    def test_node_unapproved_cannot_prepare(self):
        with patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1': (
                'node_vite_offline', lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}):
            r, _, vm = self._run(adapter='node_vite_offline_v1', approved=False,
                                  preparation=agent.DependencyPreparation(enabled=True,
                                      allow_trusted_network=True))
        self.assertEqual(r['status'], 'NEEDS_OPERATOR_APPROVAL')
        vm.assert_not_called()

    def test_node_prep_requires_explicit_network_authorization(self):
        with patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1': (
                'node_vite_offline', lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}):
            r, _, vm = self._run(adapter='node_vite_offline_v1',
                                  preparation=agent.DependencyPreparation(enabled=True))
        self.assertEqual(r['failure_code'], 'TRUSTED_DEPENDENCY_NETWORK_NOT_AUTHORIZED')
        vm.assert_not_called()

    def test_node_prep_requires_pinned_runtime(self):
        with patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1': (
                'node_vite_offline', lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}):
            r, _, _ = self._run(adapter='node_vite_offline_v1',
                                preparation=agent.DependencyPreparation(enabled=True,
                                    allow_trusted_network=True))
        self.assertEqual(r['failure_code'], 'PINNED_NODE_ARTIFACT_REQUIRED')

    def test_python_prep_requires_pip_wheel(self):
        with patch.object(agent, 'approved_python_web_assets', side_effect=agent.OfflineAssetError('MISSING')):
            r, _, _ = self._run(adapter=agent.PYTHON_ADAPTER,
                                preparation=agent.DependencyPreparation(enabled=True,
                                    allow_trusted_network=True))
        self.assertEqual(r['failure_code'], 'PINNED_PIP_ARTIFACT_REQUIRED')

    def _node_profile(self):
        # Exercise the real offline-assets verifier, not a stubbed successful
        # verifier. These are inert, non-executed test bytes; the VM runner is
        # mocked. The production verifier must check file hashes and names.
        node = self.root / 'node-v24.22.3-linux-x64.tar.xz'
        cache = self.root / 'npm-cache.zip'
        node.write_bytes(b'INERT_NODE_TEST_BYTES')
        cache.write_bytes(b'INERT_NPM_TEST_BYTES')
        return {
            'node_version': 'v24.22.3',
            'node_tarball': str(node),
            'node_sha256': hashlib.sha256(node.read_bytes()).hexdigest(),
            'npm_cache_zip': str(cache),
            'npm_cache_sha256': hashlib.sha256(cache.read_bytes()).hexdigest(),
            'lockfile_sha256': 'f' * 64,
        }

    def test_existing_node_profile_runs_without_network_preparation(self):
        profile = self._node_profile()
        r, _, vm = self._run(
            adapter='node_vite_offline_v1',
            run_value=_vm(adapter='node_vite_offline_v1'),
            extra_config={'node_vite_offline': {IDENTITY: profile}},
        )
        self.assertEqual(r['status'], 'STARTUP_VERIFIED')
        self.assertEqual(r['dependency_state'], 'ALREADY_VERIFIED_OR_NOT_REQUIRED')
        self.assertEqual(r['steps'][3]['status'], 'PASS')
        vm.assert_called_once()
        self.assertEqual(vm.call_args.args[1]['node_vite_offline'][IDENTITY], profile)

    def test_existing_node_profile_hash_mismatch_denies_execution(self):
        profile = self._node_profile()
        profile['npm_cache_sha256'] = '0' * 64
        r, _, vm = self._run(
            adapter='node_vite_offline_v1',
            run_value=_vm(adapter='node_vite_offline_v1'),
            extra_config={'node_vite_offline': {IDENTITY: profile}},
        )
        self.assertEqual(r['status'], 'NEEDS_OFFLINE_DEPENDENCIES')
        self.assertEqual(r['failure_code'], 'PREPARATION_NOT_ENABLED')
        vm.assert_not_called()

    def test_exception_sanitized_without_leaking_credentials(self):
        def fail(*args, **kwargs):
            raise RuntimeError('SECRET_PASSWORD_123')
        with patch.object(agent, 'run_pipeline', side_effect=fail):
            r = agent.run_submission(repo_url=REPO, output_dir=self.out,
                                     vm_config=self.config)
        self.assertEqual(r['status'], 'ORCHESTRATION_ERROR')
        self.assertEqual(r['failure_code'], 'ANALYSIS_ERROR')
        self.assertNotIn('SECRET_PASSWORD_123', json.dumps(r))

    def test_provenance_mismatch_prevents_execution(self):
        def plan_mismatch(*, output_dir, **kw):
            Path(output_dir).mkdir(parents=True)
            (Path(output_dir) / 'phase2c_candidate.json').write_text(json.dumps(_candidate()))
            return {'source': {'repository':'Evil/Repo'}, 'status':'PLANNED_NO_EXECUTION'}
        with patch.object(agent, 'run_pipeline', side_effect=plan_mismatch), \
             patch.object(agent, 'execute_vm_candidate') as vm:
            r = agent.run_submission(repo_url=REPO, output_dir=self.out,
                                     vm_config=self.config)
        self.assertEqual(r['failure_code'], 'ANALYSIS_ERROR')
        vm.assert_not_called()

    def test_output_report_refuses_overwrite(self):
        self.out.mkdir()
        (self.out / 'orchestration_result.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(agent.VMPlanError):
            agent.run_submission(repo_url=REPO, output_dir=self.out,
                                 vm_config=self.config)

    def test_vm_config_required_for_execution(self):
        with self.assertRaises(agent.VMPlanError):
            agent.run_submission(repo_url=REPO, output_dir=self.out)

    def test_real_worker_exception_keeps_cleanup_unverified(self):
        cfg = {'approved_snapshots': [IDENTITY]}
        with patch.object(agent, 'run_pipeline', side_effect=self._plan()), \
             patch.object(agent, 'load_config', return_value=cfg), \
             patch.object(agent, 'execute_vm_candidate', side_effect=OSError('SECRET')):
            r = agent.run_submission(repo_url=REPO, output_dir=self.out,
                                     vm_config=self.config)
        self.assertEqual(r['status'], 'ORCHESTRATION_ERROR')
        self.assertNotEqual(r['cleanup'], agent.CLEAN)

    def test_agent_class_delegates(self):
        with patch.object(agent, 'run_submission', return_value={'status':'STARTUP_VERIFIED'}) as run:
            result = agent.ExecutionerAgent(vm_config=self.config).run(REPO, output_dir=self.out)
        self.assertEqual(result['status'], 'STARTUP_VERIFIED')
        self.assertEqual(run.call_args.kwargs['vm_config'], self.config)


    # Dashboard diagnostic coverage reuses the same no-network/no-VM fixture.
    def _ingestion_failure(self, exception):
        with patch.object(agent, 'run_pipeline', side_effect=exception), \
             patch.object(agent, 'execute_vm_candidate') as vm:
            result = agent.run_submission(repo_url=REPO, output_dir=self.out,
                                          vm_config=self.config)
        vm.assert_not_called()
        return result

    def test_finquest_archive_size_returns_actionable_dashboard_issue(self):
        result = self._ingestion_failure(agent.IngestionError(
            'Repository ZIP exceeds the configured download size limit.'))
        self.assertEqual(result['status'], 'ORCHESTRATION_ERROR')
        self.assertEqual(result['failure_code'], 'REPOSITORY_ZIP_TOO_LARGE')
        self.assertEqual(result['steps'][0]['status'], 'FAILED')
        self.assertEqual(result['execution'], 'NOT_PERFORMED')
        self.assertEqual(result['cleanup'], 'NOT_STARTED')
        issue = result['issues'][0]
        self.assertEqual(issue['code'], 'REPOSITORY_ZIP_TOO_LARGE')
        self.assertEqual(issue['stage'], 'analysis')
        self.assertEqual(issue['audience'], 'submitter')
        self.assertIn('30 MiB', issue['title'])
        self.assertIn('virtual environments', issue['recommended_action'])
        self.assertEqual(json.loads((self.out / 'orchestration_result.json').read_text())['issues'], result['issues'])

    def test_invalid_github_url_is_specific(self):
        r = self._ingestion_failure(agent.IngestionError(
            'Expected an HTTPS GitHub repository URL: https://github.com/owner/repo'))
        self.assertEqual(r['failure_code'], 'GITHUB_INVALID_URL')

    def test_github_not_found_is_specific(self):
        r = self._ingestion_failure(agent.IngestionError(
            'GitHub repository/ref not found, private, or inaccessible.'))
        self.assertEqual(r['failure_code'], 'GITHUB_REPOSITORY_UNAVAILABLE')

    def test_github_rate_limit_is_specific(self):
        r = self._ingestion_failure(agent.IngestionError(
            'GitHub request was rate-limited or forbidden. Set GITHUB_TOKEN for API requests, or try later.'))
        self.assertEqual(r['failure_code'], 'GITHUB_RATE_LIMITED_OR_FORBIDDEN')
        self.assertTrue(r['issues'][0]['retryable'])

    def test_github_network_error_does_not_echo_exception(self):
        r = self._ingestion_failure(agent.IngestionError(
            'Network request failed: SECRET_LOCAL_PATH'))
        self.assertEqual(r['failure_code'], 'GITHUB_NETWORK_FAILURE')
        self.assertNotIn('SECRET_LOCAL_PATH', json.dumps(r))

    def test_unknown_ingestion_error_stays_generic_and_redacted(self):
        r = self._ingestion_failure(agent.IngestionError('SECRET_PASSWORD_OR_PATH'))
        self.assertEqual(r['failure_code'], 'ANALYSIS_ERROR')
        self.assertNotIn('SECRET_PASSWORD_OR_PATH', json.dumps(r))

    def test_expanded_archive_limit(self):
        r = self._ingestion_failure(agent.ArchiveError('Archive exceeds expanded size limit.'))
        self.assertEqual(r['failure_code'], 'REPOSITORY_EXPANDED_TOO_LARGE')

    def test_archive_file_count_limit(self):
        r = self._ingestion_failure(agent.ArchiveError('Archive contains too many files.'))
        self.assertEqual(r['failure_code'], 'REPOSITORY_TOO_MANY_FILES')

    def test_unsafe_archive_is_reported_not_executed(self):
        r = self._ingestion_failure(agent.ArchiveError('Archive contains symlinks; refusing extraction.'))
        self.assertEqual(r['failure_code'], 'REPOSITORY_ARCHIVE_UNSAFE')
        self.assertEqual(r['issues'][0]['category'], 'source_security')

    def test_unapproved_has_useful_review_issue(self):
        r, _, vm = self._run(approved=False)
        self.assertEqual(r['issues'][0]['code'], 'PINNED_SHA_NOT_APPROVED')
        self.assertIn('review', r['issues'][0]['recommended_action'].lower())
        vm.assert_not_called()

    def test_unsupported_adapter_has_compatibility_issue(self):
        r, _, vm = self._run(adapter=None)
        self.assertEqual(r['issues'][0]['category'], 'compatibility')
        self.assertEqual(r['issues'][0]['code'], 'NO_SUPPORTED_ADAPTER')
        vm.assert_not_called()

    def test_missing_offline_cache_has_specific_issue(self):
        with patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1': (
                'node_vite_offline', lambda *_: (_ for _ in ()).throw(agent.OfflineAssetError('MISSING')))}):
            r, _, vm = self._run(adapter='node_vite_offline_v1')
        self.assertEqual(r['issues'][0]['code'], 'PREPARATION_NOT_ENABLED')
        self.assertEqual(r['issues'][0]['category'], 'dependencies')
        vm.assert_not_called()

    def test_vm_cleanup_error_never_claims_verified(self):
        r, _, _ = self._run(run_value=_vm(cleanup='CLEANUP_FAILED'))
        self.assertEqual(r['failure_code'], 'VM_CLEANUP_UNVERIFIED')
        self.assertEqual(r['issues'][0]['code'], 'VM_CLEANUP_UNVERIFIED')
        self.assertEqual(r['steps'][-1]['status'], 'FAILED')

    def test_vm_http_failure_has_readiness_issue(self):
        r, _, _ = self._run(run_value=_vm(guest_result={'status': 'FAILED', 'http_status': 503}))
        self.assertEqual(r['failure_code'], 'VM_HTTP_NOT_READY')
        self.assertEqual(r['issues'][0]['category'], 'startup')

    def test_vm_evidence_mismatch_is_security_issue(self):
        r, _, _ = self._run(run_value=_vm(backend='HOST_SUBPROCESS'))
        self.assertEqual(r['failure_code'], 'VM_EVIDENCE_INCONSISTENT')
        self.assertEqual(r['issues'][0]['category'], 'security')

    def test_success_has_no_issues(self):
        r, _, _ = self._run()
        self.assertEqual(r['status'], 'STARTUP_VERIFIED')
        self.assertEqual(r['issues'], [])


class PreparationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_network_not_authorized_prevents_any_preparation(self):
        config = {'approved_snapshots': [IDENTITY]}
        c, state = agent._prepare_assets(repo=REPO, sha=SHA, adapter='node_vite_offline_v1',
            identity=IDENTITY, config_path=self.root / 'config.json', config=config,
            out=self.root, options=agent.DependencyPreparation(enabled=True))
        self.assertIs(c, config)
        self.assertEqual(state, 'TRUSTED_DEPENDENCY_NETWORK_NOT_AUTHORIZED')

    def test_node_preparation_profile_rechecked_before_execution(self):
        import sys
        import types
        c = {'approved_snapshots':[IDENTITY]}
        stub = types.ModuleType('projectiq_executioner.phase2.phase2d.cache_prep')
        def prepare_cache(*, output_dir, **kwargs):
            Path(output_dir).mkdir(parents=True)
            f = Path(output_dir) / 'node_vite_profile.json'
            f.write_text(json.dumps({'node_vite_offline': {IDENTITY: {'test_profile': True}}}))
            return {'profile_json': str(f)}
        stub.prepare_cache = prepare_cache
        path = stub.__name__
        with patch.dict(sys.modules, {path:stub}), \
             patch.dict(agent.NODE_ADAPTERS, {'node_vite_offline_v1':(
                 'node_vite_offline', lambda config, identity: config['node_vite_offline'][identity])}):
            modified, state = agent._prepare_assets(
                repo=REPO, sha=SHA, adapter='node_vite_offline_v1', identity=IDENTITY,
                config_path=self.root/'config.json', config=c, out=self.root,
                options=agent.DependencyPreparation(enabled=True, allow_trusted_network=True,
                    node_tarball=self.root/'node.tar.xz', node_sha256='b'*64))
        self.assertEqual(state, 'PREPARED_AND_VERIFIED')
        self.assertNotIn('node_vite_offline', c)
        self.assertTrue(modified['node_vite_offline'][IDENTITY]['test_profile'])

    def test_prepared_profile_wrong_identity_denied(self):
        import sys
        import types
        c = {'approved_snapshots':[IDENTITY]}
        stub = types.ModuleType('projectiq_executioner.phase2.phase2d.cache_prep')
        def prepare_cache(*, output_dir, **kwargs):
            Path(output_dir).mkdir(parents=True)
            f = Path(output_dir) / 'node_vite_profile.json'
            f.write_text(json.dumps({'node_vite_offline': {'Wrong/Repo@' + SHA: {}}}))
            return {'profile_json': str(f)}
        stub.prepare_cache = prepare_cache
        with patch.dict(sys.modules, {stub.__name__: stub}):
            with self.assertRaises(agent.VMPlanError):
                agent._prepare_assets(
                    repo=REPO, sha=SHA, adapter='node_vite_offline_v1', identity=IDENTITY,
                    config_path=self.root/'config.json', config=c, out=self.root,
                    options=agent.DependencyPreparation(enabled=True, allow_trusted_network=True,
                        node_tarball=self.root/'node.tar.xz', node_sha256='b'*64))

    def test_python_preparation_uses_separate_contract(self):
        import sys
        import types
        c = {'approved_snapshots':[IDENTITY]}
        stub = types.ModuleType('projectiq_executioner.phase2.phase2d.python_cache_prep')
        def prep(*, output_dir, **kwargs):
            Path(output_dir).mkdir(parents=True)
            f = Path(output_dir) / 'python_web_profile.json'
            f.write_text(json.dumps({'python_web_offline': {IDENTITY: {'version': '3.12'}}}))
            return {'profile_json': str(f)}
        stub.prepare_python_cache = prep
        with patch.dict(sys.modules, {stub.__name__:stub}), \
             patch.object(agent, 'approved_python_web_assets',
                          side_effect=lambda cfg, ident: cfg['python_web_offline'][ident]):
            modified, state = agent._prepare_assets(
                repo=REPO, sha=SHA, adapter=agent.PYTHON_ADAPTER, identity=IDENTITY,
                config_path=self.root/'config.json', config=c, out=self.root,
                options=agent.DependencyPreparation(enabled=True, allow_trusted_network=True,
                    pip_wheel=self.root/'pip.whl', pip_sha256='b'*64))
        self.assertEqual(state, 'PREPARED_AND_VERIFIED')
        self.assertEqual(modified['python_web_offline'][IDENTITY]['version'],'3.12')


if __name__ == '__main__':
    unittest.main()
