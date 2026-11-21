"""D2 Flask/FastAPI+SQLite pilot regressions. No student source executes on host."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import build_vm_plan
from projectiq_executioner.phase2.phase2d.capabilities import build_capability_plan
from projectiq_executioner.phase2.phase2d.python_cache_prep import (
    prepare_python_cache, PythonCacheError,
)
from projectiq_executioner.phase2.vm_worker.offline_assets import (
    approved_python_web_assets, OfflineAssetError,
)
from projectiq_executioner.phase2.vm_worker.guest_agent import (
    GuestFailure, _check_python_requirements, _check_python_entry,
    _python_pip_wheel, _verify_python_pip, _python_guest_root,
    _unpack_python_wheelhouse, _prepare_python_web,
    prepare_python_wheelhouse, _probe_python_web,
)
from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
from projectiq_executioner.phase2.vm_worker.qemu import SSHControl, WorkerFailure

SHA = 'c' * 40
IDENTITY = 'Fixture/PythonWeb@' + SHA


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(framework='flask', *, root='.', sqlite=False):
    services = [{'name': 'backend', 'kind': 'backend', 'technology': framework,
                 'root': root, 'depends_on': []}]
    if sqlite:
        services.append({'name': 'sqlite', 'kind': 'database', 'technology': 'SQLite',
                         'root': root, 'depends_on': []})
    prefix = '' if root == '.' else root + '/'
    return {'schema_version': '1.0', 'phase': 1,
            'source': {'kind': 'github', 'repository': 'Fixture/PythonWeb', 'commit_sha': SHA},
            'technologies': [{'name': framework.capitalize() if framework == 'flask' else 'FastAPI',
                              'category': 'backend'}],
            'setup_files': [prefix + 'requirements.txt'], 'services': services,
            'pages': [], 'routes': [], 'database_schema_files': [], 'inventory': {},
            'environment_variables': [], 'findings': []}


def plans(m):
    p2a = build_execution_plan(m)
    p2b = create_preflight(m, p2a)
    c = build_vm_plan(m, p2a, p2b)
    return p2a, p2b, c


def make_pip_file(base):
    file = Path(base) / 'python_pip.whl'
    with zipfile.ZipFile(file, 'w') as z:
        z.writestr('pip/__init__.py', '# pip placeholder\n')
        z.writestr('pip/__main__.py', '# pip placeholder\n')
        z.writestr('pip-25.2.dist-info/WHEEL', 'Wheel-Version: 1.0\n')
    return file


def make_wheel_zip(base, names=('flask-3.1.1-py3-none-any.whl',)):
    file = Path(base) / 'python_wheelhouse.zip'
    with zipfile.ZipFile(file, 'w') as z:
        for name in names:
            z.writestr('wheels/' + name, b'fake-wheel-content')
    return file


def write_python(root, framework='flask', req=None):
    Path(root).mkdir(exist_ok=True, parents=True)
    if req is None:
        req = ('flask==3.1.1\n' if framework == 'flask'
               else 'fastapi==0.115.12\nuvicorn==0.34.2\n')
    (Path(root) / 'requirements.txt').write_text(req, encoding='utf-8')
    (Path(root) / ('app.py' if framework == 'flask' else 'main.py')).write_text(
        'from flask import Flask\napp = Flask(__name__)\n' if framework == 'flask'
        else 'from fastapi import FastAPI\napp=FastAPI()\n', encoding='utf-8')
    return sha(Path(root) / 'requirements.txt')


class PythonPlanningTests(unittest.TestCase):
    def test_flask_single_service(self):
        m = manifest()
        a, b, c = plans(m)
        self.assertEqual(c['adapter'], 'python_web_offline_v1')
        self.assertEqual(c['python_framework'], 'flask')
        self.assertEqual(c['project_root'], '.')
        self.assertEqual(c['probe_path'], '/')
        self.assertEqual(c['status'], 'CANDIDATE_NEEDS_VM_VERIFICATION')
        d = build_capability_plan(m, a, b, c)
        self.assertEqual(d['status'], 'PHASE2D_OFFLINE_PYTHON_ADAPTER_CONDITIONAL')
        self.assertEqual(d['missing_profiles'], [])
        self.assertEqual(d['execution'], 'NOT_PERFORMED')

    def test_fastapi_nested_root_sqlite(self):
        m = manifest('fastapi', root='backend', sqlite=True)
        a, b, c = plans(m)
        self.assertEqual(c['adapter'], 'python_web_offline_v1')
        self.assertEqual(c['python_framework'], 'fastapi')
        self.assertEqual(c['project_root'], 'backend')
        self.assertEqual(c['probe_path'], '/openapi.json')
        d = build_capability_plan(m, a, b, c)
        self.assertEqual(d['status'], 'PHASE2D_OFFLINE_PYTHON_ADAPTER_CONDITIONAL')
        self.assertIn('PHASE2D_PYTHON_SQLITE_EPHEMERAL_GUEST_ONLY',
                      [r['implementation_state'] for r in d['services']])

    def test_flask_with_embedded_static_templates(self):
        m = manifest('flask')
        m['services'].append({
            'name': 'frontend', 'kind': 'frontend',
            'technology': 'HTML/JS frontend (inferred)',
            'root': '.', 'depends_on': ['backend'],
        })
        _, _, c = plans(m)
        self.assertEqual(c['adapter'], 'python_web_offline_v1')
        self.assertEqual(c['project_root'], '.')
        self.assertEqual(c['probe_path'], '/')
        self.assertIn('D2_COHOSTED_STATIC_UI_SERVED_BY_PYTHON_BACKEND_ONLY',
                      c['application_preparation']['review_items'])

    def test_flask_with_templates_and_sqlite(self):
        m = manifest('flask', sqlite=True)
        m['services'].append({
            'name': 'frontend', 'kind': 'frontend',
            'technology': 'HTML/JS frontend (inferred)',
            'root': '.', 'depends_on': ['backend'],
        })
        _, _, c = plans(m)
        self.assertEqual(c['adapter'], 'python_web_offline_v1')
        self.assertEqual(c['python_framework'], 'flask')

    def test_separate_frontend_root_stays_unsupported(self):
        m = manifest('flask')
        m['services'].append({
            'name': 'frontend', 'kind': 'frontend',
            'technology': 'HTML/JS frontend (inferred)',
            'root': 'client', 'depends_on': ['backend'],
        })
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_node_frontend_stays_unsupported(self):
        m = manifest('flask')
        m['services'].append({
            'name': 'frontend', 'kind': 'frontend',
            'technology': 'React', 'root': '.', 'depends_on': ['backend'],
        })
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_cohosted_frontend_cannot_depend_on_other_services(self):
        m = manifest('flask', sqlite=True)
        m['services'].append({
            'name': 'frontend', 'kind': 'frontend',
            'technology': 'HTML/JS frontend (inferred)',
            'root': '.', 'depends_on': ['sqlite'],
        })
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_multiple_static_frontends_stay_unsupported(self):
        m = manifest('flask')
        for name in ('frontend1', 'frontend2'):
            m['services'].append({
                'name': name, 'kind': 'frontend',
                'technology': 'HTML/JS frontend (inferred)',
                'root': '.', 'depends_on': ['backend'],
            })
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_unsupported_without_evidenced_framework(self):
        m = manifest()
        m['technologies'] = []
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_both_frameworks_are_ambiguous(self):
        m = manifest()
        m['technologies'].append({'name': 'FastAPI'})
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_missing_requirements_not_executable(self):
        m = manifest()
        m['setup_files'] = []
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_cloud_dependency_not_executable(self):
        m = manifest('fastapi')
        m['services'].append({'name': 'cloud', 'kind': 'external_dependency',
                              'technology': 'AWS S3', 'root': 'external', 'depends_on': []})
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_postgres_not_executable(self):
        m = manifest('flask')
        m['services'].append({'name': 'db', 'kind': 'database', 'root': '.',
                              'technology': 'PostgreSQL', 'depends_on': []})
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_multi_backend_not_executable(self):
        m = manifest()
        m['services'].append({'name': 'other', 'kind': 'backend', 'root': 'other',
                              'technology': 'FastAPI', 'depends_on': []})
        self.assertEqual(plans(m)[2]['status'], 'NEEDS_ADAPTER')

    def test_modified_candidate_python_framework_rejected(self):
        m = manifest('fastapi')
        a, b, c = plans(m)
        c['python_framework'] = 'flask'
        with self.assertRaisesRegex(Exception, 'differs'):
            build_capability_plan(m, a, b, c)

    def test_d1_still_supported(self):
        from projectiq_executioner.tests.test_phase2d_d1 import _vite_manifest
        self.assertEqual(plans(_vite_manifest())[2]['adapter'], 'node_vite_offline_v1')


class PythonAssetsTests(unittest.TestCase):
    def test_valid_sha_pinned_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pip = root / 'pip-25.2-py3-none-any.whl'
            pip.write_bytes(b'pinned-pip')
            wheels = root / 'python-wheelhouse.zip'
            wheels.write_bytes(b'pinned-wheels')
            profile = {'python_version': '3.12', 'framework': 'flask',
                       'pip_wheel': str(pip), 'pip_sha256': sha(pip),
                       'wheelhouse_zip': str(wheels), 'wheelhouse_sha256': sha(wheels),
                       'requirements_sha256': 'b' * 64}
            result = approved_python_web_assets({'python_web_offline': {IDENTITY: profile}}, IDENTITY)
            self.assertEqual(result['pip_wheel'], pip)
            wheels.write_bytes(b'tampered-wheels')
            with self.assertRaisesRegex(OfflineAssetError, 'MISMATCH'):
                approved_python_web_assets({'python_web_offline': {IDENTITY: profile}}, IDENTITY)

    def test_missing_profile_denied(self):
        with self.assertRaisesRegex(OfflineAssetError, 'NOT_APPROVED'):
            approved_python_web_assets({}, IDENTITY)

    def test_wrong_python_version_denied(self):
        with self.assertRaisesRegex(OfflineAssetError, 'RUNTIME_PROFILE_INVALID'):
            approved_python_web_assets({'python_web_offline': {IDENTITY: {
                'python_version': '3.13', 'framework': 'flask'}}}, IDENTITY)

    def test_wrong_pip_sha_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            pip = r/'pip-25.2-py3-none-any.whl'; pip.write_bytes(b'a')
            zipfile = r/'python-wheelhouse.zip'; zipfile.write_bytes(b'b')
            data = {'python_version':'3.12','framework':'flask', 'pip_wheel':str(pip),
                    'pip_sha256':'a'*64,'wheelhouse_zip':str(zipfile),
                    'wheelhouse_sha256':sha(zipfile),'requirements_sha256':'b'*64}
            with self.assertRaisesRegex(OfflineAssetError, 'MISMATCH'):
                approved_python_web_assets({'python_web_offline': {IDENTITY: data}}, IDENTITY)

    def test_unreviewed_python_assets_stop_before_vm_launch(self):
        c = plans(manifest())[2]
        cfg = {'approved_snapshots': [IDENTITY]}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('projectiq_executioner.phase2.vm_worker.orchestrator.verify_host_toolchain') as toolchain:
            out = Path(tmp) / 'report.json'
            result = execute_vm_candidate(c, cfg, out, reviewed_fixture=True)
            toolchain.assert_not_called()
            self.assertEqual(result['status'], 'DENIED')
            self.assertIn('NOT_APPROVED', result['reason'])


class PythonRequirementsTests(unittest.TestCase):
    def test_flask_pinned_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); expected = write_python(root)
            self.assertEqual(_check_python_requirements(root, expected, 'flask'),
                             root / 'requirements.txt')
            _check_python_entry(root, 'flask')

    def test_fastapi_requires_uvicorn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_python(root, 'fastapi', 'fastapi==0.115.12\n')
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_python_requirements(root, sha(root / 'requirements.txt'), 'fastapi')

    def test_reject_requirements_options_urls_unpinned(self):
        bad = ['flask>=3.0\n', '-r ../other.txt\nflask==3.1.1\n',
               '--extra-index-url https://example.com\nflask==3.1.1\n',
               'flask @ https://example.com/a.whl\n', 'flask==3.1.1 ; python_version>=3.0\n',
               'flask==3.1.1\nFLASK==3.1.1\n', 'flask==3.1.1 # inline\n',
               'flask==3.1.1\n-e git+https://a.example/repo\n']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for content in bad:
                (root / 'requirements.txt').write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                    _check_python_requirements(root, sha(root/'requirements.txt'), 'flask')

    def test_missing_file_and_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_python(root)
            with self.assertRaisesRegex(GuestFailure, 'HASH_MISMATCH'):
                _check_python_requirements(root, '0' * 64, 'flask')
            (root / 'requirements.txt').unlink()
            with self.assertRaisesRegex(GuestFailure, 'UNSUPPORTED'):
                _check_python_requirements(root, '0' * 64, 'flask')

    def test_no_arbitrary_entrypoint_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_python(root)
            (root/'app.py').write_text('print("hello")\n', encoding='utf-8')
            with self.assertRaisesRegex(GuestFailure, 'ENTRYPOINT_UNSUPPORTED'):
                _check_python_entry(root, 'flask')

    def test_root_traversal_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            for path in ('../outside', '/tmp', 'folder//x', '.', None):
                if path == '.':
                    self.assertEqual(_python_guest_root(checkout, {'project_root': path}), checkout)
                else:
                    with self.subTest(path=path), self.assertRaisesRegex(GuestFailure, 'INVALID_PROJECT_ROOT'):
                        _python_guest_root(checkout, {'project_root': path})


class PythonWheelhouseTests(unittest.TestCase):
    def test_valid_wheels_unpacked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle = make_wheel_zip(root)
            extracted = _unpack_python_wheelhouse(bundle, root/'wheels')
            self.assertEqual(len(extracted), 1)
            self.assertEqual(extracted[0].read_bytes(), b'fake-wheel-content')

    def test_reject_unsafe_wheel_archive_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for member in ('../outside.whl', 'wheels/../../evil.whl',
                           'wheels/other/file.whl', 'wheels/not_a_wheel.py', 'random.whl'):
                with self.subTest(member=member):
                    file=root/'archive.zip'
                    with zipfile.ZipFile(file, 'w') as z:
                        z.writestr(member, b'abc')
                    with self.assertRaisesRegex(GuestFailure, 'WHEELHOUSE_INVALID'):
                        _unpack_python_wheelhouse(file, root/('extract-'+str(abs(hash(member)))))

    def test_reject_zip_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/'archive.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                i=zipfile.ZipInfo('wheels/unsafe-1.0-py3-none-any.whl')
                i.create_system=3
                i.external_attr=(stat.S_IFLNK|0o777)<<16
                z.writestr(i, '../outside')
            with self.assertRaisesRegex(GuestFailure, 'WHEELHOUSE_INVALID'):
                _unpack_python_wheelhouse(archive, root/'extract')

    def test_reject_duplicate_wheel_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); archive=root/'archive.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('wheels/A-1.0-py3-none-any.whl', b'abc')
                z.writestr('wheels/a-1.0-py3-none-any.whl', b'abc')
            with self.assertRaisesRegex(GuestFailure, 'WHEELHOUSE_INVALID'):
                _unpack_python_wheelhouse(archive, root/'extract')

    def test_pip_wheel_internal_layout_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); pip=make_pip_file(root)
            self.assertEqual(_python_pip_wheel(root, {'pip_sha256':sha(pip)}), pip)
            with self.assertRaisesRegex(GuestFailure, 'PIP_BOOTSTRAP_INVALID'):
                _python_pip_wheel(root, {'pip_sha256':'a'*64})

    def test_pip_cli_checked_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); pip=make_pip_file(root)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',
                       return_value=SimpleNamespace(returncode=0,stdout=b'pip 25.2 from /...\n')) as proc:
                env = _verify_python_pip(root, pip)
            self.assertIn(str(pip), env['PYTHONPATH'])
            self.assertEqual(proc.call_args.args[0], ['/usr/bin/python3', '-m', 'pip', '--version'])

    def test_offline_install_command_never_accesses_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); checkout=root/'checkout'; checkout.mkdir()
            lock=write_python(checkout)
            pip=make_pip_file(root); archive=make_wheel_zip(root)
            req={'adapter':'python_web_offline_v1', 'python_version':'3.12',
                 'python_framework':'flask','project_root':'.','pip_sha256':sha(pip),
                 'requirements_sha256':lock,'wheelhouse_sha256':sha(archive)}
            calls=[]
            def proc(args, **kwargs):
                calls.append((args,kwargs))
                return SimpleNamespace(returncode=0,
                                       stdout=b'pip 25.2 from /...\n' if '--version' in args else b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._check_guest_python312'), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=proc):
                program, env, _ = _prepare_python_web(checkout, root, req)
            self.assertEqual(program[:4], ['/usr/bin/python3','-m','flask','--app'])
            install, metadata = [c for c in calls if 'install' in c[0]][0]
            self.assertIn('--no-index', install)
            self.assertIn('--only-binary=:all:', install)
            self.assertIn('--target', install)
            self.assertEqual(env['PYTHONNOUSERSITE'], '1')
            self.assertEqual(metadata['cwd'], root)  # pip must not import local student pip.py
            self.assertTrue(all(c[0][0] == '/usr/bin/python3' for c in calls))

    def test_networked_prep_only_downloads_wheels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); checkout=root/'checkout'; checkout.mkdir()
            lock=write_python(checkout, 'fastapi')
            pip=make_pip_file(root)
            (root/'snapshot.zip').write_bytes(b'bounded-input')
            req={'operation':'prepare_python_wheels','adapter':'python_web_offline_v1',
                 'project_root':'.','python_framework':'fastapi','python_version':'3.12',
                 'pip_sha256':sha(pip),'requirements_sha256':lock}
            (root/'request.json').write_text(json.dumps(req),'utf-8')
            calls=[]
            def proc(args, **kwargs):
                calls.append((args,kwargs))
                if 'download' in args:
                    idx=args.index('--dest')
                    wheels=Path(args[idx+1]); wheels.mkdir(exist_ok=True)
                    (wheels/'fastapi-0.115.12-py3-none-any.whl').write_bytes(b'dummy-wheel')
                return SimpleNamespace(returncode=0,
                                       stdout=b'pip 25.2 from /...\n' if '--version' in args else b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._check_guest_python312'), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.safe_extract',return_value=checkout), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',side_effect=proc):
                out=prepare_python_wheelhouse(root/'request.json',base_dir=root)
            self.assertEqual(out['status'],'PYTHON_WHEELHOUSE_PREPARED')
            self.assertTrue((root/'python_wheelhouse.zip').is_file())
            downloads=[c for c in calls if 'download' in c[0]]
            self.assertEqual(len(downloads),1)
            self.assertIn('--only-binary=:all:', downloads[0][0])
            self.assertEqual(downloads[0][1]['cwd'], root)
            self.assertIn('https://pypi.org/simple', downloads[0][0])
            self.assertFalse(any('install' in c[0] for c in calls))

    def test_python_prep_needs_explicit_network_approval(self):
        with self.assertRaisesRegex(PythonCacheError, 'Explicit'):
            prepare_python_cache(repo_url='https://github.com/Fixture/PythonWeb',
                                 commit_sha=SHA, vm_config=Path('missing'),
                                 pip_wheel=Path('missing'), pip_sha256='a'*64,
                                 output_dir=Path('never'), allow_trusted_pypi_network=False)

    def test_ssh_denies_unapproved_upload(self):
        controller=SSHControl.__new__(SSHControl)
        with self.assertRaises(WorkerFailure):
            controller.copy_offline_asset(Path('x'), 'secrets.txt')
        with self.assertRaises(WorkerFailure):
            controller.receive_offline_cache(Path('x'), remote_filename='../../private')


class PythonIntegrationContractTests(unittest.TestCase):
    def test_fastapi_openapi_probe_requires_json_metadata(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"openapi":"3.1.0","paths":{}}'
        with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                   return_value=Response()) as request:
            status, details = _probe_python_web('/openapi.json', 'fastapi')
        self.assertEqual((status, details), (200, 'HTTP_RESPONSE_OK'))
        self.assertEqual(request.call_count, 1)
        with self.assertRaisesRegex(GuestFailure, 'HTTP_PROBE_FAILED'):
            _probe_python_web('/unauthorized', 'fastapi')

    def test_flask_probe_accepts_nonempty_api_responses(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"status":"ok"}'
        with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                   return_value=Response()):
            self.assertEqual(_probe_python_web('/', 'flask')[0], 200)

    def test_host_prepare_is_gated_and_exports_only_verified_wheels(self):
        from projectiq_executioner.models import RepositorySource
        import projectiq_executioner.phase2.phase2d.python_cache_prep as prep
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=root/'vm_config.json'
            cfg.write_text(json.dumps({'base_image':'trusted.qcow2', 'base_sha256':'1'*64,
                         'accelerator':'whpx', 'approved_snapshots':[IDENTITY]}),'utf-8')
            pip_file=root/'pip-25.2-py3-none-any.whl'
            pip_file.write_bytes(b'mock-pip-archive')
            wheel_zip=make_wheel_zip(root)
            content=wheel_zip.read_bytes()
            source=RepositorySource(kind='github', repository='Fixture/PythonWeb',
                url='https://github.com/Fixture/PythonWeb', requested_ref=SHA,
                resolved_ref=SHA, commit_sha=SHA)
            test_calls=[]
            class Client:
                def __init__(self, *args, **kwargs): pass
                def resolve(self, repo, ref): return source
                def download(self, src, destination):
                    test_calls.append('download')
                    destination.write_bytes(b'mocked-snapshot')
            class VM:
                def __init__(self,*args,**kwargs):
                    self.proc=SimpleNamespace(poll=lambda: None)
                    self.config=kwargs
                def start(self,seed): test_calls.append('start')
                def stop(self): test_calls.append('stop')
            class SSH:
                def __init__(self,*args): pass
                def wait_ssh(self,*args,**kwargs): pass
                def copy(self,src,dest): test_calls.append(('copy',dest))
                def copy_offline_asset(self,src,dest): test_calls.append(('asset',dest))
                def ssh(self,*args,**kwargs): return SimpleNamespace(returncode=0)
                def receive(self, name, local):
                    self.last_local=local
                    local.write_text(json.dumps({'status':'PYTHON_WHEELHOUSE_PREPARED',
                        'framework':'flask','python_version':'3.12',
                        'requirements_sha256':req_sha,'wheelhouse_sha256':hashlib.sha256(content).hexdigest()}),'utf-8')
                def receive_offline_cache(self,local,*,remote_filename):
                    self.last_remote=remote_filename
                    local.write_bytes(content)
                    test_calls.append(('retrieved',remote_filename))
            checkout=root/'source';checkout.mkdir()
            req_sha=write_python(checkout)
            out=root/'out'
            candidate={'adapter':'python_web_offline_v1','project_root':'.','python_framework':'flask'}
            with patch.object(prep,'GitHubClient',Client), \
                 patch.object(prep,'verify_host_toolchain',return_value={'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
                 patch.object(prep,'extract_snapshot',return_value=checkout), \
                 patch.object(prep,'analyze_directory',return_value=SimpleNamespace(to_dict=lambda:manifest())), \
                 patch.object(prep,'plan_from_manifest',return_value=(candidate,{})), \
                 patch.object(prep,'make_ssh_key',return_value=(root/'key','dummy-public-key')), \
                 patch.object(prep,'make_nocloud_iso',return_value=root/'seed.iso'), \
                 patch.object(prep,'QemuWorker',VM), \
                 patch.object(prep,'SSHControl',SSH):
                result=prepare_python_cache(repo_url=source.url,commit_sha=SHA,vm_config=cfg,
                    pip_wheel=pip_file,pip_sha256=sha(pip_file), output_dir=out,
                    allow_trusted_pypi_network=True)
            self.assertEqual(result['status'],'OFFLINE_PYTHON_WHEELHOUSE_PREPARED_NOT_SANDBOX_ATTESTED')
            self.assertIn(('asset','python_pip.whl'),test_calls)
            self.assertIn(('retrieved','python_wheelhouse.zip'),test_calls)
            self.assertTrue((out/'python_web_profile.json').is_file())
            self.assertEqual((out/'python-wheelhouse.zip').read_bytes(),content)
            self.assertEqual(test_calls[-1],'stop')

    def test_host_refuses_unapproved_pinned_source_before_download(self):
        from projectiq_executioner.models import RepositorySource
        import projectiq_executioner.phase2.phase2d.python_cache_prep as prep
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=root/'vm.json'; cfg.write_text(json.dumps({'base_image':'image','base_sha256':'1'*64,
                'approved_snapshots':[]}), 'utf-8')
            pip=root/'pip-25.2-py3-none-any.whl'; pip.write_bytes(b'fake')
            src=RepositorySource(kind='github',repository='Fixture/PythonWeb',
                url='https://github.com/Fixture/PythonWeb',requested_ref=SHA,
                resolved_ref=SHA,commit_sha=SHA)
            with patch.object(prep,'GitHubClient') as factory:
                factory.return_value.resolve.return_value=src
                with self.assertRaisesRegex(PythonCacheError,'not approved'):
                    prepare_python_cache(repo_url=src.url,commit_sha=SHA,vm_config=cfg,
                        pip_wheel=pip,pip_sha256=sha(pip),output_dir=root/'out',
                        allow_trusted_pypi_network=True)
                factory.return_value.download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
