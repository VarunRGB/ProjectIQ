"""Offline Gate 2B.1 acceptance tests; no VM or memory stress occurs here."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_memory_acceptance as acceptance
from projectiq_executioner.phase2.vm_worker import d5_memory_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import qemu


class MemoryAcceptanceTests(unittest.TestCase):
    def test_policy_matches_actual_root_owned_eval_slice(self):
        with tempfile.TemporaryDirectory() as td:
            cgroot = Path(td)/'sys'; work = Path(td)/'work'; work.mkdir()
            parts = ['user.slice', 'user-1000.slice', 'user@1000.service',
                     'projectiq.slice', 'projectiq-eval.slice']
            node = cgroot.joinpath(*parts); node.mkdir(parents=True)
            (node/'memory.max').write_text(str(guest.MEMORY_LIMIT))
            (node/'pids.max').write_text(str(guest.TASK_LIMIT))
            (node/'io.max').write_text(f'8:1 wbps={guest.WRITE_BPS}\n')
            group = '/' + '/'.join(parts)
            with patch.object(guest, 'CGROUP', cgroot), patch.object(guest, 'WORKSPACE', work), \
                 patch.object(guest.os, 'major', return_value=8, create=True), \
                 patch.object(guest.os, 'minor', return_value=1, create=True):
                self.assertEqual(guest._policy_path(1000, group), node)
                self.assertIsNone(guest._policy_path(1001, group))
                (node/'memory.max').write_text('max')
                self.assertIsNone(guest._policy_path(1000, group))
                (node/'memory.max').write_text(str(guest.MEMORY_LIMIT))
                (node/'io.max').write_text('8:2 wbps=16777216\n')
                self.assertIsNone(guest._policy_path(1000, group))

    def test_kernel_events_parsing_rejects_missing_counters(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'memory.events'
            path.write_text('low 0\nhigh 0\nmax 9\noom 1\noom_kill 2\n')
            self.assertEqual(guest._read_events(path)['oom_kill'], 2)
            for bad in ('max 2\noom 1\n', 'max x\noom 1\noom_kill 0\n', ''):
                path.write_text(bad)
                with self.assertRaises(ValueError): guest._read_events(path)

    def test_guest_code_is_bounded_unprivileged_and_no_sigstop(self):
        self.assertEqual(guest.MEMORY_LIMIT, 2147483648)
        self.assertEqual(acceptance.VM_RAM_MIB, 3584)
        self.assertIn("marker.lstat()", guest.WORKER)
        self.assertIn('projectiq_run', guest.WORKER)
        self.assertNotIn('SIGSTOP', guest.WORKER)
        self.assertNotIn('setuid(0)', guest.WORKER)
        self.assertIn('O_EXCL', guest._publish_release.__code__.co_names)
        self.assertNotIn('projectiq-eval-bootstrap.ready', qemu.CGROUP_BOOTSTRAP_SCRIPT)
        self.assertIn('MemoryMax=2147483648', qemu.APP_CGROUP_BOOTSTRAP_SCRIPT)

    def test_guest_parser_denies_forged_oom_claim_and_wrong_schema(self):
        checks = {name: 'PASS' for name in guest.CHECKS}
        valid = {'schema_version':'0.1', 'checks': checks,
                 'diagnostics': {
                     'verified_worker_count':2,
                     'memory_max_events_delta': 5,
                     'memory_oom_kill_delta': 1,
                     'memory_peak_bytes': guest.MEMORY_LIMIT,
                     'scope_cleanup_observed': True,
                     'cooperative_stop_markers_published': 2,
                     'scope_cleanup_observations': [
                         {'worker': i, 'kill_returncode': 0, 'stop_returncode': 0,
                          'scope_state': 'ABSENT', 'remaining_tasks': 0,
                          'wrapper_exit_code_after': -9,
                          'stop_marker_published': True,
                          'kill_failure_reason': 'NONE'} for i in (1, 2)],
                     'controller_home_mode_before': 448,
                     'controller_home_mode_after': 457,
                     'release_markers_visible_to_app': True,
                     'stress_monitor_outcome': 'KERNEL_OOM_EVENT',
                     'wrapper_exit_codes_before_cleanup': [-1, -1]}}
        self.assertIsNotNone(acceptance._parse_guest(json.dumps(valid).encode()))
        for changed in (
                {'diagnostics': {**valid['diagnostics'], 'memory_oom_kill_delta': 0}},
                {'diagnostics': {**valid['diagnostics'], 'memory_max_events_delta': 0}},
                {'diagnostics': {**valid['diagnostics'], 'verified_worker_count': 1}},
                {'diagnostics': {**valid['diagnostics'], 'memory_peak_bytes': 1000}},
                {'diagnostics': {**valid['diagnostics'], 'scope_cleanup_observed': False}},
                {'checks': {**checks, 'invented_check': 'PASS'}},
                {'schema_version': '99'},
                {'diagnostics': {**valid['diagnostics'], 'secret': 'not allowed'}}):
            with self.subTest(changed=changed):
                doc = {**valid, **changed}
                self.assertIsNone(acceptance._parse_guest(json.dumps(doc).encode()))
        self.assertIsNone(acceptance._parse_guest(b'X' * 4000))

    def test_not_verified_never_becomes_a_pass(self):
        c = {name:'PASS' for name in guest.CHECKS}
        c['memory_events_cgroup_oom_kill'] = 'NOT_VERIFIED'
        self.assertEqual(acceptance._parse_guest(json.dumps({
            'schema_version':'0.1', 'checks':c,
            'diagnostics':{'verified_worker_count':2,
                'memory_peak_bytes': guest.MEMORY_LIMIT,
                'scope_cleanup_observed': True,
                     'cooperative_stop_markers_published': 2,
                'scope_cleanup_observations': [
                    {'worker': i, 'kill_returncode': 0, 'stop_returncode': 0,
                     'scope_state': 'ABSENT', 'remaining_tasks': 0,
                     'wrapper_exit_code_after': -9,
                          'stop_marker_published': True,
                          'kill_failure_reason': 'NONE'} for i in (1, 2)],
                'controller_home_mode_after': 0o711,
                'release_markers_visible_to_app': True}}).encode())[0]['memory_events_cgroup_oom_kill'],
                         'NOT_VERIFIED')

    def test_inspection_does_not_start_vm_or_stress_host(self):
        with patch.object(acceptance, 'verify_host_toolchain', return_value={
                'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(acceptance, 'QemuWorker') as launched:
            out = acceptance.run({}, run_clean_vm=False)
            self.assertEqual(out['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
            self.assertEqual(out['security_attestation'], acceptance.ATTESTATION)
            self.assertFalse(out['student_code_executed'])
            self.assertEqual(out['checks']['memory_events_cgroup_oom_kill'], 'NOT_VERIFIED')
            self.assertEqual(acceptance.run({}, run_clean_vm=True)['status'],
                             'HIGH_MEMORY_VM_CONFIRMATION_REQUIRED')
            launched.assert_not_called()

    def test_clean_vm_opt_in_never_enables_network_or_student_code(self):
        with patch.object(acceptance, 'verify_host_toolchain', return_value={
                'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(acceptance, 'make_ssh_key', return_value=(Path('key'), 'ssh-ed25519 ABC')), \
             patch.object(acceptance, 'make_nocloud_iso', return_value=Path('seed')), \
             patch.object(acceptance, '_free_loopback_port', return_value=45678), \
             patch.object(acceptance, 'effective_vm_vcpus', return_value=1), \
             patch.object(acceptance, 'SSHControl') as sshclass, \
             patch.object(acceptance, 'QemuWorker') as vmclass, \
             patch.object(acceptance.threading, 'Timer') as timer:
            proc = SimpleNamespace(poll=lambda: 0)
            vm = vmclass.return_value
            vm.proc = proc
            vm.network_spec.return_value = 'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:45678-:22'
            # This mock is deliberately a VM that exited early. Even then,
            # the request must have been created in restricted clean-VM mode.
            result = acceptance.run({}, run_clean_vm=True, acknowledge_memory=True)
            self.assertEqual(result['status'], 'MEMORY_PROBE_INCONCLUSIVE_OR_FAILED')
            vmclass.assert_called_once()
            self.assertEqual(vmclass.call_args.kwargs['ram_mib'], 3584)
            self.assertEqual(vmclass.call_args.kwargs['cpus'], 1)
            self.assertEqual(vmclass.call_args.args[0]['workspace_disk_mib'], 96)
            self.assertTrue(acceptance.make_nocloud_iso.call_args.kwargs['app_cgroup'])
            self.assertNotIn('mysql_bootstrap', acceptance.make_nocloud_iso.call_args.kwargs)
            sshclass.assert_not_called()
            timer.assert_not_called()
            self.assertEqual(result['security_attestation'], acceptance.ATTESTATION)

    def test_real_root_owned_release_file_is_exclusive(self):
        if not hasattr(os, 'getuid') or not hasattr(os, 'fchmod'):
            self.skipTest('POSIX ownership test')
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            dest = home/'.projectiq-mem-release-0123456789abcdef'
            with patch.object(guest, 'CONTROLLER_HOME', home):
                guest._publish_release(dest, os.getuid())
                self.assertTrue(dest.is_file())
                self.assertEqual(dest.stat().st_mode & 0o777, 0o644)
                with self.assertRaises(ValueError):
                    guest._publish_release(dest, os.getuid())

    def test_clean_status_requires_all_eight_checks(self):
        self.assertEqual(len(guest.CHECKS), 8)
        self.assertEqual(acceptance._base_report()['checks']['guest_ram_headroom'],
                         'NOT_VERIFIED')
        self.assertEqual(acceptance._base_report()['security_attestation'],
                         'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')


if __name__ == '__main__':
    unittest.main()
