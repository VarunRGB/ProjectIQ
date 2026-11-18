"""VM pilot contract + offline trusted-fixture tests (NO VM, no Docker required)."""
from __future__ import annotations

import copy
import json
import os
import socket
import stat
import tempfile
from unittest.mock import patch, MagicMock
import unittest
import zipfile
from pathlib import Path

from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import VMPlanError, build_vm_plan
from projectiq_executioner.phase2.vm_worker.guest_agent import (
    GuestFailure, _check_sql, _php_pdo_rebind, run, safe_extract,
)
from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
from projectiq_executioner.phase2.vm_worker.qemu import QemuWorker, sha256_file, make_nocloud_iso, WorkerFailure
from projectiq_executioner.phase2.vm_worker.template_bake import bake_template

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _load_samples(name: str):
    p1 = json.loads((FIXTURES / (name + '.json')).read_text('utf-8'))
    p2 = json.loads((FIXTURES / (name + '_2a.json')).read_text('utf-8'))
    return p1, p2, create_preflight(p1, p2)


def _fsdl():
    source = {'kind': 'github', 'repository': 'VarunRGB/FSDL',
              'commit_sha': 'f358b6edda071da6666e587a8f1db054965ba106'}
    p1 = {'schema_version': '1.0', 'phase': 1, 'source': source,
          'services': [{'name': 'frontend', 'kind': 'frontend',
                        'root': '.', 'technology': 'HTML/JS frontend (inferred)',
                        'depends_on': []}],
          'pages': ['index.html'], 'routes': [], 'database_schema_files': [],
          'inventory': {}, 'environment_variables': [], 'findings': []}
    p2 = build_execution_plan(p1)
    p3 = create_preflight(p1, p2)
    return p1, p2, p3


class VMContractsTest(unittest.TestCase):
    def test_fsdl_positive_control(self):
        res = build_vm_plan(*_fsdl())
        self.assertEqual(res['adapter'], 'static_html_v1')
        self.assertEqual(res['status'], 'CANDIDATE_NEEDS_VM_VERIFICATION')
        self.assertEqual(res['probe_path'], '/index.html')
        self.assertIn('NO_DOCKER', res['runtime_dependency'])
        self.assertEqual(res['launch_authorization'], 'DENIED_UNTIL_OPERATOR_APPROVAL_AND_RUNTIME_GATES')

    def test_old_docker_failure_is_not_a_vm_launch_gate(self):
        p1, p2, p3 = _fsdl()
        p3['status'] = 'BLOCKED'
        p3['runtime_capability_probe'] = {'capability_status': 'POLICY_REQUIREMENTS_UNMET'}
        p3['blocking_conditions'] = ['CONTAINER_ENGINE_REPORTED_POLICY_GAPS']
        res = build_vm_plan(p1, p2, p3)
        self.assertEqual(res['adapter'], 'static_html_v1')
        self.assertTrue(res['application_preparation']['docker_probe_ignored_on_vm_path'])

    def test_mini_php_mysql(self):
        res = build_vm_plan(*_load_samples('mini_task'))
        self.assertEqual(res['adapter'], 'php_mysql_v1')
        self.assertEqual(res['schema_path'], 'sql/mini_collab_app.sql')
        self.assertEqual(res['probe_path'], '/public/login.html')
        self.assertEqual(res['vm_limits']['ram_mib'], 3072)

    def test_external_integrations_still_require_adapters(self):
        for name in ('cloud_drop', 'moss_meadows'):
            with self.subTest(name=name):
                result = build_vm_plan(*_load_samples(name))
                self.assertIsNone(result['adapter'])
                self.assertEqual(result['status'], 'NEEDS_ADAPTER')

    def test_refuse_phase2b_provenance_tamper(self):
        p1, p2, p3 = _fsdl()
        p3['source']['commit_sha'] = '0' * 40
        with self.assertRaises(VMPlanError):
            build_vm_plan(p1, p2, p3)

    def test_refuse_phase2b_topology_tamper(self):
        p1, p2, p3 = _fsdl()
        p3['logical_services'][0]['root'] = '/host'
        with self.assertRaises(VMPlanError):
            build_vm_plan(p1, p2, p3)

    def test_require_explicit_approved_fixture(self):
        candidate = build_vm_plan(*_fsdl())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'result.json'
            res = execute_vm_candidate(candidate, {'approved_snapshots': []}, out, reviewed_fixture=True)
            self.assertEqual(res['status'], 'DENIED')
            self.assertEqual(res['execution'], 'NOT_PERFORMED')
            self.assertTrue(out.is_file())


class QemuSecurityContractTest(unittest.TestCase):
    def test_qemu_flags_restrict_guest_network_and_no_host_shared_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'base.qcow2'
            base.write_bytes(b'trusted-test-image-data')
            seed = root / 'seed.iso'
            seed.write_bytes(b'test-seed')
            cfg = {'base_image': str(base), 'qemu_binary': '/trusted/qemu',
                   'qemu_img_binary': '/trusted/qemu-img',
                   'accelerator': 'whpx' if os.name == 'nt' else 'kvm'}
            mock_proc = MagicMock()
            mock_proc.poll.return_value = 0
            with patch('projectiq_executioner.phase2.vm_worker.qemu._tool', side_effect=lambda n, p: p or n), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as fake_check, \
                 patch('projectiq_executioner.phase2.vm_worker.qemu.subprocess.Popen', return_value=mock_proc) as fake_popen:
                fake_check.return_value.returncode = 0
                vm = QemuWorker(cfg, root, 47635, ram_mib=1024, cpus=2)
                vm.start(seed)
                args = fake_popen.call_args.args[0]
                self.assertIn('user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:47635-:22', args)
                self.assertNotIn('-virtfs', args)
                self.assertNotIn('-fsdev', args)
                self.assertNotIn('-chardev', args)
                vm.stop()

    def test_wrong_host_accelerator_fails_closed(self):
        """Mocked launch must still reject KVM on Windows / WHPX on Linux."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'base.qcow2'
            base.write_bytes(b'test')
            seed = root / 'seed.iso'
            seed.write_bytes(b'test')
            wrong_accelerator = 'kvm' if os.name == 'nt' else 'whpx'
            cfg = {'base_image': str(base), 'qemu_binary': 'qemu',
                   'qemu_img_binary': 'qemu-img', 'accelerator': wrong_accelerator}
            with patch('projectiq_executioner.phase2.vm_worker.qemu._tool', side_effect=lambda n, p: p or n), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as fake_check, \
                 patch('projectiq_executioner.phase2.vm_worker.qemu.subprocess.Popen') as fake_popen:
                fake_check.return_value.returncode = 0
                worker = QemuWorker(cfg, root, 47635)
                with self.assertRaisesRegex(WorkerFailure, r'(KVM requires Linux|WHPX requires Windows)'):
                    worker.start(seed)
                fake_popen.assert_not_called()

    def test_sha256_pins_immutable_base_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'base.qcow2'
            file.write_bytes(b'known image')
            self.assertEqual(len(sha256_file(file)), 64)
            self.assertEqual(sha256_file(file), sha256_file(file))

    def test_disallow_mismatched_nonapproved_run_even_when_adapter_supported(self):
        candidate = build_vm_plan(*_fsdl())
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'approved_snapshots': ['Unrelated/Repo@' + '0' * 40]}
            result = execute_vm_candidate(candidate, cfg, Path(tmp) / 'report.json', reviewed_fixture=True)
            self.assertEqual(result['status'], 'DENIED')
            self.assertEqual(result['cleanup'], 'NOT_STARTED')


    def test_template_bake_requires_explicit_setup_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkerFailure):
                bake_template(Path(tmp) / 'cloud.qcow2', '0' * 64,
                              Path(tmp) / 'gold.qcow2', {},
                              explicitly_allow_setup_network=False)

    def test_nocloud_runtime_user_never_gets_full_sudo(self):
        class Iso:
            def __init__(self): self.data = {}
            def new(self, **kwargs): pass
            def add_fp(self, data, length, **kwargs):
                self.data[kwargs['rr_name']] = data.read()
            def write(self, path): Path(path).write_bytes(b'fake ISO')
            def close(self): pass
        import sys
        import types
        iso = Iso()
        fake = types.SimpleNamespace(PyCdlib=lambda: iso)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'pycdlib': fake}):
            make_nocloud_iso(Path(tmp), 'ssh-ed25519 AAAATEST TEST', 'projectiq-123')
            self.assertIn(b'NOPASSWD: /usr/bin/mysql', iso.data['user-data'])
            self.assertNotIn(b'NOPASSWD:ALL', iso.data['user-data'])
            iso2 = Iso()
            fake2 = types.SimpleNamespace(PyCdlib=lambda: iso2)
            with patch.dict(sys.modules, {'pycdlib': fake2}):
                make_nocloud_iso(Path(tmp), 'ssh-ed25519 AAAATEST TEST',
                                 'projectiq-456', template_setup=True)
            self.assertIn(b'NOPASSWD:ALL', iso2.data['user-data'])

    def test_only_trusted_template_mode_has_setup_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'base.qcow2'
            base.write_bytes(b'test')
            seed = root / 'seed.iso'
            seed.write_bytes(b'test')
            config = {'base_image': str(base), 'qemu_binary': 'qemu',
                      'qemu_img_binary': 'qemu-img',
                      'accelerator': 'whpx' if os.name == 'nt' else 'kvm'}
            p = MagicMock()
            p.poll.return_value = 0
            with patch('projectiq_executioner.phase2.vm_worker.qemu._tool', side_effect=lambda n, v: v or n), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as cmd, \
                 patch('projectiq_executioner.phase2.vm_worker.qemu.subprocess.Popen', return_value=p) as launch:
                cmd.return_value.returncode = 0
                worker = QemuWorker(config, root, 50000, trusted_template_setup=True)
                worker.start(seed)
                self.assertIn('user,id=net0,hostfwd=tcp:127.0.0.1:50000-:22', launch.call_args.args[0])
                self.assertNotIn('user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:50000-:22', launch.call_args.args[0])
                worker.stop()


class OfflineGuestTest(unittest.TestCase):
    def test_static_html_guest_end_to_end_no_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with zipfile.ZipFile(base / 'snapshot.zip', 'w') as z:
                z.writestr('FSDL-ref/index.html', '<!DOCTYPE html><html><body>test</body></html>')
            (base / 'request.json').write_text(json.dumps({'adapter': 'static_html_v1',
                                                            'probe_path': '/index.html', 'schema_path': None}))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            res = run(base / 'request.json', base_dir=base, port=port)
            self.assertEqual(res['status'], 'STARTUP_VERIFIED')
            self.assertEqual(res['http_status'], 200)

    def test_safe_extract_reject_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with zipfile.ZipFile(base / 'bad.zip', 'w') as z:
                z.writestr('repo/../../escape.txt', 'bad')
            with self.assertRaises(GuestFailure):
                safe_extract(base / 'bad.zip', base / 'out')
            self.assertFalse((base / 'escape.txt').exists())

    def test_safe_extract_reject_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item = zipfile.ZipInfo('repo/symlink')
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(base / 'bad.zip', 'w') as z:
                z.writestr(item, '../../etc/passwd')
            with self.assertRaises(GuestFailure):
                safe_extract(base / 'bad.zip', base / 'out')

    def test_mysql_sql_guard_allows_standard_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            sql = Path(tmp) / 'schema.sql'
            sql.write_text("/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;\n"
                           "CREATE TABLE users(id INT NOT NULL);\nINSERT INTO users VALUES (1);", 'utf-8')
            self.assertIn(b'CREATE TABLE', _check_sql(sql))

    def test_mysql_sql_guard_blocks_escaping_commands(self):
        for statement in ('CREATE USER attacker IDENTIFIED BY \'x\';',
                          'SELECT * INTO OUTFILE \'/tmp/pwn\' FROM users;',
                          'SYSTEM curl http://example.com;',
                          '\\! sh -c whoami', 'USE mysql;',
                          'DROP DATABASE mysql;'):
            with self.subTest(statement=statement), tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / 'dump.sql'
                f.write_text(statement, 'utf-8')
                with self.assertRaises(GuestFailure):
                    _check_sql(f)

    def test_generic_php_pdo_rewrite_only_guest_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'db' / 'db.php'
            file.parent.mkdir()
            file.write_text('<?php\n$host = "localhost";\n$dbname="studentdb";\n'
                            '$user="root";\n$pass="example-password";\n'
                            '$pdo = new PDO("mysql:host=$host;dbname=$dbname",$user,$pass);\n', 'utf-8')
            _php_pdo_rebind(Path(tmp))
            text = file.read_text('utf-8')
            self.assertIn("getenv('PROJECTIQ_DB_PASSWORD')", text)
            self.assertNotIn('example-password', text)
            self.assertNotIn('"root"', text)

    def test_php_rewrite_reject_unknown_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'db' / 'db.php'
            file.parent.mkdir()
            file.write_text('<?php $pdo = new PDO("mysql:dbname=x", "root", "x");', 'utf-8')
            with self.assertRaises(GuestFailure):
                _php_pdo_rebind(Path(tmp))


if __name__ == '__main__':
    unittest.main()
