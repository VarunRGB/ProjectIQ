"""D5d Gate 2: independent process-boundary acceptance tests (no VM required)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5d_process_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import guest_agent as runtime_guest
from projectiq_executioner.phase2.vm_worker import d5d_process_acceptance as host


class GuestKernelEvidenceTests(unittest.TestCase):
    def test_synthetic_build_reaps_child_after_trusted_stop_marker(self):
        # Behavior regression for source-free Linux probe (not a host VM run).
        if not Path('/usr/bin/python3').is_file():
            self.skipTest('Linux synthetic test helper unavailable')
        with tempfile.TemporaryDirectory() as td:
            marker, stop = Path(td) / 'marker', Path(td) / 'stop'
            proc = subprocess.Popen([sys.executable, '-I', '-c', guest.SYNTHETIC_BUILD,
                                     str(marker), str(stop)])
            try:
                deadline = time.monotonic() + 4
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                parent_pid, child_pid = map(int, marker.read_text('ascii').split())
                self.assertEqual(parent_pid, proc.pid)
                self.assertTrue(Path('/proc', str(child_pid)).exists())
                stop.write_text('STOP\n', encoding='ascii')
                self.assertEqual(proc.wait(timeout=5), 0)
                self.assertFalse(Path('/proc', str(child_pid)).exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=4)

    def test_mysql_limits_require_kernel_evidence_before_credentials(self):
        from projectiq_executioner.phase2.vm_worker import qemu
        source = qemu.MYSQL_BOOTSTRAP_SCRIPT
        self.assertIn('/etc/systemd/system/mysql.service.d/', source)
        self.assertIn('MemoryMax=536870912', source)
        self.assertIn('TasksMax=64', source)
        self.assertIn('IOWriteBandwidthMax=/var/lib/mysql 8388608', source)
        self.assertIn("'/sys/fs/cgroup/system.slice/mysql.service'", source)
        self.assertIn("require(bounded('memory.max', 536870912))", source)
        self.assertIn("require(bounded('pids.max', 64))", source)
        self.assertLess(source.index('MYSQL_KERNEL_VERIFY\nready=0'),
                        source.index('secret=$(/usr/bin/openssl rand -hex 24)'))


    def test_kernel_group_read_from_unified_proc(self):
        with tempfile.TemporaryDirectory() as td:
            proc = Path(td)
            (proc / '123').mkdir()
            (proc / '123' / 'cgroup').write_text('0::/system.slice/mysql.service\n')
            self.assertEqual(guest._kernel_group(123, proc), '/system.slice/mysql.service')
            (proc / '123' / 'cgroup').write_text('1:name=systemd:/wrong\n')
            self.assertIsNone(guest._kernel_group(123, proc))
            self.assertIsNone(guest._kernel_group(0, proc))

    def test_process_owner_and_ppid_independently_verified(self):
        with tempfile.TemporaryDirectory() as td:
            proc = Path(td)
            (proc / '123').mkdir()
            (proc / '123' / 'status').write_text('Name:\tpython\nUid:\t111 111 111 111\nPPid:\t55\n')
            self.assertTrue(guest._proc_identity(123, 111, ppid=55, proc=proc))
            self.assertFalse(guest._proc_identity(123, 112, ppid=55, proc=proc))
            self.assertFalse(guest._proc_identity(123, 111, ppid=56, proc=proc))
            (proc / '123' / 'status').write_text('Uid:\t111 0 111 111\nPPid:\t55\n')
            self.assertFalse(guest._proc_identity(123, 111, ppid=55, proc=proc))

    def test_scoped_kernel_members_reject_malformed_pid(self):
        with tempfile.TemporaryDirectory() as td:
            scope = Path(td)
            (scope / 'cgroup.procs').write_text('123\n456\n')
            self.assertEqual(guest._kernel_members(scope), {123, 456})
            (scope / 'cgroup.procs').write_text('123\nroot\n')
            self.assertIsNone(guest._kernel_members(scope))

    def test_drain_uses_populated_not_just_empty_cgroup_procs(self):
        with tempfile.TemporaryDirectory() as td:
            scope = Path(td)
            (scope / 'cgroup.procs').write_text('')
            (scope / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
            self.assertFalse(guest._scope_drained(scope))
            (scope / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
            self.assertTrue(guest._scope_drained(scope))
            (scope / 'cgroup.events').write_text('unexpected\n')
            self.assertFalse(guest._scope_drained(scope))

    def test_resource_caps_reject_max_and_overlimit(self):
        with tempfile.TemporaryDirectory() as td:
            scope = Path(td)
            (scope / 'memory.max').write_text('max\n')
            (scope / 'pids.max').write_text('100\n')
            (scope / 'io.max').write_text('8:1 wbps=9000000 rbps=max\n')
            with patch.object(guest.os, 'major', return_value=8, create=True), \
                 patch.object(guest.os, 'minor', return_value=1, create=True):
                self.assertFalse(guest._write_cap(scope, 536870912))
                self.assertFalse(guest._pids_cap(scope, 64))
                self.assertFalse(guest._io_cap(scope, 10, 8388608))
                (scope / 'memory.max').write_text('536870912\n')
                (scope / 'pids.max').write_text('64\n')
                (scope / 'io.max').write_text('8:1 rbps=max wbps=8388608\n')
                self.assertTrue(guest._write_cap(scope, 536870912))
                self.assertTrue(guest._pids_cap(scope, 64))
                self.assertTrue(guest._io_cap(scope, 10, 8388608))

    def test_backing_disk_id_is_accepted_only_with_partition_proof(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / 'sys'
            sysdev = base / 'dev' / 'block'
            part_node = base / 'devices' / 'pci0' / 'block' / 'vda' / 'vda1'
            part_node.mkdir(parents=True)
            sysdev.mkdir(parents=True)
            (part_node / 'partition').write_text('1\n')
            (part_node.parent / 'dev').write_text('252:0\n')
            original_resolve = Path.resolve
            def fake_resolve(path, *, strict=False):
                if path == sysdev / '252:1':
                    return part_node
                return original_resolve(path, strict=strict)
            with patch.object(guest.os, 'major', return_value=252, create=True), \
                 patch.object(guest.os, 'minor', return_value=1, create=True), \
                 patch.object(Path, 'resolve', autospec=True, side_effect=fake_resolve):
                self.assertEqual(guest._backing_io_devices(123, sysdev=sysdev),
                                 {'252:1', '252:0'})
                (part_node / 'partition').write_text('bad')
                self.assertEqual(guest._backing_io_devices(123, sysdev=sysdev),
                                 {'252:1'})
                (part_node / 'partition').write_text('1')
                (part_node.parent / 'dev').write_text('8:7')
                self.assertEqual(guest._backing_io_devices(123, sysdev=sysdev),
                                 {'252:1', '8:7'})

    def test_io_cap_checks_only_proven_device_not_arbitrary_bounded_disk(self):
        with tempfile.TemporaryDirectory() as td:
            scope = Path(td)
            (scope / 'io.max').write_text('8:99 wbps=8388608\n')
            with patch.object(guest, '_backing_io_devices', return_value={'252:1', '252:0'}):
                self.assertFalse(guest._io_cap(scope, 42, 8388608))
                (scope / 'io.max').write_text('252:0 wbps=8388608\n')
                self.assertTrue(guest._io_cap(scope, 42, 8388608))
                (scope / 'io.max').write_text('252:0 wbps=max\n')
                self.assertFalse(guest._io_cap(scope, 42, 8388608))

    def test_build_polling_does_not_stop_at_first_missing_child(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            unit = 'piq-d5d-' + 'a' * 16
            (base / (unit + '.marker')).write_text('111 222')
            (base / (unit + '.child-ready')).write_text('READY')
            group_root = base / 'cgroup'
            scope = group_root / 'projectiq-eval.slice' / (unit + '.scope')
            parent = scope.parent
            proc = MagicMock()
            proc.poll.return_value = None
            obs = {key: False for key in guest.FIELDS}
            expected_group = '/' + scope.relative_to(group_root).as_posix()
            with (
                patch.object(guest, 'WORKSPACE', base),
                patch.object(guest, 'CGROUP', group_root),
                patch.object(guest.secrets, 'token_hex', return_value='a' * 16),
                patch.object(guest.subprocess, 'Popen', return_value=proc),
                patch.object(guest, '_kernel_members', side_effect=[{111}, {111, 222}]) as members,
                patch.object(guest, '_kernel_group', return_value=expected_group),
                patch.object(guest, '_proc_identity', return_value=True),
                patch.object(guest, '_prepare_stop_parent', return_value=True),
                patch.object(guest, '_scope_drained', return_value=True),
                patch.object(guest, '_cmd', return_value=''),
                patch.object(guest.time, 'monotonic', side_effect=[0, 1, 2, 3, 4]),
                patch.object(guest.time, 'sleep', return_value=None),
            ):
                guest._synthetic_scope(1000, 1001, {}, parent, obs)
            self.assertEqual(members.call_count, 2)
            self.assertTrue(obs['build_parent_in_scope'])
            self.assertTrue(obs['build_child_in_scope'])
            self.assertTrue(obs['build_scope_drained'])

    def test_child_cgroup_must_be_direct_or_proven_descendant(self):
        with tempfile.TemporaryDirectory() as td:
            cgroot = Path(td)
            scope = cgroot / 'user.slice' / 'app.scope'
            nested = scope / 'child.group'
            nested.mkdir(parents=True)
            (nested / 'cgroup.procs').write_text('222\n', 'ascii')
            with patch.object(guest, 'CGROUP', cgroot):
                child_group = '/user.slice/app.scope/child.group'
                self.assertEqual(guest._child_membership(scope, 222, child_group, {111}),
                                 ('DESCENDANT', True))
                self.assertEqual(guest._child_membership(scope, 222,
                                 '/user.slice/other.scope/child.group', {111}),
                                 ('OUTSIDE', False))
                self.assertEqual(guest._child_membership(scope, 222, child_group, {111}),
                                 ('DESCENDANT', True))
                (nested / 'cgroup.procs').write_text('999\n', 'ascii')
                self.assertEqual(guest._child_membership(scope, 222, child_group, {111}),
                                 ('DESCENDANT', False))
                self.assertEqual(guest._child_membership(scope, 222,
                                 '/user.slice/app.scope/../evil', {111}),
                                 ('OUTSIDE', False))

    def test_searchable_stop_parent_requires_trusted_owner_and_no_writers(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            fake = types.SimpleNamespace(st_uid=1000, st_mode=0o40700)
            with patch.object(Path, 'lstat', return_value=fake), \
                 patch.object(Path, 'chmod'):
                self.assertFalse(guest._prepare_stop_parent(1001, home))
            good = types.SimpleNamespace(st_uid=1000, st_mode=0o40711)
            with patch.object(Path, 'lstat', side_effect=[fake,good]), \
                 patch.object(Path, 'is_symlink', return_value=False), \
                 patch.object(Path, 'chmod') as change:
                self.assertTrue(guest._prepare_stop_parent(1000, home))
                change.assert_called_once_with(0o711)
            bad = types.SimpleNamespace(st_uid=1000, st_mode=0o40730)
            with patch.object(Path, 'lstat', return_value=bad), \
                 patch.object(Path, 'chmod'):
                self.assertFalse(guest._prepare_stop_parent(1000, home))

    def test_scope_drain_requires_recursive_populated_zero(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / 'cgroup.procs').write_text('', 'ascii')
            (p / 'cgroup.events').write_text('populated 1\n', 'ascii')
            self.assertEqual(guest._scope_state(p), 'POPULATED')
            self.assertFalse(guest._scope_drained(p))
            (p / 'cgroup.events').write_text('populated 0\n', 'ascii')
            self.assertEqual(guest._scope_state(p), 'DRAINED')
            self.assertTrue(guest._scope_drained(p))

    def test_mysql_policy_never_inferred_from_unit_name_alone(self):
        obs = {k: False for k in guest.FIELDS}
        answers = {'MainPID': '433', 'ControlGroup': '/system.slice/mysql.service',
                   'ActiveState': 'active'}
        fake_pwd = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=115))
        def fake_cmd(*args, **kwargs):
            return answers.get(args[3], None) if len(args) >= 4 else None
        with patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.dict('sys.modules', {'pwd': fake_pwd}), \
             patch.object(guest, '_cmd', side_effect=fake_cmd), \
             patch.object(guest, '_kernel_members', return_value={433}), \
             patch.object(guest, '_proc_identity', return_value=True), \
             patch.object(guest, '_kernel_group', return_value='/system.slice/mysql.service'), \
             patch.object(guest, '_write_cap', return_value=False), \
             patch.object(guest, '_pids_cap', return_value=False), \
             patch.object(guest, '_io_cap', return_value=False):
            guest._mysql(obs)
        self.assertTrue(obs['mysql_daemon_identity'])
        self.assertTrue(obs['mysql_separate_system_scope'])
        self.assertFalse(obs['mysql_memory_bounded'])
        self.assertFalse(obs['mysql_pids_bounded'])
        self.assertFalse(obs['mysql_io_bounded'])

    def test_mysql_fake_process_is_not_accepted(self):
        obs = {k: False for k in guest.FIELDS}
        answers = {'MainPID': '433', 'ControlGroup': '/system.slice/mysql.service',
                   'ActiveState': 'active'}
        fake_pwd = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=115))
        def fake_cmd(*args, **kwargs):
            return answers.get(args[3], None) if len(args) >= 4 else None
        with patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.dict('sys.modules', {'pwd': fake_pwd}), \
             patch.object(guest, '_cmd', side_effect=fake_cmd), \
             patch.object(guest, '_kernel_members', return_value={433}), \
             patch.object(guest, '_proc_identity', return_value=False), \
             patch.object(guest, '_kernel_group', return_value='/system.slice/mysql.service'):
            guest._mysql(obs)
        self.assertFalse(obs['mysql_daemon_identity'])
        self.assertFalse(obs['mysql_separate_system_scope'])


class HostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {k: False for k in guest.FIELDS}

    def encode(self):
        return json.dumps({'schema': 'd5d-process-1', 'observations': self.evidence}).encode()

    def test_host_accepts_only_exact_bounded_typed_payload(self):
        self.assertEqual(host._parse_guest(self.encode()), self.evidence)
        self.assertIsNone(host._parse_guest(b'garbage'))
        self.evidence['mysql_memory_bounded'] = 'PASS'
        self.assertIsNone(host._parse_guest(self.encode()))

    def test_new_diagnostics_reject_arbitrary_output_and_fake_success(self):
        self.evidence.update(controller_identity=True, bootstrap_and_workspace=True,
                             evaluation_slice_policy=True, build_parent_in_scope=True,
                             build_child_in_scope=True, build_scope_drained=True)
        diag = guest._empty_diagnostics()
        diag.update(marker_ready=True, parent_verified=True,
                    child_uid_verified=True, child_parent_verified=True,
                    child_cgroup_member_verified=True, stop_marker_published=True,
                    child_cgroup_location='DIRECT', final_scope_state='DRAINED')
        report = {'schema':'d5d-process-1', 'observations':self.evidence,
                  'diagnostics':diag}
        self.assertEqual(host._parse_guest(json.dumps(report).encode()), self.evidence)
        diag['child_parent_verified'] = False
        self.assertIsNone(host._parse_guest(json.dumps(report).encode()))
        diag['child_parent_verified'] = True
        diag['child_cgroup_location'] = '/proc/1234/secrets'
        self.assertIsNone(host._parse_guest(json.dumps(report).encode()))
        diag['child_cgroup_location'] = 'DIRECT'
        diag['final_scope_state'] = 'POPULATED'
        self.assertIsNone(host._parse_guest(json.dumps(report).encode()))

    def test_no_false_child_or_drain_without_parent(self):
        self.evidence.update(controller_identity=True, bootstrap_and_workspace=True,
                             evaluation_slice_policy=True, build_child_in_scope=True)
        self.assertIsNone(host._parse_guest(self.encode()))
        self.evidence['build_parent_in_scope'] = True
        self.assertIsNotNone(host._parse_guest(self.encode()))
        self.evidence['build_scope_drained'] = True
        self.assertIsNotNone(host._parse_guest(self.encode()))
        self.evidence['build_child_in_scope'] = False
        self.assertIsNone(host._parse_guest(self.encode()))

    def test_mysql_scope_requires_verified_mysql_uid(self):
        self.evidence.update(controller_identity=True, bootstrap_and_workspace=True,
                             evaluation_slice_policy=True, mysql_separate_system_scope=True)
        self.assertIsNone(host._parse_guest(self.encode()))

    def test_status_requires_mysql_caps_for_complete_baseline(self):
        r = host._base()
        host._classify(r)
        self.assertEqual(r['status'], host.STATUS_INCOMPLETE)
        for k in host.CORE:
            r['checks'][k] = 'PASS'
        host._classify(r)
        self.assertEqual(r['status'], host.STATUS_MYSQL_PENDING)
        for k in guest.FIELDS[-3:]:
            r['checks'][k] = 'PASS'
        host._classify(r)
        self.assertEqual(r['status'], host.STATUS_OBSERVED)
        self.assertEqual(r['security_attestation'], host.ATTESTATION)

    def test_inspection_only_never_starts_vm(self):
        with patch.object(host, 'verify_host_toolchain', return_value={
                'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(host, 'QemuWorker') as qemu:
            result = host.run({}, run_clean_vm=False)
        self.assertEqual(result['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
        qemu.assert_not_called()

    def test_missing_image_blocks_before_vm_launch(self):
        with patch.object(host, 'verify_host_toolchain', return_value={'status': 'BLOCKED'}), \
             patch.object(host, 'QemuWorker') as qemu:
            result = host.run({}, run_clean_vm=True)
        self.assertEqual(result['status'], 'HOST_GATES_BLOCKED')
        qemu.assert_not_called()

    def test_security_attestation_cannot_be_claimed_by_missing_evidence(self):
        self.assertEqual(host._base()['security_attestation'],
                         'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        for k in guest.FIELDS:
            self.assertEqual(host._base()['checks'][k], 'NOT_VERIFIED')



class MysqlRuntimeBoundaryTests(unittest.TestCase):
    def _make_fixture(self, root: Path):
        cg = root / 'cgroup'
        pg = root / 'proc'
        data = root / 'mysql'
        scope = cg / 'system.slice' / 'mysql.service'
        scope.mkdir(parents=True)
        data.mkdir()
        (pg / '456').mkdir(parents=True)
        (pg / '456' / 'status').write_text('Uid:\t115\t115\t115\t115\n')
        (pg / '456' / 'cgroup').write_text('0::/system.slice/mysql.service\n')
        (scope / 'cgroup.procs').write_text('456\n')
        (scope / 'memory.max').write_text('536870912\n')
        (scope / 'pids.max').write_text('64\n')
        (scope / 'io.max').write_text('8:1 rbps=max wbps=8388608\n')
        return cg,pg,data,scope

    def _verify(self, cg: Path, proc: Path, data: Path):
        answers = {'ActiveState': b'active\n', 'ControlGroup': b'/system.slice/mysql.service\n',
                   'MainPID': b'456\n'}
        def fake_cmd(args, *, timeout=8):
            return subprocess.CompletedProcess(args, 0, answers[args[3]], b'')
        fake_pwd = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=115))
        with patch.object(runtime_guest, 'pwd', fake_pwd), \
             patch.object(runtime_guest, '_trusted_process', side_effect=fake_cmd), \
             patch.object(runtime_guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(runtime_guest.os, 'major', return_value=8, create=True), \
             patch.object(runtime_guest.os, 'minor', return_value=1, create=True):
            return runtime_guest._verify_mysql_service_cgroup(
                cgroup_root=cg, proc_root=proc, mysql_data=data)

    def test_real_mysql_daemon_kernel_evidence_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            cg,pg,data,scope=self._make_fixture(Path(td))
            self.assertIsNone(self._verify(cg,pg,data))
            (scope / 'cgroup.procs').write_text('999\n')
            with self.assertRaisesRegex(runtime_guest.GuestFailure,
                                        'MYSQL_RESOURCE_ENFORCEMENT_UNAVAILABLE'):
                self._verify(cg,pg,data)

    def test_unbounded_memory_or_pids_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            cg,pg,data,scope=self._make_fixture(Path(td))
            for cap in ('memory.max','pids.max'):
                with self.subTest(cap=cap):
                    target=scope / cap
                    original=target.read_text()
                    target.write_text('max\n')
                    with self.assertRaises(runtime_guest.GuestFailure):
                        self._verify(cg,pg,data)
                    target.write_text(original)

    def test_wrong_io_device_or_missing_write_limit_fails(self):
        with tempfile.TemporaryDirectory() as td:
            cg,pg,data,scope=self._make_fixture(Path(td))
            for row in ('8:2 wbps=8388608\n','8:1 wbps=max\n','8:1 rbps=8388608\n'):
                with self.subTest(io=row):
                    (scope / 'io.max').write_text(row)
                    with self.assertRaises(runtime_guest.GuestFailure):
                        self._verify(cg,pg,data)

    def test_root_mysql_or_unmatched_cgroup_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cg,pg,data,scope=self._make_fixture(Path(td))
            (pg / '456' / 'status').write_text('Uid:\t0\t0\t0\t0\n')
            with self.assertRaises(runtime_guest.GuestFailure):
                self._verify(cg,pg,data)
            (pg / '456' / 'status').write_text('Uid:\t115\t115\t115\t115\n')
            (pg / '456' / 'cgroup').write_text('0::/user.slice/wrong\n')
            with self.assertRaises(runtime_guest.GuestFailure):
                self._verify(cg,pg,data)

    def test_runtime_checks_kernel_before_returning_credentials(self):
        import inspect
        source=inspect.getsource(runtime_guest._wait_mysql_runtime_ready)
        self.assertIn('_verify_mysql_service_cgroup()',source)
        self.assertLess(source.index('_verify_mysql_service_cgroup()'),
                        source.index('return meta'))


if __name__ == '__main__':
    unittest.main()
