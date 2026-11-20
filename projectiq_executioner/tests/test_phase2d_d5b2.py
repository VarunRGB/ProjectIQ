"""D5b.2 tests: bounded storage enforcement design and failure injection.

No host filesystem mounts or real QEMU runs. A clean Ubuntu VM probe is the
separate acceptance gate, not a claim of independent sandbox attestation.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import types
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_disk_acceptance as accept
from projectiq_executioner.phase2.vm_worker import d5_disk_guest_probe as guest_probe
from projectiq_executioner.phase2.vm_worker import guest_agent as guest
from projectiq_executioner.phase2.vm_worker import qemu


class FakeISO:
    def __init__(self):
        self.parts = {}
    def new(self, **kwargs):
        pass
    def add_fp(self, fp, length, **kwargs):
        self.parts[kwargs['rr_name']] = fp.read()
    def write(self, path):
        Path(path).write_bytes(b'fake-seed')
    def close(self):
        pass


def stub_tools(valid=True):
    return {'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' if valid else 'BLOCKED'}


class FakeProc:
    def __init__(self):
        self.closed = False
    def poll(self):
        return 0 if self.closed else None
    def kill(self):
        self.closed = True


class FakeQemu:
    last = None
    should_fail_start = False
    should_fail_stop = False
    def __init__(self, config, workdir, port, *, ram_mib, cpus, diagnostic_serial=False):
        assert diagnostic_serial is True
        self.config = config
        self.workdir = Path(workdir)
        self.port = port
        self.proc = FakeProc()
        FakeQemu.last = self
    def network_spec(self):
        return f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22'
    def start(self, seed):
        assert self.config['workspace_disk_mib'] == accept.PROBE_MIB
        assert Path(seed).is_file()
        if self.should_fail_start:
            raise OSError('simulated QEMU launch failure')
    def stop(self):
        if self.should_fail_stop:
            raise OSError('simulated QEMU shutdown failure')
        self.proc.closed = True


class FakeSSH:
    checks = {k: 'PASS' for k in accept.CHECKS}
    status = 0
    def __init__(self, config, workdir, port, key):
        pass
    def wait_ssh(self, worker, seconds):
        pass
    def ssh(self, *args, input, timeout):
        assert args == ('python3', '-', str(accept.PROBE_MIB))
        assert b'posix_fallocate' in input
        assert b'projectiq_run' in input
        return SimpleNamespace(returncode=self.status, stdout=json.dumps({
            'schema_version': '0.1', 'workspace_mib': accept.PROBE_MIB,
            'checks': self.checks}).encode())


class Timer:
    def __init__(self, *args):
        self.daemon = None
    def start(self):
        pass
    def cancel(self):
        pass


class D5B2Tests(unittest.TestCase):
    def setUp(self):
        FakeQemu.should_fail_start = False
        FakeQemu.should_fail_stop = False
        FakeSSH.checks = {k: 'PASS' for k in accept.CHECKS}
        FakeSSH.status = 0

    def _seed(self, *, workspace=96, mysql=False, template=False):
        iso = FakeISO()
        module = types.SimpleNamespace(PyCdlib=lambda: iso)
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {'pycdlib': module}):
            qemu.make_nocloud_iso(Path(temp), 'ssh-ed25519 AAAATEST TEST', 'projectiq-d5b2',
                                  workspace_disk_mib=workspace, mysql_bootstrap=mysql,
                                  template_setup=template)
        return iso.parts['user-data'].decode('utf-8')

    def test_seed_has_fixed_root_owned_workspace_bootstrap(self):
        body = self._seed()
        self.assertIn('/usr/local/sbin/projectiq-workspace-bootstrap', body)
        self.assertIn("permissions: '0700'", body)
        self.assertIn('name: projectiq_run', body)
        self.assertNotIn('NOPASSWD: /usr/bin/mysql', body)
        script = qemu.WORKSPACE_BOOTSTRAP_SCRIPT
        for expected in ('/dev/disk/by-id/virtio-PIQWORK0001', 'mkfs.ext4',
                         'mount -t ext4', 'nosuid,nodev', 'chmod 2770'):
            self.assertIn(expected, script)
        self.assertNotIn('snapshot.zip', script)
        self.assertNotIn('curl', script)

    def test_workspace_mount_is_shared_without_exposing_controller_home(self):
        # The clean VM does not run application staging, so it cannot rely on
        # that stage's chmod of the controller home for app UID traversal.
        mount = '/srv/projectiq-workspace'
        self.assertEqual(qemu.WORKSPACE_MOUNT, mount)
        self.assertEqual(guest.WORKSPACE_PATH.as_posix(), mount)
        self.assertEqual(guest_probe.WORK.as_posix(), mount)
        self.assertNotIn('/home/projectiq/workspace', qemu.WORKSPACE_BOOTSTRAP_SCRIPT)
        self.assertIn(f'/bin/mkdir -p {mount}', qemu.WORKSPACE_BOOTSTRAP_SCRIPT)
        self.assertIn(f'/bin/mount -t ext4 -o rw,nosuid,nodev,noatime "$device" {mount}',
                      qemu.WORKSPACE_BOOTSTRAP_SCRIPT)

    def test_workspace_mount_grants_shared_group_only_not_world_write(self):
        # Root-owned provisioning grants access to the two known guest users
        # using their shared group, without sudo or chmod 777.
        script = qemu.WORKSPACE_BOOTSTRAP_SCRIPT
        self.assertIn('/usr/bin/chown projectiq:projectiq_exec /srv/projectiq-workspace', script)
        self.assertIn('/bin/chmod 2770 /srv/projectiq-workspace', script)
        self.assertNotIn('chmod 777', script)
        seed = self._seed()
        self.assertIn('groups: [projectiq_exec]', seed)
        self.assertIn('sudo: false', seed)

    def test_guest_and_quota_probe_share_single_workspace_contract(self):
        self.assertEqual(str(guest.WORKSPACE_PATH), str(guest_probe.WORK))
        self.assertIn(guest.WORKSPACE_PATH.as_posix() + '/d5b2-quota-proof',
              guest_probe.APP_PROOF)
        self.assertNotIn('/home/projectiq/workspace', guest_probe.APP_PROOF)

    def test_mysql_bootstraps_after_mount_setup(self):
        body = self._seed(mysql=True)
        self.assertLess(body.index('runcmd:'), body.index('projectiq-workspace-bootstrap ]'))
        self.assertLess(body.index('projectiq-workspace-bootstrap ]'),
                        body.index('projectiq-mysql-bootstrap ]'))

    def test_no_workspace_on_trusted_template_bake(self):
        with self.assertRaises(qemu.WorkerFailure):
            self._seed(template=True)

    def test_all_invalid_budget_types_fail(self):
        for invalid in (None, True, False, 0, 63, 2049, 4.5, '96', -1):
            with self.subTest(value=invalid), self.assertRaises(qemu.WorkerFailure):
                qemu._validate_workspace_mib(invalid)
        for allowed in (64, 96, 1536, 2048):
            self.assertEqual(qemu._validate_workspace_mib(allowed), allowed)

    def test_qemu_runtime_rejects_invalid_budget(self):
        for val in (True, '1536', 9999):
            with self.assertRaises(qemu.WorkerFailure):
                qemu.QemuWorker({'workspace_disk_mib': val}, Path('.'), 48001)
        with self.assertRaises(qemu.WorkerFailure):
            qemu.QemuWorker({'workspace_disk_mib': 512}, Path('.'), 48001,
                            trusted_template_setup=True)

    def test_worker_creates_bounded_raw_disk_and_attaches_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)/'template.qcow2'
            base.write_bytes(b'fixture')
            seed = Path(td)/'seed.iso'
            seed.write_bytes(b'seed')
            logs = []
            class FakePopen:
                def __init__(self, argv, **kwargs):
                    logs.append(argv)
                def poll(self): return 0
            def cmd(argv, **kwargs):
                logs.append(argv)
                return SimpleNamespace(returncode=0)
            with patch.object(qemu, '_bounded', side_effect=cmd), \
                 patch.object(qemu, '_tool', side_effect=lambda t, p: t), \
                 patch.object(qemu.sys, 'platform', 'linux'), \
                 patch.object(qemu.subprocess, 'Popen', FakePopen):
                worker = qemu.QemuWorker({'base_image': str(base), 'accelerator': 'kvm',
                                          'workspace_disk_mib': 96}, Path(td), 41013)
                worker.start(seed)
                worker.stop()
            raw = next(x for x in logs if len(x)>2 and x[1:4]==['create','-f','raw'])
            self.assertEqual(raw[-1], '96M')
            args = next(x for x in logs if x[0]=='qemu-system-x86_64')
            self.assertIn('virtio-blk-pci,drive=bootdisk,bootindex=1', args)
            self.assertIn('virtio-blk-pci,drive=workdisk,serial=PIQWORK0001,bootindex=2', args)
            self.assertLess(args.index('virtio-blk-pci,drive=bootdisk,bootindex=1'),
                            args.index('virtio-blk-pci,drive=workdisk,serial=PIQWORK0001,bootindex=2'))
            self.assertIn('restrict=on', ' '.join(args))
            self.assertNotIn('virtiofs', ' '.join(args))

    def test_single_disk_launch_preserves_existing_root_drive_layout(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / 'template.qcow2'
            base.write_bytes(b'fixture')
            seed = Path(td) / 'seed.iso'
            seed.write_bytes(b'seed')
            logs = []
            class FakePopen:
                def __init__(self, argv, **kwargs):
                    logs.append(argv)
                def poll(self): return 0
            def cmd(argv, **kwargs):
                return SimpleNamespace(returncode=0)
            with patch.object(qemu, '_bounded', side_effect=cmd), \
                 patch.object(qemu, '_tool', side_effect=lambda t, p: t), \
                 patch.object(qemu.sys, 'platform', 'linux'), \
                 patch.object(qemu.subprocess, 'Popen', FakePopen):
                worker = qemu.QemuWorker({'base_image': str(base), 'accelerator': 'kvm'},
                                          Path(td), 41014)
                worker.start(seed)
                worker.stop()
            args = logs[0]
            self.assertTrue(any(',if=virtio,format=qcow2' in a for a in args))
            self.assertNotIn('virtio-blk-pci,drive=bootdisk,bootindex=1', args)
            self.assertFalse(any('workdisk' in a for a in args))

    def test_guest_staging_without_mount_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(guest.Path, 'is_file', return_value=False), \
             patch.object(guest.os.path, 'ismount', return_value=False), \
             patch.object(guest.time, 'monotonic', side_effect=[0, 86]):
            with self.assertRaisesRegex(guest.GuestFailure, 'WORKSPACE_BOOTSTRAP_NOT_READY'):
                guest._stage_bounded_workspace({'workspace_disk_mib': 96}, base=Path(tmp))

    def test_root_bootstrap_marker_after_chown_and_chmod(self):
        script = qemu.WORKSPACE_BOOTSTRAP_SCRIPT
        self.assertEqual(guest.WORKSPACE_READY_MARKER.as_posix(),
                         '/run/projectiq-workspace-bootstrap.ready')
        self.assertLess(script.index('/usr/bin/chown projectiq:projectiq_exec'),
                        script.index('/bin/chmod 2770'))
        self.assertLess(script.index('/bin/chmod 2770'),
                        script.index('/usr/bin/touch /run/projectiq-workspace-bootstrap.ready'))
        self.assertIn('set -eu', script)

    def test_workspace_marker_must_be_root_owned_and_not_writable(self):
        from types import SimpleNamespace
        import stat
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guest.Path, 'is_file', return_value=True), \
             patch.object(guest.Path, 'lstat', return_value=SimpleNamespace(
                 st_uid=1000, st_mode=stat.S_IFREG | 0o666)), \
             patch.object(guest.Path, 'is_symlink', return_value=False):
            with self.assertRaisesRegex(guest.GuestFailure, 'WORKSPACE_BOOTSTRAP_NOT_READY'):
                guest._stage_bounded_workspace({'workspace_disk_mib': 96}, base=Path(td))

    def test_verified_marker_does_not_replace_mount_check(self):
        from types import SimpleNamespace
        import stat
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guest.Path, 'is_file', return_value=True), \
             patch.object(guest.Path, 'lstat', return_value=SimpleNamespace(
                 st_uid=0, st_mode=stat.S_IFREG | 0o600)), \
             patch.object(guest.Path, 'is_symlink', return_value=False), \
             patch.object(guest.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(guest.GuestFailure, 'WORKSPACE_DISK_QUOTA_UNAVAILABLE'):
                guest._stage_bounded_workspace({'workspace_disk_mib': 96}, base=Path(td))

    def test_guest_staging_malformed_quota_fails_before_mount(self):
        for val in (False, '1536', 0, 2049, None):
            with self.subTest(val=val), self.assertRaisesRegex(guest.GuestFailure,
                                                               'WORKSPACE_DISK_QUOTA_UNAVAILABLE'):
                guest._stage_bounded_workspace({'workspace_disk_mib': val}, base=Path('.'))

    def test_app_launch_uses_quota_home_and_temp_directory(self):
        with patch.dict(os.environ, {'PROJECTIQ_QUOTA_HOME': '/srv/projectiq-workspace/app_home',
                                     'PROJECTIQ_QUOTA_TMP': '/srv/projectiq-workspace/tmp'}):
            argv = guest._project_command(['/bin/true'], {'PORT': '8765'})
        self.assertIn('HOME=/srv/projectiq-workspace/app_home', argv)
        self.assertIn('TMPDIR=/srv/projectiq-workspace/tmp', argv)
        self.assertIn('TMP=/srv/projectiq-workspace/tmp', argv)
        self.assertNotIn('HOME=/home/projectiq_run', argv)
        self.assertIn('PORT=8765', argv)

    def test_guest_write_probe_uses_actual_allocation_not_sparse_truncate(self):
        self.assertIn('os.posix_fallocate', guest_probe.APP_PROOF)
        self.assertIn('errno.ENOSPC', guest_probe.APP_PROOF)
        self.assertNotIn('truncate(', guest_probe.APP_PROOF)
        self.assertNotIn('ftruncate(', guest_probe.APP_PROOF)
        self.assertIn('projectiq_run', __import__('inspect').getsource(guest_probe.main))

    def test_clean_vm_allocation_targets_existing_mount_root_not_app_tmp(self):
        # The clean-VM probe never runs application workspace staging; only
        # the ext4 mount point exists. A proof in /tmp would be skipped or
        # return NOT_VERIFIED without ever allocating a byte.
        self.assertIn("path = Path('/srv/projectiq-workspace/d5b2-quota-proof')",
                      guest_probe.APP_PROOF)
        self.assertNotIn("workspace/tmp/d5b2-quota-proof", guest_probe.APP_PROOF)
        self.assertIn('os.posix_fallocate', guest_probe.APP_PROOF)

    def test_guest_probe_retains_bounded_failure_diagnostics_without_passing(self):
        import contextlib
        import io
        output = io.StringIO()
        fake = SimpleNamespace(returncode=1, stdout=b'',
                               stderr=b'PermissionError: write denied')
        with patch.object(guest_probe.sys, 'argv', ['probe', '96']), \
             patch.object(guest_probe.os.path, 'ismount', return_value=True), \
             patch.object(guest_probe, '_mount_observation', return_value='PASS'), \
             patch.object(guest_probe.subprocess, 'run', return_value=fake), \
             contextlib.redirect_stdout(output):
            self.assertEqual(guest_probe.main(), 0)
        doc = json.loads(output.getvalue())
        self.assertEqual(doc['checks']['app_write_over_quota_denied'], 'NOT_VERIFIED')
        self.assertEqual(doc['checks']['app_write_after_denial'], 'NOT_VERIFIED')
        self.assertEqual(doc['probe_diagnostics']['app_probe_state'], 'PROCESS_EXITED_NONZERO')
        self.assertEqual(doc['probe_diagnostics']['app_probe_exit_code'], 1)
        self.assertIn('PermissionError', doc['probe_diagnostics']['app_probe_stderr_tail'])

    def test_d5b2_probe_diagnostics_do_not_bypass_checks(self):
        fake = SimpleNamespace(returncode=0, stdout=json.dumps({
            'schema_version': '0.1', 'workspace_mib': accept.PROBE_MIB,
            'checks': {k: 'NOT_VERIFIED' for k in accept.CHECKS},
            'probe_diagnostics': {'app_probe_exit_code': 1,
                                  'app_probe_stderr_tail': 'PermissionError'}
        }).encode())
        self.assertEqual(accept._parse(fake.stdout)['workspace_mount_bounded'], 'NOT_VERIFIED')

    def test_report_never_claims_guest_wide_disk_quota_or_attestation(self):
        report = accept._base_report()
        self.assertIn('ROOT_QCOW2_OVERLAY_GROWTH_NOT_QUOTA_ENFORCED', report['remaining_security_gates'])
        self.assertIn('NON_WORKSPACE_GUEST_WRITABLE_PATHS_NOT_CONFINED', report['remaining_security_gates'])
        self.assertEqual(report['security_attestation'], 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertFalse(report['student_code_executed'])

    def test_disk_report_schema_rejects_invented_passes(self):
        good = {'schema_version': '0.1', 'workspace_mib': accept.PROBE_MIB,
                'checks': {k:'PASS' for k in accept.CHECKS}}
        self.assertEqual(accept._parse(json.dumps(good).encode()), good['checks'])
        for invalid in ({**good, 'workspace_mib':1536},
                        {**good, 'checks': {**good['checks'], 'other':'PASS'}},
                        {**good, 'checks': {'workspace_mount_bounded':'PASS'}},
                        {**good, 'checks': {k:'TRUE' for k in accept.CHECKS}}):
            self.assertIsNone(accept._parse(json.dumps(invalid).encode()))
        self.assertIsNone(accept._parse(b'x'*3000))

    def _run(self, ssh_class=FakeSSH):
        def key(_config, work):
            p = work/'key'; p.write_bytes(b'key'); return p, 'ssh-ed25519 mocked'
        def seed(work, pub, ident, **kwargs):
            assert kwargs['workspace_disk_mib'] == accept.PROBE_MIB
            p = work/'seed.iso';p.write_bytes(b'seed');return p
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(accept, 'verify_host_toolchain', return_value=stub_tools()), \
             patch.object(accept, 'make_ssh_key', side_effect=key), \
             patch.object(accept, 'make_nocloud_iso', side_effect=seed), \
             patch.object(accept, '_free_loopback_port', return_value=41015), \
             patch.object(accept, 'QemuWorker', FakeQemu), \
             patch.object(accept, 'SSHControl', ssh_class), \
             patch.object(accept.threading, 'Timer', Timer):
            report = accept.run({}, run_clean_vm=True, work_root=Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])
        return report

    def test_clean_vm_success_is_observed_not_attested(self):
        r = self._run()
        self.assertEqual(r['status'], 'WORKSPACE_QUOTA_OBSERVED_NOT_ATTESTED')
        self.assertEqual(r['execution'], 'TRUSTED_DISK_PROBE_EXECUTED_IN_CLEAN_VM')
        self.assertEqual(r['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
        self.assertFalse(r['student_code_executed'])

    def test_fallocate_success_fails_acceptance(self):
        FakeSSH.checks = {**FakeSSH.checks, 'app_write_over_quota_denied': 'FAIL'}
        self.assertEqual(self._run()['status'], 'WORKSPACE_DISK_QUOTA_FAILED')

    def test_nonroot_quota_unverified_is_blocked(self):
        FakeSSH.checks = {**FakeSSH.checks, 'workspace_mount_bounded': 'NOT_VERIFIED'}
        self.assertEqual(self._run()['status'], 'WORKSPACE_DISK_QUOTA_NOT_VERIFIED')

    def test_incomplete_guest_schema_fails_closed(self):
        FakeSSH.checks = {'workspace_mount_bounded': 'PASS'}
        r = self._run()
        self.assertEqual(r['status'], 'DISK_QUOTA_PROBE_INCONCLUSIVE_OR_FAILED')
        self.assertEqual(r['error_code'], 'INVALID_OR_FAILED_GUEST_DISK_PROBE')

    def test_launch_failure_cannot_pass(self):
        FakeQemu.should_fail_start = True
        r = self._run()
        self.assertEqual(r['status'], 'DISK_QUOTA_PROBE_INCONCLUSIVE_OR_FAILED')

    def test_failed_boot_records_limited_diagnostics_and_cleans_temp_storage(self):
        class TimeoutSSH(FakeSSH):
            def wait_ssh(self, worker, seconds):
                self.workdir.joinpath('qemu.log').write_text('qemu startup error\n')
                self.workdir.joinpath('guest-serial.log').write_text('guest boot trace\n')
                raise qemu.WorkerFailure('Worker VM SSH readiness timed out')
            def __init__(self, config, workdir, port, key):
                self.workdir = Path(workdir)
            def ssh(self, *args, timeout):
                return SimpleNamespace(returncode=255, stderr=b'Connection refused')
        r = self._run(TimeoutSSH)
        self.assertEqual(r['status'], 'DISK_QUOTA_PROBE_INCONCLUSIVE_OR_FAILED')
        self.assertEqual(r['error_detail'], 'Worker VM SSH readiness timed out')
        self.assertTrue(r['vm_alive_at_error'])
        self.assertEqual(r['ssh_last_error'], 'Connection refused')
        self.assertIn('guest boot trace', r['guest_serial_tail'])
        self.assertIn('qemu startup error', r['qemu_log_tail'])
        self.assertEqual(r['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')

    def test_diagnostic_tail_never_reads_more_than_bound(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'guest.log'
            f.write_bytes(b'A' * 3000 + b'END')
            tail = accept._bounded_boot_tail(f, limit=30)
            self.assertTrue(tail.endswith('END'))
            self.assertLessEqual(len(tail), 30)
            self.assertEqual(accept._bounded_boot_tail(Path(td) / 'missing'), '')

    def test_stop_failure_cannot_pass(self):
        FakeQemu.should_fail_stop = True
        # A running fake QEMU without a working stop operation cannot be
        # assumed cleaned up even if TemporaryDirectory removal succeeds.
        r = self._run()
        self.assertEqual(r['status'], 'CLEANUP_FAILED')

    def test_explicit_opt_in_required_for_vm(self):
        with patch.object(accept, 'verify_host_toolchain', return_value=stub_tools()), \
             patch.object(accept, 'QemuWorker', side_effect=AssertionError('no VM')):
            r = accept.run({}, run_clean_vm=False)
        self.assertEqual(r['status'], 'INSPECTION_ONLY_NOT_ATTESTED')

    def test_missing_operator_host_gates_block_vm(self):
        with patch.object(accept, 'verify_host_toolchain', return_value=stub_tools(False)), \
             patch.object(accept, 'QemuWorker', side_effect=AssertionError('no VM')):
            r = accept.run({}, run_clean_vm=True)
        self.assertEqual(r['status'], 'HOST_GATES_BLOCKED')


if __name__ == '__main__':
    unittest.main()
