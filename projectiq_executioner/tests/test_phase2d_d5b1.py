"""D5b.1 privilege boundary contract tests (no QEMU VM is launched here).

These mock only infrastructure; real Linux/WHPX acceptance is required separately.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import guest_agent as guest
from projectiq_executioner.phase2.vm_worker import d5_acceptance as d5
from projectiq_executioner.phase2.vm_worker import d5_guest_probe
from projectiq_executioner.phase2.vm_worker import orchestrator
from projectiq_executioner.phase2.vm_worker.qemu import (
    MYSQL_BOOTSTRAP_SCRIPT, WorkerFailure, make_nocloud_iso,
)


class FakeISO:
    def __init__(self):
        self.parts = {}
    def new(self, **kwargs):
        pass
    def add_fp(self, handle, length, **kwargs):
        self.parts[kwargs['rr_name']] = handle.read()
    def write(self, path):
        Path(path).write_bytes(b'operator-owned-fake-iso')
    def close(self):
        pass


class D5B1PrivilegeTests(unittest.TestCase):
    def _seed(self, *, template=False, mysql=False):
        iso = FakeISO()
        fake_pycdlib = types.SimpleNamespace(PyCdlib=lambda: iso)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'pycdlib': fake_pycdlib}):
            make_nocloud_iso(Path(tmp), 'ssh-ed25519 AAAATEST TEST', 'projectiq-d5b1',
                             template_setup=template, mysql_bootstrap=mysql)
        return iso.parts['user-data'].decode('utf-8')

    def test_runtime_seed_has_separate_runner_not_mysql_sudo(self):
        text = self._seed()
        self.assertIn('name: projectiq_run', text)
        self.assertIn('groups: [projectiq_exec]', text)
        self.assertIn('sudo: false', text)
        self.assertIn('ALL=(projectiq_run) NOPASSWD: ALL', text)
        self.assertNotIn('NOPASSWD: /usr/bin/mysql', text)
        self.assertNotIn('ALL=(ALL) NOPASSWD:', text)
        self.assertNotIn('runcmd:', text)

    def test_bootstrap_only_in_explicit_php_seed(self):
        ordinary = self._seed()
        mysql = self._seed(mysql=True)
        self.assertNotIn('write_files:', ordinary)
        self.assertIn('runcmd:', mysql)
        self.assertIn('/usr/local/sbin/projectiq-mysql-bootstrap', mysql)
        self.assertIn('owner: root:root', mysql)
        self.assertIn("permissions: '0700'", mysql)
        self.assertNotIn('snapshot.zip', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertNotIn('guest_agent.py', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertIn('CREATE DATABASE IF NOT EXISTS', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertIn('GRANT ALL ON', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertNotIn('GRANT ALL ON *.*', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertIn('openssl rand -hex 24', MYSQL_BOOTSTRAP_SCRIPT)
        self.assertIn('chmod 0600', MYSQL_BOOTSTRAP_SCRIPT)
        with self.assertRaises(WorkerFailure):
            # This mode combination must never silently run a privileged
            # initialization step in the template builder.
            self._seed(template=True, mysql=True)

    def test_template_setup_still_has_trusted_setup_sudo(self):
        text = self._seed(template=True)
        self.assertIn('ALL=(ALL) NOPASSWD:ALL', text)
        self.assertNotIn('name: projectiq_run', text)

    def test_app_command_switches_uid_and_sanitizes_environment(self):
        cmd = guest._project_command(['/usr/bin/node', '/app/server.js'], {
            'PORT': '8765', 'HOST': '127.0.0.1',
            'NODE_OPTIONS': '--require /tmp/evil.js',
            'HOME': '/home/projectiq',
            'NPM_CONFIG_IGNORE_SCRIPTS': 'true',
            'PROJECTIQ_DB_PASSWORD': 'guest-scoped-secret',
            'AWS_SECRET_ACCESS_KEY': 'do-not-expose',
        })
        self.assertEqual(cmd[:7], ['sudo', '-n', '-u', 'projectiq_run', '--',
                                    '/usr/bin/env', '-i'])
        self.assertIn('HOME=/home/projectiq_run', cmd)
        self.assertIn('NPM_CONFIG_IGNORE_SCRIPTS=true', cmd)
        self.assertIn('PROJECTIQ_DB_PASSWORD=guest-scoped-secret', cmd)
        self.assertNotIn('NODE_OPTIONS=--require /tmp/evil.js', cmd)
        self.assertNotIn('AWS_SECRET_ACCESS_KEY=do-not-expose', cmd)
        self.assertNotIn('HOME=/home/projectiq', cmd)
        self.assertEqual(cmd[-2:], ['/usr/bin/node', '/app/server.js'])

    def test_project_process_always_uses_runas(self):
        with patch.object(guest.subprocess, 'run') as process:
            process.return_value = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            guest._trusted_process(['/usr/bin/node', 'app.js'], env={'PORT': '8765'},
                                   project_code=True)
            args = process.call_args.args[0]
        self.assertEqual(args[:5], ['sudo', '-n', '-u', guest.APP_USER, '--'])
        self.assertEqual(args[-2:], ['/usr/bin/node', 'app.js'])

    def test_trusted_helper_may_use_owned_tools_without_shell(self):
        with patch.object(guest.subprocess, 'run') as process:
            process.return_value = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            guest._trusted_process(['/usr/bin/node', '--version'])
            args = process.call_args.args[0]
        self.assertEqual(args, ['/usr/bin/node', '--version'])

    def test_server_process_uses_runner_and_no_shell(self):
        with patch.object(guest.subprocess, 'Popen') as popen:
            guest._project_popen(['/usr/bin/php', '-S', '127.0.0.1:8765'],
                                 cwd=Path('/fake/code'), env={}, log=object())
        args = popen.call_args.args[0]
        self.assertEqual(args[:5], ['sudo', '-n', '-u', guest.APP_USER, '--'])
        self.assertEqual(args[-3:], ['/usr/bin/php', '-S', '127.0.0.1:8765'])
        self.assertNotIn('shell', popen.call_args.kwargs)

    def test_root_and_app_sudo_gate_fail_closed(self):
        names = {'projectiq': SimpleNamespace(pw_uid=1000),
                 'projectiq_run': SimpleNamespace(pw_uid=1001)}
        group = SimpleNamespace(gr_gid=1022, gr_mem=['projectiq', 'projectiq_run'])
        result = [SimpleNamespace(returncode=0, stdout=b'1001\n'),
                  SimpleNamespace(returncode=1, stdout=b''),
                  SimpleNamespace(returncode=1, stdout=b'')]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(guest.os, 'geteuid', return_value=1000, create=True), \
             patch.object(guest, 'pwd', SimpleNamespace(getpwnam=names.__getitem__)), \
             patch.object(guest, 'grp', SimpleNamespace(getgrnam=lambda _: group)), \
             patch.object(guest, '_trusted_process', side_effect=result):
            guest._verify_execution_identity(Path(tmp))
        # Root sudo or app-to-root sudo success is a hard blocker.
        for failed in (1, 2):
            results = [SimpleNamespace(returncode=0, stdout=b'1001\n'),
                       SimpleNamespace(returncode=1, stdout=b''),
                       SimpleNamespace(returncode=1, stdout=b'')]
            results[failed] = SimpleNamespace(returncode=0, stdout=b'0\n')
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(guest.os, 'geteuid', return_value=1000, create=True), \
                 patch.object(guest, 'pwd', SimpleNamespace(getpwnam=names.__getitem__)), \
                 patch.object(guest, 'grp', SimpleNamespace(getgrnam=lambda _: group)), \
                 patch.object(guest, '_trusted_process', side_effect=results):
                with self.assertRaisesRegex(guest.GuestFailure, 'PRIVILEGE_BOUNDARY'):
                    guest._verify_execution_identity(Path(tmp))

    def test_guest_observation_schema_requires_app_boundary(self):
        d = {'schema_version': '0.1', 'checks': {
            **{k: 'PASS' for k in d5.GUEST_CHECKS},
            'mysql_sudo_exception': 'NOT_OBSERVED'}}
        values, error = d5._parse_guest(json.dumps(d).encode())
        self.assertFalse(error)
        self.assertEqual(values['app_root_sudo_denied'], 'PASS')
        d['checks'].pop('app_uid_distinct_from_controller')
        _, error = d5._parse_guest(json.dumps(d).encode())
        self.assertEqual(error, 'INVALID_GUEST_REPORT')

    def test_non_attestation_even_all_checks_pass(self):
        with patch.object(d5, 'verify_host_toolchain') as tools:
            tools.return_value = {'status': 'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED',
                                  'toolchain': {k: 'x' for k in ('qemu-system-x86_64',
                                      'qemu-img', 'ssh', 'ssh-keygen')},
                                  'template_sha256_matches': True,
                                  'uefi_firmware_sha256_matches': None}
            report = d5.inspect_and_probe({}, run_clean_vm=False)
        self.assertEqual(report['security_attestation'], 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertFalse(report['student_code_executed'])

    def test_separate_vm_mysql_provisioning_is_opt_in(self):
        import inspect
        source = inspect.getsource(orchestrator.execute_vm_candidate)
        self.assertIn("mysql_bootstrap=(candidate['adapter'] == 'php_mysql_v1')", source)


if __name__ == '__main__':
    unittest.main()
