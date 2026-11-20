"""D5a acceptance-contract tests (no real VM launched by the unit suite)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_acceptance as d5
from projectiq_executioner.phase2.vm_worker import d5_guest_probe as guest
from projectiq_executioner.phase2.vm_worker.qemu import QemuWorker


GUEST_VALUES = {
    'non_root_user': 'PASS',
    'root_sudo_denied': 'PASS',
    'no_host_shared_mounts_observed': 'PASS',
    'external_ipv4_tcp_attempt': 'PASS',
    'external_ipv6_tcp_attempt': 'PASS',
    'external_dns_attempt': 'PASS',
    'mysql_sudo_exception': 'PRESENT',
    'app_uid_distinct_from_controller': 'PASS',
    'controller_runas_app': 'PASS',
    'app_root_sudo_denied': 'PASS',
}


def toolchain(valid=True):
    return {
        'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED' if valid else 'BLOCKED',
        'toolchain': {'qemu-system-x86_64': '/fake/qemu', 'qemu-img': '/fake/qemu-img',
                      'ssh': '/fake/ssh', 'ssh-keygen': '/fake/ssh-keygen'} if valid else
                     {'ssh': None},
        'template_sha256_matches': valid,
        'uefi_firmware_sha256_matches': None,
    }


class StubWorker:
    instances = []
    fail_stop = False
    fail_start = False
    def __init__(self, config, workdir, port, *, ram_mib, cpus):
        self.workdir = Path(workdir)
        self.port = port
        self.ram_mib = ram_mib
        self.cpus = cpus
        self._terminated = False
        self.proc = SimpleNamespace(poll=lambda: 0 if self._terminated else None,
                                    kill=lambda: setattr(self, '_terminated', True))
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def network_spec(self):
        return f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22'

    def start(self, seed):
        if self.fail_start:
            raise RuntimeError('simulated VM launch failure')
        self.started = True
        assert Path(seed).is_file()

    def stop(self):
        if self.fail_stop:
            raise RuntimeError('simulated VM stop failure')
        self.stopped = True
        self._terminated = True


class StubSSH:
    response = GUEST_VALUES
    fail_code = 0
    def __init__(self, config, workdir, port, key):
        self.key = key

    def wait_ssh(self, worker, seconds):
        assert worker.started

    def ssh(self, *cmd, input, timeout):
        assert cmd == ('python3', '-')
        assert b'def main()' in input
        assert b'snapshot' not in input.lower()
        return SimpleNamespace(returncode=self.fail_code,
            stdout=json.dumps({'schema_version': '0.1', 'checks': self.response}).encode())


class NoTimer:
    def __init__(self, *a):
        self.daemon = False
    def start(self):
        pass
    def cancel(self):
        pass


class D5ATests(unittest.TestCase):
    def setUp(self):
        StubWorker.instances.clear()
        StubWorker.fail_start = False
        StubWorker.fail_stop = False
        StubSSH.response = dict(GUEST_VALUES)
        StubSSH.fail_code = 0

    def test_d5_report_never_claims_attestation(self):
        with patch.object(d5, 'verify_host_toolchain', return_value=toolchain()):
            report = d5.inspect_and_probe({}, run_clean_vm=False)
        self.assertEqual(report['security_attestation'], d5.ATTESTATION)
        self.assertFalse(report['student_code_executed'])
        self.assertEqual(report['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
        self.assertEqual(report['cleanup'], 'NOT_STARTED')
        self.assertIn('DYNAMIC_DISK_WRITE_QUOTA_NOT_ENFORCED', report['remaining_security_gates'])

    def test_inspection_only_does_not_launch_qemu(self):
        with patch.object(d5, 'verify_host_toolchain', return_value=toolchain()), \
             patch.object(d5.QemuWorker, 'start', side_effect=AssertionError('VM must not start')):
            d5.inspect_and_probe({}, run_clean_vm=False)

    def test_disallow_networked_modes_in_d5(self):
        runtime = QemuWorker({}, Path('.'), 48301)
        self.assertEqual(runtime.network_spec(),
                         'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:48301-:22')
        for flag in ('trusted_template_setup', 'trusted_dependency_fetch'):
            networked = QemuWorker({}, Path('.'), 48301, **{flag: True})
            self.assertNotIn('restrict=on', networked.network_spec())

    def test_host_gate_denies_live_boot_on_hash_or_tool_failure(self):
        with patch.object(d5, 'verify_host_toolchain', return_value=toolchain(False)), \
             patch.object(d5, '_live_probe', side_effect=AssertionError('No live VM allowed')):
            report = d5.inspect_and_probe({}, run_clean_vm=True)
        self.assertEqual(report['status'], 'HOST_GATES_BLOCKED')
        with patch.object(d5, 'verify_host_toolchain', return_value=toolchain(False)):
            report = d5.inspect_and_probe({}, run_clean_vm=False)
        self.assertEqual(report['status'], 'HOST_GATES_BLOCKED')

    def test_guest_json_accepts_only_fixed_schema_and_enums(self):
        value, err = d5._parse_guest(json.dumps({'schema_version': '0.1',
                                               'checks': GUEST_VALUES}).encode())
        self.assertFalse(err)
        self.assertEqual(value, GUEST_VALUES)
        for data in [b'{', b'[]', b'{"schema_version":"0.1","checks":{}}',
                     b'{}', b'0' * 10_000]:
            self.assertTrue(d5._parse_guest(data)[1])

    def test_guest_does_not_trust_unexpected_fields(self):
        values = dict(GUEST_VALUES, unknown='PASS')
        self.assertTrue(d5._parse_guest(json.dumps({'schema_version':'0.1',
                                                    'checks': values}).encode())[1])
        values = dict(GUEST_VALUES, external_ipv4_tcp_attempt='SUCCESS')
        self.assertTrue(d5._parse_guest(json.dumps({'schema_version':'0.1',
                                                    'checks': values}).encode())[1])

    def _live(self, *, work_root):
        def make_key(_, work):
            key = work / 'key'
            key.write_bytes(b'key')
            return key, 'ssh-ed25519 example'
        def seed(_, work, pub, ident):
            iso = work / 'seed.iso'
            iso.write_bytes(b'iso')
            return iso
        with patch.object(d5, 'QemuWorker', StubWorker), \
             patch.object(d5, 'SSHControl', StubSSH), \
             patch.object(d5, 'make_ssh_key', make_key), \
             patch.object(d5, 'make_nocloud_iso', lambda work, pub, ident: seed(None,work,pub,ident)), \
             patch.object(d5, '_free_loopback_port', return_value=40999), \
             patch.object(d5, 'threading', SimpleNamespace(Timer=NoTimer, Event=__import__('threading').Event)):
            report = d5._init_report()
            d5._live_probe({}, work_root, report)
        return report

    def test_live_probe_uses_clean_vm_and_removes_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._live(work_root=Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(report['status'], 'RESTRICTED_SUDO_EXCEPTION_REQUIRES_REVIEW')
        self.assertEqual(report['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
        self.assertTrue(StubWorker.instances[-1].stopped)
        self.assertTrue(StubWorker.instances[-1].started)
        self.assertEqual(StubWorker.instances[-1].ram_mib, 1024)
        self.assertFalse(report['student_code_executed'])

    def test_cleared_mysql_sudo_exception_makes_clean_checks_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubSSH.response = dict(GUEST_VALUES, mysql_sudo_exception='NOT_OBSERVED')
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'CLEAN_VM_OBSERVATIONS_PASSED_NOT_ATTESTED')

    def test_outbound_connect_is_reported_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubSSH.response = dict(GUEST_VALUES, external_ipv4_tcp_attempt='FAIL')
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'SECURITY_OBSERVATION_FAILED')
        self.assertEqual(report['checks']['external_ipv4_tcp_attempt'], 'FAIL')
        self.assertEqual(report['security_attestation'], d5.ATTESTATION)

    def test_nonroot_failure_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubSSH.response = dict(GUEST_VALUES, non_root_user='FAIL')
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'SECURITY_OBSERVATION_FAILED')

    def test_missing_ipv6_is_partial_not_full_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubSSH.response = dict(GUEST_VALUES, external_ipv6_tcp_attempt='NOT_VERIFIED',
                                    mysql_sudo_exception='NOT_OBSERVED')
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'OBSERVATIONS_PARTIAL_NOT_ATTESTED')

    def test_invalid_or_failing_guest_report_still_stops_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubSSH.response = {'some': 'invalid'}
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'ACCEPTANCE_PROBE_INCONCLUSIVE_OR_FAILED')
        self.assertEqual(report['error_code'], 'INVALID_GUEST_REPORT')
        self.assertTrue(StubWorker.instances[-1].stopped)


    def test_vm_start_failure_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubWorker.fail_start = True
            report = self._live(work_root=Path(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(report['status'], 'ACCEPTANCE_PROBE_INCONCLUSIVE_OR_FAILED')
        self.assertEqual(report['error_code'], 'VM_PROBE_RUNTIMEERROR')
        self.assertEqual(report['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')

    def test_vm_stop_failure_cannot_be_silently_reported_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            StubWorker.fail_stop = True
            report = self._live(work_root=Path(tmp))
        self.assertEqual(report['status'], 'CLEANUP_FAILED')
        self.assertEqual(report['cleanup'], 'CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED')

    def test_vm_network_config_mismatch_prevents_start(self):
        original = StubWorker.network_spec
        try:
            StubWorker.network_spec = lambda self: 'user,id=net0,hostfwd=tcp:127.0.0.1:40999-:22'
            with tempfile.TemporaryDirectory() as tmp:
                report = self._live(work_root=Path(tmp))
            self.assertEqual(report['status'], 'ACCEPTANCE_PROBE_INCONCLUSIVE_OR_FAILED')
            self.assertEqual(report['error_code'], 'RUNTIME_NETWORK_CONFIG_UNSAFE')
            self.assertFalse(StubWorker.instances[-1].started)
        finally:
            StubWorker.network_spec = original

    def test_shared_mount_detection_flags_virtiofs(self):
        import io
        with patch('builtins.open', return_value=io.StringIO('host /home/projectiq/shared virtiofs rw 0 0')):
            self.assertEqual(guest._shared_mounts(), 'FAIL')

    def test_write_report_is_json_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'out' / 'd5.json'
            d5._report_path(p, d5._init_report())
            data = json.loads(p.read_text('utf-8'))
            self.assertEqual(data['security_attestation'], d5.ATTESTATION)
            self.assertFalse(data['student_code_executed'])

    def test_no_adapter_names_in_d5_probe(self):
        src = Path(d5.__file__).read_text('utf-8')
        for adapter in ('node_vite_offline_v1', 'node_fullstack_offline_v1',
                        'python_web_offline_v1', 'php_mysql_v1'):
            self.assertNotIn(adapter, src)

    def test_guest_root_and_sudo_failure(self):
        with patch.object(guest.os, 'geteuid', return_value=0, create=True), \
             patch.object(guest, '_root_sudo', return_value='FAIL'), \
             patch.object(guest, '_shared_mounts', return_value='PASS'), \
             patch.object(guest, '_tcp', return_value='PASS'), \
             patch.object(guest, '_dns', return_value='PASS'), \
             patch.object(guest, '_mysql_sudo', return_value='PRESENT'), \
             patch('builtins.print') as printer:
            self.assertEqual(guest.main(), 0)
        values = json.loads(printer.call_args.args[0])['checks']
        self.assertEqual(values['non_root_user'], 'FAIL')
        self.assertEqual(values['root_sudo_denied'], 'FAIL')
        self.assertEqual(values['mysql_sudo_exception'], 'PRESENT')

    def test_dns_success_is_denial_failure(self):
        with patch.object(guest.socket, 'getaddrinfo', return_value=[('x',)]):
            self.assertEqual(guest._dns(), 'FAIL')

    def test_tcp_connection_success_is_denial_failure(self):
        class Sock:
            def __enter__(self): return self
            def __exit__(self,*x): return False
            def settimeout(self,t): pass
            def connect(self,a): pass
        with patch.object(guest.socket,'socket',return_value=Sock()):
            self.assertEqual(guest._tcp(guest.socket.AF_INET,('1.1.1.1',443)), 'FAIL')


if __name__ == '__main__':
    unittest.main()
