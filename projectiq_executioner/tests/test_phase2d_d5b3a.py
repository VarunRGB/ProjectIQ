"""D5b.3a per-UID resource ceilings, clean-VM harness and failure tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import guest_agent as guest
from projectiq_executioner.phase2.vm_worker import d5_resource_acceptance as accept


class FakeProc:
    def __init__(self):
        self.alive = True
    def poll(self):
        return None if self.alive else 0
    def kill(self):
        self.alive = False


class FakeQemu:
    fail_start = False
    fail_stop = False
    fail_network = False
    def __init__(self, config, workdir, port, *, ram_mib, cpus):
        self.config = config
        self.port = port
        self.proc = FakeProc()
        assert config['workspace_disk_mib'] == 0
        assert ram_mib == 1024
    def network_spec(self):
        return ('UNSAFE' if self.fail_network else
                f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22')
    def start(self, seed):
        if self.fail_start:
            raise OSError('simulated start')
    def stop(self):
        if self.fail_stop:
            raise OSError('simulated stop')
        self.proc.alive = False


class FakeSSH:
    checks = {k: 'PASS' for k in accept.CHECKS}
    status = 0
    def __init__(self, *args):
        pass
    def wait_ssh(self, worker, seconds):
        assert seconds == 90
    def ssh(self, *args, timeout, input):
        assert args == ('python3', '-')
        assert b'resource.setrlimit' in input
        assert b'__PROJECTIQ_LAUNCHER_EXPR__' not in input
        return SimpleNamespace(returncode=self.status, stdout=json.dumps({
            'schema_version': '0.1', 'checks': self.checks,
            'diagnostics': {'cgroup_v2_available': 'YES',
              'memory_controller_available': 'YES',
              'pids_controller_available': 'YES',
              'io_controller_available': 'NO',
              'app_probe_exit_code': 0}}).encode())


class FakeTimer:
    def __init__(self, *args):
        pass
    def start(self):
        pass
    def cancel(self):
        pass


class D5B3ATests(unittest.TestCase):
    def setUp(self):
        FakeQemu.fail_start = False
        FakeQemu.fail_stop = False
        FakeQemu.fail_network = False
        FakeSSH.status = 0
        FakeSSH.checks = {k: 'PASS' for k in accept.CHECKS}

    def test_resource_launcher_is_after_uid_drop_and_sanitized_env(self):
        command = guest._project_command(['/usr/bin/node', '/app/app.js'],
                                         {'PORT': '8765', 'NODE_OPTIONS': '--require bad'})
        self.assertEqual(command[:7], ['sudo', '-n', '-u', 'projectiq_run',
                                        '--', '/usr/bin/env', '-i'])
        self.assertIn('PORT=8765', command)
        self.assertNotIn('NODE_OPTIONS=--require bad', command)
        self.assertIn('/usr/bin/python3', command)
        self.assertIn('-I', command)
        self.assertEqual(command[-2:], ['/usr/bin/node', '/app/app.js'])
        self.assertEqual(command[command.index('-c') + 1], guest.APP_LIMIT_LAUNCHER)
        self.assertLess(command.index('projectiq_run'), command.index('-c'))

    def test_resource_policy_fails_closed_for_invalid_commands(self):
        for value in ([], ['bad\x00thing'], [None]):
            with self.subTest(value=value), self.assertRaises(guest.GuestFailure):
                guest._project_command(value)

    def test_every_project_process_uses_launcher(self):
        with patch.object(guest.subprocess, 'run') as process:
            process.return_value = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            guest._trusted_process(['/usr/bin/python3', 'main.py'], project_code=True)
        self.assertIn(guest.APP_LIMIT_LAUNCHER, process.call_args.args[0])
        with patch.object(guest.subprocess, 'Popen') as popen:
            guest._project_popen(['/usr/bin/php', '-v'], cwd=Path('/tmp'), env={}, log=object())
        self.assertIn(guest.APP_LIMIT_LAUNCHER, popen.call_args.args[0])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux resource rlimits only')
    def test_actual_resource_launcher_sets_hard_ceiling_before_exec(self):
        import resource
        script = '''import json,resource
print(json.dumps([list(resource.getrlimit(x)) for x in (resource.RLIMIT_NPROC, resource.RLIMIT_NOFILE, resource.RLIMIT_CORE)]))'''
        appcmd = ['/usr/bin/python3', '-I', '-c', guest.APP_LIMIT_LAUNCHER,
                  '/usr/bin/python3', '-I', '-c', script]
        if os.geteuid() == 0:
            appcmd = ['/usr/sbin/runuser', '-u', 'nobody', '--'] + appcmd
        p = subprocess.run(appcmd,
                           capture_output=True, timeout=8, check=False)
        self.assertEqual(p.returncode, 0, p.stderr.decode('utf-8', errors='replace'))
        result = json.loads(p.stdout)
        self.assertEqual(result, [[96, 96], [512, 512], [0, 0]])

    def test_source_contains_no_shell_or_root_privilege(self):
        code = guest.APP_LIMIT_LAUNCHER
        self.assertIn('os.execvpe', code)
        self.assertIn('resource.RLIMIT_NPROC', code)
        self.assertIn('resource.RLIMIT_NOFILE', code)
        self.assertIn('resource.RLIMIT_CORE', code)
        self.assertNotIn('shell=True', code)
        self.assertNotIn('chmod', code)
        self.assertNotIn('sudo', code)

    def test_rejects_bogus_guest_reports(self):
        good = {'schema_version': '0.1', 'checks': {k: 'PASS' for k in accept.CHECKS},
                'diagnostics': {n: 'YES' for n in ('cgroup_v2_available',
                   'memory_controller_available', 'pids_controller_available',
                   'io_controller_available')}}
        self.assertIsNotNone(accept._parse(json.dumps(good).encode()))
        for mutation in ('missing', 'badcheck', 'badcontroller', 'schema'):
            payload = json.loads(json.dumps(good))
            if mutation == 'missing': payload['checks'].pop(next(iter(accept.CHECKS)))
            elif mutation == 'badcheck': payload['checks'][accept.CHECKS[0]] = True
            elif mutation == 'badcontroller': payload['diagnostics']['io_controller_available'] = 'UNSAFE'
            else: payload['schema_version'] = '9.0'
            with self.subTest(case=mutation):
                self.assertIsNone(accept._parse(json.dumps(payload).encode()))
        self.assertIsNone(accept._parse(b'{}'))
        self.assertIsNone(accept._parse(b'\x00'))
        self.assertIsNone(accept._parse(b'x' * 5000))

    def _call(self, *, real=True, valid=True):
        host = {'status': ('TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'
                          if valid else 'BLOCKED')}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(accept, 'verify_host_toolchain', return_value=host), \
             patch.object(accept, 'QemuWorker', FakeQemu), \
             patch.object(accept, 'SSHControl', FakeSSH), \
             patch.object(accept, 'make_nocloud_iso', return_value=Path(tmp)/'seed.iso'), \
             patch.object(accept, 'make_ssh_key', return_value=(Path(tmp)/'key', 'ssh-ed25519 AAAATEST')), \
             patch.object(accept, 'effective_vm_vcpus', return_value=1), \
             patch.object(accept.threading, 'Timer', FakeTimer):
            return accept.run({}, run_clean_vm=real, work_root=Path(tmp))

    def test_static_inspection_is_nonexecuting(self):
        report = self._call(real=False)
        self.assertEqual(report['status'], 'INSPECTION_ONLY_NOT_ATTESTED')
        self.assertFalse(report['student_code_executed'])

    def test_failed_host_gates_never_boot(self):
        report = self._call(valid=False)
        self.assertEqual(report['status'], 'HOST_GATES_BLOCKED')

    def test_successful_observation_never_attests(self):
        report = self._call()
        self.assertEqual(report['status'], 'PER_UID_RESOURCE_CEILINGS_OBSERVED_NOT_ATTESTED')
        self.assertEqual(report['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
        self.assertEqual(report['security_attestation'], 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertIn('CGROUP_V2_IO_MAX_NOT_ENFORCED_OR_VERIFIED', report['remaining_security_gates'])

    def test_bad_guest_check_is_not_accepted(self):
        FakeSSH.checks = {k: 'PASS' for k in accept.CHECKS}
        FakeSSH.checks['app_nproc_hard_cap'] = 'FAIL'
        self.assertEqual(self._call()['status'], 'RESOURCE_CEILINGS_FAILED')

    def test_unverified_guest_check_is_not_success(self):
        FakeSSH.checks = {k: 'PASS' for k in accept.CHECKS}
        FakeSSH.checks['app_nproc_hard_cap'] = 'NOT_VERIFIED'
        self.assertEqual(self._call()['status'], 'RESOURCE_CEILINGS_NOT_VERIFIED')

    def test_guest_nonzero_is_inconclusive(self):
        FakeSSH.status = 1
        self.assertEqual(self._call()['status'], 'RESOURCE_PROBE_INCONCLUSIVE_OR_FAILED')

    def test_unsafe_qemu_network_rejected(self):
        FakeQemu.fail_network = True
        self.assertEqual(self._call()['status'], 'RESOURCE_PROBE_INCONCLUSIVE_OR_FAILED')

    def test_vm_launch_failure_cleanup_reported(self):
        FakeQemu.fail_start = True
        report = self._call()
        self.assertEqual(report['status'], 'RESOURCE_PROBE_INCONCLUSIVE_OR_FAILED')
        self.assertEqual(report['cleanup'], 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')

    def test_vm_stop_failure_prevents_success(self):
        FakeQemu.fail_stop = True
        report = self._call()
        self.assertEqual(report['status'], 'CLEANUP_FAILED')


if __name__ == '__main__':
    unittest.main()
