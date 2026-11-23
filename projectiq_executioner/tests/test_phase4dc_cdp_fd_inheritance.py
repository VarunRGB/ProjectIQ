"""4D-C CDP FD 3/4 inheritance regression; Linux runtime + Windows-safe contracts."""
import inspect
import pathlib
import subprocess
import sys
import tempfile
import unittest

from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from projectiq_executioner.phase4 import guest_browser_ui as ui


class ChromePipeInheritanceTests(unittest.TestCase):
    def test_private_debugging_pipe_and_explicit_inheritance(self):
        src = inspect.getsource(cdp._start_chrome)
        self.assertIn("'--remote-debugging-pipe'", src)
        self.assertNotIn("'--remote-debugging-port'", src)
        self.assertNotIn("'--no-sandbox'", src)
        self.assertIn('os.set_inheritable(3, True)', src)
        self.assertIn('os.set_inheritable(4, True)', src)
        self.assertIn('close_fds=False', src)

    def test_allowlisted_failure_categories_keep_guest_reports_valid(self):
        source = {'kind': 'github', 'repository': 'Fixture/Reviewed', 'commit_sha': 'a' * 40}
        bundle = {
            'schema_version': '0.1', 'source': source,
            'adapter': 'node_fullstack_offline_v1', 'catalog_sha256': 'b' * 64,
            'page_path': '/index.html', 'browser_binary': '/usr/bin/google-chrome-stable',
            'checks': [{'tag': 'h1', 'exact_text': 'ProjectIQ D4 pilot'},
                       {'tag': 'p', 'exact_text': 'SQLite verified'}],
        }
        cases = {
            b'CDP_PIPE_CLOSED\n': 'BROWSER_CDP_PIPE_CLOSED',
            b'CDP_PROTOCOL_ERROR\n': 'BROWSER_CDP_PROTOCOL_ERROR',
            b'CDP_RUNTIME_ERROR\n': 'BROWSER_CDP_RUNTIME_ERROR',
            b'CDP_REPLY_TIMEOUT\n': 'BROWSER_CDP_TIMEOUT',
            b'CDP_CONNECTION_REFUSED\n': 'BROWSER_CDP_CONNECTION_REFUSED',
        }
        for stderr, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(ui._cdp_failure_issue(119, stderr), expected)
                report = ui._empty(bundle, issue=expected)
                ui.validate_browser_ui_report(bundle, report)
        self.assertEqual(ui._cdp_failure_issue(118, b''), 'BROWSER_CDP_HELPER_REJECTED')
        self.assertEqual(ui._cdp_failure_issue(119, b'unknown'), 'BROWSER_CDP_UNAVAILABLE')
        self.assertEqual(ui._cdp_failure_issue(119, '<malformed>'), 'BROWSER_CDP_UNAVAILABLE')

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX fork/exec FD regression is Linux-only')
    def test_actual_fresh_python_process_passes_fd3_fd4(self):
        # Run in a fresh Python interpreter: fd 3 is commonly the FIRST os.pipe()
        # endpoint. The old code fails because dup2(3,3) leaves FD_CLOEXEC set.
        project_root = str(pathlib.Path(__file__).resolve().parents[2])
        with tempfile.TemporaryDirectory() as td:
            fake = pathlib.Path(td) / 'fake-chrome'
            fake.write_text('#!' + sys.executable + '\n'
                            'import os\n'
                            'if not (os.get_inheritable(3) and os.get_inheritable(4)):\n'
                            '    raise SystemExit(8)\n'
                            'if os.read(3, 5) != b"HELLO":\n'
                            '    raise SystemExit(9)\n'
                            'os.write(4, b"REPLY")\n', encoding='utf-8')
            fake.chmod(0o755)
            # Isolated process: -I removes ambient import path/environment.
            isolated = """import os,sys,pathlib
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from projectiq_executioner.phase4 import guest_browser_cdp as cdp
from pathlib import Path
r0,w0=os.pipe()
assert r0==3, 'Isolated interpreter did not expose the fd3 collision'
os.close(r0);os.close(w0)
with patch.object(cdp, 'BROWSER', sys.argv[2]):
    proc,w,r=cdp._start_chrome(Path(sys.argv[3])/'profile')
    try:
        os.write(w,b'HELLO')
        assert os.read(r,5)==b'REPLY'
        assert proc.wait(timeout=5)==0
    finally:
        os.close(w);os.close(r)
        cdp._stop(proc)
print('FD3_FD4_TRANSPORT_VERIFIED')
"""
            p = subprocess.run([sys.executable, '-I', '-c', isolated,
                                project_root, str(fake), td],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=10, check=False)
            self.assertEqual(p.returncode, 0, msg=p.stderr.decode('utf-8', 'replace'))
            self.assertEqual(p.stdout.strip(), b'FD3_FD4_TRANSPORT_VERIFIED')
