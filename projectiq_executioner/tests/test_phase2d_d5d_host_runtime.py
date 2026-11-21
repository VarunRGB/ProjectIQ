"""D5d Gate 4: strict independent Windows process/posture evidence tests.

Pure-policy unit tests run on Windows and Linux. Real Win32Process/Get-NetTCP
and a clean WHPX VM are acceptance tests on the operator's Windows machine.
"""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5d_host_runtime_acceptance as gate


class Gate4SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.binary = Path(self.tmp.name) / 'trusted-qemu.exe'
        self.binary.write_bytes(b'projectiq-trusted-test-binary')
        self.pin = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.args = ['qemu.exe', '-accel', 'whpx', '-netdev',
                     'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:51111-:22']
        self.snapshot = {
            'pid': 1234, 'parent_pid': 4321, 'owner_same': True,
            'image': str(self.binary), 'command_line': 'TRUSTED_PLACEHOLDER',
            'listeners': [{'address': '127.0.0.1', 'owner_pid': 1234, 'local_port': 51111}],
        }

    def classify(self, **changes):
        record = {**self.snapshot, **changes}
        return gate._validate_snapshot(
            record, process_pid=1234, controller_pid=4321, port=51111,
            binary=self.binary, expected=self.args, pin=self.pin,
            observed_argv=self.args,
        )

    def test_good_live_host_snapshot(self):
        result = self.classify()
        self.assertTrue(all(result.values()))
        self.assertEqual(set(result), {
            'live_windows_process_identity_and_parent',
            'live_qemu_executable_matches_operator_binary',
            'live_qemu_executable_digest_matches',
            'live_qemu_arguments_match_restricted_policy',
            'host_ssh_listener_owned_by_qemu_loopback_only',
        })

    def test_wrong_live_pid_is_not_verified(self):
        self.assertFalse(self.classify(pid=1235)['live_windows_process_identity_and_parent'])

    def test_wrong_parent_is_not_verified(self):
        self.assertFalse(self.classify(parent_pid=999)['live_windows_process_identity_and_parent'])

    def test_wrong_process_owner_is_not_verified(self):
        self.assertFalse(self.classify(owner_same=False)['live_windows_process_identity_and_parent'])

    def test_wrong_image_is_not_verified(self):
        value = self.classify(image=str(self.binary) + '.fake')
        self.assertFalse(value['live_qemu_executable_matches_operator_binary'])
        self.assertFalse(value['live_qemu_executable_digest_matches'])

    def test_changed_binary_is_not_verified(self):
        self.binary.write_bytes(b'ATTACKED')
        self.assertFalse(self.classify()['live_qemu_executable_digest_matches'])

    def test_missing_binary_is_not_verified(self):
        self.binary.unlink()
        self.assertFalse(self.classify()['live_qemu_executable_digest_matches'])

    def test_listener_on_wrong_port_is_not_verified(self):
        self.assertFalse(self.classify(listeners=[{'address': '127.0.0.1', 'owner_pid': 1234, 'local_port': 54321}])[
            'host_ssh_listener_owned_by_qemu_loopback_only'])

    def test_wrong_listener_owner_is_not_verified(self):
        self.assertFalse(self.classify(listeners=[{'address': '127.0.0.1', 'owner_pid': 4321, 'local_port': 51111}])[
            'host_ssh_listener_owned_by_qemu_loopback_only'])

    def test_wildcard_listener_is_not_verified(self):
        self.assertFalse(self.classify(listeners=[{'address': '0.0.0.0', 'owner_pid': 1234, 'local_port': 51111}])[
            'host_ssh_listener_owned_by_qemu_loopback_only'])

    def test_extra_ipv6_listener_is_not_verified(self):
        records = self.snapshot['listeners'] + [{'address': '::', 'owner_pid': 1234, 'local_port': 51111}]
        self.assertFalse(self.classify(listeners=records)[
            'host_ssh_listener_owned_by_qemu_loopback_only'])

    def test_no_listener_is_not_verified(self):
        self.assertFalse(self.classify(listeners=[])['host_ssh_listener_owned_by_qemu_loopback_only'])

    def test_empty_os_process_image_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(image='')

    def test_empty_command_line_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(command_line='')

    def test_wrong_kind_pid_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(pid=True)

    def test_wrong_kind_listener_owner_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(listeners=[{'address': '127.0.0.1', 'owner_pid': True, 'local_port': 51111}])

    def test_too_many_listener_records_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(listeners=self.snapshot['listeners'] * 13)

    def test_unexpected_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.classify(unknown='UNSAFE')

    def test_missing_fields_rejected(self):
        bad = self.snapshot.copy()
        bad.pop('owner_same')
        with self.assertRaises(ValueError):
            gate._validate_snapshot(bad, process_pid=1234, controller_pid=4321,
                                    port=51111, binary=self.binary, expected=self.args,
                                    pin=self.pin, observed_argv=self.args)

    def test_argument_extra_host_mount_rejected(self):
        altered = self.args + ['-virtfs', 'local,path=C:/Users,mount_tag=host']
        result = gate._validate_snapshot(self.snapshot, process_pid=1234,
            controller_pid=4321, port=51111, binary=self.binary,
            expected=self.args, pin=self.pin, observed_argv=altered)
        self.assertFalse(result['live_qemu_arguments_match_restricted_policy'])

    def test_argument_unrestricted_network_rejected(self):
        changed = self.args.copy()
        changed[-1] = changed[-1].replace('restrict=on,', '')
        result = gate._validate_snapshot(self.snapshot, process_pid=1234,
                controller_pid=4321, port=51111, binary=self.binary,
                expected=self.args, pin=self.pin, observed_argv=changed)
        self.assertFalse(result['live_qemu_arguments_match_restricted_policy'])

    def test_argument_extra_monitor_rejected(self):
        changed = self.args + ['-monitor', 'tcp:0.0.0.0:2222,server']
        result = gate._validate_snapshot(self.snapshot, process_pid=1234,
                controller_pid=4321, port=51111, binary=self.binary,
                expected=self.args, pin=self.pin, observed_argv=changed)
        self.assertFalse(result['live_qemu_arguments_match_restricted_policy'])

    def test_wrong_argv0_rejected(self):
        changed = ['other.exe', *self.args[1:]]
        result = gate._validate_snapshot(self.snapshot, process_pid=1234,
                controller_pid=4321, port=51111, binary=self.binary,
                expected=self.args, pin=self.pin, observed_argv=changed)
        self.assertFalse(result['live_qemu_arguments_match_restricted_policy'])


class Gate4PolicyTests(unittest.TestCase):
    def test_pinning_requires_exact_sha256_hex(self):
        self.assertTrue(gate._valid_pin('A' * 64))
        for value in ('', 'abc', 'g' * 64, 'f' * 65, 'f' * 63, None, True):
            with self.subTest(value=value):
                self.assertFalse(gate._valid_pin(value))

    def test_documented_status_stays_non_attested(self):
        r = gate._base()
        self.assertEqual(r['security_attestation'], 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertFalse(r['student_code_executed'])
        self.assertTrue(all(k in r['checks'] for k in gate.CHECKS))
        self.assertTrue(all(v == 'NOT_VERIFIED' for v in r['checks'].values()))

    def test_expected_argv_contains_only_one_network_adapter_and_two_disks(self):
        with patch.object(gate, '_tool', return_value='C:/QEMU/qemu-system-x86_64.exe'):
            tokens = gate._allowed_qemu_argv({}, Path('run'), Path('run/seed.iso'), 51111)
        self.assertEqual(tokens.count('-netdev'), 1)
        self.assertEqual(tokens.count('-drive'), 3)  # root, readonly seed, workspace
        self.assertEqual(tokens.count('-device'), 3)  # root blk, nic, workspace blk
        self.assertIn('user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:51111-:22', tokens)
        self.assertIn('none', tokens)
        self.assertNotIn('-virtfs', tokens)
        self.assertNotIn('-incoming', tokens)
        self.assertNotIn('-object', tokens)

    def test_operator_uefi_file_is_only_optional_argv(self):
        with patch.object(gate, '_tool', return_value='qemu.exe'):
            tokens = gate._allowed_qemu_argv({'uefi_firmware': 'some/firmware.fd'},
                                             Path('run'), Path('run/seed.iso'), 51111)
        self.assertEqual(tokens.count('-bios'), 1)

    def test_absent_windows_provider_blocks_without_launch(self):
        if os.name == 'nt':
            self.skipTest('Host-neutral Linux test; Windows requires real operator review')
        with patch.object(gate, 'make_ssh_key', side_effect=AssertionError('MUST NOT LAUNCH')):
            result = gate.run({}, 'f' * 64, run_clean_vm=True)
        self.assertEqual(result['status'], 'HOST_GATES_BLOCKED')
        self.assertEqual(result['cleanup'], 'NOT_STARTED')

    @unittest.skipUnless(os.name == 'nt', 'Windows CommandLineToArgvW is Windows-only')
    def test_real_windows_commandline_parser_roundtrip(self):
        tokens = ['C:\\Program Files\\QEMU\\qemu-system-x86_64.exe', '-drive',
                  'file=C:\\My VM\\root.qcow2,if=none,id=bootdisk', '-monitor', 'none']
        self.assertEqual(gate._windows_argv(subprocess.list2cmdline(tokens)), tokens)


if __name__ == '__main__':
    unittest.main()
