"""D3: approved, offline Express API single-service VM pilot regression tests.

No submitted code executes on the test host and no QEMU VM is launched.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.analyzer import analyze_directory
from projectiq_executioner.models import RepositorySource
from projectiq_executioner.phase2.phase2d.pipeline import plan_from_manifest
from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import build_vm_plan
from projectiq_executioner.phase2.phase2d.capabilities import build_capability_plan
from projectiq_executioner.phase2.vm_worker.offline_assets import (
    approved_node_express_assets, OfflineAssetError,
)
from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
from projectiq_executioner.phase2.vm_worker.guest_agent import (
    GuestFailure, _check_express_manifest, _node_express_root,
    _prepare_express, _probe_express, prepare_npm_cache,
)
from projectiq_executioner.phase2.phase2d.cache_prep import (
    prepare_cache, CachePreparationError,
)

SHA = 'e' * 40
IDENTITY = 'Fixtures/ExpressAPI@' + SHA
VERSION = 'v24.22.3'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(*, path='/health', root='.') -> dict:
    prefix = '' if root == '.' else root + '/'
    return {
        'schema_version': '1.0', 'phase': 1,
        'source': {'kind': 'github', 'repository': 'Fixtures/ExpressAPI', 'commit_sha': SHA},
        'technologies': [{'name': 'Express', 'category': 'backend'},
                         {'name': 'Node.js', 'category': 'runtime'}],
        'services': [{'name': 'backend', 'kind': 'backend', 'technology': 'Express',
                      'root': root, 'depends_on': []}],
        'routes': [{'path': path, 'method': 'GET', 'kind': 'declared_http_route',
                    'confidence': 'high', 'evidence': [{'path': prefix + 'server.js', 'line': 3}]}],
        'setup_files': [prefix + 'package.json', prefix + 'package-lock.json'],
        'pages': [], 'database_schema_files': [], 'inventory': {},
        'environment_variables': [], 'findings': [],
    }


def plans(m):
    a = build_execution_plan(m)
    b = create_preflight(m, a)
    c = build_vm_plan(m, a, b)
    return a, b, c


def create_express(root: Path, *, main='server.js', resolved='https://registry.npmjs.org/express/-/express-5.1.0.tgz'):
    root.mkdir(parents=True, exist_ok=True)
    package = {'name': 'projectiq-express-fixture', 'version': '1.0.0',
               'type': 'commonjs', 'main': main, 'dependencies': {'express': '5.1.0'},
               'scripts': {'start': 'node /dangerous/ignored-script.js'}}
    (root / 'package.json').write_text(json.dumps(package), 'utf-8')
    lock = {'name': package['name'], 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': package['name'], 'version': '1.0.0',
                             'dependencies': {'express': '5.1.0'}},
                         'node_modules/express': {'version': '5.1.0',
                                                  'resolved': resolved,
                                                  'integrity': 'sha512-test'}}}
    (root / 'package-lock.json').write_text(json.dumps(lock), 'utf-8')
    (root / main).write_text("const express = require('express');\nconst app=express();\napp.get('/health',(req,res)=>res.json({status:'ok'}));\napp.listen(process.env.PORT,process.env.HOST);\n", 'utf-8')
    return digest(root / 'package-lock.json')


class D3PlanningTests(unittest.TestCase):
    def test_express_candidate(self):
        m = manifest()
        a, b, c = plans(m)
        self.assertEqual(c['adapter'], 'node_express_offline_v1')
        self.assertEqual(c['probe_path'], '/health')
        self.assertEqual(c['project_root'], '.')
        self.assertEqual(c['vm_limits']['ram_mib'], 3072)
        self.assertEqual(c['status'], 'CANDIDATE_NEEDS_VM_VERIFICATION')
        d = build_capability_plan(m, a, b, c)
        self.assertEqual(d['status'], 'PHASE2D_OFFLINE_NODE_ADAPTER_CONDITIONAL')
        self.assertEqual(d['phase2d_adapter'], 'node_express_offline_v1')
        self.assertEqual(d['missing_profiles'], [])
        self.assertIn('PHASE2D_NODE_EXPRESS_OFFLINE_ASSETS_REQUIRED',
                      [x['implementation_state'] for x in d['services']])

    def test_nested_root(self):
        c = plans(manifest(root='backend'))[2]
        self.assertEqual(c['adapter'], 'node_express_offline_v1')
        self.assertEqual(c['project_root'], 'backend')

    def test_probes_from_real_routes(self):
        for path in ('/health', '/api/health', '/'):
            with self.subTest(path=path):
                self.assertEqual(plans(manifest(path=path))[2]['probe_path'], path)

    def test_priority_health_over_api_health_and_root(self):
        m = manifest(path='/')
        m['routes'].extend([{'path': '/api/health', 'method': 'GET', 'kind': 'declared_http_route'},
                            {'path': '/health', 'method': 'GET', 'kind': 'declared_http_route'}])
        self.assertEqual(plans(m)[2]['probe_path'], '/health')

    def test_missing_or_mutable_lock_rejected(self):
        m = manifest(); m['setup_files'].remove('package-lock.json')
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_no_express_evidence_rejected(self):
        m = manifest(); m['technologies'] = [{'name': 'Node.js'}]
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_post_or_unknown_health_route_rejected(self):
        for kind, method in [('declared_http_route', 'POST'), ('client_call', 'GET')]:
            m = manifest(); m['routes'][0].update(kind=kind, method=method)
            self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_unsupported_get_route_rejected(self):
        self.assertEqual(plans(manifest(path='/login'))[2]['status'], 'NEEDS_ADAPTER')

    def test_frontend_multi_service_and_cloud_stay_blocked(self):
        for service in [
            {'name': 'frontend', 'kind': 'frontend', 'technology': 'React', 'root': 'web', 'depends_on': []},
            {'name': 'mysql', 'kind': 'database', 'technology': 'MySQL', 'root': '.', 'depends_on': []},
            {'name': 'cloud', 'kind': 'external_dependency', 'technology': 'AWS S3', 'root': 'external', 'depends_on': []},
            {'name': 'extra', 'kind': 'backend', 'technology': 'Express', 'root': 'other', 'depends_on': []},
        ]:
            with self.subTest(service=service['kind']):
                m = manifest(); m['services'].append(service)
                self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_reject_tampered_plan(self):
        m = manifest(); a, b, c = plans(m)
        c['probe_path'] = '/other'
        with self.assertRaisesRegex(Exception, 'differs'):
            build_capability_plan(m, a, b, c)

    def test_real_analyzer_detects_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d);create_express(source)
            x = RepositorySource('github', 'Fixtures/ExpressAPI',
                                 'https://github.com/Fixtures/ExpressAPI', SHA, SHA, SHA)
            m = analyze_directory(source, x).to_dict()
            c, _ = plan_from_manifest(m, source / 'report')
            self.assertEqual(c['adapter'], 'node_express_offline_v1')
            self.assertEqual(c['probe_path'], '/health')


class D3AssetsTests(unittest.TestCase):
    def test_missing_profile_denied(self):
        with self.assertRaisesRegex(OfflineAssetError, 'NODE_EXPRESS_OFFLINE_FIXTURE_ASSETS_NOT_APPROVED'):
            approved_node_express_assets({}, IDENTITY)

    def test_pinned_assets_and_mutation_denial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = root / f'node-{VERSION}-linux-x64.tar.xz';node.write_bytes(b'node')
            cache = root / 'cache.zip';cache.write_bytes(b'cache')
            profile = {'node_version': VERSION, 'node_tarball': str(node),
                       'node_sha256': digest(node), 'npm_cache_zip': str(cache),
                       'npm_cache_sha256': digest(cache), 'lockfile_sha256': 'b' * 64}
            config = {'node_express_offline': {IDENTITY: profile}}
            self.assertEqual(approved_node_express_assets(config, IDENTITY)['node_version'], VERSION)
            cache.write_bytes(b'changed')
            with self.assertRaisesRegex(OfflineAssetError, 'NODE_EXPRESS_OFFLINE_ASSET_SHA256_MISMATCH'):
                approved_node_express_assets(config, IDENTITY)

    def test_wrong_profile_key_denied(self):
        with self.assertRaisesRegex(OfflineAssetError, 'NOT_APPROVED'):
            approved_node_express_assets({'node_vite_offline': {IDENTITY: {}}}, IDENTITY)

    def test_unapproved_execution_denied_before_toolchain(self):
        candidate = plans(manifest())[2]
        with tempfile.TemporaryDirectory() as tmp, \
             patch('projectiq_executioner.phase2.vm_worker.orchestrator.verify_host_toolchain') as tools:
            result = execute_vm_candidate(candidate, {'approved_snapshots': [IDENTITY]},
                                          Path(tmp) / 'report.json', reviewed_fixture=True)
            self.assertEqual(result['status'], 'DENIED')
            self.assertIn('NOT_APPROVED', result['reason'])
            tools.assert_not_called()


class D3GuestValidationTests(unittest.TestCase):
    def test_valid_manifest_and_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sha = create_express(root)
            self.assertEqual(_check_express_manifest(root, sha), 'server.js')

    def test_lock_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);create_express(root)
            with self.assertRaisesRegex(GuestFailure, 'LOCKFILE_HASH_MISMATCH'):
                _check_express_manifest(root, '0' * 64)

    def test_lock_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            (root/'package-lock.json').unlink()
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_alternate_registry_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root, resolved='https://evil.example/malicious.tgz')
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_npmrc_and_shrinkwrap_denied(self):
        for name in ('.npmrc', 'npm-shrinkwrap.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);sha=create_express(root)
                (root/name).write_text('bad', 'utf-8')
                with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                    _check_express_manifest(root, sha)

    def test_arbitrary_entrypoint_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root, main='server.js')
            pkg=json.loads((root/'package.json').read_text());pkg['main']='../../evil.js'
            (root/'package.json').write_text(json.dumps(pkg))
            with self.assertRaisesRegex(GuestFailure, 'ENTRYPOINT_UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_ambiguous_entrypoints_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            pkg=json.loads((root/'package.json').read_text());pkg.pop('main')
            (root/'package.json').write_text(json.dumps(pkg))
            (root/'app.js').write_text('other')
            with self.assertRaisesRegex(GuestFailure, 'ENTRYPOINT_UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_fixed_app_and_index_entrypoints(self):
        for name in ('app.js', 'index.js'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);sha=create_express(root, main=name)
                self.assertEqual(_check_express_manifest(root, sha), name)

    def test_missing_express_dependency_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            pkg=json.loads((root/'package.json').read_text());pkg['dependencies']={}
            (root/'package.json').write_text(json.dumps(pkg))
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_workspaces_disallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            pkg=json.loads((root/'package.json').read_text());pkg['workspaces']=['packages/*']
            (root/'package.json').write_text(json.dumps(pkg))
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root, sha)

    def test_file_dependency_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            lock=json.loads((root/'package-lock.json').read_text())
            lock['packages']['node_modules/express']['version']='file:../package'
            (root/'package-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root, digest(root/'package-lock.json'))

    def test_root_traversal_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('../other', '/tmp/escape', 'a//b', 'a/../b', ''):
                with self.subTest(value=value), self.assertRaisesRegex(GuestFailure, 'INVALID_PROJECT_ROOT'):
                    _node_express_root(Path(tmp), {'project_root': value})

    def test_nested_root_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'api').mkdir()
            self.assertEqual(_node_express_root(root, {'project_root': 'api'}), root/'api')

    def test_offline_install_disables_scripts_and_uses_pinned_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'checkout';root.mkdir()
            locksha=create_express(root)
            (base/'node_runtime.tar.xz').write_bytes(b'node-tar-data')
            (base/'npm_cache.zip').write_bytes(b'cache-data')
            node=base/'node_runtime'/'node-v24.22.3-linux-x64'/'bin'/'node'
            npm=node.parent/'npm'
            cmd=[]
            def process(args, **kwargs):
                cmd.append((args, kwargs))
                if args[1]=='ci':
                    package=root/'node_modules'/'express'/'package.json'
                    package.parent.mkdir(parents=True)
                    package.write_text('{}')
                return SimpleNamespace(returncode=0, stdout=(VERSION+'\n').encode(), stderr=b'')
            req={'adapter':'node_express_offline_v1','project_root':'.',
                 'node_version':VERSION,'node_sha256':digest(base/'node_runtime.tar.xz'),
                 'npm_cache_sha256':digest(base/'npm_cache.zip'),'lockfile_sha256':locksha}
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract',return_value=(node,npm)), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=base/'npm-cache'), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=process):
                command, env, note = _prepare_express(root,base,req)
            self.assertEqual(command, [str(node),str(root/'server.js')])
            self.assertIn('--offline',cmd[-1][0])
            self.assertIn('--ignore-scripts',cmd[-1][0])
            self.assertNotIn('run',cmd[-1][0])
            self.assertEqual(env['HOST'],'127.0.0.1')
            self.assertEqual(env['PORT'],'8765')
            self.assertEqual(env['NPM_CONFIG_OFFLINE'],'true')
            self.assertNotEqual(env['NPM_CONFIG_USERCONFIG'],env['NPM_CONFIG_GLOBALCONFIG'])

    def test_wrong_node_tar_hash_denied_before_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'checkout';root.mkdir();sha=create_express(root)
            (base/'node_runtime.tar.xz').write_bytes(b'node')
            (base/'npm_cache.zip').write_bytes(b'cache')
            req={'adapter':'node_express_offline_v1','project_root':'.','node_version':VERSION,
                 'node_sha256':'a'*64,'npm_cache_sha256':digest(base/'npm_cache.zip'),
                 'lockfile_sha256':sha}
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract') as extract:
                with self.assertRaisesRegex(GuestFailure, 'NODE_HASH_MISMATCH'):
                    _prepare_express(root,base,req)
                extract.assert_not_called()

    def test_http_probe_accepts_json_only(self):
        class FakeResponse:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def read(self,n):return b'{"status":"ok"}'
        with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',return_value=FakeResponse()):
            self.assertEqual(_probe_express('/health'),(200,'HTTP_RESPONSE_OK'))

    def test_http_probe_rejects_non_json(self):
        class FakeResponse:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def read(self,n):return b'<html>error</html>'
        with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',return_value=FakeResponse()), \
             patch('projectiq_executioner.phase2.vm_worker.guest_agent.time.sleep'):
            with self.assertRaisesRegex(GuestFailure, 'HTTP_PROBE_FAILED'):
                _probe_express('/health')

    def test_http_probe_rejects_unknown_path(self):
        with self.assertRaisesRegex(GuestFailure, 'HTTP_PROBE_FAILED'):
            _probe_express('/unapproved')


class D3CachePrepTests(unittest.TestCase):
    def test_no_network_authorization_denied(self):
        with self.assertRaisesRegex(CachePreparationError, 'approval required'):
            prepare_cache(repo_url='https://github.com/Fixtures/ExpressAPI',commit_sha=SHA,
                          vm_config=Path('none'),node_tarball=Path('none'),node_sha256='0'*64,
                          node_version=VERSION,output_dir=Path('unused'),
                          adapter='node_express_offline_v1')

    def test_unknown_adapter_denied(self):
        with self.assertRaisesRegex(CachePreparationError, 'Unsupported'):
            prepare_cache(repo_url='https://github.com/Fixtures/ExpressAPI',commit_sha=SHA,
                          vm_config=Path('none'),node_tarball=Path('none'),node_sha256='0'*64,
                          node_version=VERSION,output_dir=Path('unused'),
                          adapter='unapproved',allow_trusted_npm_network=True)

    def test_guest_cache_preparation_accepts_express_without_source_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);snapshot=base/'snapshot.zip';snapshot.write_bytes(b'archive')
            checkout=base/'checkout';checkout.mkdir()
            sha=create_express(checkout)
            tar=base/'node_runtime.tar.xz';tar.write_bytes(b'node-tar')
            req={'operation':'prepare_npm_cache','adapter':'node_express_offline_v1',
                 'project_root':'.','node_version':VERSION,
                 'node_sha256':digest(tar),'lockfile_sha256':sha}
            request=base/'request.json';request.write_text(json.dumps(req))
            node=base/'runtime'/'bin'/'node';npm=node.parent/'npm'
            def process(args, **kwargs):
                if args[1]=='ci':
                    cache=base/'npm-cache'/'_cacache';cache.mkdir(parents=True,exist_ok=True)
                    (cache/'content').write_bytes(b'downloaded package data')
                    self.assertIn('--ignore-scripts', args)
                    self.assertNotIn('--offline', args)
                return SimpleNamespace(returncode=0,stdout=(VERSION+'\n').encode(),stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=checkout), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract',return_value=(node,npm)), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=process):
                result=prepare_npm_cache(request,base_dir=base)
            self.assertEqual(result['status'],'CACHE_PREPARED')
            self.assertEqual(result['adapter'],'node_express_offline_v1')
            self.assertTrue((base/'npm_cache.zip').is_file())
            self.assertTrue(result['npm_cache_sha256'])


if __name__ == '__main__':
    unittest.main()

class D3AdditionalSecurityTests(unittest.TestCase):
    def test_non_registry_direct_spec_rejected_even_if_lock_looks_legit(self):
        for spec in ('file:../express', 'git+https://evil/repo',
                     'npm:other@1.0.0', 'https://bad.example/express.tgz', '*'):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(spec=spec):
                root = Path(tmp); sha=create_express(root)
                pkg=json.loads((root/'package.json').read_text())
                lock=json.loads((root/'package-lock.json').read_text())
                pkg['dependencies']['express']=spec
                lock['packages']['']['dependencies']['express']=spec
                (root/'package.json').write_text(json.dumps(pkg))
                (root/'package-lock.json').write_text(json.dumps(lock))
                with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                    _check_express_manifest(root,digest(root/'package-lock.json'))

    def test_manifest_and_lock_root_dependency_disagreement_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            pkg=json.loads((root/'package.json').read_text())
            pkg['dependencies']['express']='^4.19.2'
            (root/'package.json').write_text(json.dumps(pkg))
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root,sha)

    def test_reject_unknown_registry_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=create_express(root)
            lock=json.loads((root/'package-lock.json').read_text())
            lock['packages']['node_modules/express'].pop('integrity')
            (root/'package-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_express_manifest(root,digest(root/'package-lock.json'))

    def test_old_vite_candidate_unchanged(self):
        from projectiq_executioner.tests.test_phase2d_d1 import _vite_manifest
        self.assertEqual(plans(_vite_manifest())[2]['adapter'],'node_vite_offline_v1')

    def test_old_flask_candidate_unchanged(self):
        from projectiq_executioner.tests.test_phase2d_d2 import manifest as python_manifest
        self.assertEqual(plans(python_manifest())[2]['adapter'],'python_web_offline_v1')

class D3IntegratedGuestTests(unittest.TestCase):
    def test_guest_run_uses_fixed_express_entrypoint_and_probe(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import run
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir()
            create_express(checkout)
            (base/'snapshot.zip').write_bytes(b'fake-snapshot')
            req={'adapter':'node_express_offline_v1','probe_path':'/health','project_root':'.'}
            request=base/'request.json';request.write_text(json.dumps(req))
            proc=SimpleNamespace(terminate=lambda:None,wait=lambda timeout:0,kill=lambda:None)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=checkout), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._prepare_express',
                       return_value=(['/usr/bin/node',str(checkout/'app.js')],{'PORT':'8765'},'Offline Express install')) as prepare, \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.subprocess.Popen',return_value=proc) as spawn, \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._probe_express',return_value=(200,'HTTP_RESPONSE_OK')) as probe:
                r=run(request,base_dir=base)
            self.assertEqual(r['status'],'STARTUP_VERIFIED')
            self.assertEqual(r['adapter'],'node_express_offline_v1')
            self.assertEqual(r['http_status'],200)
            self.assertEqual(spawn.call_args.kwargs['cwd'],checkout)
            self.assertEqual(spawn.call_args.args[0][0], 'sudo')
            self.assertEqual(spawn.call_args.args[0][1:4], ['-n', '-u', 'projectiq_run'])
            self.assertIn('/usr/bin/node', spawn.call_args.args[0])
            self.assertEqual(probe.call_args.args[0],'/health')
            prepare.assert_called_once()

    def test_guest_run_rejects_unknown_adapter_without_popen(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import run
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);request=base/'request.json'
            request.write_text(json.dumps({'adapter':'evil','probe_path':'/'}))
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(GuestFailure, 'Unapproved guest adapter'):
                    run(request,base_dir=base)
                spawn.assert_not_called()
