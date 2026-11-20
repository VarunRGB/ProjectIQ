"""Gate 2A.3: independent kernel placement of paused build/install launchers."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from projectiq_executioner.phase2.vm_worker import guest_agent as guest


class BuildHandoffTests(unittest.TestCase):
    def tearDown(self):
        guest._APP_BUILD_HANDOFFS.clear()

    def _fake_kernel(self, directory: str, *, state='S', uid=1001, correct_scope=True, cmd=None):
        root = Path(directory)
        cg = root / 'sys' / 'fs' / 'cgroup'
        proc = root / 'proc'
        unit = 'piq-app-0123456789abcdef'
        group = ('/user.slice/user-1000.slice/user@1000.service/'
                 'projectiq.slice/projectiq-eval.slice/' + unit + '.scope')
        scope = cg.joinpath(*group.strip('/').split('/'))
        scope.mkdir(parents=True)
        (scope / 'cgroup.procs').write_text('3456\n')
        p = proc / '3456'
        p.mkdir(parents=True)
        (p / 'status').write_text(
            f'State:\t{state} (sleeping)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        (p / 'cgroup').write_text('0::' + (group if correct_scope else '/user.slice/other.scope') + '\n')
        command = cmd or ['/usr/bin/node', '/app/build.js']
        argv = ['/usr/bin/python3','-I','-c',guest.APP_LIMIT_LAUNCHER,*command]
        (p / 'cmdline').write_bytes(b'\x00'.join(x.encode() for x in argv) + b'\x00')
        return unit, command, cg, proc

    def test_kernel_observer_accepts_stopped_exact_launcher_only(self):
        with tempfile.TemporaryDirectory() as td:
            unit, command, cg, proc = self._fake_kernel(td)
            observe = lambda: guest._gated_build_pid(unit, command, controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=proc)
            self.assertEqual(observe(), 3456)
            self.assertIsNone(guest._gated_build_pid(unit, ['/bin/false'], controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=proc))
            self.assertIsNone(guest._gated_build_pid('bad/../../', command, controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=proc))
            (proc/'3456'/'status').write_text('State:\tT (stopped)\nUid:\t1001\t1001\t1001\t1001\n')
            self.assertIsNone(observe())

    def test_wrong_uid_or_cgroup_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            unit, command, cg, proc = self._fake_kernel(td, uid=0)
            self.assertIsNone(guest._gated_build_pid(unit, command, controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=proc))
        with tempfile.TemporaryDirectory() as td:
            unit, command, cg, proc = self._fake_kernel(td, correct_scope=False)
            self.assertIsNone(guest._gated_build_pid(unit, command, controller_uid=1000,
                app_uid=1001, cgroup_root=cg, proc_root=proc))

    def test_scoped_command_uses_immutable_pause_marker(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest.secrets, 'token_hex', side_effect=[
                 '0123456789abcdef0123456789abcdef','0123456789abcdef']):
            command = guest._project_command(['/usr/bin/node', 'build.js'],
                {'PROJECTIQ_ATTEST_PAUSE': '0'}, audit_preexec=True)
        self.assertIn('PROJECTIQ_ATTEST_PAUSE=1', command)
        self.assertNotIn('PROJECTIQ_ATTEST_PAUSE=0', command)
        self.assertIn('PROJECTIQ_EVAL_REQUIRED=1', command)
        self.assertIn('PROJECTIQ_ATTEST_GATE', guest.APP_LIMIT_LAUNCHER)
        with patch.object(guest, '_APP_CGROUP_ACTIVE', False):
            with self.assertRaises(guest.GuestFailure):
                guest._project_command(['/bin/true'], audit_preexec=True)

    def test_audited_process_requires_verified_gate_publication(self):
        child = SimpleNamespace(returncode=0, poll=lambda: None,
                                communicate=lambda **kw: (b'output', b''))
        marker = 'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef', marker]), \
             patch.object(guest, '_gated_build_pid', return_value=3456), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_publish_build_release') as published:
            p = guest._audited_build_process(['/bin/true'], env=None, cwd=None,
                                             input=None, timeout=20, kind='npm_ci')
        self.assertEqual(p.returncode, 0)
        self.assertEqual(guest._APP_BUILD_HANDOFFS, ['npm_ci'])
        self.assertEqual(published.call_args.args[0].as_posix(), marker.partition('=')[2])

    def test_observation_failure_blocks_and_stops_scope(self):
        class FakeProc:
            def poll(self): return None
            def kill(self): self.killed = True
            def communicate(self, **kw): return b'', b''
        child = FakeProc()
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef',
                          'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32]), \
             patch.object(guest, '_gated_build_pid', return_value=None), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_stop_build_scope') as stop, \
             patch.object(guest.time, 'monotonic', side_effect=[0,0,20]):
            with self.assertRaises(guest.GuestFailure):
                guest._audited_build_process(['/bin/true'], env=None, cwd=None,
                                             input=None, timeout=20, kind='vite_build')
        self.assertTrue(child.killed)
        stop.assert_called_once()
        self.assertEqual(guest._APP_BUILD_HANDOFFS, [])

    def test_denied_gate_release_cannot_record_a_handoff(self):
        class FakeProc:
            def poll(self): return None
            def kill(self): self.killed = True
            def communicate(self, **kw): return b'', b''
        child = FakeProc()
        marker = 'PROJECTIQ_ATTEST_GATE=/home/projectiq/.projectiq-build-release-' + 'a'*32
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_uid=1001))), \
             patch.object(guest, '_project_command', return_value=['--unit=piq-app-0123456789abcdef', marker]), \
             patch.object(guest, '_gated_build_pid', return_value=3456), \
             patch.object(guest.subprocess, 'Popen', return_value=child), \
             patch.object(guest, '_publish_build_release', side_effect=guest.GuestFailure('APP_CGROUP_BUILD_GATE_RELEASE_FAILED')), \
             patch.object(guest, '_stop_build_scope') as stop:
            with self.assertRaisesRegex(guest.GuestFailure, '^APP_CGROUP_BUILD_GATE_RELEASE_FAILED$'):
                guest._audited_build_process(['/bin/true'], env=None, cwd=None,
                                             input=None, timeout=20, kind='npm_ci')
        self.assertTrue(child.killed)
        stop.assert_called_once()
        self.assertEqual(guest._APP_BUILD_HANDOFFS, [])

    def test_exact_counts_are_required_for_each_adapter(self):
        guest._APP_BUILD_HANDOFFS[:] = ['npm_ci','npm_ci','vite_build']
        report = guest._build_handoff_report('node_fullstack_offline_v1')
        self.assertEqual(report['status'], 'PREEXEC_KERNEL_HANDOFF_VERIFIED')
        self.assertEqual(report['npm_ci'], 2)
        self.assertEqual(report['vite_build'], 1)
        with self.assertRaises(guest.GuestFailure):
            guest._build_handoff_report('node_vite_offline_v1')
        guest._APP_BUILD_HANDOFFS.clear()
        self.assertEqual(guest._build_handoff_report('static_html_v1')['status'], 'NOT_APPLICABLE')

    def test_normal_scope_launcher_does_not_pause(self):
        with patch.object(guest, '_APP_CGROUP_ACTIVE', True), \
             patch.object(guest.os, 'getuid', return_value=1000, create=True):
            command = guest._project_command(['/usr/bin/php','-v'])
        self.assertNotIn('PROJECTIQ_ATTEST_PAUSE=1', command)
        self.assertFalse(any(x.startswith('PROJECTIQ_ATTEST_GATE=') for x in command))
        self.assertIn('--scope', command)

if __name__ == '__main__':
    unittest.main()
