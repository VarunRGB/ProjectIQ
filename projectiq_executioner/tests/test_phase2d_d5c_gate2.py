"""D5c Gate 2: offline failure injection and fail-closed evidence tests."""
from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from projectiq_executioner.phase2.vm_worker import d5c_startup_cancel_acceptance as gate
from projectiq_executioner.phase2.vm_worker.qemu import SSHControl, WorkerFailure


class MarkerTests(unittest.TestCase):
    def test_marker_published_atomically_and_once(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'cancel.marker'
            gate._publish_cancel(marker)
            self.assertTrue(gate._cancel_requested(marker))
            self.assertFalse(marker.with_name(marker.name + '.writing').exists())
            with self.assertRaises(ValueError):
                gate._publish_cancel(marker)

    def test_missing_cancel_does_not_count_as_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(gate._cancel_requested(Path(temp) / 'missing'))

    def test_exact_cancel_marker_required(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'cancel.marker'
            for bad in (b'', b'CANCEL', b'CANCEL\r\n', b'CANCEL\nMORE'):
                marker.write_bytes(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    gate._cancel_requested(marker)
            marker.write_bytes(gate.CANCEL_BYTES)
            self.assertTrue(gate._cancel_requested(marker))

    def test_unreadable_marker_is_not_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'cancel.marker'
            with patch.object(Path, 'lstat', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    gate._cancel_requested(marker)

    def test_symlink_never_counts_as_cancellation_if_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / 'real'
            target.write_bytes(gate.CANCEL_BYTES)
            marker = base / 'cancel.marker'
            try:
                marker.symlink_to(target)
            except OSError:
                self.skipTest('Windows symlink permission unavailable')
            with self.assertRaises(ValueError):
                gate._cancel_requested(marker)

    def test_bounded_cancel_wait_times_out(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(gate.time, 'monotonic', side_effect=[0.0, 0.0, 1.0]):
                with self.assertRaisesRegex(RuntimeError, 'TIMEOUT'):
                    gate._wait_cancel(Path(temp) / 'missing', seconds=0.01)


class EvidenceTests(unittest.TestCase):
    def test_scenarios_and_policy_are_fixed(self):
        self.assertEqual(gate.SCENARIOS, ('pre_launch', 'post_launch', 'ssh_wait', 'guest_ready'))
        self.assertEqual(gate.VM_RAM_MIB, 1536)
        self.assertEqual(gate.WORKSPACE_MIB, 96)
        self.assertEqual(gate.ATTESTATION, 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')

    def test_stage_cannot_be_forged(self):
        ok = {'schema_version': '0.1', 'scenario': 'ssh_wait', 'ready': True}
        self.assertTrue(gate._stage_valid(ok, 'ssh_wait'))
        for bad in (None, [], {**ok, 'ready': 1}, {**ok, 'scenario': 'guest_ready'},
                    {**ok, 'extra': True}, {**ok, 'ready': False}):
            with self.subTest(bad=bad):
                self.assertFalse(gate._stage_valid(bad, 'ssh_wait'))

    def test_launch_requires_typed_exact_pid_and_guard(self):
        ok = {'schema_version': '0.1', 'vm_pid': 321, 'guarded': True}
        self.assertTrue(gate._launch_valid(ok))
        for bad in ({**ok, 'vm_pid': True}, {**ok, 'vm_pid': -1},
                    {**ok, 'guarded': 1}, {**ok, 'guarded': False},
                    {**ok, 'extra': 1}, None):
            self.assertFalse(gate._launch_valid(bad))

    def test_cancellation_proof_requires_all_fields(self):
        ok = {'schema_version': '0.1', 'scenario': 'post_launch',
              'cancellation_seen': True, 'qemu_started': True, 'qemu_exited': True}
        self.assertTrue(gate._cancelled_valid(ok, 'post_launch'))
        for bad in ({**ok, 'qemu_exited': False}, {**ok, 'qemu_started': False},
                    {**ok, 'cancellation_seen': 1}, {**ok, 'extra': 'anything'},
                    {**ok, 'scenario': 'pre_launch'}, None):
            self.assertFalse(gate._cancelled_valid(bad, 'post_launch'))
        self.assertTrue(gate._cancelled_valid({**ok, 'scenario': 'pre_launch',
                                               'qemu_started': False}, 'pre_launch'))

    def test_guest_setup_is_not_claimed_as_running_student_code(self):
        src = inspect.getsource(gate._child)
        self.assertIn("ssh.ssh('true'", src)
        self.assertIn("scenario == 'pre_launch'", src)
        self.assertIn('verify_child_in_job(vm_proc)', src)
        self.assertIn('worker.stop()', src)
        self.assertNotIn('approved_snapshots', src)
        self.assertNotIn('exec(student', src)

    def test_prelaunch_cancel_never_starts_qemu(self):
        code = inspect.getsource(gate._child)
        self.assertLess(code.index("if scenario == 'pre_launch':"), code.index('worker.start(seed)'))

    def test_witness_before_ssh_cancel_requires_a_failed_probe(self):
        code = inspect.getsource(gate._child)
        self.assertIn('failed_once = True', code)
        self.assertIn("'SSH_PROBE_NOT_PERFORMED'", code)
        self.assertIn('cancel_requested=lambda:', code)


class SSHCancelTests(unittest.TestCase):
    def _ssh(self):
        return SSHControl({'ssh_binary': 'ssh'}, Path('.'), 19999, Path('key'))

    def test_cancel_before_first_ssh_probe(self):
        ssh = self._ssh()
        vm = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None))
        with patch.object(ssh, 'ssh') as send:
            with self.assertRaisesRegex(WorkerFailure, 'startup cancelled'):
                ssh.wait_ssh(vm, seconds=10, cancel_requested=lambda: True)
            send.assert_not_called()

    def test_cancel_between_failed_probes(self):
        ssh = self._ssh()
        vm = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None))
        flags = iter([False, True])
        with patch.object(ssh, 'ssh', return_value=SimpleNamespace(returncode=255)) as send:
            with self.assertRaisesRegex(WorkerFailure, 'startup cancelled'):
                ssh.wait_ssh(vm, seconds=10, cancel_requested=lambda: next(flags, True))
            self.assertEqual(send.call_count, 1)

    def test_existing_success_without_token_is_unchanged(self):
        ssh = self._ssh()
        vm = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None))
        with patch.object(ssh, 'ssh', return_value=SimpleNamespace(returncode=0)) as send:
            self.assertIsNone(ssh.wait_ssh(vm, seconds=10))
            send.assert_called_once()

    def test_cancel_marker_error_not_swallowed_as_not_cancelled(self):
        ssh = self._ssh()
        vm = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None))
        def unreadable():
            raise PermissionError('marker unreadable')
        with self.assertRaises(PermissionError):
            ssh.wait_ssh(vm, seconds=10, cancel_requested=unreadable)

    def test_invalid_callback_type_fails_closed(self):
        ssh = self._ssh()
        vm = SimpleNamespace(proc=SimpleNamespace(poll=lambda: None))
        with self.assertRaises(WorkerFailure):
            ssh.wait_ssh(vm, seconds=10, cancel_requested='yes')


class ChildCompletionTests(unittest.TestCase):
    def test_no_success_before_worker_really_exits(self):
        class FakeProcess:
            pid = 123
            alive = True
            def poll(self):
                return None if self.alive else 0
        class FakeVM:
            def __init__(self, *args, **kwargs):
                self.proc = None
                self.process = FakeProcess()
                self.refuse_stop = False
            def network_spec(self):
                return 'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:5555-:22'
            def start(self, seed):
                self.proc = self.process
            def stop(self):
                if not self.refuse_stop:
                    self.process.alive = False
        class FakeSSH:
            def __init__(self,*args):
                self.port = 5555
            def wait_ssh(self,vm,seconds=80,*,cancel_requested=None):
                if cancel_requested is not None:
                    result=self.ssh('true',timeout=6)
                    if result.returncode!=0 and cancel_requested():
                        raise WorkerFailure('Worker VM startup cancelled')
                return None
            def ssh(self,*args,**kwargs):
                return SimpleNamespace(returncode=255 if self.port!=5555 else 0)
        for scenario in gate.SCENARIOS:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp, \
                 patch.object(gate, 'load_config', return_value={}), \
                 patch.object(gate, 'verify_host_toolchain', return_value={
                     'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
                 patch.object(gate, 'ensure_controller_job'), \
                 patch.object(gate, 'verify_child_in_job', return_value=True), \
                 patch.object(gate, 'make_ssh_key', return_value=(Path('key'),'ssh-ed25519 AAA')), \
                 patch.object(gate, 'make_nocloud_iso', return_value=Path('seed.iso')), \
                 patch.object(gate, '_free_loopback_port', side_effect=[5555,5556]), \
                 patch.object(gate, 'QemuWorker', FakeVM), \
                 patch.object(gate, 'SSHControl', FakeSSH):
                workspace=Path(temp)
                gate._publish_cancel(workspace/'cancel.marker')
                rc=gate._child(Path('config'),workspace,scenario)
                self.assertEqual(rc,0)
                result=gate._read_record(workspace/'cancelled.json')
                self.assertTrue(gate._cancelled_valid(result,scenario))
                self.assertTrue(gate._stage_valid(gate._read_record(workspace/'stage.json'),scenario))

    def test_still_running_qemu_cannot_publish_cancel_success(self):
        class FakeProcess:
            pid=123
            def poll(self):return None
        class FakeVM:
            def __init__(self,*args,**kwargs):self.proc=None
            def network_spec(self):
                return 'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:5555-:22'
            def start(self,seed):self.proc=FakeProcess()
            def stop(self):pass  # deliberately leaves QEMU alive
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(gate, 'load_config', return_value={}), \
             patch.object(gate, 'verify_host_toolchain', return_value={
                 'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(gate, 'ensure_controller_job'), \
             patch.object(gate, 'verify_child_in_job', return_value=True), \
             patch.object(gate, 'make_ssh_key', return_value=(Path('key'),'ssh-ed25519 AAA')), \
             patch.object(gate, 'make_nocloud_iso', return_value=Path('seed.iso')), \
             patch.object(gate, '_free_loopback_port', return_value=5555), \
             patch.object(gate, 'QemuWorker', FakeVM):
            workspace=Path(temp)
            gate._publish_cancel(workspace/'cancel.marker')
            self.assertEqual(gate._child(Path('config'),workspace,'post_launch'),2)
            self.assertIsNone(gate._read_record(workspace/'cancelled.json'))


class HostCaseTests(unittest.TestCase):
    def test_cooperative_parent_handoff_and_handle_cleanup(self):
        class FakeHandle:
            instances = []
            def __init__(self, pid, expected_image):
                self.__class__.instances.append(self)
                self.live = True
                self.closed = False
                self.pid = pid
                self.assert_image = expected_image
            def is_live(self):
                return self.live
            def wait_exited(self, seconds):
                self.live = False
                return True
            def emergency_terminate(self):
                raise AssertionError('Emergency kill must not occur on PASS')
            def close(self):
                self.closed = True

        class FakeController:
            def __init__(self, argv, **kwargs):
                workspace = Path(argv[argv.index('--_workspace') + 1])
                scenario = argv[argv.index('--_scenario') + 1]
                self.workspace = workspace
                self.scenario = scenario
                self.dead = False
                gate._atomic_record(workspace / 'job_ready.json',
                                    {'schema_version': '0.1', 'armed': True})
                gate._atomic_record(workspace / 'stage.json', {
                    'schema_version': '0.1', 'scenario': scenario, 'ready': True})
                if scenario != 'pre_launch':
                    gate._atomic_record(workspace / 'launched.json', {
                        'schema_version': '0.1', 'vm_pid': 987, 'guarded': True})
            def poll(self):
                return 0 if self.dead else None
            def wait(self, timeout):
                self.dead = True
                if self.workspace.joinpath('cancel.marker').read_bytes() != gate.CANCEL_BYTES:
                    raise AssertionError('Cancellation marker missing')
                gate._atomic_record(self.workspace / 'cancelled.json', {
                    'schema_version': '0.1', 'scenario': self.scenario,
                    'cancellation_seen': True,
                    'qemu_started': self.scenario != 'pre_launch',
                    'qemu_exited': True})
                return 0
            def kill(self):
                self.dead = True

        for scenario in ('pre_launch', 'post_launch', 'ssh_wait', 'guest_ready'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp, \
                 patch.object(gate.subprocess, 'Popen', FakeController), \
                 patch.object(gate, '_WindowsProcessHandle', FakeHandle):
                passed, removed, reason = gate._case(Path('config'), Path(temp), scenario, '/fake/qemu')
                self.assertTrue(passed)
                self.assertTrue(removed)
                self.assertEqual(reason, 'CANCELLATION_AND_QEMU_EXIT_VERIFIED')
                if scenario != 'pre_launch':
                    self.assertTrue(FakeHandle.instances[-1].closed)

    def test_stage_not_reached_still_removes_temporary_storage(self):
        class FakeController:
            def __init__(self, argv, **kwargs):
                self.dead = False
            def poll(self):
                return None if not self.dead else -9
            def kill(self):
                self.dead = True
            def wait(self, timeout):
                self.dead = True
                return -9
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(gate.subprocess, 'Popen', FakeController), \
             patch.object(gate, 'STAGE_TIMEOUT', 0):
            passed, removed, reason = gate._case(Path('config'), Path(temp), 'pre_launch', '/fake/qemu')
            self.assertFalse(passed)
            self.assertTrue(removed)
            self.assertIn('STAGE', reason)


class HostTests(unittest.TestCase):
    def test_inspection_mode_never_spawns_vm(self):
        with patch.object(gate, 'os', SimpleNamespace(name='nt')), \
             patch.object(gate, 'load_config', return_value={}), \
             patch.object(gate, 'verify_host_toolchain', return_value={
                 'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(gate, '_expected_qemu', return_value='qemu.exe'), \
             patch.object(gate, '_case') as cases:
            r = gate.run(Path('config.json'), execute=False)
            self.assertEqual(r['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
            cases.assert_not_called()

    def test_no_acknowledgment_never_spawns_vm(self):
        with patch.object(gate, 'os', SimpleNamespace(name='nt')), \
             patch.object(gate, 'load_config', return_value={}), \
             patch.object(gate, 'verify_host_toolchain', return_value={
                 'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(gate, '_expected_qemu', return_value='qemu.exe'), \
             patch.object(gate, '_case') as cases:
            r = gate.run(Path('config.json'), execute=True, acknowledge=False)
            self.assertEqual(r['status'], 'STARTUP_CANCELLATION_ACKNOWLEDGMENT_REQUIRED')
            cases.assert_not_called()

    def test_all_scenarios_must_pass_before_status_pass(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(gate, 'os', SimpleNamespace(name='nt')), \
             patch.object(gate, 'load_config', return_value={}), \
             patch.object(gate, 'verify_host_toolchain', return_value={
                 'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(gate, '_expected_qemu', return_value='qemu.exe'), \
             patch.object(gate, '_case', return_value=(True, True, 'CANCELLATION_AND_QEMU_EXIT_VERIFIED')) as cases:
            r = gate.run(Path('config.json'), execute=True, acknowledge=True, work_root=Path(temp))
            self.assertEqual(r['status'], gate.STATUS_PASS)
            self.assertEqual(cases.call_count, 4)
            self.assertTrue(all(v == 'PASS' for v in r['checks'].values()))

    def test_failing_or_unclean_case_cannot_pass(self):
        for reply in ((False, True, 'BAD_STAGE'), (False, False, 'LEFTOVER')):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as temp, \
                 patch.object(gate, 'os', SimpleNamespace(name='nt')), \
                 patch.object(gate, 'load_config', return_value={}), \
                 patch.object(gate, 'verify_host_toolchain', return_value={
                     'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
                 patch.object(gate, '_expected_qemu', return_value='qemu.exe'), \
                 patch.object(gate, '_case', return_value=reply):
                r = gate.run(Path('config.json'), execute=True, acknowledge=True,
                             work_root=Path(temp))
                self.assertNotEqual(r['status'], gate.STATUS_PASS)
                self.assertEqual(r['failure_code'], reply[2])

    def test_no_windows_cannot_claim_acceptance(self):
        if gate.os.name == 'nt':
            self.skipTest('This negative path is for a non-Windows host')
        r = gate.run(Path('no-config'), execute=True, acknowledge=True)
        self.assertEqual(r['status'], 'WINDOWS_JOB_ACCEPTANCE_REQUIRES_WINDOWS')


if __name__ == '__main__':
    unittest.main()
