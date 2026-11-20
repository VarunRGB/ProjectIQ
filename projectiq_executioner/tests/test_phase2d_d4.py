"""D4a: two-service Vite/Express + SQLite-reviewed-fixture regression tests.

This suite runs only trusted synthetic fixtures and fake processes on the host.
It does NOT execute arbitrary submission source and does NOT security-attest QEMU.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.analyzer import analyze_directory
from projectiq_executioner.models import RepositorySource
from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import build_vm_plan, VMPlanError
from projectiq_executioner.phase2.phase2d.capabilities import build_capability_plan
from projectiq_executioner.phase2.phase2d.cache_prep import prepare_cache, CachePreparationError
from projectiq_executioner.phase2.vm_worker.offline_assets import (
    approved_node_fullstack_assets, OfflineAssetError,
)
from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
from projectiq_executioner.phase2.vm_worker.guest_agent import (
    GuestFailure, _d4_roots, _d4_verify_manifests, _d4_npm_ci,
    _d4_install_and_build, prepare_fullstack_cache, _d4_json,
)

SHA = 'd' * 40
IDENTITY = 'Fixtures/Fullstack@' + SHA
VERSION = 'v24.22.3'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(frontend='frontend', backend='backend', api='/api/sqlite-check'):
    return {
        'schema_version': '1.0', 'phase': 1,
        'source': {'kind': 'github', 'repository': 'Fixtures/Fullstack', 'commit_sha': SHA},
        'services': [
            {'name': 'frontend', 'kind': 'frontend', 'technology': 'React',
             'root': frontend, 'depends_on': ['backend']},
            {'name': 'backend', 'kind': 'backend', 'technology': 'Express',
             'root': backend, 'depends_on': []},
        ],
        'technologies': [{'name': 'Express'}, {'name': 'Vite'}, {'name': 'React'}, {'name': 'Node.js'}],
        'setup_files': [frontend+'/'+name for name in ('package.json', 'package-lock.json')]
                       + [backend+'/'+name for name in ('package.json', 'package-lock.json')],
        'routes': [
            {'path': '/health', 'method': 'GET', 'kind': 'declared_http_route'},
            {'path': api, 'method': 'GET', 'kind': 'declared_http_route'},
            {'path': api, 'method': 'GET', 'kind': 'client_http_call'},
        ],
        'pages': [], 'database_schema_files': [], 'findings': [], 'inventory': {},
        'environment_variables': [],
    }


def plans(m):
    a = build_execution_plan(m)
    b = create_preflight(m, a)
    c = build_vm_plan(m, a, b)
    return a, b, c


def fixture(directory):
    base = Path(directory)
    front, back = base/'frontend', base/'backend'
    front.mkdir(parents=True, exist_ok=True)
    back.mkdir(parents=True, exist_ok=True)
    fdeps = {'react':'^19.0.0','react-dom':'^19.0.0'}
    fdev = {'vite':'^6.2.1'}
    (front/'package.json').write_text(json.dumps({'name':'d4-front','version':'1.0.0',
                                                  'dependencies': fdeps, 'devDependencies': fdev}), 'utf-8')
    (front/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{
        '':{'dependencies':fdeps,'devDependencies':fdev},
        'node_modules/vite': {'version':'6.2.1','resolved':'https://registry.npmjs.org/vite/-/vite-6.2.1.tgz',
                              'integrity':'sha512-test'},
    }}), 'utf-8')
    (front/'index.html').write_text('<!doctype html><html><body>hello</body></html>', 'utf-8')
    (front/'src.jsx').write_text("fetch('/api/sqlite-check');", 'utf-8')
    bdeps = {'express':'^4.19.2'}
    (back/'package.json').write_text(json.dumps({'name':'d4-back','version':'1.0.0',
                                                 'main':'app.js', 'dependencies': bdeps}), 'utf-8')
    (back/'package-lock.json').write_text(json.dumps({'lockfileVersion':3,'packages':{
        '':{'dependencies':bdeps},
        'node_modules/express':{'version':'4.19.2','resolved':'https://registry.npmjs.org/express/-/express-4.19.2.tgz',
                               'integrity':'sha512-test'}
    }}), 'utf-8')
    (back/'app.js').write_text("const express=require('express'); const app=express();\n"
                              "app.get('/health',(req,res)=>res.json({ok:true}));\n"
                              "app.get('/api/sqlite-check',(req,res)=>res.json({sqlite:true}));\n"
                              "app.listen(process.env.PORT);\n", 'utf-8')
    return {'frontend_lockfile_sha256': digest(front/'package-lock.json'),
            'backend_lockfile_sha256': digest(back/'package-lock.json')}


class D4PlanningTests(unittest.TestCase):
    def test_candidate_and_service_graph(self):
        m = manifest(); a,b,c = plans(m)
        self.assertEqual(c['adapter'],'node_fullstack_offline_v1')
        self.assertEqual(c['probe_path'],'/api/sqlite-check')
        self.assertEqual(c['backend_probe_path'],'/health')
        self.assertEqual(c['service_roots'],{'frontend':'frontend','backend':'backend'})
        self.assertGreaterEqual(c['vm_limits']['ram_mib'],3072)
        self.assertEqual(c['status'],'CANDIDATE_NEEDS_VM_VERIFICATION')
        d = build_capability_plan(m,a,b,c)
        self.assertEqual(d['phase2d_adapter'],'node_fullstack_offline_v1')
        self.assertEqual(d['missing_profiles'],[])
        self.assertEqual(len([x for x in d['services']
                              if x['implementation_state']=='PHASE2D_FULLSTACK_OFFLINE_ASSETS_REQUIRED']),2)

    def test_health_only_api_supported_without_sqlite_claim(self):
        m=manifest(api='/api/health'); c=plans(m)[2]
        self.assertEqual(c['adapter'],'node_fullstack_offline_v1')
        self.assertEqual(c['probe_path'],'/api/health')

    def test_unlinked_frontend_blocked(self):
        m=manifest(); m['services'][0]['depends_on']=[]
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_unmatched_frontend_call_blocked(self):
        m=manifest(); m['routes'][-1]['path']='/api/other'
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_unknown_route_and_post_only_blocked(self):
        m=manifest(); m['routes'][-2]['method']='POST'
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_lacks_backend_health_blocked(self):
        m=manifest(); m['routes']=m['routes'][1:]
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_missing_evidence_blocked(self):
        m=manifest(); m['technologies']=[{'name':'Express'}]
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_missing_lockfile_blocked(self):
        m=manifest(); m['setup_files'].remove('backend/package-lock.json')
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_external_service_and_mysql_blocked(self):
        for service in (
            {'name':'remote','kind':'external_dependency','technology':'AWS S3','root':'external','depends_on':[]},
            {'name':'database','kind':'database','technology':'MySQL','root':'.','depends_on':[]},
            {'name':'backend2','kind':'backend','technology':'Express','root':'other','depends_on':[]},
        ):
            with self.subTest(service=service['name']):
                m=manifest();m['services'].append(service)
                self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_mismatched_roots_blocked(self):
        m=manifest(frontend='backend',backend='backend')
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')

    def test_tampered_service_roots_detected(self):
        m=manifest();a,b,c=plans(m)
        c['service_roots']={'frontend':'../../x','backend':'backend'}
        with self.assertRaisesRegex(VMPlanError,'differs'):
            build_capability_plan(m,a,b,c)

    def test_real_analyzer_matches_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            src=RepositorySource('github','Fixtures/Fullstack',
                                 'https://github.com/Fixtures/Fullstack',SHA,SHA,SHA)
            m=analyze_directory(Path(tmp),src).to_dict()
            a,b,c=plans(m)
            self.assertEqual(c['adapter'],'node_fullstack_offline_v1')
            self.assertEqual(c['probe_path'],'/api/sqlite-check')


class D4AssetTests(unittest.TestCase):
    def _profile(self,root):
        node=root/f'node-{VERSION}-linux-x64.tar.xz';node.write_bytes(b'node')
        cache=root/'cache.zip';cache.write_bytes(b'cache')
        return {'node_version':VERSION,'node_tarball':str(node),'node_sha256':digest(node),
                'npm_cache_zip':str(cache),'npm_cache_sha256':digest(cache),
                'service_roots':{'frontend':'frontend','backend':'backend'},
                'frontend_lockfile_sha256':'1'*64,'backend_lockfile_sha256':'2'*64}

    def test_missing_profile_rejected(self):
        with self.assertRaisesRegex(OfflineAssetError,'NOT_APPROVED'):
            approved_node_fullstack_assets({}, IDENTITY)

    def test_valid_profile_and_cache_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self._profile(root)
            c={'node_fullstack_offline':{IDENTITY:p}}
            x=approved_node_fullstack_assets(c,IDENTITY)
            self.assertEqual(x['service_roots'],p['service_roots'])
            (root/'cache.zip').write_bytes(b'altered')
            with self.assertRaisesRegex(OfflineAssetError,'SHA256_MISMATCH'):
                approved_node_fullstack_assets(c, IDENTITY)

    def test_invalid_roots_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self._profile(Path(tmp))
            for bad in ('../backend','backend/../x','a//b','/home/guest','.'):
                p['service_roots']={'frontend':bad,'backend':'backend'}
                with self.subTest(bad=bad),self.assertRaisesRegex(OfflineAssetError,'SERVICE_ROOTS'):
                    approved_node_fullstack_assets({'node_fullstack_offline':{IDENTITY:p}}, IDENTITY)

    def test_missing_second_hash_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self._profile(Path(tmp));p.pop('backend_lockfile_sha256')
            with self.assertRaisesRegex(OfflineAssetError,'LOCKFILE_SHA256_INVALID'):
                approved_node_fullstack_assets({'node_fullstack_offline':{IDENTITY:p}},IDENTITY)

    def test_unapproved_source_denied_before_host_toolchain(self):
        c=plans(manifest())[2]
        with tempfile.TemporaryDirectory() as tmp,\
             patch('projectiq_executioner.phase2.vm_worker.orchestrator.verify_host_toolchain') as tools:
            result=execute_vm_candidate(c,{'approved_snapshots':[IDENTITY]},
                                        Path(tmp)/'vm.json',reviewed_fixture=True)
            self.assertEqual(result['status'],'DENIED')
            tools.assert_not_called()


class D4GuestTests(unittest.TestCase):
    def test_manifest_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes=fixture(tmp)
            req={**hashes,'operation':'prepare_fullstack_cache','node_sha256':'a'*64}
            f,b=_d4_roots(Path(tmp),{'service_roots':{'frontend':'frontend','backend':'backend'}})
            self.assertEqual(_d4_verify_manifests(f,b,req),'app.js')

    def test_failing_manifest_and_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes=fixture(tmp)
            req={**hashes,'operation':'prepare_fullstack_cache','node_sha256':'a'*64}
            f,b=_d4_roots(Path(tmp),{'service_roots':{'frontend':'frontend','backend':'backend'}})
            req['frontend_lockfile_sha256']='f'*64
            with self.assertRaisesRegex(GuestFailure,'LOCKFILE_HASH_MISMATCH'):
                _d4_verify_manifests(f,b,req)

    def test_arbitrary_direct_frontend_dependencies_denied(self):
        for spec in ('git+https://evil.example/foo','file:../other','npm:alias@1.0.0','*'):
            with tempfile.TemporaryDirectory() as tmp,self.subTest(spec=spec):
                hashes=fixture(tmp)
                f=Path(tmp)/'frontend';b=Path(tmp)/'backend'
                pkg=json.loads((f/'package.json').read_text())
                lock=json.loads((f/'package-lock.json').read_text())
                pkg['dependencies']['react']=spec
                lock['packages']['']['dependencies']['react']=spec
                (f/'package.json').write_text(json.dumps(pkg))
                (f/'package-lock.json').write_text(json.dumps(lock))
                req={**hashes,'frontend_lockfile_sha256':digest(f/'package-lock.json'),
                     'operation':'prepare_fullstack_cache','node_sha256':'a'*64}
                with self.assertRaisesRegex(GuestFailure,'UNSUPPORTED_MANIFEST'):
                    _d4_verify_manifests(f,b,req)

    def test_root_traversal_and_missing_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ('../escape','frontend/../other','frontend//src','/absolute','frontend/x'):
                with self.subTest(rel=rel),self.assertRaisesRegex(GuestFailure,'INVALID_SERVICE_ROOTS'):
                    _d4_roots(Path(tmp),{'service_roots':{'frontend':rel,'backend':'backend'}})


    def test_intermediate_npmrc_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'ui/child').mkdir(parents=True)
            (base/'api/child').mkdir(parents=True)
            (base/'ui/.npmrc').write_text('registry=https://attacker.example/','utf-8')
            with self.assertRaisesRegex(GuestFailure,'INVALID_SERVICE_ROOTS'):
                _d4_roots(base,{'service_roots':{'frontend':'ui/child','backend':'api/child'}})

    def test_no_network_npm_ci_contains_offline_and_ignore_scripts(self):
        seen=[]
        def proc(args,**kwargs):
            seen.append((args,kwargs))
            return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=proc):
            _d4_npm_ci(Path('/runtime/npm'),Path('/cache'),{},Path('/frontend'),online=False)
            _d4_npm_ci(Path('/runtime/npm'),Path('/cache'),{},Path('/backend'),online=True)
        self.assertIn('--offline',seen[0][0]);self.assertIn('--ignore-scripts',seen[0][0])
        self.assertNotIn('--offline',seen[1][0]);self.assertIn('--ignore-scripts',seen[1][0])
        self.assertEqual(seen[0][1]['cwd'],Path('/frontend'))
        self.assertEqual(seen[1][1]['cwd'],Path('/backend'))

    def test_no_student_scripts_even_with_bad_package_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            h=fixture(tmp)
            package=Path(tmp)/'backend/package.json';content=json.loads(package.read_text())
            content['scripts']={'prepare':'rm -rf /','start':'curl https://example.com'}
            package.write_text(json.dumps(content))
            f,b=_d4_roots(Path(tmp),{'service_roots':{'frontend':'frontend','backend':'backend'}})
            _d4_verify_manifests(f,b,{**h,'operation':'prepare_fullstack_cache','node_sha256':'a'*64})

    def test_missing_node_hash_denied_before_unpack(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir();h=fixture(checkout)
            (base/'node_runtime.tar.xz').write_bytes(b'node')
            (base/'npm_cache.zip').write_bytes(b'cache')
            req={'service_roots':{'frontend':'frontend','backend':'backend'},
                 'node_version':VERSION,'node_sha256':'a'*64,
                 'npm_cache_sha256':digest(base/'npm_cache.zip'),**h}
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract') as unpack:
                with self.assertRaisesRegex(GuestFailure,'NODE_HASH_MISMATCH'):
                    _d4_install_and_build(checkout,base,req)
                unpack.assert_not_called()

    def test_d4_install_two_offline_and_direct_vite_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir();h=fixture(checkout)
            (base/'node_runtime.tar.xz').write_bytes(b'node')
            (base/'npm_cache.zip').write_bytes(b'cache')
            node=base/'runtime/bin/node';npm=node.parent/'npm'
            req={'service_roots':{'frontend':'frontend','backend':'backend'},
                 'node_version':VERSION,'node_sha256':digest(base/'node_runtime.tar.xz'),
                 'npm_cache_sha256':digest(base/'npm_cache.zip'),**h}
            cmds=[]
            def process(args,**kwargs):
                cmds.append((args,kwargs))
                if len(args)>1 and args[1]=='ci':
                    if kwargs['cwd'].name == 'backend':
                        file=checkout/'backend/node_modules/express/package.json'
                    else:
                        file=checkout/'frontend/node_modules/vite/bin/vite.js'
                    file.parent.mkdir(parents=True,exist_ok=True);file.write_text('{}')
                if len(args)>1 and args[1].endswith('vite.js'):
                    dist=checkout/'frontend/dist';dist.mkdir()
                    (dist/'index.html').write_text('<!doctype html><html></html>')
                return SimpleNamespace(returncode=0,stdout=(VERSION+'\n').encode(),stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract',return_value=(node,npm)),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=base/'npm_cache'),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=process):
                dist,program,env,cwd=_d4_install_and_build(checkout,base,req)
            self.assertEqual([x[0][1] for x in cmds if len(x[0])>1 and x[0][1]=='ci'],['ci','ci'])
            self.assertIn('--offline',cmds[1][0]);self.assertIn('--offline',cmds[2][0])
            self.assertEqual(program,[str(node),str(checkout/'backend/app.js')])
            self.assertEqual(env['PORT'],'8766');self.assertEqual(env['HOST'],'127.0.0.1')
            self.assertTrue((dist/'index.html').is_file())
            self.assertEqual(cwd,checkout/'backend')

    def test_preparation_does_not_enable_student_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'snapshot.zip').write_bytes(b'snapshot')
            checkout=base/'checkout';checkout.mkdir();h=fixture(checkout)
            tar=base/'node_runtime.tar.xz';tar.write_bytes(b'node')
            req={'operation':'prepare_fullstack_cache','adapter':'node_fullstack_offline_v1',
                 'service_roots':{'frontend':'frontend','backend':'backend'},
                 'node_version':VERSION,'node_sha256':digest(tar),**h}
            request=base/'request.json';request.write_text(json.dumps(req))
            node=base/'runtime/bin/node';npm=node.parent/'npm'
            cmds=[]
            def process(args,**kwargs):
                cmds.append((args,kwargs))
                if len(args)>1 and args[1]=='ci':
                    f=base/'npm-cache/_cacache/content';f.parent.mkdir(parents=True,exist_ok=True)
                    f.write_bytes(b'cached dependencies')
                return SimpleNamespace(returncode=0,stdout=(VERSION+'\n').encode(),stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=checkout),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._node_tar_extract',return_value=(node,npm)),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=process):
                result=prepare_fullstack_cache(request,base_dir=base)
            self.assertEqual(result['status'],'CACHE_PREPARED')
            self.assertEqual(len([x for x in cmds if len(x[0])>1 and x[0][1]=='ci']),2)
            self.assertTrue((base/'npm_cache.zip').exists())


class D4PreparationPolicyTests(unittest.TestCase):
    def test_no_operator_network_consent(self):
        with self.assertRaisesRegex(CachePreparationError,'approval required'):
            prepare_cache(repo_url='https://github.com/Fixtures/Fullstack',commit_sha=SHA,
                          vm_config=Path('unused'),node_tarball=Path('unused'),
                          node_sha256='f'*64,node_version=VERSION,output_dir=Path('unused'),
                          adapter='node_fullstack_offline_v1')

    def test_unpinned_ref_denied(self):
        with self.assertRaisesRegex(CachePreparationError,'40-character'):
            prepare_cache(repo_url='https://github.com/Fixtures/Fullstack',commit_sha='main',
                          vm_config=Path('unused'),node_tarball=Path('unused'),
                          node_sha256='f'*64,node_version=VERSION,output_dir=Path('unused'),
                          adapter='node_fullstack_offline_v1',allow_trusted_npm_network=True)

    def test_fullstack_unsupported_extra_database(self):
        m=manifest();m['services'].append({'name':'db','kind':'database',
                                           'technology':'PostgreSQL','root':'.','depends_on':[]})
        self.assertEqual(plans(m)[2]['status'],'NEEDS_ADAPTER')


if __name__ == '__main__':
    unittest.main()

class D4IntegrationTests(unittest.TestCase):
    def test_local_proxy_html_and_json_through_two_trusted_services(self):
        """Exercise the exact trusted proxy server, without executing project code."""
        import contextlib
        import http.server
        import socket
        import subprocess
        import sys
        import time
        import urllib.error
        import urllib.request
        from projectiq_executioner.phase2.vm_worker import guest_agent

        class Backend(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/api/sqlite-check':
                    data = b'{"sqlite":true,"value":"verified"}'
                    self.send_response(200)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(data)))
                    self.end_headers();self.wfile.write(data)
                elif self.path == '/api/redirect':
                    self.send_response(302)
                    self.send_header('Location','https://example.com/')
                    self.end_headers()
                else:
                    self.send_error(404)

            def log_message(self, *_):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp)/'dist';dist.mkdir()
            (dist/'index.html').write_text('<!doctype html><html>ProjectIQ</html>','utf-8')
            (dist/'asset.js').write_text('console.log("ok")','utf-8')
            backend=http.server.ThreadingHTTPServer(('127.0.0.1',0),Backend)
            import threading
            thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); front_port=sock.getsockname()[1]
            cmd=[sys.executable,str(Path(guest_agent.__file__).resolve()),
                 '--serve-d4-frontend',str(dist),str(front_port),str(backend.server_port)]
            proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{front_port}/index.html',timeout=1) as response:
                            self.assertEqual(response.status,200)
                            self.assertIn(b'ProjectIQ',response.read())
                            break
                    except (urllib.error.URLError,OSError):
                        time.sleep(0.05)
                else:
                    self.fail('frontend proxy did not start')
                payload=_d4_json('/api/sqlite-check',front_port)
                self.assertEqual(payload,{'sqlite':True,'value':'verified'})
                with urllib.request.urlopen(f'http://127.0.0.1:{front_port}/asset.js',timeout=2) as resp:
                    self.assertEqual(resp.status,200)
                # Redirects must never send a host request to a third-party URL.
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(f'http://127.0.0.1:{front_port}/api/redirect',timeout=2)
                self.assertEqual(err.exception.code,502)
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(f'http://127.0.0.1:{front_port}/api/https://evil',timeout=2)
                self.assertEqual(err.exception.code,400)
            finally:
                proc.terminate()
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.communicate(timeout=3)
                backend.shutdown();backend.server_close();thread.join(timeout=3)

    def test_guest_requires_proxy_and_backend_matching_json(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import run_fullstack
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); checkout=base/'checkout';checkout.mkdir()
            h=fixture(checkout)
            front=checkout/'frontend/dist';front.mkdir(parents=True)
            events=[]
            class FakeProc:
                def poll(self): return None
                def wait(self,timeout=None): return 0
            def spawn(*args,**kwargs):
                events.append(('spawn',args[0]));return FakeProc()
            def stop(p): events.append(('stop',p))
            req={'adapter':'node_fullstack_offline_v1',
                 'probe_path':'/api/sqlite-check','backend_probe_path':'/health'}
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_install_and_build',
                       return_value=(front,['/node','app.js'],{},checkout/'backend')),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.subprocess.Popen',side_effect=spawn),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_wait_json',return_value={'status':'ok'}),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._probe',return_value=(200,'HTML_RESPONSE_OK')),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._probe_vite_asset',return_value='JS verified'),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_json',
                       side_effect=[{'sqlite':True},{'sqlite':False}]),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_stop_process',side_effect=stop):
                with self.assertRaisesRegex(GuestFailure,'CROSS_SERVICE_PROBE_FAILED'):
                    run_fullstack(checkout,base,req)
            self.assertEqual(len([e for e in events if e[0]=='spawn']),2)
            self.assertEqual(len([e for e in events if e[0]=='stop']),2)

    def test_guest_success_only_when_sqlite_response_true(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import run_fullstack
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir();fixture(checkout)
            dist=checkout/'frontend/dist';dist.mkdir(parents=True)
            class FakeProc:
                def poll(self): return None
                def wait(self,timeout=None): return 0
            req={'adapter':'node_fullstack_offline_v1','probe_path':'/api/sqlite-check',
                 'backend_probe_path':'/health'}
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_install_and_build',
                       return_value=(dist,['/node','app.js'],{},checkout/'backend')),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.subprocess.Popen',return_value=FakeProc()),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_wait_json',return_value={'status':'ok'}),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._probe',return_value=(200,'HTML_RESPONSE_OK')),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._probe_vite_asset',return_value='ok'),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_json',return_value={'sqlite':True}),\
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._d4_stop_process') as stop:
                r=run_fullstack(checkout,base,req)
            self.assertEqual(r['status'],'STARTUP_VERIFIED')
            self.assertTrue(r['service_results']['sqlite_verified'])
            self.assertEqual(stop.call_count,2)
