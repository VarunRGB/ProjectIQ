"""Phase 4D-D: exact-file trusted CDP import from 0711 controller directory.

Purely offline; never starts Chrome or QEMU. Linux privilege probe is optional.
"""
from __future__ import annotations
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from projectiq_executioner.phase4 import guest_browser_login_cdp as helper


class TrustedCdpImport(unittest.TestCase):
    def test_01_fixed_path_not_supplied_by_checkout(self):
        self.assertEqual(helper._TRUSTED_CDP_MODULE, '/home/projectiq/guest_browser_cdp.py')

    def test_02_explicit_file_import_without_module_search(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / 'guest_browser_cdp.py'
            module.write_text('SAFE_TEST_VALUE = 42\n', encoding='utf8')
            original = list(sys.path)
            with patch.object(helper, '_TRUSTED_CDP_MODULE', str(module)):
                result = helper._load_trusted_cdp_module()
            self.assertEqual(result.SAFE_TEST_VALUE, 42)
            self.assertEqual(sys.path, original)

    def test_03_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(helper, '_TRUSTED_CDP_MODULE', str(Path(folder) / 'missing.py')):
                with self.assertRaises((ImportError, FileNotFoundError, OSError)):
                    helper._load_trusted_cdp_module()

    def test_04_invalid_python_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'guest_browser_cdp.py'
            target.write_text('def broken(:\n', encoding='utf8')
            with patch.object(helper, '_TRUSTED_CDP_MODULE', str(target)):
                with self.assertRaises(SyntaxError):
                    helper._load_trusted_cdp_module()

    def test_05_no_sys_path_mutation_or_dynamic_student_path(self):
        src = Path(helper.__file__).read_text(encoding='utf8')
        self.assertNotIn("sys.path.insert(0, '/home/projectiq')", src)
        self.assertNotIn('import guest_browser_cdp as pipe', src)
        self.assertIn('pipe = _load_trusted_cdp_module()', src)
        self.assertIn("importlib.util.spec_from_file_location('guest_browser_cdp',", src)
        self.assertIn('spec.loader.exec_module(module)', src)

    def test_06_existing_sandbox_and_quota_checks_remain(self):
        src = Path(helper.__file__).read_text(encoding='utf8')
        self.assertIn("quota_text = os.environ.get('HOME', '')", src)
        self.assertIn(".is_relative_to(Path('/srv/projectiq-workspace'))", src)
        self.assertIn("os.environ.get('PROJECTIQ_BROWSER_PROFILE') != '1'", src)
        self.assertIn("if sys.platform != 'linux' or os.geteuid() == 0", src)
        self.assertNotIn('--no-sandbox', src)

    def test_07_error_reporting_and_exit_policy_unchanged(self):
        src = Path(helper.__file__).read_text(encoding='utf8')
        self.assertIn('pipe._start_chrome(profile)', src)
        self.assertIn('_emit_stdout_diagnostic(reason)\n        return 119', src)
        self.assertIn("_DIAG_STAGE = 'HELPER_IMPORT'\n        pipe = _load_trusted_cdp_module()", src)
        self.assertIn('finally:\n        for fd in fds:', src)
        ast.parse(src)

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0
                         and shutil.which('runuser') is not None, 'requires root on Linux')
    def test_08_unprivileged_import_from_0711_dir(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='piq4dd-boundary-') as folder:
            d = Path(folder)
            p = d / 'guest_browser_cdp.py'
            p.write_text('TRUSTED = 7\n', encoding='utf8')
            p.chmod(0o644)
            d.chmod(0o711)
            try:
                plain = subprocess.run(['runuser', '-u', 'nobody', '--', 'python3', '-I', '-c',
                    'import sys;sys.path.insert(0,sys.argv[1]);import guest_browser_cdp', str(d)],
                    capture_output=True, timeout=6)
                exact = subprocess.run(['runuser', '-u', 'nobody', '--', 'python3', '-I', '-c',
                    'import importlib.util,sys;'
                    's=importlib.util.spec_from_file_location("guest_browser_cdp",sys.argv[1]);'
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                    'assert m.TRUSTED==7', str(p)],
                    capture_output=True, timeout=6)
                self.assertNotEqual(plain.returncode, 0)
                self.assertEqual(exact.returncode, 0)
            finally:
                d.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
