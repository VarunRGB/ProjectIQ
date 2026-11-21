"""D5c Gate 1: Windows parent-death guard and fail-closed acceptance tests."""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from projectiq_executioner.phase2.vm_worker import d5c_windows_job as guard
from projectiq_executioner.phase2.vm_worker import d5c_host_interruption_acceptance as acceptance
from projectiq_executioner.phase2.vm_worker import qemu


class GuardEvidenceTests(unittest.TestCase):
    def test_shared_qemu_launcher_attaches_parent_job_before_popen(self):
        code = inspect.getsource(qemu.QemuWorker.start)
        self.assertIn('ensure_controller_job()', code)
        self.assertIn('verify_child_in_job(self.proc)', code)
        self.assertLess(code.index('ensure_controller_job()'), code.index('subprocess.Popen(args'))
        self.assertIn('raise WorkerFailure', code)

    def test_job_is_never_closed_after_controller_join(self):
        source = inspect.getsource(guard.ensure_controller_job)
        self.assertIn('AssignProcessToJobObject', source)
        self.assertIn('SetInformationJobObject', source)
        self.assertIn('0x2000', source)
        self.assertIn('_JOB_HANDLE = int(job)', source)
        self.assertIn('if not attached:', source)

    def test_child_uses_no_student_source_and_fixed_resources(self):
        source = inspect.getsource(acceptance._child_vm)
        self.assertIn('app_cgroup=True', source)
        self.assertIn('restrict=on', source)
        self.assertIn('verify_child_in_job(worker.proc)', source)
        self.assertIn('ssh.wait_ssh(worker', source)
        self.assertEqual(acceptance.VM_RAM_MIB, 1536)
        self.assertEqual(acceptance.WORKSPACE_MIB, 96)
        self.assertEqual(acceptance.ATTESTATION, 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS')

    def test_static_mode_fails_closed_on_linux(self):
        if os.name != 'nt':
            result = acceptance.run(Path('does-not-exist.json'), execute=False)
            self.assertEqual(result['status'], 'WINDOWS_JOB_ACCEPTANCE_REQUIRES_WINDOWS')
            self.assertEqual(result['cleanup'], 'NOT_STARTED')
        self.assertEqual(set(acceptance._base_report()['checks']), set(acceptance.CHECKS))

    def test_no_forced_success_from_probe_error(self):
        r = acceptance._base_report()
        self.assertTrue(all(v == 'NOT_VERIFIED' for v in r['checks'].values()))
        self.assertFalse(r['student_code_executed'])
        self.assertNotEqual(r['status'], acceptance.STATUS_PASS)

    def test_marker_must_be_json_dict_and_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sample.json'
            self.assertIsNone(acceptance._read_record(path))
            path.write_text('not-json')
            self.assertIsNone(acceptance._read_record(path))
            path.write_text('"true"')
            self.assertIsNone(acceptance._read_record(path))
            path.write_text('x' * 700)
            self.assertIsNone(acceptance._read_record(path))
            acceptance._atomic_record(path, {'schema_version': '0.1', 'armed': True})
            self.assertEqual(acceptance._read_record(path), {'schema_version':'0.1','armed':True})

    def test_linux_job_guard_never_claims_windows(self):
        if os.name != 'nt':
            guard.ensure_controller_job()
            self.assertIsNone(guard._JOB_HANDLE)
            self.assertTrue(guard.verify_child_in_job(object()))

    def test_controller_killed_only_after_vm_alive_and_booted(self):
        source = inspect.getsource(acceptance.run)
        self.assertIn('vm_alive_at_kill =', source)
        self.assertIn('controller.kill()', source)
        self.assertIn('qemu.emergency_terminate()', source)
        self.assertIn("'QEMU_NOT_ALIVE_BEFORE_HARD_TERMINATION'", source)
        self.assertIn("'QEMU_LIFETIME_UNVERIFIED'", source)


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Object kernel')
class WindowsKernelTests(unittest.TestCase):
    def test_killing_controller_terminates_its_real_child(self):
        # No QEMU required: smoke-test the exact Windows job lifetime contract.
        # The parent never joins the child-controller Job Object.
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / 'child-pid.json'
            code = (
                'import json,subprocess,sys,time\n'
                'from pathlib import Path\n'
                'from projectiq_executioner.phase2.vm_worker.d5c_windows_job import '
                'ensure_controller_job,verify_child_in_job\n'
                'ensure_controller_job()\n'
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(90)"],'
                'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                'if not verify_child_in_job(p):sys.exit(4)\n'
                f'Path({str(marker)!r}).write_text(json.dumps({{"pid":p.pid}}))\n'
                'time.sleep(90)\n'
            )
            p = subprocess.Popen([sys.executable, '-c', code],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            handle = None
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not marker.exists():
                    if p.poll() is not None:
                        self.fail('Controller could not establish kill-on-close Job Object')
                    time.sleep(.05)
                self.assertTrue(marker.exists(), 'Controller never published child PID')
                child_pid = json.loads(marker.read_text())['pid']
                handle = acceptance._WindowsProcessHandle(child_pid, sys.executable)
                self.assertTrue(handle.is_live())
                p.kill()
                p.wait(timeout=8)
                self.assertTrue(handle.wait_exited(15), 'Windows did not terminate job child')
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=8)
                if handle is not None:
                    if handle.is_live():
                        handle.emergency_terminate()
                    handle.close()


if __name__ == '__main__':
    unittest.main()
