"""VM pilot contract + offline trusted-fixture tests (NO VM, no Docker required)."""
from __future__ import annotations

import copy
import json
import os
import shlex
import socket
import stat
import tempfile
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import unittest
import zipfile
from pathlib import Path

from projectiq_executioner.phase2.planner import build_execution_plan
from projectiq_executioner.phase2.preflight import create_preflight
from projectiq_executioner.phase2.vm_worker.contracts import VMPlanError, build_vm_plan
from projectiq_executioner.phase2.vm_worker.guest_agent import (
    GuestFailure, _check_sql, _php_pdo_rebind, _wait_mysql_runtime_ready,
    _provision_mysql, run, safe_extract,
)
from projectiq_executioner.phase2.vm_worker.orchestrator import execute_vm_candidate
from projectiq_executioner.phase2.vm_worker.qemu import (
    QemuWorker, SSHControl, WorkerFailure, effective_vm_vcpus, make_nocloud_iso, sha256_file,
)
from projectiq_executioner.phase2.vm_worker.template_bake import (
    SEAL_SCRIPT, _check_seal_serial, _launch_seal, _wait_qemu_shutdown, _write_seal_script,
    _wait_mysql_ready, bake_template,
)

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


class SSHCommandContractTest(unittest.TestCase):
    def test_remote_mysql_query_remains_one_argument(self):
        # OpenSSH combines all remote argv elements into a shell command string.
        # The SQL expression must survive that remote shell as one -e argument.
        with tempfile.TemporaryDirectory() as tmp:
            ssh = SSHControl({}, Path(tmp), 2222, Path(tmp) / 'ephemeral_key')
            with patch('projectiq_executioner.phase2.vm_worker.qemu._tool', return_value='ssh'), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as execute:
                execute.return_value.returncode = 0
                ssh.ssh('sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;')
                command = execute.call_args.args[0]
                self.assertEqual(command[-2], 'projectiq@127.0.0.1')
                self.assertEqual(shlex.split(command[-1]),
                                 ['sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;'])
                self.assertEqual(command[-1], "sudo -n /usr/bin/mysql -e 'SELECT 1;'")

    def test_remote_shell_metacharacters_are_quoted_as_data(self):
        # Regression: shell metacharacters cannot escape the argument boundary.
        with tempfile.TemporaryDirectory() as tmp:
            ssh = SSHControl({}, Path(tmp), 2222, Path(tmp) / 'ephemeral_key')
            payload = "SELECT 'a'; echo should_not_execute"
            with patch('projectiq_executioner.phase2.vm_worker.qemu._tool', return_value='ssh'), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as execute:
                execute.return_value.returncode = 0
                ssh.ssh('/usr/bin/mysql', '-e', payload)
                command = execute.call_args.args[0]
                self.assertEqual(shlex.split(command[-1]), ['/usr/bin/mysql', '-e', payload])
                self.assertEqual(command[-2], 'projectiq@127.0.0.1')


class MySQLReadinessTest(unittest.TestCase):
    def test_waits_for_real_mysql_query_not_just_systemctl_start(self):
        ssh = MagicMock()
        ssh.ssh.side_effect = [
            SimpleNamespace(returncode=1, stdout=b'', stderr=b'missing socket'),
            SimpleNamespace(returncode=0, stdout=b'1\n1\n', stderr=b''),
        ]
        with patch('projectiq_executioner.phase2.vm_worker.template_bake.time.sleep'):
            _wait_mysql_ready(ssh, seconds=20)
        self.assertEqual(ssh.ssh.call_count, 2)
        self.assertEqual(ssh.ssh.call_args.args,
                         ('sudo', '-n', '/usr/bin/mysql', '-e', 'SELECT 1;'))

    def test_failure_is_diagnostic_and_cannot_be_treated_as_ready(self):
        ssh = MagicMock()
        def mock_ssh(*args, **kwargs):
            if '/usr/bin/mysql' in args:
                return SimpleNamespace(returncode=1, stdout=b'',
                                       stderr=b"Can't connect to local MySQL server through socket")
            if args[0] == 'systemctl':
                return SimpleNamespace(returncode=0, stdout=b'ActiveState=failed\n', stderr=b'')
            if 'journalctl' in args:
                return SimpleNamespace(returncode=0, stdout=b'mysqld exited\n', stderr=b'')
            return SimpleNamespace(returncode=2, stdout=b'', stderr=b'not present')
        ssh.ssh.side_effect = mock_ssh
        with patch('projectiq_executioner.phase2.vm_worker.template_bake.time.monotonic', side_effect=[100.0, 100.1, 101.0]):
            with self.assertRaisesRegex(WorkerFailure, 'ActiveState=failed') as failure:
                _wait_mysql_ready(ssh, seconds=0.1)
        self.assertIn('mysqld exited', str(failure.exception))
        self.assertIn('never became ready', str(failure.exception))

    def test_mysql_readiness_window_must_be_positive(self):
        with self.assertRaisesRegex(WorkerFailure, 'readiness window'):
            _wait_mysql_ready(MagicMock(), seconds=0)


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
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.ensure_controller_job'), \
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.verify_child_in_job', return_value=True), \
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

    def test_whpx_smp_boot_compatibility_cap_only_reduces_cpu(self):
        # A 2-vCPU Ubuntu WHPX guest stalled during SMP boot on the pilot host.
        self.assertEqual(effective_vm_vcpus({'accelerator': 'whpx'}, 2), 1)
        self.assertEqual(effective_vm_vcpus({'accelerator': 'whpx'}, 1), 1)
        self.assertEqual(effective_vm_vcpus({'accelerator': 'kvm'}, 2), 2)
        with self.assertRaises(WorkerFailure):
            effective_vm_vcpus({'accelerator': 'whpx'}, 0)

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
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.ensure_controller_job'), \
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.verify_child_in_job', return_value=True), \
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
            self.assertNotIn(b'NOPASSWD: /usr/bin/mysql', iso.data['user-data'])
            self.assertIn(b'ALL=(projectiq_run) NOPASSWD: ALL', iso.data['user-data'])
            self.assertIn(b'name: projectiq_run', iso.data['user-data'])
            self.assertIn(b'sudo: false', iso.data['user-data'])
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
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.ensure_controller_job'), \
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.verify_child_in_job', return_value=True), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu.subprocess.Popen', return_value=p) as launch:
                cmd.return_value.returncode = 0
                worker = QemuWorker(config, root, 50000, trusted_template_setup=True)
                worker.start(seed)
                self.assertIn('user,id=net0,hostfwd=tcp:127.0.0.1:50000-:22', launch.call_args.args[0])
                self.assertNotIn('user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:50000-:22', launch.call_args.args[0])
                worker.stop()


class TemplateSealContractTest(unittest.TestCase):
    def test_seal_script_written_as_lf_bytes_for_linux_systemd(self):
        # Regression: write_text() translates LF to CRLF on Windows, making
        # the shebang /bin/sh\r and causing systemd status=203/EXEC.
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'template_seal.sh'
            digest = _write_seal_script(script)
            content = script.read_bytes()
            self.assertTrue(content.startswith(b'#!/bin/sh\n'))
            self.assertNotIn(b'\r', content)
            self.assertEqual(content, SEAL_SCRIPT.encode('utf-8'))
            self.assertEqual(digest, sha256_file(script))

    def test_seal_writer_refuses_non_posix_shebang(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('projectiq_executioner.phase2.vm_worker.template_bake.SEAL_SCRIPT', '#!/bin/sh\r\necho bad\r\n'):
            script = Path(tmp) / 'template_seal.sh'
            with self.assertRaisesRegex(WorkerFailure, 'POSIX LF shebang'):
                _write_seal_script(script)
            self.assertFalse(script.exists())

    def test_seal_runs_as_independent_service_not_delayed_timer(self):
        with patch('projectiq_executioner.phase2.vm_worker.template_bake._ok') as run:
            _launch_seal(MagicMock())
        args = run.call_args.args[1:]
        self.assertEqual(args[:3], ('sudo', '-n', 'systemd-run'))
        self.assertIn('--no-block', args)
        self.assertIn('--service-type=exec', args)
        self.assertNotIn('--on-active=4s', args)
        self.assertEqual(args[-1], '/usr/local/sbin/projectiq-template-seal')
        self.assertLess(SEAL_SCRIPT.index('sleep 4'), SEAL_SCRIPT.index('rm -f /etc/sudoers.d/90-cloud-init-users'))

    def test_preseal_serial_probe_fails_closed_when_unobservable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vm = QemuWorker({'base_image': 'base'}, root, 5555, trusted_template_setup=True)
            with patch('projectiq_executioner.phase2.vm_worker.template_bake._ok'), \
                 patch('projectiq_executioner.phase2.vm_worker.template_bake.time.monotonic', side_effect=[0, -1, 9]):
                with self.assertRaisesRegex(WorkerFailure, 'serial seal-stage channel unavailable'):
                    _check_seal_serial(MagicMock(), vm)
            (root / 'guest-serial.log').write_text('PROJECTIQ_SEAL_DIAG:serial_ready\n')
            with patch('projectiq_executioner.phase2.vm_worker.template_bake._ok'):
                _check_seal_serial(MagicMock(), vm)

    def test_shutdown_timeout_includes_guest_unit_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = QemuWorker({'base_image': 'base'}, Path(tmp), 5555)
            vm.proc = MagicMock()
            vm.proc.poll.return_value = None
            fake_ssh = MagicMock()
            fake_ssh.ssh.return_value.returncode = 0
            fake_ssh.ssh.return_value.stdout = b'ActiveState=failed\nResult=exit-code\n'
            with self.assertRaisesRegex(WorkerFailure, 'ActiveState=failed'):
                _wait_qemu_shutdown(vm, timeout=0, ssh=fake_ssh)

    def test_seal_clears_privileges_before_poweroff_and_reports_stages(self):
        self.assertLess(SEAL_SCRIPT.index('rm -f /etc/sudoers.d/90-cloud-init-users'),
                        SEAL_SCRIPT.index('seal_stage poweroff_requested'))
        self.assertLess(SEAL_SCRIPT.index('cloud-init clean --logs --seed'),
                        SEAL_SCRIPT.index('seal_stage poweroff_requested'))

    def test_shutdown_requires_seal_progress_even_when_qemu_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vm = QemuWorker({'base_image': str(root / 'base.qcow2')}, root, 5555)
            vm.proc = MagicMock()
            vm.proc.poll.return_value = 0
            vm.proc.returncode = 0
            (root / 'guest-serial.log').write_text('PROJECTIQ_SEAL_STAGE:started\n')
            with self.assertRaisesRegex(WorkerFailure, 'without confirmed seal completion'):
                _wait_qemu_shutdown(vm, timeout=1)
            (root / 'guest-serial.log').write_text('PROJECTIQ_SEAL_STAGE:poweroff_requested\n')
            _wait_qemu_shutdown(vm, timeout=1)

    def test_trusted_template_captures_serial_but_regular_vm_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / 'seed.iso'
            seed.write_bytes(b'fake')
            base = root / 'base.qcow2'
            base.write_bytes(b'fake')
            cfg = {'base_image': str(base), 'accelerator': 'kvm'}
            fake_proc = MagicMock()
            fake_proc.poll.return_value = 0
            with patch('projectiq_executioner.phase2.vm_worker.qemu.sys.platform', 'linux'), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._tool', side_effect=lambda n, p: p or n), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu._bounded') as cmd, \
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.ensure_controller_job'), \
                 patch('projectiq_executioner.phase2.vm_worker.d5c_windows_job.verify_child_in_job', return_value=True), \
                 patch('projectiq_executioner.phase2.vm_worker.qemu.subprocess.Popen', return_value=fake_proc) as popen:
                cmd.return_value.returncode = 0
                vm = QemuWorker(cfg, root, 50505, trusted_template_setup=True)
                vm.start(seed)
                args = popen.call_args.args[0]
                self.assertTrue(args[args.index('-serial') + 1].startswith('file:'))
                vm.stop()
                vm = QemuWorker(cfg, root, 50505)
                vm.start(seed)
                args = popen.call_args.args[0]
                self.assertEqual(args[args.index('-serial') + 1], 'none')
                vm.stop()


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
            # Local unit smoke deliberately avoids sudo; real VM launches use
            # the separate projectiq_run identity and are tested independently.
            import subprocess
            def local_only(program, *, cwd, env, log):
                return subprocess.Popen(program, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._project_popen',
                       side_effect=local_only):
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

    def test_mysql_sql_guard_accepts_bom_utf16_dump_and_normalizes_to_utf8(self):
        sample = "-- MySQL dump\nCREATE TABLE users(id INT NOT NULL);\nINSERT INTO users VALUES (1);\n"
        with tempfile.TemporaryDirectory() as tmp:
            for encoding in ('utf-16', 'utf-16-be'):
                with self.subTest(encoding=encoding):
                    sql = Path(tmp) / 'schema.sql'
                    if encoding == 'utf-16-be':
                        data = b'\xfe\xff' + sample.encode('utf-16-be')
                    else:
                        data = sample.encode('utf-16')
                    sql.write_bytes(data)
                    self.assertEqual(_check_sql(sql), sample.encode('utf-8'))

    def test_mysql_sql_guard_blocks_unsafe_utf16_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            sql = Path(tmp) / 'schema.sql'
            sql.write_bytes('CREATE USER attacker IDENTIFIED BY \'bad\';'.encode('utf-16'))
            with self.assertRaisesRegex(GuestFailure, 'outside the pilot allowlist'):
                _check_sql(sql)

    def test_mysql_sql_guard_rejects_binary_and_malformed_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            sql = Path(tmp) / 'schema.sql'
            for data in (b'CREATE TABLE users(id INT);\x00',
                         'CREATE TABLE users(id INT);'.encode('utf-16-le'),
                         b'\xff\xfe\x41',
                         b'\xff\xffCREATE TABLE users(id INT);',
                         b'\xff\xfe\x00\x00'):
                with self.subTest(data=data[:16]):
                    sql.write_bytes(data)
                    with self.assertRaises(GuestFailure):
                        _check_sql(sql)

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

    def test_guest_mysql_waits_for_cloud_init_scoped_credentials(self):
        # Simulate Linux UID/mode metadata: Windows has no os.geteuid(), and
        # its chmod/stat do not represent full POSIX permission bits.
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'db.json'
            file.write_text(json.dumps({'host': '127.0.0.1',
                'database': 'projectiq_run', 'user': 'projectiq_app',
                'password': 'a' * 48}), 'utf-8')
            file.chmod(0o600)
            simulated_stat = SimpleNamespace(st_uid=1001, st_mode=0o100600,
                                             st_size=file.stat().st_size)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.os.geteuid',
                       return_value=1001, create=True), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.Path.stat',
                       return_value=simulated_stat):
                result = _wait_mysql_runtime_ready(seconds=1, credential_path=file)
            self.assertEqual(result['database'], 'projectiq_run')

    def test_mysql_credential_permissions_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'db.json'
            file.write_text(json.dumps({'host': '127.0.0.1',
                'database': 'projectiq_run', 'user': 'projectiq_app',
                'password': 'a' * 48}), 'utf-8')
            file.chmod(0o644)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent.time.sleep'):
                with patch('projectiq_executioner.phase2.vm_worker.guest_agent.time.monotonic',
                           side_effect=[100.0, 200.0]):
                    with self.assertRaisesRegex(GuestFailure, 'cloud-init bootstrap'):
                        _wait_mysql_runtime_ready(seconds=5, credential_path=file)

    def test_php_import_never_uses_sudo_or_mysql_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'schema.sql').write_text('CREATE TABLE users(id INT);', 'utf-8')
            (base / 'db').mkdir()
            (base / 'db/db.php').write_text(
                '<?php\n$host="h";\n$dbname="d";\n$user="u";\n$pass="p";\n'
                '$pdo = new PDO("mysql:host=$host;dbname=$dbname",$user,$pass);', 'utf-8')
            commands = []
            def fake_proc(args, **kwargs):
                commands.append((args, kwargs))
                return SimpleNamespace(returncode=0, stdout=b'users\n', stderr=b'')
            metadata = {'host': '127.0.0.1', 'database': 'projectiq_run',
                        'user': 'projectiq_app', 'password': 'a'*48}
            # Redirect only the Linux /tmp fixture credential file to the
            # test's temporary directory; production remains Linux-only.
            original_named_tempfile = tempfile.NamedTemporaryFile
            def temporary_mysql_config(*args, **kwargs):
                self.assertEqual(kwargs.get('dir'), '/tmp')
                kwargs['dir'] = tmp
                return original_named_tempfile(*args, **kwargs)
            with patch('projectiq_executioner.phase2.vm_worker.guest_agent._wait_mysql_runtime_ready',
                       return_value=metadata), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent._trusted_process',
                       side_effect=fake_proc), \
                 patch('projectiq_executioner.phase2.vm_worker.guest_agent.tempfile.NamedTemporaryFile',
                       side_effect=temporary_mysql_config):
                _provision_mysql(base, 'schema.sql')
            self.assertFalse(list(base.glob('projectiq_mysql_*')))
            self.assertTrue(commands)
            self.assertTrue(all('sudo' not in str(cmd[0]) for cmd in commands))
            self.assertTrue(commands[-1][1].get('project_code'))

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
