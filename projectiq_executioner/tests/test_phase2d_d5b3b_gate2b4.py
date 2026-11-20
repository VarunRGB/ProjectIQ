"""Gate 2B.4: hard QEMU termination/fresh-overlay recovery, offline tests."""
import copy
import inspect
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5_recovery_guest_probe as guest
from projectiq_executioner.phase2.vm_worker import d5_recovery_acceptance as host


def guest_record(mode):
    arm = mode == 'arm'
    return {
        'schema_version': '0.1', 'mode': mode,
        'bootstrap_ready': True, 'workspace_verified': True,
        'slice_policy_verified': True, 'scope_verified': arm,
        'sentinel_written': arm, 'sentinel_absent': not arm,
        'eval_tasks_current': 1 if arm else 0,
        'outcome': 'WORKER_ARMED' if arm else 'FRESH_VM_CLEAN',
        'crash_observed': arm, 'crash_scope_drained': arm,
        'cancel_observed': arm, 'cancel_scope_drained': arm,
        'probe_stage': 'HARD_WORKER_ARMED' if arm else 'FRESH_VM_CLEAN',
        'crash_exit_code': 42 if arm else -1,
        'cancel_exit_code': 0 if arm else -1,
    }


class GuestEvidenceTests(unittest.TestCase):
    def test_positive_reports_require_complete_evidence(self):
        for mode in ('arm', 'recover'):
            record = guest_record(mode)
            self.assertTrue(guest._validated_guest_report(record, mode))
            self.assertIsNotNone(host._parse_guest(json.dumps(record).encode(), mode))
            for key in ('bootstrap_ready', 'workspace_verified', 'slice_policy_verified'):
                bad = {**record, key: False}
                self.assertFalse(guest._validated_guest_report(bad, mode))
            for key in ('bootstrap_ready', 'workspace_verified', 'slice_policy_verified'):
                bad = {**record, key: 1}
                self.assertFalse(guest._validated_guest_report(bad, mode))

    def test_negative_crash_and_recovery_claims_are_rejected(self):
        for mode in ('arm', 'recover'):
            record = guest_record(mode)
            for key in ('scope_verified', 'sentinel_written', 'sentinel_absent',
                        'crash_observed','crash_scope_drained',
                        'cancel_observed','cancel_scope_drained'):
                bad = {**record, key: not record[key]}
                self.assertFalse(guest._validated_guest_report(bad, mode))
            bad = {**record, 'eval_tasks_current': (0 if mode=='arm' else 1)}
            self.assertFalse(guest._validated_guest_report(bad, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'outcome':'PASS'}, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'mode':'other'}, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'untrusted_extra':'x'}, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'eval_tasks_current':True}, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'probe_stage':'OTHER'}, mode))
            self.assertFalse(guest._validated_guest_report({**record, 'crash_exit_code':True}, mode))

    def test_bounded_typed_parse_rejects_forged_or_oversized_report(self):
        self.assertIsNone(host._parse_guest(b'not json', 'arm'))
        self.assertIsNone(guest._bounded_diagnostics({'bad':'extra'},'arm'))
        self.assertIsNone(host._parse_guest(b'\xff', 'arm'))
        self.assertIsNone(host._parse_guest(b'x'*2500, 'arm'))
        self.assertIsNone(host._parse_guest(json.dumps(guest_record('recover')).encode(), 'arm'))
        self.assertIsNone(host._parse_guest(json.dumps(guest_record('arm')).encode()+b'garbage', 'arm'))

    def test_invalid_exit_code_or_probe_stage_cannot_pass(self):
        record=guest_record('arm')
        for patch_row in ({'crash_exit_code':1},{'cancel_exit_code':42},
                          {'probe_stage':'CRASH_WRAPPER_EXITED'},
                          {'probe_stage': 'UNBOUNDED_DIAGNOSTIC_VALUE'},
                          {'crash_exit_code':999},{'cancel_exit_code':True}):
            with self.subTest(patch_row=patch_row):
                bad={**record,**patch_row}
                self.assertFalse(guest._validated_guest_report(bad,'arm'))
        partial={**record,'outcome':'NOT_VERIFIED',
                 'probe_stage':'CANCEL_WRAPPER_EXITED',
                 'cancel_exit_code':1}
        self.assertIsNotNone(guest._bounded_diagnostics(partial,'arm'))
        self.assertFalse(guest._validated_guest_report(partial,'arm'))

    def test_host_records_fixed_stage_without_promoting_failure(self):
        report=guest_record('arm')
        report.update(outcome='NOT_VERIFIED',probe_stage='CANCEL_WRAPPER_EXITED',
                      cancel_exit_code=1)
        class SSH:
            def ssh(self,*args,**kwargs):
                return subprocess.CompletedProcess(args,0,json.dumps(report).encode(),b'')
        diagnostic={}
        self.assertIsNone(host._guest_evidence(SSH(),'arm','0'*32,diagnostic))
        self.assertEqual(diagnostic['arm_guest_probe_stage'],'CANCEL_WRAPPER_EXITED')
        self.assertEqual(diagnostic['arm_cancel_exit_code'],1)

    def test_worker_is_fixed_and_cannot_cooperate_before_vm_kill(self):
        compile(guest.WORKER, 'fixed trusted hang worker', 'exec')
        self.assertIn('while True:', guest.WORKER)
        self.assertNotIn('subprocess', guest.WORKER)
        self.assertNotIn('open(', guest.WORKER)
        source = inspect.getsource(guest._run)
        self.assertIn("'/usr/bin/sudo', '-n', '-u', 'projectiq_run'", source)
        self.assertIn("SENTINEL.open('xb')", source)
        self.assertIn('os.fsync', source)
        self.assertIn('_scope_member', source)
        self.assertIn('pids.current', source)
        self.assertIn("hashlib.sha256(('arm:' + token)",source)
        self.assertIn('_exercise_controlled_failures', source)
        compile(guest.CRASH_WORKER, 'trusted crash worker', 'exec')
        compile(guest.CANCEL_WORKER, 'trusted cancel worker', 'exec')

    def test_scope_member_requires_exact_uid_cgroup_and_argv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cg = root/'cgroup'; scope = cg/'user.slice'/'b4.scope'
            scope.mkdir(parents=True)
            proc = root/'proc'; task = proc/'123'; task.mkdir(parents=True)
            (scope/'cgroup.procs').write_text('123\n', encoding='ascii')
            (task/'status').write_text('Name: python3\nUid:\t1002\t1002\t1002\t1002\n',encoding='utf-8')
            (task/'cgroup').write_text('0::/user.slice/b4.scope\n',encoding='ascii')
            (task/'cmdline').write_bytes(b'/usr/bin/python3\x00-I\x00-c\x00fixed\x00')
            with patch.object(guest,'CGROUP',cg):
                expected=['/usr/bin/python3','-I','-c','fixed']
                self.assertTrue(guest._scope_member(scope,1002,expected,proc_root=proc))
                self.assertFalse(guest._scope_member(scope,1003,expected,proc_root=proc))
                self.assertFalse(guest._scope_member(scope,1002,expected[:-1],proc_root=proc))
                (task/'cgroup').write_text('0::/other.slice\n')
                self.assertFalse(guest._scope_member(scope,1002,expected,proc_root=proc))

    def test_scope_drain_requires_real_empty_kernel_events(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'piq-b4-test.scope'
            self.assertTrue(guest._scope_drained(path))
            path.mkdir()
            (path/'cgroup.procs').write_text('',encoding='ascii')
            (path/'cgroup.events').write_text('populated 0\nfrozen 0\n',encoding='ascii')
            self.assertTrue(guest._scope_drained(path))
            (path/'cgroup.procs').write_text('313\n',encoding='ascii')
            self.assertFalse(guest._scope_drained(path))
            (path/'cgroup.procs').write_text('',encoding='ascii')
            (path/'cgroup.events').write_text('populated 1\n',encoding='ascii')
            self.assertFalse(guest._scope_drained(path))
            (path/'cgroup.events').write_text('broken\n',encoding='ascii')
            self.assertFalse(guest._scope_drained(path))

    def test_fresh_vm_marker_must_be_missing_not_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            sentinel=Path(td)/'.projectiq-b3b-recovery-sentinel'
            with patch.object(guest,'SENTINEL',sentinel):
                self.assertTrue(guest._sentinel_is_absent())
                sentinel.write_text('old data',encoding='ascii')
                self.assertFalse(guest._sentinel_is_absent())
                sentinel.unlink()
                # An unreadable marker is not the same as a missing marker,
                # even on Windows where symlink creation may require privilege.
                with patch.object(type(sentinel), 'lstat', side_effect=PermissionError(13, 'Access denied')):
                    self.assertFalse(guest._sentinel_is_absent())
                # Check a real dangling link whenever the host permits it.
                # WinError 1314 means Windows denies nonprivileged symlinks.
                try:
                    sentinel.symlink_to(Path(td)/'nonexistent')
                except OSError as exc:
                    if getattr(exc, 'winerror', None) != 1314:
                        raise
                else:
                    self.assertFalse(guest._sentinel_is_absent())

    def test_scope_termination_and_no_raw_stdout_are_expected(self):
        code=inspect.getsource(host.run)
        self.assertIn('first_proc.kill()',code)
        self.assertIn('first_proc.wait(timeout=18)',code)
        self.assertIn("result['checks']['first_vm_storage_removed']",code)
        self.assertIn("result['checks']['fresh_vm_workspace_and_slice_clean']",code)
        self.assertNotIn('subprocess.check_output',code)

    def test_budget_and_network_contract_is_fixed(self):
        self.assertEqual(guest.RAM_MAX, 2*1024**3)
        self.assertEqual(guest.TASKS_MAX, 80)
        self.assertEqual(guest.WRITE_BPS, 16*1024**2)
        self.assertEqual(guest.WORKSPACE_MIB, 96)
        self.assertEqual(host.WORKSPACE_MIB, 96)
        self.assertEqual(host.VM_RAM_MIB, 1536)
        self.assertEqual(len(host.CHECKS), 10)


class MockProc:
    def __init__(self):
        self.dead = False
        self.kill_called = False
    def poll(self):
        return -9 if self.dead else None
    def kill(self):
        self.kill_called = True
        self.dead = True
    def wait(self, timeout=None):
        return -9 if self.dead else None


class MockVM:
    created=[]
    def __init__(self, config, workdir, port, *, ram_mib, cpus):
        self.config=config
        self.workdir=workdir
        self.port=port
        self.proc=MockProc()
        self.ram_mib=ram_mib
        self.cpus=cpus
        self.__class__.created.append(self)
    def network_spec(self):
        return f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{self.port}-:22'
    def start(self, seed):
        (self.workdir/'run.qcow2').write_bytes(b'overlay')
        (self.workdir/'workspace.raw').write_bytes(b'bounded')
    def stop(self):
        self.proc.dead=True


class MockSSH:
    bad_mode=None
    def __init__(self, config, workdir, port, key):
        self.workdir=workdir
    def wait_ssh(self, worker, seconds):
        self.worker=worker
    def ssh(self, *args, input, timeout):
        mode=args[2]
        record=guest_record(mode)
        if self.bad_mode == mode:
            record['sentinel_absent'] = False
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(record).encode(),stderr=b'')


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        MockVM.created=[]
        MockSSH.bad_mode=None
        self.patches=[
            patch.object(host,'verify_host_toolchain',return_value={
                'status':'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'}),
            patch.object(host,'make_ssh_key',side_effect=lambda cfg,w:(w/'key','ssh-ed25519 synthetic')),
            patch.object(host,'make_nocloud_iso',side_effect=lambda w,pub,instance,**kw:w/'seed.iso'),
            patch.object(host,'QemuWorker',MockVM),
            patch.object(host,'SSHControl',MockSSH),
            patch.object(host,'effective_vm_vcpus',return_value=1),
            patch.object(host,'_free_loopback_port',return_value=40123),
            patch.object(host,'_guest_evidence',side_effect=lambda ssh,mode,token,observations=None: (
                None if MockSSH.bad_mode==mode else guest_record(mode))),
        ]
        for item in self.patches:item.start()
        self.addCleanup(lambda:[item.stop() for item in reversed(self.patches)])

    def test_live_success_kills_first_vm_reuses_no_overlay(self):
        with tempfile.TemporaryDirectory() as td:
            result=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True,work_root=Path(td))
            self.assertEqual(result['status'],'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED')
            self.assertEqual(result['cleanup'],'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
            self.assertTrue(all(x=='PASS' for x in result['checks'].values()))
            self.assertTrue(MockVM.created[0].proc.kill_called)
            self.assertFalse(MockVM.created[1].proc.kill_called)
            self.assertNotEqual(MockVM.created[0].workdir,MockVM.created[1].workdir)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_missing_acknowledgment_cannot_start_vm(self):
        with tempfile.TemporaryDirectory() as td:
            r=host.run({},run_clean_vm=True,work_root=Path(td))
            self.assertEqual(r['status'],'HARD_VM_TERMINATION_CONFIRMATION_REQUIRED')
            self.assertEqual(len(MockVM.created),0)

    def test_static_inspection_cannot_start_vm(self):
        r=host.run({},run_clean_vm=False)
        self.assertEqual(r['status'],'INSPECTION_ONLY_NOT_ATTESTED')
        self.assertEqual(len(MockVM.created),0)

    def test_bad_first_vm_evidence_cannot_pass_and_vm_is_stopped(self):
        MockSSH.bad_mode='arm'
        with tempfile.TemporaryDirectory() as td:
            r=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True,work_root=Path(td))
            self.assertNotEqual(r['status'],'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED')
            self.assertEqual(len(MockVM.created),1)
            self.assertTrue(MockVM.created[0].proc.dead)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_bad_fresh_vm_evidence_cannot_pass_and_both_stopped(self):
        MockSSH.bad_mode='recover'
        with tempfile.TemporaryDirectory() as td:
            r=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True,work_root=Path(td))
            self.assertNotEqual(r['status'],'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED')
            self.assertEqual(len(MockVM.created),2)
            self.assertTrue(all(vm.proc.dead for vm in MockVM.created))
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_no_host_toolchain_means_no_execution(self):
        with patch.object(host,'verify_host_toolchain',return_value={'status':'BLOCKED'}):
            r=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True)
        self.assertEqual(r['status'],'HOST_GATES_BLOCKED')
        self.assertEqual(len(MockVM.created),0)

    def test_failed_hard_termination_never_boots_second_vm(self):
        def no_op_kill(proc):
            proc.kill_called = True
        with patch.object(MockProc,'kill',no_op_kill):
            with tempfile.TemporaryDirectory() as td:
                r=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True,
                           work_root=Path(td))
                self.assertNotEqual(r['status'],'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED')
                self.assertNotEqual(r['checks']['forced_vm_termination_observed'],'PASS')
                self.assertEqual(len(MockVM.created),1)

    def test_vm_network_policy_is_fail_closed(self):
        with patch.object(MockVM,'network_spec',return_value='user,id=net0'):
            with tempfile.TemporaryDirectory() as td:
                r=host.run({},run_clean_vm=True,acknowledge_hard_vm_kill=True,work_root=Path(td))
                self.assertNotEqual(r['status'],'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED')
                self.assertEqual(list(Path(td).iterdir()),[])


if __name__=='__main__':unittest.main()
