"""Regression, threat-model and architecture tests for Phase 2B (no execution)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import (
    InvalidPreflightInput, _family, create_preflight, preflight_from_files, report_markdown, safe_path,
)
from projectiq_executioner.phase2.runtime_probe import probe_container_runtime, unprobed_runtime

FIXTURES = Path(__file__).parent / 'fixtures'


def mkmanifest(services, **kwargs):
    return {
        'schema_version':'1.0','phase':1,
        'source':{'kind':'github','repository':'example/project','commit_sha':'a'*40},
        'services':services,'routes':kwargs.get('routes',[]),
        'pages':kwargs.get('pages',[]),
        'environment_variables':kwargs.get('env',[]),
        'inventory':{'environment_variable_details':kwargs.get('details',{})},
        'database_schema_files':kwargs.get('schemas',[]),
        'findings':kwargs.get('findings',[]),
    }


def service(name,kind,tech,root='.',deps=None):
    return {'name':name,'kind':kind,'technology':tech,'root':root,
            'depends_on': deps or []}


def prepare(manifest):
    return create_preflight(manifest,build_execution_plan(manifest))


class Phase2BStackTests(unittest.TestCase):
    def test_minimal_frontend_has_no_compatibility_blockers_but_is_never_ready(self):
        m=mkmanifest([service('frontend','frontend','React/Vite','frontend')])
        b=prepare(m)
        self.assertEqual(b['status'],'PREPARED_NOT_VERIFIED')
        self.assertEqual(b['blocking_conditions'],[])
        self.assertEqual(b['planning_status'],'SUCCESS')
        self.assertEqual(b['launch_authorization'],'DENIED')
        self.assertTrue(b['verification_gates'])

    def test_real_sanitized_cloud_drop_fixture(self):
        m=json.loads((FIXTURES/'cloud_drop.json').read_text())
        p=json.loads((FIXTURES/'cloud_drop_2a.json').read_text())
        b=create_preflight(m,p)
        self.assertEqual(b['candidate_container_count'],2)
        self.assertEqual(b['container_startup_order_candidate'],['backend','frontend'])
        self.assertEqual(len([s for s in b['noncontainer_dependencies'] if s['kind']=='embedded_database']),1)
        self.assertIn('UNRESOLVED_EXTERNAL_DEPENDENCY:amazon_s3',b['blocking_conditions'])
        self.assertIn('JWT_SECRET',[e['name'] for e in b['configuration_contract'] if e['required_hint']])
        self.assertIn('S3_BUCKET_NAME',[e['name'] for e in b['configuration_contract'] if e['required_hint']])
        self.assertEqual([x['from']+':'+x['to'] for x in b['interservice_network_intents']],['frontend:backend'])
        self.assertEqual([x['paths'] for x in b['readiness_probe_candidates'] if x['logical_service']=='backend'],[['/api/health']])
        self.assertEqual(b['launch_authorization'],'DENIED')

    def test_real_sanitized_php_mini_task_fixture(self):
        m=json.loads((FIXTURES/'mini_task.json').read_text())
        b=create_preflight(m,build_execution_plan(m))
        self.assertEqual(b['candidate_container_count'],2)
        self.assertEqual({u['name'] for u in b['candidate_containers']},{'mysql','backend'})
        self.assertEqual(next(u for u in b['candidate_containers'] if u['name']=='backend')['logical_services'],['backend','frontend'])
        self.assertEqual(b['container_startup_order_candidate'],['mysql','backend'])
        self.assertIn('DATABASE_SCHEMA_REVIEW_AND_TEST_IMPORT_PENDING',b['blocking_conditions'])
        self.assertIn('DATABASE_OR_SERVICE_CREDENTIAL_REBINDING_REQUIRES_REVIEW',b['blocking_conditions'])
        self.assertEqual(b['database_initialization_candidates'][0]['path'],'sql/mini_collab_app.sql')
        self.assertTrue(any(x['probe_kind']=='HTTP_STATIC_PAGE_CANDIDATE' for x in b['readiness_probe_candidates']))

    def test_real_sanitized_nextjs_frontend_with_remote_backend_fixture(self):
        m=json.loads((FIXTURES/'moss_meadows.json').read_text())
        b=create_preflight(m,build_execution_plan(m))
        self.assertEqual(b['candidate_container_count'],1)
        self.assertEqual(b['candidate_containers'][0]['runtime_family'],'nextjs')
        self.assertIn('UNRESOLVED_EXTERNAL_DEPENDENCY:external_http_api',b['blocking_conditions'])

    def test_backend_stack_catalog(self):
        for technology,family,image in [
            ('FastAPI','python','python'),('Flask','python','python'),('Django','python','python'),
            ('Express/Node.js','node','node'),('NestJS','node','node'),('PHP (native)','php','php'),
            ('Laravel PHP','php','php'),('Spring Boot','java','eclipse-temurin'),
            ('ASP.NET Core','dotnet','dotnet'),('Ruby on Rails','ruby','ruby'),
            ('Rust Axum','rust','rust'),('Go/Gin-gonic','go','golang')]:
            with self.subTest(technology=technology):
                b=prepare(mkmanifest([service('backend','backend',technology)]))
                self.assertEqual(b['candidate_container_count'],1)
                self.assertEqual(b['candidate_containers'][0]['runtime_family'],family)
                self.assertEqual(b['candidate_containers'][0]['image_family_hint'],image)
                self.assertEqual(b['execution'],'NOT_PERFORMED')

    def test_frontend_stack_catalog(self):
        for tech,family in [('Next.js','nextjs'),('Vue/Nuxt','nuxt'),('SvelteKit','sveltekit'),
                            ('Angular','angular'),('React','node_frontend'),('HTML/JS frontend (inferred)','static_web')]:
            with self.subTest(tech=tech):
                b=prepare(mkmanifest([service('frontend','frontend',tech,'frontend')]))
                self.assertEqual(b['candidate_containers'][0]['runtime_family'],family)

    def test_database_families(self):
        for tech,family in [('MySQL','mysql'),('PostgreSQL','postgresql'),('MongoDB','mongodb'),('Redis','redis'),('MariaDB','mariadb')]:
            with self.subTest(tech=tech):
                b=prepare(mkmanifest([service('db','database',tech)]))
                self.assertEqual(b['candidate_containers'][0]['runtime_family'],family)
                self.assertEqual(b['candidate_containers'][0]['kind'],'database_service')
        b=prepare(mkmanifest([service('sqlite','database','SQLite')]))
        self.assertEqual(b['candidate_container_count'],0)
        self.assertEqual(b['noncontainer_dependencies'][0]['kind'],'embedded_database')

    def test_unknown_architecture_cannot_be_claimed_supported(self):
        b=prepare(mkmanifest([service('backend','backend','CustomLang/Build System')]))
        self.assertEqual(b['status'],'BLOCKED')
        self.assertIn('UNSUPPORTED_RUNTIME_OR_ARCHITECTURE:backend',b['blocking_conditions'])

    def test_worker_requires_adapter_known_or_unknown(self):
        b=prepare(mkmanifest([service('worker','worker','Python')]))
        self.assertEqual(b['candidate_container_count'],1)
        self.assertEqual(b['readiness_probe_candidates'][0]['probe_kind'],'WORKER_HEARTBEAT_OR_PROCESS_CHECK_TO_DISCOVER')

    def test_php_frontend_not_cohosted_if_outside_backend_root(self):
        b=prepare(mkmanifest([service('backend','backend','PHP','backend'),
                              service('frontend','frontend','HTML / JavaScript','frontend',['backend'])]))
        self.assertEqual(b['candidate_container_count'],2)

    def test_external_dependency_never_assumed_available(self):
        b=prepare(mkmanifest([service('backend','backend','Python','server',['storage']),
                              service('storage','external_dependency','Amazon S3','external')]))
        self.assertTrue(any('UNRESOLVED_EXTERNAL' in x for x in b['blocking_conditions']))
        self.assertEqual(b['noncontainer_dependencies'][0]['compatibility_verified'],False)
        self.assertEqual(b['noncontainer_dependencies'][0]['network_contact'],'FORBIDDEN_BY_DEFAULT')


class Phase2BValidationTests(unittest.TestCase):
    def test_invalid_paths(self):
        for value in ['../secret','backend/../secret','/etc/passwd','C:/Users/foo','backend\\foo','a//b',' a','a;rm','a$HOME','a/./b','']:
            with self.subTest(value=value):
                self.assertIsNone(safe_path(value))

    def test_manifest_and_plan_sha_mismatch_rejected(self):
        m=mkmanifest([service('backend','backend','Flask')])
        p=build_execution_plan(m);p['source']['commit_sha']='b'*40
        with self.assertRaises(InvalidPreflightInput):create_preflight(m,p)

    def test_manifest_and_plan_root_mismatch_rejected(self):
        m=mkmanifest([service('backend','backend','Flask')])
        p=build_execution_plan(m);p['proposed_services'][0]['source_root']='something_else'
        with self.assertRaises(InvalidPreflightInput):create_preflight(m,p)

    def test_unknown_deps_and_cycles_fail_closed(self):
        for m, error in [
            (mkmanifest([service('backend','backend','Flask','.', ['missing'])]),'UNKNOWN_SERVICE_DEPENDENCIES'),
            (mkmanifest([service('backend','backend','Flask','.', ['frontend']),service('frontend','frontend','React','.', ['backend'])]),'CYCLIC_SERVICE_DEPENDENCIES'),
        ]:
            with self.subTest(error=error):
                b=prepare(m)
                self.assertIn(error,b['blocking_conditions'])
                self.assertEqual(b['provisioning_order_logical'],[])

    def test_duplicate_names_fail_closed(self):
        m=mkmanifest([service('backend','backend','Python'),service('backend','backend','Node')])
        with self.assertRaises(InvalidPreflightInput):prepare(m)

    def test_nonsensical_or_malicious_service_rejected(self):
        m=mkmanifest([service('../host','backend','FastAPI')])
        with self.assertRaises(InvalidPreflightInput):prepare(m)

    def test_untrusted_code_and_secret_values_not_reflected(self):
        m=mkmanifest([service('backend','backend','Flask')],env=['JWT_SECRET'],details={'JWT_SECRET':{
            'required_hint':True,'default_present':False,'value':'SECRET_VALUE_SHOULD_NOT_APPEAR','path':'backend/app/config.py'}})
        m['package_scripts']={'package.json':{'start':'DO_NOT_EXECUTE_SHOULD_NOT_APPEAR'}}
        m['custom_field']={'secret':'PRIVATE_VALUE_SHOULD_NOT_APPEAR'}
        b=prepare(m)
        text=json.dumps(b)
        self.assertNotIn('SECRET_VALUE_SHOULD_NOT_APPEAR',text)
        self.assertNotIn('PRIVATE_VALUE_SHOULD_NOT_APPEAR',text)
        self.assertNotIn('DO_NOT_EXECUTE_SHOULD_NOT_APPEAR',text)
        self.assertEqual(b['configuration_contract'][0]['value'],'NOT_LOADED')

    def test_schema_traversal_is_blocked(self):
        m=mkmanifest([service('mysql','database','MySQL')],schemas=['../private.sql'])
        b=prepare(m)
        self.assertIn('UNSAFE_DATABASE_SCHEMA_PATH',b['blocking_conditions'])
        self.assertEqual(b['database_initialization_candidates'],[])

    def test_no_source_execution_even_when_unexpected_commands_in_manifest(self):
        m=mkmanifest([service('backend','backend','Python')])
        m['entrypoints']=[{'path':'run dangerous command && rm','kind':'startup_candidate'}]
        with patch('subprocess.run',side_effect=AssertionError('subprocess should not run')):
            b=prepare(m)
        self.assertEqual(b['launch_authorization'],'DENIED')
        self.assertFalse(b['candidate_containers'][0]['source_scripts_executable'])

    def test_hardening_fields_restrict_host_mounts_network_and_privileged_mode(self):
        b=prepare(mkmanifest([service('backend','backend','Python')]))
        policy=b['candidate_containers'][0]['container_hardening_candidate']
        for key in ('privileged','host_network','publish_host_ports','mount_host_docker_socket','allow_external_egress'):
            self.assertFalse(policy[key])
        for key in ('read_only_rootfs','drop_all_capabilities','memory_limit_required'):
            self.assertTrue(policy[key])
        self.assertFalse(b['runtime_capability_probe']['policy_attested'])

    def test_no_services_fails_closed(self):
        b=prepare(mkmanifest([]))
        self.assertIn('NO_RUNNABLE_SERVICES_IDENTIFIED',b['blocking_conditions'])

    def test_empty_health_routes_do_not_invent_a_port(self):
        b=prepare(mkmanifest([service('backend','backend','Spring')]))
        self.assertIsNone(b['readiness_probe_candidates'][0]['port'])

    def test_report_is_readable_without_external_tools(self):
        b=prepare(mkmanifest([service('backend','backend','Flask')]))
        md=report_markdown(b)
        self.assertIn('NOT PERFORMED',md)
        self.assertIn('DENIED',md)
        self.assertIn('backend',md)

    def test_files_function_and_cli_smoke(self):
        m=json.loads((FIXTURES/'mini_task.json').read_text())
        a=build_execution_plan(m)
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);p=d/'one.json';q=d/'two.json';o=d/'out.json';md=d/'out.md'
            p.write_text(json.dumps(m));q.write_text(json.dumps(a))
            b=preflight_from_files(p,q,o,markdown_path=md)
            self.assertEqual(json.loads(o.read_text()),b)
            self.assertIn('Mini-Task-Collaboration',md.read_text())
            run=subprocess.run([sys.executable,'-m','projectiq_executioner.phase2.preflight',
                                '--manifest',str(p),'--plan',str(q),'--out',str(o)],
                               text=True,capture_output=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('NO EXECUTION',run.stdout)

    def test_bad_files_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);m=d/'manifest';p=d/'plan';o=d/'result'
            m.write_text('{');p.write_text('{}')
            with self.assertRaises(InvalidPreflightInput):preflight_from_files(m,p,o)
            m.write_text('0'*(8_000_001))
            with self.assertRaises(InvalidPreflightInput):preflight_from_files(m,p,o)


class Phase2BRuntimeProbeTests(unittest.TestCase):
    def test_unprobed_by_default(self):
        s=unprobed_runtime()
        self.assertEqual(s['probe_status'],'NOT_REQUESTED')
        self.assertFalse(s['policy_attested'])

    def test_docker_rootless_linux_candidate_does_not_attest(self):
        def fake(binary,args,timeout):
            if args[0] == 'context':
                return {'Endpoints': {'docker': {'Host': 'unix:///run/user/1000/docker.sock'}}}
            return {'OSType':'linux','ServerVersion':'29.5.1','CgroupVersion':'2','CgroupDriver':'systemd',
                    'SecurityOptions':['name=rootless','name=seccomp,profile=builtin']}
        r=probe_container_runtime('docker',locator=lambda x:'/usr/bin/docker',runner=fake,environment={})
        self.assertEqual(r['selected_engine'],'docker')
        self.assertEqual(r['capability_status'],'CANDIDATE_REQUIRES_ATTESTATION')
        self.assertFalse(r['vm_or_worker_boundary_verified'])
        self.assertFalse(r['policy_attested'])

    def test_docker_desktop_rootful_not_automatically_approved(self):
        r=probe_container_runtime('docker',locator=lambda x:'docker',runner=lambda _,args,__:
            ({'Endpoints':{'docker':{'Host':'npipe:////./pipe/docker_engine'}}} if args[0]=='context' else
             {'OSType':'linux','ServerVersion':'28.0.0','CgroupVersion':'2','CgroupDriver':'systemd',
             'SecurityOptions':['name=seccomp,profile=builtin']}),environment={})
        self.assertEqual(r['capability_status'],'POLICY_REQUIREMENTS_UNMET')
        self.assertIn('rootless_engine_confirmed',r['observations'][0]['missing_requirements'])

    def test_windows_container_mode_not_eligible(self):
        r=probe_container_runtime('docker',locator=lambda x:'docker',runner=lambda _,args,__:
            ({'Endpoints':{'docker':{'Host':'npipe:////./pipe/docker_engine'}}} if args[0]=='context' else
             {'OSType':'windows','CgroupVersion':'2','CgroupDriver':'systemd','SecurityOptions':['name=rootless','name=seccomp']}),environment={})
        self.assertIn('linux_container_engine',r['observations'][0]['missing_requirements'])

    def test_rootless_podman_candidate(self):
        info={'host':{'os':'linux','cgroupVersion':'v2','cgroupManager':'systemd','security':{'rootless':True,'seccompEnabled':True}},
              'version':{'Version':'5.5.0'}}
        r=probe_container_runtime('podman',locator=lambda x:'/usr/bin/podman',runner=lambda *_:info,environment={})
        self.assertEqual(r['capability_status'],'CANDIDATE_REQUIRES_ATTESTATION')
        self.assertEqual(r['selected_engine'],'podman')

    def test_custom_remote_docker_host_refused_without_running_probe(self):
        def fail(*_):raise AssertionError('Must not contact remote daemon')
        r=probe_container_runtime('docker',locator=lambda x:'docker',runner=fail,
                                  environment={'DOCKER_HOST':'tcp://remote.example:2376'})
        self.assertIsNone(r['selected_engine'])
        self.assertEqual(r['observations'][0]['status'],'REMOTE_OR_CUSTOM_DAEMON_CONTEXT_NOT_PROBED')

    def test_custom_docker_context_refused(self):
        r=probe_container_runtime('docker',locator=lambda x:'docker',runner=lambda *_:None,
                                  environment={'DOCKER_CONTEXT':'production'})
        self.assertEqual(r['observations'][0]['status'],'REMOTE_OR_CUSTOM_DAEMON_CONTEXT_NOT_PROBED')

    def test_not_installed_or_running(self):
        r=probe_container_runtime('auto',locator=lambda x: None,runner=lambda *_:None,environment={})
        self.assertEqual(r['probe_status'],'UNAVAILABLE_OR_UNVERIFIED')
        self.assertEqual([o['status'] for o in r['observations']],['NOT_INSTALLED','NOT_INSTALLED'])

    def test_docker_current_remote_context_refused(self):
        def fake(binary,args,timeout):
            if args[0] == 'context':
                return {'Endpoints': {'docker': {'Host': 'ssh://other.example'}}}
            raise AssertionError('Must never query remote Docker info')
        r=probe_container_runtime('docker',locator=lambda x:'docker',runner=fake,environment={})
        self.assertIsNone(r['selected_engine'])
        self.assertEqual(r['observations'][0]['status'],'DAEMON_CONTEXT_ENDPOINT_NOT_VERIFIED_LOCAL')

    def test_preflight_not_marked_ready_after_successful_probe(self):
        m=mkmanifest([service('backend','backend','Flask')])
        info={'probe_status':'PROBED','capability_status':'CANDIDATE_REQUIRES_ATTESTATION','selected_engine':'docker',
              'policy_attested':False}
        b=create_preflight(m,build_execution_plan(m),info)
        self.assertEqual(b['status'],'PREPARED_NOT_VERIFIED')
        self.assertEqual(b['launch_authorization'],'DENIED')


if __name__=='__main__':unittest.main()
