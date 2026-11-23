"""4D-D SSH helper transfer regression. No VM, SSH, or external network used."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker.qemu import SSHControl, WorkerFailure


class Trusted4DDTransfers(unittest.TestCase):
    def test_only_two_reviewed_login_helper_filenames_were_added(self):
        # A mock SSH session proves the exact transfer names are accepted.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            controller=SSHControl({}, root, 12345, root/'fakekey')
            artifact=root/'trusted-script.py'
            artifact.write_bytes(b'print(1)\n')
            with patch.object(controller,'ssh',return_value=SimpleNamespace(returncode=0)) as ssh:
                for name in ('guest_browser_login.py','guest_browser_login_cdp.py'):
                    controller.copy(artifact,name)
                    argv=ssh.call_args.args
                    self.assertEqual(argv,('dd','of=/home/projectiq/'+name,'status=none'))
                    self.assertEqual(ssh.call_args.kwargs['input'],b'print(1)\n')

    def test_existing_browser_helpers_still_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            controller=SSHControl({},root,12345,root/'fakekey')
            src=root/'helper.py';src.write_bytes(b'hello')
            with patch.object(controller,'ssh',return_value=SimpleNamespace(returncode=0)):
                for name in ('guest_browser_ui.py','guest_browser_cdp.py'):
                    controller.copy(src,name)

    def test_student_supplied_remote_name_still_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            controller=SSHControl({},root,12345,root/'fakekey')
            src=root/'sample.py';src.write_bytes(b'not secret')
            with patch.object(controller,'ssh') as ssh:
                for name in ('../escape.py','/tmp/example.py','guest_browser_login_cdp.py;id',
                             'guest_browser_login_cdp.py/../../foo','notes.txt','evil.py'):
                    with self.subTest(name=name):
                        with self.assertRaises(WorkerFailure):
                            controller.copy(src,name)
                ssh.assert_not_called()

    def test_transfer_failure_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            controller=SSHControl({},root,12345,root/'fakekey')
            src=root/'sample.py';src.write_bytes(b'test')
            with patch.object(controller,'ssh',return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(WorkerFailure):
                    controller.copy(src,'guest_browser_login_cdp.py')

    def test_missing_local_helper_still_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            controller=SSHControl({},root,12345,root/'fakekey')
            with patch.object(controller,'ssh') as ssh:
                with self.assertRaises(OSError):
                    controller.copy(root/'no-such-file.py','guest_browser_login.py')
                ssh.assert_not_called()

    def test_new_helper_copy_is_in_live_vm_orchestration(self):
        from projectiq_executioner.phase2.vm_worker import orchestrator
        # Test the installed source boundary rather than requiring QEMU to boot.
        import inspect
        source=inspect.getsource(orchestrator.execute_vm_candidate)
        self.assertIn("for module in ('guest_browser_login.py', 'guest_browser_login_cdp.py',",source)
        self.assertIn("ssh.copy(Path(__file__).resolve().parents[2] / 'phase4' / module,",source)


if __name__=='__main__':
    unittest.main()
