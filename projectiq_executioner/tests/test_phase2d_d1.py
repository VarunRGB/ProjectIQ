"""Phase 2D D1 offline Node/Vite pilot; no npm execution on the test host."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import build_vm_plan
from projectiq_executioner.phase2.vm_worker.offline_assets import (OfflineAssetError, approved_node_vite_assets)
from projectiq_executioner.phase2.vm_worker.guest_agent import (_check_vite_manifest, _node_tar_extract, _prepare_vite, _isolated_npm_config_paths, GuestFailure)
from projectiq_executioner.phase2.phase2d.capabilities import build_capability_plan

SHA = '3b' * 20
IDENTITY = 'Example/ViteDemo@' + SHA
VER = 'v24.22.3'


def _vite_manifest(root='.'):
    prefix = '' if root == '.' else root + '/'
    return {
        'schema_version': '1.0', 'phase': 1,
        'source': {'kind': 'github', 'repository': 'Example/ViteDemo', 'commit_sha': SHA},
        'services': [{'name': 'frontend', 'kind': 'frontend', 'root': root,
                      'technology': 'React', 'depends_on': []}],
        'setup_files': [prefix + 'package.json', prefix + 'package-lock.json'],
        'technologies': [{'name': 'Vite', 'category': 'build'}],
        'pages': ['index.html'], 'routes': [], 'database_schema_files': [],
        'environment_variables': [], 'inventory': {},
    }


def _plans(manifest):
    a = build_execution_plan(manifest)
    b = create_preflight(manifest, a)
    return a, b, build_vm_plan(manifest, a, b)


def _fixtures(tmp):
    root = Path(tmp)
    node = root / f'node-{VER}-linux-x64.tar.xz'
    node.write_bytes(b'node-runtime-bundle')
    cache = root / 'npm-cache.zip'
    cache.write_bytes(b'offline-cache')
    return node, cache


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NodeViteD1Tests(unittest.TestCase):
    def test_vite_standalone_selects_conditional_offline_adapter(self):
        m = _vite_manifest()
        a, b, candidate = _plans(m)
        self.assertEqual(candidate['adapter'], 'node_vite_offline_v1')
        self.assertEqual(candidate['probe_path'], '/index.html')
        self.assertEqual(candidate['project_root'], '.')
        self.assertEqual(candidate['vm_limits']['ram_mib'], 3072)
        self.assertEqual(candidate['vm_limits']['wall_seconds'], 480)
        cap = build_capability_plan(m, a, b, candidate)
        self.assertEqual(cap['status'], 'PHASE2D_OFFLINE_NODE_ADAPTER_CONDITIONAL')
        self.assertEqual(cap['phase2d_adapter'], 'node_vite_offline_v1')
        self.assertEqual(cap['execution'], 'NOT_PERFORMED')

    def test_npm_user_global_configs_are_distinct_empty_guest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            values = _isolated_npm_config_paths(base)
            user = Path(values['NPM_CONFIG_USERCONFIG'])
            global_cfg = Path(values['NPM_CONFIG_GLOBALCONFIG'])
            self.assertNotEqual(user, global_cfg)
            for config in (user, global_cfg):
                self.assertTrue(config.is_file())
                self.assertEqual(config.read_bytes(), b'')
                self.assertEqual(config.parent, base / '.projectiq-npm-config')
                if os.name != 'nt':
                    self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertTrue(all(str(config).startswith(str(base)) for config in (user, global_cfg)))

    def test_safe_nested_vite_root_supported(self):
        m = _vite_manifest('frontend')
        self.assertEqual(_plans(m)[2]['project_root'], 'frontend')

    def test_not_vite_without_lockfile_or_evidence(self):
        for mutation in ('no_vite', 'no_lock', 'unsafe_root', 'backend'):
            m = _vite_manifest()
            if mutation == 'no_vite': m['technologies'] = []
            if mutation == 'no_lock': m['setup_files'].remove('package-lock.json')
            if mutation == 'unsafe_root': m['services'][0]['root'] = '../outside'
            if mutation == 'backend':
                m['services'].append({'name':'api', 'kind':'backend', 'technology':'Express',
                                      'root':'api', 'depends_on':[]})
            if mutation == 'unsafe_root':
                with self.assertRaises(Exception): _plans(m)
            else:
                self.assertIsNone(_plans(m)[2]['adapter'], mutation)

    def test_rejects_node_candidate_tampering(self):
        m = _vite_manifest(); a,b,c = _plans(m)
        c['project_root'] = '../../evil'
        with self.assertRaisesRegex(Exception, 'differs'):
            build_capability_plan(m,a,b,c)

    def test_missing_config_denied(self):
        with self.assertRaisesRegex(OfflineAssetError, 'NOT_APPROVED'):
            approved_node_vite_assets({}, IDENTITY)

    def test_pinned_valid_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            node,cache=_fixtures(tmp)
            cfg={'node_vite_offline':{IDENTITY:{
                'node_version':VER,'node_tarball':str(node), 'node_sha256':_hash(node),
                'npm_cache_zip':str(cache),'npm_cache_sha256':_hash(cache),
                'lockfile_sha256':'c'*64}}}
            profile=approved_node_vite_assets(cfg,IDENTITY)
            self.assertEqual(profile['node_tarball'],node)
            self.assertEqual(profile['lockfile_sha256'],'c'*64)
            node.write_bytes(b'changed')
            with self.assertRaisesRegex(OfflineAssetError, 'MISMATCH'):
                approved_node_vite_assets(cfg,IDENTITY)

    def test_does_not_allow_wrong_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            node,cache=_fixtures(tmp)
            node2=node.with_name('node-v24.22.3-win-x64.zip')
            node.rename(node2)
            cfg={'node_vite_offline':{IDENTITY:{
                'node_version':VER,'node_tarball':str(node2),'node_sha256':_hash(node2),
                'npm_cache_zip':str(cache),'npm_cache_sha256':_hash(cache),
                'lockfile_sha256':'c'*64}}}
            with self.assertRaisesRegex(OfflineAssetError, 'LINUX_NODE_TARBALL_EXPECTED'):
                approved_node_vite_assets(cfg,IDENTITY)

    def test_rejects_unpinned_sha_and_unsupported_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            node,cache=_fixtures(tmp)
            obj={'node_version':VER,'node_tarball':str(node),'node_sha256':_hash(node),
                 'npm_cache_zip':str(cache),'npm_cache_sha256':_hash(cache),'lockfile_sha256':'c'*64}
            for key,value,part in [('node_version','v30.0.0','UNSUPPORTED_NODE_VERSION'),
                                   ('lockfile_sha256','oops','SHA256_INVALID')]:
                with self.subTest(key=key):
                    mutated={**obj,key:value}
                    with self.assertRaisesRegex(OfflineAssetError,part):
                        approved_node_vite_assets({'node_vite_offline':{IDENTITY:mutated}},IDENTITY)

    def _write_package(self, root, resolved='https://registry.npmjs.org/vite/-/vite-7.0.0.tgz'):
        root.mkdir(exist_ok=True,parents=True)
        pkg={'name':'fixture','version':'1.0','devDependencies':{'vite':'7.0.0'}, 'scripts':{'build':'vite build'}}
        (root/'package.json').write_text(json.dumps(pkg),'utf-8')
        lock={'lockfileVersion':3,'name':'fixture','packages':{'':{'name':'fixture'},'node_modules/vite':{
            'resolved':resolved,'integrity':'sha512-test'}}}
        lock_bytes=json.dumps(lock).encode()
        (root/'package-lock.json').write_bytes(lock_bytes)
        return _hash(root/'package-lock.json')

    def test_manifest_pinned_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); locksha=self._write_package(root)
            _check_vite_manifest(root,locksha)
            with self.assertRaisesRegex(GuestFailure,'LOCKFILE_HASH_MISMATCH'):
                _check_vite_manifest(root,'0'*64)

    def test_bad_registry_url_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sha=self._write_package(root,'https://evil.example/vite.tgz')
            with self.assertRaisesRegex(GuestFailure,'MANIFEST_OR_LOCKFILE_UNSUPPORTED'):
                _check_vite_manifest(root,sha)

    def test_project_npmrc_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sha=self._write_package(root)
            (root/'.npmrc').write_text('registry=https://evil.example\n')
            with self.assertRaisesRegex(GuestFailure,'MANIFEST_OR_LOCKFILE_UNSUPPORTED'):
                _check_vite_manifest(root,sha)

    def _make_tar(self,path, *, evil=False):
        root=f'node-{VER}-linux-x64'
        with tarfile.open(path,'w:xz') as tar:
            for rel,data in [('bin/node',b'fake-node'),('lib/node_modules/npm/bin/npm-cli.js',b'fake-npm')]:
                content=io.BytesIO(data)
                info=tarfile.TarInfo(('outside' if evil else root)+'/'+rel)
                info.size=len(data);info.mode=0o755
                tar.addfile(info,content)
            if not evil:
                info=tarfile.TarInfo(root+'/bin/npm');info.type=tarfile.SYMTYPE
                info.linkname='../lib/node_modules/npm/bin/npm-cli.js'
                tar.addfile(info)

    def test_official_style_node_archive_extracts_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);a=base/'node.tar.xz';self._make_tar(a)
            dest=base/'extract';dest.mkdir()
            node,npm=_node_tar_extract(a,VER,dest)
            self.assertTrue(node.is_file())
            self.assertTrue(npm.is_file())
            # The actual guest is Linux, where npm remains a symlink. On
            # Windows, tarfile may copy the link target when creating a
            # symlink is disallowed by local privileges or Developer Mode.
            if os.name == 'nt' and not npm.is_symlink():
                self.assertEqual(npm.read_bytes(), b'fake-npm')
            else:
                self.assertTrue(npm.is_symlink())
                self.assertEqual(os.readlink(npm), '../lib/node_modules/npm/bin/npm-cli.js')

    def test_node_tar_symlink_fallback_copies_only_validated_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / 'node.tar.xz'
            self._make_tar(archive)
            dest = base / 'extract'
            dest.mkdir()
            # Emulate Windows without permission to create symbolic links.
            with patch('tarfile.os.symlink', side_effect=OSError('symlink privilege missing')):
                node, npm = _node_tar_extract(archive, VER, dest)
            self.assertTrue(node.is_file())
            self.assertTrue(npm.is_file())
            self.assertFalse(npm.is_symlink())
            self.assertEqual(npm.read_bytes(), b'fake-npm')

    def test_node_tar_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);a=base/'node.tar.xz';self._make_tar(a,evil=True)
            dest=base/'extract';dest.mkdir()
            with self.assertRaisesRegex(GuestFailure,'NODE_ARCHIVE_INVALID'):
                _node_tar_extract(a,VER,dest)

    def test_prepare_rejects_mutated_node_tar(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir()
            sha=self._write_package(checkout)
            (base/'node_runtime.tar.xz').write_bytes(b'bad')
            (base/'npm_cache.zip').write_bytes(b'zip')
            req={'project_root':'.','node_version':VER, 'node_sha256':'0'*64,
                 'npm_cache_sha256':_hash(base/'npm_cache.zip'), 'lockfile_sha256':sha}
            with self.assertRaisesRegex(GuestFailure,'NODE_HASH_MISMATCH'):
                _prepare_vite(checkout,base,req)

    def test_real_guest_path_uses_offline_flags_without_host_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);checkout=base/'checkout';checkout.mkdir()
            sha=self._write_package(checkout)
            tar=base/'node_runtime.tar.xz';self._make_tar(tar)
            cache=base/'npm_cache.zip'
            with zipfile.ZipFile(cache,'w') as z:
                z.writestr('npm-cache/_cacache/index-v5/1',b'data')
            req={'project_root':'.','node_version':VER,'node_sha256':_hash(tar),
                 'npm_cache_sha256':_hash(cache),'lockfile_sha256':sha}
            calls=[]
            def runner(argv,**kw):
                calls.append((argv,kw))
                if '--version' in argv:
                    return SimpleNamespace(returncode=0,stdout=(VER+'\n').encode(),stderr=b'')
                if 'build' in argv:
                    out=checkout/'dist';out.mkdir();(out/'index.html').write_text('<html>Built</html>')
                if 'ci' in argv:
                    vite=checkout/'node_modules/vite/bin';vite.mkdir(parents=True)
                    (vite/'vite.js').write_text('')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',runner):
                out,note=_prepare_vite(checkout,base,req)
            self.assertEqual(out,checkout/'dist')
            npm_call=next((c for c in calls if 'ci' in c[0]),None)
            self.assertIsNotNone(npm_call)
            self.assertIn('--offline',npm_call[0])
            self.assertIn('--ignore-scripts',npm_call[0])
            self.assertEqual(npm_call[1]['cwd'],checkout)
            self.assertEqual(npm_call[1]['env']['NPM_CONFIG_OFFLINE'],'true')
            self.assertNotEqual(npm_call[1]['env']['NPM_CONFIG_USERCONFIG'], npm_call[1]['env']['NPM_CONFIG_GLOBALCONFIG'])
            self.assertEqual(Path(npm_call[1]['env']['NPM_CONFIG_USERCONFIG']).read_bytes(), b'')
            self.assertEqual(Path(npm_call[1]['env']['NPM_CONFIG_GLOBALCONFIG']).read_bytes(), b'')
            self.assertIn('dist/index.html',note)


if __name__=='__main__':
    unittest.main()

class NodeViteCachePrepTests(unittest.TestCase):
    def test_node_cache_prep_cannot_use_network_without_explicit_approval(self):
        from projectiq_executioner.phase2.phase2d.cache_prep import prepare_cache, CachePreparationError
        with self.assertRaisesRegex(CachePreparationError, 'Explicit'):
            prepare_cache(repo_url='https://github.com/Example/ViteDemo', commit_sha=SHA,
                          vm_config=Path('not-present.json'), node_tarball=Path('missing.tar.xz'),
                          node_sha256='0'*64, node_version=VER, output_dir=Path('out'))

    def test_node_cache_prep_rejects_mutable_commit_before_file_io(self):
        from projectiq_executioner.phase2.phase2d.cache_prep import prepare_cache, CachePreparationError
        with self.assertRaisesRegex(CachePreparationError, '40-character'):
            prepare_cache(repo_url='https://github.com/Example/ViteDemo', commit_sha='main',
                          vm_config=Path('missing'), node_tarball=Path('missing'),
                          node_sha256='0'*64, node_version=VER,
                          output_dir=Path('out'), allow_trusted_npm_network=True)

    def test_node_cache_prep_rejects_bad_pinned_official_archive_before_network(self):
        from projectiq_executioner.phase2.phase2d.cache_prep import prepare_cache, CachePreparationError
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/f'node-{VER}-linux-x64.tar.xz';p.write_bytes(b'bad-sha')
            with patch('projectiq_executioner.phase2.phase2d.cache_prep.GitHubClient') as github:
                with self.assertRaisesRegex(CachePreparationError, 'SHA256 mismatched'):
                    prepare_cache(repo_url='https://github.com/Example/ViteDemo', commit_sha=SHA,
                                  vm_config=Path('missing'), node_tarball=p,
                                  node_sha256='0'*64, node_version=VER,
                                  output_dir=Path(tmp), allow_trusted_npm_network=True)
                github.assert_not_called()

    def test_runtime_qemu_remains_egress_restricted(self):
        from projectiq_executioner.phase2.vm_worker.qemu import QemuWorker
        with tempfile.TemporaryDirectory() as tmp:
            vm=QemuWorker({'accelerator':'whpx'},Path(tmp),5555,ram_mib=2048,cpus=1)
            self.assertFalse(vm.trusted_dependency_fetch)
            self.assertFalse(vm.trusted_template_setup)
            with self.assertRaisesRegex(Exception, 'cannot share'):
                QemuWorker({'accelerator':'whpx'},Path(tmp),5555,
                           trusted_template_setup=True, trusted_dependency_fetch=True)

    def test_cache_preparation_guest_does_not_run_build_scripts(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import prepare_npm_cache
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); checkout=base/'checkout';checkout.mkdir()
            helper=NodeViteD1Tests()
            locksha=helper._write_package(checkout)
            tar=base/'node_runtime.tar.xz';helper._make_tar(tar)
            with zipfile.ZipFile(base/'snapshot.zip','w') as z:
                for rel in ('package.json','package-lock.json'):
                    z.write(checkout/rel,arcname='snapshot/'+rel)
            (base/'checkout'/'package.json').unlink()
            (base/'checkout'/'package-lock.json').unlink()
            (base/'checkout').rmdir()
            req={'operation':'prepare_npm_cache','adapter':'node_vite_offline_v1',
                 'project_root':'.','node_version':VER,'node_sha256':_hash(tar),
                 'lockfile_sha256':locksha}
            request=base/'request.json';request.write_text(json.dumps(req))
            commands=[]
            def runner(argv,**kwargs):
                commands.append((argv,kwargs))
                if 'ci' in argv:
                    cache=base/'npm-cache/_cacache';cache.mkdir(parents=True)
                    (cache/'dummy').write_bytes(b'data')
                return SimpleNamespace(returncode=0,stdout=(VER+'\n').encode(),stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',runner):
                response=prepare_npm_cache(request,base_dir=base)
            self.assertEqual(response['status'],'CACHE_PREPARED')
            self.assertEqual(response['lockfile_sha256'],locksha)
            self.assertEqual(response['npm_cache_sha256'],_hash(base/'npm_cache.zip'))
            npm=next(c for c in commands if 'ci' in c[0])
            self.assertIn('--ignore-scripts',npm[0])
            self.assertNotIn('run',npm[0])
            self.assertEqual(npm[1]['cwd'],base/'checkout')
            self.assertNotEqual(npm[1]['env']['NPM_CONFIG_USERCONFIG'], npm[1]['env']['NPM_CONFIG_GLOBALCONFIG'])
            self.assertEqual(Path(npm[1]['env']['NPM_CONFIG_USERCONFIG']).read_bytes(), b'')
            self.assertEqual(Path(npm[1]['env']['NPM_CONFIG_GLOBALCONFIG']).read_bytes(), b'')
            self.assertNotIn('NPM_CONFIG_OFFLINE',npm[1]['env'])
            with zipfile.ZipFile(base/'npm_cache.zip') as z:
                self.assertEqual(z.namelist(),['npm-cache/_cacache/dummy'])

    def test_cache_preparation_guest_enforces_explicit_operation(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import prepare_npm_cache
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'request.json';p.write_text(json.dumps({'adapter':'node_vite_offline_v1'}))
            with self.assertRaisesRegex(GuestFailure,'NODE_VITE_INVALID_REQUEST'):
                prepare_npm_cache(p,base_dir=Path(tmp))

class NodeViteNpmDiagnosticsTests(unittest.TestCase):
    def test_npm_cache_failure_categories(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _classify_npm_cache_failure
        cases = (
            (b'npm error code EAI_AGAIN', 'NODE_VITE_NPM_NETWORK_DNS'),
            (b'npm ERR! code ETIMEDOUT', 'NODE_VITE_NPM_NETWORK_TIMEOUT'),
            (b'npm ERR! code ECONNRESET', 'NODE_VITE_NPM_NETWORK_CONNECTIVITY'),
            (b'npm error code SELF_SIGNED_CERT_IN_CHAIN', 'NODE_VITE_NPM_TLS_CERTIFICATE'),
            (b'npm error code ENOSPC', 'NODE_VITE_NPM_DISK_SPACE'),
            (b'npm ERR! code EACCES', 'NODE_VITE_NPM_FILE_PERMISSION'),
            (b'npm error code EINTEGRITY', 'NODE_VITE_NPM_INTEGRITY'),
            (b'npm error code ERESOLVE', 'NODE_VITE_NPM_DEPENDENCY_CONFLICT'),
            (b'npm error code E403', 'NODE_VITE_NPM_REGISTRY_DENIED'),
            (b'npm error code E404', 'NODE_VITE_NPM_REGISTRY_NOT_FOUND'),
            (b'npm error code EBADPLATFORM', 'NODE_VITE_NPM_PLATFORM_UNSUPPORTED'),
            (b'npm ci can only install packages when your package.json is in sync',
             'NODE_VITE_NPM_LOCKFILE_INCONSISTENT'),
        )
        for stderr, expected in cases:
            with self.subTest(stderr=stderr):
                self.assertEqual(_classify_npm_cache_failure(stderr), expected)

    def test_unrecognized_output_never_echoed(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _classify_npm_cache_failure
        output = b'private token abcdef12345, https://private.example/secret?token=abcdef'
        actual = _classify_npm_cache_failure(output)
        self.assertEqual(actual, 'NODE_VITE_NPM_FAILURE_UNCLASSIFIED')
        self.assertNotIn('abcdef', actual)
        self.assertNotIn('private.example', actual)

    def test_guest_reports_only_sanitized_npm_category(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import prepare_npm_cache
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = base / 'fixture'
            checkout.mkdir()
            helper = NodeViteD1Tests()
            locksha = helper._write_package(checkout)
            node_tar = base / 'node_runtime.tar.xz'
            helper._make_tar(node_tar)
            with zipfile.ZipFile(base / 'snapshot.zip', 'w') as archive:
                for name in ('package.json', 'package-lock.json'):
                    archive.write(checkout / name, arcname='snapshot/' + name)
            req = {
                'operation': 'prepare_npm_cache', 'adapter': 'node_vite_offline_v1',
                'project_root': '.', 'node_version': VER,
                'node_sha256': _hash(node_tar), 'lockfile_sha256': locksha,
            }
            request = base / 'request.json'
            request.write_text(json.dumps(req))
            def failed_npm(argv, **kw):
                if '--version' in argv:
                    return SimpleNamespace(returncode=0, stdout=(VER + '\n').encode(), stderr=b'')
                return SimpleNamespace(returncode=1, stdout=b'',
                                       stderr=b'npm ERR! code EAI_AGAIN https://private.example/?token=abcdef')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process', failed_npm):
                with self.assertRaises(GuestFailure) as error:
                    prepare_npm_cache(request, base_dir=base)
            self.assertEqual(str(error.exception), 'NODE_VITE_NPM_NETWORK_DNS')

class NodeViteNpmDeepDiagnosticTests(unittest.TestCase):
    def test_stderr_is_checked_before_debug_log(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / '_logs').mkdir()
            (cache / '_logs' / '2026-debug-0.log').write_bytes(b'npm error code ENOENT')
            failure = SimpleNamespace(returncode=1, stdout=b'', stderr=b'npm ERR! code EAI_AGAIN')
            self.assertEqual(_npm_failure_with_debug_context(failure, cache), 'NODE_VITE_NPM_NETWORK_DNS')

    def test_stdout_is_classified_if_stderr_is_empty(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        with tempfile.TemporaryDirectory() as tmp:
            failure = SimpleNamespace(returncode=1, stdout=b'npm error code E401 token=SECRET', stderr=b'')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen') as opened:
                code = _npm_failure_with_debug_context(failure, Path(tmp))
            self.assertEqual(code, 'NODE_VITE_NPM_AUTH_REQUIRED')
            opened.assert_not_called()
            self.assertNotIn('SECRET', code)

    def test_npm_debug_log_is_classified_without_exporting_contents(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / '_logs').mkdir()
            (cache / '_logs' / '2026-11-20-debug-0.log').write_bytes(
                b'private URL https://registry.npmjs.org/package?token=SECRET\nnpm error code ENOENT\n')
            failure = SimpleNamespace(returncode=1, stdout=b'', stderr=b'npm failed with private=SECRET')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen') as opened:
                code = _npm_failure_with_debug_context(failure, cache)
            self.assertEqual(code, 'NODE_VITE_NPM_MISSING_FILE')
            opened.assert_not_called()
            self.assertNotIn('SECRET', code)

    def test_unclassified_failure_reports_registry_reachable_not_raw_output(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        from contextlib import nullcontext
        failure = SimpleNamespace(returncode=1, stdout=b'private=SECRET', stderr=b'unrecognized error')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                       return_value=nullcontext(SimpleNamespace(status=200))) as opened:
                code = _npm_failure_with_debug_context(failure, Path(tmp))
            self.assertEqual(code, 'NODE_VITE_NPM_UNCLASSIFIED_REGISTRY_REACHABLE')
            self.assertEqual(opened.call_args.args[0], 'https://registry.npmjs.org/-/ping')
            self.assertNotIn('SECRET', code)

    def test_registry_dns_failure_is_distinguished(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        import socket
        import urllib.error
        failure = SimpleNamespace(returncode=1, stdout=b'', stderr=b'something unexpected')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                       side_effect=urllib.error.URLError(socket.gaierror(-3, 'Temporary failure in name resolution'))):
                code = _npm_failure_with_debug_context(failure, Path(tmp))
            self.assertEqual(code, 'NODE_VITE_NPM_NETWORK_DNS')

    def test_unclassified_signal_does_not_need_registry_probe(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_failure_with_debug_context
        failure = SimpleNamespace(returncode=137, stdout=b'', stderr=b'unexpected failure')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen') as opened:
                code = _npm_failure_with_debug_context(failure, Path(tmp))
            self.assertEqual(code, 'NODE_VITE_NPM_PROCESS_TERMINATED')
            opened.assert_not_called()


class NodeViteRuntimeGateTests(unittest.TestCase):
    def test_disposable_vm_refuses_missing_node_profile_without_launch(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        m=_vite_manifest();a,b,c=_plans(m)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result.json'
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.verify_host_toolchain') as toolchain:
                result=execute_vm_candidate(c,{'approved_snapshots':[IDENTITY]},out,reviewed_fixture=True)
            self.assertEqual(result['status'],'DENIED')
            self.assertEqual(result['reason'],'NODE_VITE_OFFLINE_FIXTURE_ASSETS_NOT_APPROVED')
            self.assertEqual(result['execution'],'NOT_PERFORMED')
            toolchain.assert_not_called()

    def test_offline_vite_refuses_unapproved_sha_before_asset_check(self):
        from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
        m=_vite_manifest();a,b,c=_plans(m)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result.json'
            with patch('projectiq_executioner.phase2.vm_worker.orchestrator.approved_node_vite_assets') as approved:
                result=execute_vm_candidate(c,{'approved_snapshots':[]},out,reviewed_fixture=True)
            self.assertEqual(result['status'],'DENIED')
            approved.assert_not_called()

    def test_vite_asset_probe_must_have_built_javascript(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _probe_vite_asset
        with tempfile.TemporaryDirectory() as tmp:
            dist=Path(tmp)/'dist';dist.mkdir()
            with self.assertRaisesRegex(GuestFailure,'BUILD_OUTPUT_MISSING'):
                _probe_vite_asset(dist,8765)
            assets=dist/'assets';assets.mkdir()
            (assets/'index-hash.js').write_bytes(b'console.log(1)')
            response=io.BytesIO(b'console.log(1)')
            response.status=200
            from contextlib import nullcontext
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                       return_value=nullcontext(response)) as opened:
                note=_probe_vite_asset(dist,8765)
            self.assertIn('HTTP 200',note)
            self.assertIn('/assets/index-hash.js',opened.call_args.args[0])


class NodeViteNpmOperatorDiagnosticTests(unittest.TestCase):
    def test_only_npm_error_lines_are_surfaced_and_sensitive_lines_dropped(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_operator_safe_hint
        failure = SimpleNamespace(returncode=1, stdout=b'irrelevant secret=PRIVATE', stderr=(
            b'npm error Exit handler never called!\n'
            b'npm error token=private should not print\n'
            b'npm error See https://registry.npmjs.org/sample\n'))
        with tempfile.TemporaryDirectory() as tmp:
            hint = _npm_operator_safe_hint(failure, Path(tmp))
        self.assertIn('Exit handler never called!', hint)
        self.assertNotIn('private', hint.lower())
        self.assertNotIn('registry.npmjs.org', hint)
        self.assertIn('[URL]', hint)

    def test_npm_diagnostic_extracts_npm_error_from_local_debug_log(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_operator_safe_hint
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp)
            (cache/'_logs').mkdir()
            (cache/'_logs'/'2026-debug-0.log').write_text('npm error Exit handler never called!\n')
            failure=SimpleNamespace(returncode=1, stdout=b'', stderr=b'')
            self.assertIn('Exit handler never called!', _npm_operator_safe_hint(failure,cache))

    def test_diagnostic_is_opt_in_and_off_by_default(self):
        import inspect
        from projectiq_executioner.phase2.phase2d.cache_prep import prepare_cache
        self.assertFalse(inspect.signature(prepare_cache).parameters['include_npm_diagnostic'].default)

    def test_prep_opt_in_exposes_only_redacted_npm_hint(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import (
            prepare_npm_cache, NpmPreparationDiagnostic)
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            src=base/'source';src.mkdir()
            locksha=NodeViteD1Tests()._write_package(src)
            archive=base/'node_runtime.tar.xz'
            NodeViteD1Tests()._make_tar(archive)
            with zipfile.ZipFile(base/'snapshot.zip','w') as z:
                for name in ('package.json','package-lock.json'):
                    z.write(src/name,arcname='snapshot/'+name)
            req={'operation':'prepare_npm_cache','adapter':'node_vite_offline_v1',
                 'project_root':'.','node_version':VER,
                 'node_sha256':_hash(archive),'lockfile_sha256':locksha,
                 'include_npm_diagnostic':True}
            (base/'request.json').write_text(json.dumps(req))
            def runner(argv,**kwargs):
                if '--version' in argv:
                    return SimpleNamespace(returncode=0,stdout=(VER+'\n').encode(),stderr=b'')
                return SimpleNamespace(returncode=1,stdout=b'',
                       stderr=b'npm error Exit handler never called!\nnpm error secret=LEAK\n')
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',runner), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.urllib.request.urlopen',
                       return_value=nullcontext(SimpleNamespace(status=200))):
                with self.assertRaises(NpmPreparationDiagnostic) as result:
                    prepare_npm_cache(base/'request.json',base_dir=base)
            self.assertIn('Exit handler never called!',result.exception.safe_hint)
            self.assertNotIn('LEAK',result.exception.safe_hint)
            self.assertEqual(str(result.exception),'NODE_VITE_NPM_UNCLASSIFIED_REGISTRY_REACHABLE')


class NodeViteNpmProcessProbeTests(unittest.TestCase):
    def test_non_npm_prefixed_node_error_is_visible_when_opted_in(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_operator_safe_hint
        with tempfile.TemporaryDirectory() as tmp:
            failure = SimpleNamespace(returncode=127, stdout=b'',
                 stderr=b'node: error while loading shared libraries: libatomic.so.1: cannot open shared object file\n')
            hint = _npm_operator_safe_hint(failure, Path(tmp))
            self.assertIn('npm exit 127', hint)
            self.assertIn('stderr bytes', hint)
            self.assertIn('loading shared libraries', hint)

    def test_process_probe_does_not_repeat_sensitive_strings(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_operator_safe_hint
        with tempfile.TemporaryDirectory() as tmp:
            failure = SimpleNamespace(returncode=1,
                stdout=b'npm error token=VERY_PRIVATE_PASSWORD\n',
                stderr=b'Error: fetch failed for https://host.test/a?secret=TOPSECRET\n'
                       b'FATAL ERROR: Reached heap limit Allocation failed\n')
            hint = _npm_operator_safe_hint(failure, Path(tmp))
            self.assertIn('heap limit', hint.lower())
            self.assertNotIn('TOPSECRET', hint)
            self.assertNotIn('VERY_PRIVATE_PASSWORD', hint)
            self.assertNotIn('host.test', hint)

    def test_empty_npm_output_produces_useful_numeric_facts(self):
        from projectiq_executioner.phase2.vm_worker.guest_agent import _npm_operator_safe_hint
        with tempfile.TemporaryDirectory() as tmp:
            failure = SimpleNamespace(returncode=1, stdout=b'', stderr=b'')
            hint = _npm_operator_safe_hint(failure, Path(tmp))
            self.assertIn('npm exit 1 stdout bytes 0 stderr bytes 0 debug logs 0',hint)
            self.assertNotIn('No shareable',hint)
