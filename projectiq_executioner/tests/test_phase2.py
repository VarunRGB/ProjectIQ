"""Phase 2A must be safe to call with untrusted project metadata."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from projectiq_executioner.phase2 import InvalidManifest, build_execution_plan, plan_from_file


def manifest(services=None, env=None, details=None, routes=None):
    return {
        'schema_version': '1.0', 'phase': 1,
        'source': {'kind':'github', 'repository':'student/example', 'commit_sha': 'a'*40},
        'services': services if services is not None else [],
        'environment_variables': env if env is not None else [],
        'inventory': {'environment_variable_details': details or {}},
        'routes': routes or [],
    }


class Phase2PlanSafetyTests(unittest.TestCase):
    def test_cloud_storage_requires_isolation_and_synthetic_secrets(self):
        m=manifest(
            services=[
                {'name':'frontend','kind':'frontend','technology':'React','root':'frontend','depends_on':['backend']},
                {'name':'backend','kind':'backend','technology':'FastAPI','root':'backend','depends_on':['sqlite','amazon_s3']},
                {'name':'sqlite','kind':'database','technology':'SQLite','root':'.'},
                {'name':'amazon_s3','kind':'external_dependency','technology':'Amazon S3','root':'external'},
            ],
            env=['JWT_SECRET','AWS_REGION'],
            details={'JWT_SECRET': {'required_hint':True}, 'AWS_REGION': {'required_hint':False}},
            routes=[{'path':'/api/health','method':'GET','kind':'declared_http_route'}])
        plan=build_execution_plan(m)
        self.assertEqual(plan['status'],'BLOCKED')
        self.assertEqual(plan['execution'],'NOT_PERFORMED')
        self.assertEqual(plan['health_endpoint_candidates'], ['/api/health'])
        self.assertIn('EXTERNAL_DEPENDENCIES_REQUIRE_MOCKS_OR_ISOLATED_AUTHORIZED_TEST_RESOURCES',plan['blocking_conditions'])
        self.assertIn('REQUIRED_CONFIGURATION_MUST_BE_INJECTED_AS_SYNTHETIC_OR_TEST_SECRETS',plan['blocking_conditions'])
        self.assertEqual(plan['environment_variables'][0]['required_hint'], False)
        self.assertTrue(plan['sandbox_policy_requirements']['rootless_containers'])
        self.assertFalse(plan['sandbox_policy_requirements']['host_docker_socket_mounts'])

    def test_frontend_with_remote_api_is_not_marked_runnable(self):
        plan=build_execution_plan(manifest([{'name':'frontend','kind':'frontend','technology':'Next.js','root':'.'},
            {'name':'external_http_api','kind':'external_dependency','technology':'HTTP API','root':'external'}]))
        self.assertEqual(plan['status'],'BLOCKED')
        self.assertFalse(any('curl' in str(v) for v in plan.values()))

    def test_local_only_compatible_stack_is_still_plan_only(self):
        plan=build_execution_plan(manifest([{'name':'backend','kind':'backend','technology':'Flask','root':'backend'}]))
        self.assertEqual(plan['status'],'PLAN_ONLY')
        self.assertEqual(plan['readiness'],'NOT_VERIFIED')

    def test_bad_checkout_or_unpinned_git_ref_blocked(self):
        m=manifest([{'name':'backend','kind':'backend','technology':'FastAPI','root':'../../host'}])
        m['source']['commit_sha']=None
        plan=build_execution_plan(m)
        self.assertEqual(plan['proposed_services'][0]['source_root'],None)
        self.assertIn('UNSAFE_OR_UNRESOLVED_SERVICE_ROOT',plan['blocking_conditions'])
        self.assertIn('MISSING_PINNED_GITHUB_COMMIT',plan['blocking_conditions'])

    def test_wrong_phase_rejected(self):
        m=manifest(); m['phase']=2
        with self.assertRaises(InvalidManifest): build_execution_plan(m)

    def test_output_does_not_contain_script_bodies_or_secret_values(self):
        m=manifest([{'name':'backend','kind':'backend','technology':'Flask','root':'app'}], ['JWT_SECRET'])
        m['package_scripts']={'package.json':{'start':'rm -rf / --no-preserve-root'}}
        m['environment_variable_details']={'JWT_SECRET':'literal secret'}
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'manifest.json'; out=Path(d)/'plan.json'
            src.write_text(json.dumps(m),encoding='utf-8')
            p=plan_from_file(src,out)
            self.assertEqual(json.loads(out.read_text()),p)
            self.assertNotIn('rm -rf',out.read_text())
            self.assertNotIn('literal secret',out.read_text())


if __name__ == '__main__':
    unittest.main()
