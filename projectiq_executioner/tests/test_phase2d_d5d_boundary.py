"""Gate 3 storage/network baseline: strict evidence and negative tests (offline)."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5d_boundary_acceptance as host
from projectiq_executioner.phase2.vm_worker import d5d_boundary_guest_probe as guest


def sample():
    return {
        'schema': 'd5d-boundary-1',
        'observations': {key: True for key in guest.FIELDS},
        'diagnostics': {
            'workspace_write': 'CREATED_CLEANED', 'root_tmp_write': 'DENIED',
            'var_tmp_write': 'DENIED', 'root_home_write': 'DENIED',
            'gateway_connect': 'DENIED', 'storage_guard_bootstrap': 'READY',
        },
    }


class GuestSchemaTests(unittest.TestCase):
    def parse(self, obj):
        return host._parse_guest(json.dumps(obj).encode('utf-8'))

    def test_complete_strict_evidence(self):
        self.assertEqual(self.parse(sample()), sample())

    def test_missing_or_extra_fields_fail(self):
        for change in ('missing_top', 'extra_top', 'missing_check', 'extra_check', 'missing_diag'):
            with self.subTest(change=change):
                x = sample()
                if change == 'missing_top':
                    del x['diagnostics']
                if change == 'extra_top':
                    x['debug'] = '/untrusted/source/path'
                if change == 'missing_check':
                    del x['observations']['controller_identity']
                if change == 'extra_check':
                    x['observations']['fake'] = True
                if change == 'missing_diag':
                    del x['diagnostics']['root_tmp_write']
                self.assertIsNone(self.parse(x))

    def test_non_boolean_claims_fail(self):
        x = sample()
        x['observations']['host_gateway_connection_denied'] = 1
        self.assertIsNone(self.parse(x))
        x = sample()
        x['observations']['controller_identity'] = 'PASS'
        self.assertIsNone(self.parse(x))

    def test_forged_write_or_network_claim_fails(self):
        for key, diagnostic, status in (
            ('app_can_write_workspace', 'workspace_write', 'DENIED'),
            ('app_root_tmp_write_denied', 'root_tmp_write', 'CREATED_CLEANED'),
            ('app_var_tmp_write_denied', 'var_tmp_write', 'ERROR'),
            ('app_root_home_write_denied', 'root_home_write', 'CREATED_CLEANED'),
            ('host_gateway_connection_denied', 'gateway_connect', 'REACHED'),
            ('trusted_storage_guard_bootstrap', 'storage_guard_bootstrap', 'MISSING')):
            with self.subTest(key=key):
                x = sample()
                x['diagnostics'][diagnostic] = status
                self.assertIsNone(self.parse(x))

    def test_guest_writes_forbidden_before_storage_guard_bootstrap(self):
        for state in ('NOT_RUN', 'MISSING', 'UNTRUSTED'):
            with self.subTest(state=state):
                record = sample()
                record['observations']['trusted_storage_guard_bootstrap'] = False
                record['diagnostics']['storage_guard_bootstrap'] = state
                for key in ('workspace_write', 'root_tmp_write', 'var_tmp_write', 'root_home_write'):
                    record['diagnostics'][key] = 'NOT_RUN'
                for key in ('app_can_write_workspace', 'app_root_tmp_write_denied',
                            'app_var_tmp_write_denied', 'app_root_home_write_denied'):
                    record['observations'][key] = False
                self.assertIsNotNone(self.parse(record))
                record['diagnostics']['root_tmp_write'] = 'CREATED_CLEANED'
                self.assertIsNone(self.parse(record))

    def test_denial_requires_verified_guest_nic(self):
        x = sample()
        x['observations']['guest_network_interface_present'] = False
        self.assertIsNone(self.parse(x))

    def test_workspace_claims_require_bootstrap_and_distinct_device(self):
        for prerequisite in ('trusted_workspace_bootstrap', 'bounded_workspace_disk',
                             'distinct_root_and_workspace_devices'):
            with self.subTest(prerequisite=prerequisite):
                x = sample()
                x['observations'][prerequisite] = False
                self.assertIsNone(self.parse(x))

    def test_unrecognized_observations_do_not_pass(self):
        for obj in ([], None, {}, {'schema':'x'}, 'PASS'):
            with self.subTest(obj=obj):
                self.assertIsNone(self.parse(obj))
        self.assertIsNone(host._parse_guest(b'not json'))
        self.assertIsNone(host._parse_guest(b'0' * 2501))

    def test_storage_gap_requires_affirmative_successful_write(self):
        x = host._base()
        x['checks'] = {key: 'PASS' for key in host.ALL_CHECKS}
        host._classify(x)
        self.assertEqual(x['status'], host.STATUS_PASS)
        x['checks']['app_root_tmp_write_denied'] = 'NOT_VERIFIED'
        x['guest_diagnostics'] = {'root_tmp_write': 'ERROR', 'var_tmp_write': 'DENIED',
                                  'root_home_write': 'DENIED'}
        host._classify(x)
        self.assertEqual(x['status'], host.STATUS_INCOMPLETE)
        x['guest_diagnostics']['root_tmp_write'] = 'CREATED_CLEANED'
        host._classify(x)
        self.assertEqual(x['status'], host.STATUS_GAP)

    def test_attestation_and_student_execution_never_claimed(self):
        r = host._base()
        self.assertEqual(r['security_attestation'], 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertIs(r['student_code_executed'], False)
        self.assertIn('ROOT_QCOW2_SIZE_AND_ALL_GUEST_WRITES_NOT_GLOBALLY_ATTESTED', r['limitations'])

    def test_inspection_does_not_create_vm(self):
        with patch.object(host, 'verify_host_toolchain', return_value={
            'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}), \
             patch.object(host, 'QemuWorker', side_effect=AssertionError('unexpected VM')):
            r = host.run({}, run_clean_vm=False)
        self.assertEqual(r['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
        self.assertEqual(r['execution'], 'NOT_PERFORMED')


class GuestProbeTests(unittest.TestCase):
    def test_marker_rejects_symlink_and_untrusted_owner(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td)/'marker'
            m.write_text('')
            with patch.object(guest.Path, 'lstat', return_value=SimpleNamespace(
                    st_mode=0o100644, st_uid=0)):
                self.assertTrue(guest._marker_trusted(m))
            for st in (SimpleNamespace(st_mode=0o120777, st_uid=0),
                       SimpleNamespace(st_mode=0o100666, st_uid=0),
                       SimpleNamespace(st_mode=0o100644, st_uid=1000)):
                with patch.object(guest.Path, 'lstat', return_value=st):
                    self.assertFalse(guest._marker_trusted(m))

    def test_guard_wait_accepts_only_root_owned_regular_marker(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / 'ready'
            self.assertEqual(guest._guard_bootstrap_state(-1, marker=marker), 'MISSING')
            marker.write_text('')
            with patch.object(guest.Path, 'lstat', return_value=SimpleNamespace(
                    st_mode=0o100644, st_uid=0)):
                self.assertEqual(guest._guard_bootstrap_state(-1, marker=marker), 'READY')
            for mode, uid in ((0o120777, 0), (0o100666, 0), (0o100644, 1001)):
                with patch.object(guest.Path, 'lstat', return_value=SimpleNamespace(
                        st_mode=mode, st_uid=uid)):
                    self.assertEqual(guest._guard_bootstrap_state(-1, marker=marker),
                                     'UNTRUSTED')

    def test_guard_wait_handles_later_marker_creation(self):
        ready = SimpleNamespace(st_mode=0o100644, st_uid=0)
        with patch.object(guest.Path, 'lstat', side_effect=[FileNotFoundError, ready]) as probe, \
             patch.object(guest.time, 'monotonic', return_value=0), \
             patch.object(guest.time, 'sleep') as sleep:
            self.assertEqual(guest._guard_bootstrap_state(5), 'READY')
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once()

    def test_probe_never_runs_app_writes_if_guard_unready(self):
        with patch.object(guest.sys, 'platform', 'linux'), \
             patch.object(guest, 'pwd', create=True) as fake_pwd, \
             patch.object(guest.os, 'getuid', return_value=1001, create=True), \
             patch.object(guest.os, 'geteuid', return_value=1001, create=True), \
             patch.object(guest, '_marker_trusted', return_value=True), \
             patch.object(guest, '_workspace_observations', return_value=(True,True,True)), \
             patch.object(guest, '_guard_bootstrap_state', return_value='MISSING'), \
             patch.object(guest, '_write_observations') as write:
            fake_pwd.getpwnam.side_effect = [SimpleNamespace(pw_uid=1001),
                                            SimpleNamespace(pw_uid=1002)]
            report = guest._observations(32445)
            write.assert_not_called()
            self.assertFalse(report['observations']['trusted_storage_guard_bootstrap'])
            self.assertEqual(report['diagnostics']['storage_guard_bootstrap'], 'MISSING')

    def test_write_parser_fails_closed_on_invalid_or_partial_output(self):
        valid = b'workspace=CREATED_CLEANED\nroot_tmp=DENIED\nvar_tmp=DENIED\nroot_home=DENIED\n'
        def result(stdout, code=0):
            return SimpleNamespace(stdout=stdout, returncode=code)
        with patch.object(guest.subprocess, 'run', return_value=result(valid)) as run:
            r = guest._write_observations()
            self.assertEqual(r['workspace'], 'CREATED_CLEANED')
            self.assertEqual(r['root_tmp'], 'DENIED')
            args = run.call_args.args[0]
            self.assertEqual(args[:6], ['/usr/bin/sudo','-n','-u','projectiq_run','--','/usr/bin/python3'])
        for output, code in ((b'workspace=CREATED_CLEANED\n', 0),
                             (valid + b'bad=CREATED_CLEANED\n', 0),
                             (b'workspace=CREATED_CLEANED\nroot_tmp=SUCCESS\nvar_tmp=DENIED\n',0),
                             (valid, 1), (b'x' * 241, 0)):
            with self.subTest(output=output[:40], code=code), \
                 patch.object(guest.subprocess, 'run', return_value=result(output, code)):
                self.assertEqual(guest._write_observations()['workspace'], 'NOT_RUN')

    def test_workspace_bootstrap_missing_fails_closed(self):
        with patch.object(guest, '_marker_trusted', return_value=False):
            self.assertEqual(guest._workspace_observations(), (False, False, False))

    def test_guest_network_interface_does_not_require_default_route(self):
        with patch.object(guest.socket, 'if_nameindex', return_value=[(1, 'lo'), (2, 'enp0s3')]), \
             patch.object(guest.Path, 'read_text', return_value='up\n'):
            self.assertTrue(guest._network_interface())
        with patch.object(guest.socket, 'if_nameindex', return_value=[(1, 'lo')]):
            self.assertFalse(guest._network_interface())
        with patch.object(guest.socket, 'if_nameindex', return_value=[(2, 'eth0')]), \
             patch.object(guest.Path, 'read_text', return_value='down\n'):
            self.assertFalse(guest._network_interface())

    def test_gateway_only_tries_fixed_slirp_host(self):
        with patch.object(guest.socket, 'create_connection', side_effect=TimeoutError('blocked')) as connect:
            self.assertEqual(guest._gateway_connect(32445), 'DENIED')
            self.assertEqual(connect.call_args.args[0], ('10.0.2.2', 32445))
        with patch.object(guest.socket, 'create_connection', side_effect=PermissionError(errno.EACCES,'no')):
            self.assertEqual(guest._gateway_connect(32445), 'DENIED')
        with patch.object(guest.socket, 'create_connection') as connect:
            self.assertEqual(guest._gateway_connect(80), 'NOT_RUN')
            connect.assert_not_called()

    def test_gateway_success_is_security_gap(self):
        conn = SimpleNamespace(
            __enter__=lambda self: self, __exit__=lambda self,*a: None,
            sendall=lambda msg: None)
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def sendall(self, msg): self.data = msg
        with patch.object(guest.socket, 'create_connection', return_value=Connection()):
            self.assertEqual(guest._gateway_connect(32445), 'REACHED')

    def test_real_host_listener_detects_direct_local_connect(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(2)
            listener.settimeout(0.1)
            stop = threading.Event()
            contacted = threading.Event()
            thread = threading.Thread(target=host._sentinel_loop, args=(listener,stop,contacted),daemon=True)
            thread.start()
            with socket.create_connection(('127.0.0.1', listener.getsockname()[1])) as client:
                client.sendall(b'local-control')
            self.assertTrue(contacted.wait(2))
            stop.set()
            thread.join(1)

    def test_probe_is_inert_on_windows(self):
        with patch.object(guest.sys, 'platform', 'win32'):
            x = guest._observations(32445)
        self.assertEqual(x['diagnostics']['gateway_connect'], 'NOT_RUN')
        self.assertFalse(any(x['observations'].values()))

    def test_guest_source_contains_no_external_internet_probe(self):
        from inspect import getsource
        body = getsource(guest._gateway_connect)
        self.assertIn('10.0.2.2', body)
        self.assertNotIn('8.8.8.8', body)
        self.assertNotIn('1.1.1.1', body)


if __name__ == '__main__':
    unittest.main()
