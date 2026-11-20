"""D5/B3b clean-VM systemd/cgroup pilot tests (host-side, never run source)."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_cgroup_acceptance as accept
from projectiq_executioner.phase2.vm_worker import d5_cgroup_guest_probe as probe
from projectiq_executioner.phase2.vm_worker import qemu


class FakeISO:
    def __init__(self):
        self.parts = {}
    def new(self, **kw): pass
    def add_fp(self, fp, length, **kw):
        self.parts[kw['rr_name']] = fp.read()
    def write(self, path): Path(path).write_bytes(b'ISO')
    def close(self): pass


class FakeProc:
    def __init__(self): self.done = False
    def poll(self): return 0 if self.done else None
    def kill(self): self.done = True


class FakeWorker:
    fail_start = False
    fail_stop = False
    def __init__(self, config, workdir, port, *, ram_mib, cpus):
        assert config['workspace_disk_mib'] == accept.PROBE_MIB
        assert ram_mib == 1536
        self.port = port
        self.proc = FakeProc()
    def network_spec(self):
        return f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22'
    def start(self, seed):
        if self.fail_start: raise OSError('simulated start failure')
    def stop(self):
        if self.fail_stop: raise OSError('simulated cleanup failure')
        self.proc.done = True


class FakeSSH:
    response = None
    def __init__(self, *args): pass
    def wait_ssh(self, worker, seconds): assert seconds == 95
    def ssh(self, *args, input, timeout):
        assert args == ('python3','-')
        assert b'projectiq-probe.slice' in input
        assert b'systemd-run' in input
        payload = self.response if self.response is not None else {
            'schema_version':'0.1','checks':{k:'PASS' for k in accept.CHECKS},
            'diagnostics':{'scope_exit_code':0}}
        return SimpleNamespace(returncode=0,stdout=json.dumps(payload).encode(),stderr=b'')


class FakeTimer:
    def __init__(self, *args): pass
    def start(self): pass
    def cancel(self): pass


class B3bTests(unittest.TestCase):
    def setUp(self):
        FakeSSH.response = None
        FakeWorker.fail_start = False
        FakeWorker.fail_stop = False

    def test_cgroup_bootstrap_is_root_owned_and_does_not_grant_root_sudo(self):
        iso = FakeISO()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {
                'pycdlib':types.SimpleNamespace(PyCdlib=lambda:iso)}):
            qemu.make_nocloud_iso(Path(tmp),'ssh-ed25519 AAAATEST comment','d5b3b-check',
                                   workspace_disk_mib=96,cgroup_probe=True)
        config = iso.parts['user-data'].decode()
        assert 'projectiq-cgroup-probe-bootstrap' in config
        self.assertIn("permissions: '0700'",config)
        self.assertIn('ALL=(projectiq_run) NOPASSWD: ALL',config)
        self.assertNotIn('NOPASSWD:ALL',config)
        script = qemu.CGROUP_BOOTSTRAP_SCRIPT
        self.assertIn('loginctl enable-linger projectiq',script)
        self.assertIn('Delegate=cpu cpuset io memory pids',script)
        self.assertIn('MemoryMax=192M',script)
        self.assertIn('TasksMax=32',script)
        self.assertIn('IOWriteBandwidthMax=/srv/projectiq-workspace 4M',script)
        self.assertNotIn('NOPASSWD',script)
        self.assertIn("  - path: /usr/local/sbin/projectiq-cgroup-probe-bootstrap",config)

    def test_rejects_missing_quota_and_mixed_trust_modes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {
                'pycdlib':types.SimpleNamespace(PyCdlib=FakeISO)}):
            for kwargs in [dict(cgroup_probe=True),
                           dict(cgroup_probe=True,workspace_disk_mib=96,template_setup=True),
                           dict(cgroup_probe=True,workspace_disk_mib=96,mysql_bootstrap=True)]:
                with self.subTest(kwargs=kwargs), self.assertRaises(qemu.WorkerFailure):
                    qemu.make_nocloud_iso(Path(tmp),'ssh-ed25519 AAAATEST comment',
                                           'd5b3b-check',**kwargs)

    def test_probe_no_student_code_and_bounded_thread_attempt(self):
        src = probe.CHILD
        self.assertIn('range(64)',src)
        self.assertIn("(stem/'io.max')",src)
        self.assertIn('PermissionError',src)
        self.assertIn('thread_denied',src)
        self.assertNotIn('shell=True',src)

    def test_parse_strict_report_schema(self):
        data={'schema_version':'0.1','checks':{k:'PASS' for k in accept.CHECKS},
              'diagnostics':{'scope_exit_code':0,'scope_error':'E'*1000,'secret':'not stored'}}
        parsed=accept._parse(json.dumps(data).encode())
        self.assertEqual(parsed[1]['scope_exit_code'],0)
        self.assertEqual(len(parsed[1]['scope_error']),350)
        self.assertNotIn('secret',parsed[1])
        bad=json.loads(json.dumps(data)); bad['checks']['pids_limit_enforced']=True
        self.assertIsNone(accept._parse(json.dumps(bad).encode()))
        bad=json.loads(json.dumps(data)); bad['checks'].pop('pids_limit_enforced')
        self.assertIsNone(accept._parse(json.dumps(bad).encode()))
        self.assertIsNone(accept._parse(b'X'*6000))

    def _run(self, *, live=True, verified=True):
        status = ('TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'
                  if verified else 'BLOCKED')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(accept, 'verify_host_toolchain', return_value={'status':status}), \
             patch.object(accept, 'QemuWorker', FakeWorker), \
             patch.object(accept, 'SSHControl', FakeSSH), \
             patch.object(accept, 'make_ssh_key', return_value=(Path(tmp)/'key','ssh-ed25519 AAA')), \
             patch.object(accept, 'make_nocloud_iso', return_value=Path(tmp)/'seed.iso') as seed, \
             patch.object(accept, 'effective_vm_vcpus',return_value=1), \
             patch.object(accept.threading, 'Timer', FakeTimer):
            report=accept.run({},run_clean_vm=live,work_root=Path(tmp))
            if live and verified:
                self.assertTrue(seed.called)
                self.assertTrue(seed.call_args.kwargs['cgroup_probe'])
                self.assertEqual(seed.call_args.kwargs['workspace_disk_mib'],96)
            return report

    def test_static_inspection_never_executes(self):
        r=self._run(live=False)
        self.assertEqual(r['status'],'INSPECTION_ONLY_NOT_ATTESTED')
        self.assertEqual(r['execution'],'NOT_PERFORMED')
        self.assertFalse(r['student_code_executed'])

    def test_denied_host_toolchain_never_launches(self):
        self.assertEqual(self._run(verified=False)['status'],'HOST_GATES_BLOCKED')

    def test_positive_observation_does_not_attest_or_enable_projects(self):
        r=self._run()
        self.assertEqual(r['status'],'CGROUP_PROBE_OBSERVED_NOT_ATTESTED')
        self.assertEqual(r['cleanup'],'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
        self.assertEqual(r['security_attestation'],'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')
        self.assertIn('NORMAL_APPLICATION_LAUNCHES_NOT_YET_ATTACHED_TO_CGROUP_SLICE',
                      r['remaining_security_gates'])

    def test_unverified_cgroup_does_not_pass(self):
        FakeSSH.response={'schema_version':'0.1','checks':{k:'PASS' for k in accept.CHECKS},'diagnostics':{}}
        FakeSSH.response['checks']['io_max_workspace_device']='NOT_VERIFIED'
        self.assertEqual(self._run()['status'],'CGROUP_ENFORCEMENT_NOT_VERIFIED')

    def test_reported_kernel_failure_does_not_pass(self):
        FakeSSH.response={'schema_version':'0.1','checks':{k:'PASS' for k in accept.CHECKS},'diagnostics':{}}
        FakeSSH.response['checks']['app_cannot_modify_limits']='FAIL'
        self.assertEqual(self._run()['status'],'CGROUP_KERNEL_OR_PRIVILEGE_CHECK_FAILED')

    def test_failed_qemu_launch_and_cleanup_are_not_success(self):
        FakeWorker.fail_start=True
        self.assertEqual(self._run()['status'],'CGROUP_PROBE_INCONCLUSIVE_OR_FAILED')
        FakeWorker.fail_start=False
        FakeWorker.fail_stop=True
        self.assertEqual(self._run()['status'],'CLEANUP_FAILED')


if __name__=='__main__':
    unittest.main()
