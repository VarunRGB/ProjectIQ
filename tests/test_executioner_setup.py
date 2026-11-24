"""Offline, standard-library-only tests for the read-only Executioner setup checker."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_executioner_setup import _load_config, check_setup, main


class ExecutionerSetupTests(unittest.TestCase):
    def test_static_never_requires_qemu(self):
        with patch('scripts.check_executioner_setup.shutil.which', side_effect=AssertionError('not queried')):
            rows = check_setup('static')
        self.assertEqual(next(x['status'] for x in rows if x['check'] == 'vm'), 'SKIPPED')
        self.assertTrue(any(x['check'] == 'github_access' and x['status'] == 'UNVERIFIED' for x in rows))

    def test_reviewed_missing_configuration_is_a_failure_not_a_vm_launch(self):
        rows = check_setup('reviewed')
        self.assertTrue(any(x['check'] == 'vm_config' and x['status'] == 'FAIL' for x in rows))

    def test_config_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'vm.json'
            p.write_text('{"base_image":"one", "base_image":"two"}', encoding='utf-8')
            config, reason = _load_config(p)
            self.assertIsNone(config)
            self.assertTrue(reason)

    def test_config_is_not_modified(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'vm.json'
            data = b'{"approved_snapshots":[]}'
            p.write_bytes(data)
            _load_config(p)
            self.assertEqual(p.read_bytes(), data)

    def test_reviewed_sha_pin_check_and_tool_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = root / 'guest.qcow2'
            base.write_bytes(b'NOT_A_REAL_VM_IMAGE')
            sha = hashlib.sha256(base.read_bytes()).hexdigest()
            config = root / 'vm.json'
            config.write_text(json.dumps({
                'base_image': str(base), 'base_sha256': sha,
                'accelerator': 'whpx',
                'approved_snapshots': ['VarunRGB/FSDL@f358b6edda071da6666e587a8f1db054965ba106'],
            }), encoding='utf-8')
            with patch('scripts.check_executioner_setup.platform.system', return_value='Windows'), \
                 patch('scripts.check_executioner_setup.shutil.which', return_value='C:/trusted.exe'), \
                 patch('scripts.check_executioner_setup.importlib.util.find_spec', return_value=object()):
                rows = check_setup('reviewed', config, check_image_sha=True)
            result = {r['check']: r['status'] for r in rows}
            self.assertEqual(result['base_image'], 'PASS')
            self.assertEqual(result['approved_snapshots'], 'PASS')
            self.assertEqual(result['pycdlib'], 'PASS')
            self.assertEqual(result['tool:qemu-img'], 'PASS')
            self.assertEqual(result['sandbox_attestation'], 'UNVERIFIED')

    def test_wrong_image_hash_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = root / 'guest.qcow2'
            base.write_bytes(b'guest')
            cfg = root / 'vm.json'
            cfg.write_text(json.dumps({'base_image': str(base), 'base_sha256': 'a'*64,
                                       'approved_snapshots': []}), encoding='utf-8')
            with patch('scripts.check_executioner_setup.shutil.which', return_value='/usr/bin/trusted'), \
                 patch('scripts.check_executioner_setup.importlib.util.find_spec', return_value=object()):
                rows = check_setup('reviewed', cfg, check_image_sha=True)
            self.assertTrue(any(x['check'] == 'base_image' and x['status'] == 'FAIL' for x in rows))

    def test_ambiguous_approvals_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = root / 'guest.qcow2'
            base.write_bytes(b'x')
            cfg = root / 'vm.json'
            cfg.write_text(json.dumps({'base_image': str(base),
                                       'base_sha256': hashlib.sha256(b'x').hexdigest(),
                                       'approved_snapshots': ['main', 'main']}), encoding='utf-8')
            with patch('scripts.check_executioner_setup.shutil.which', return_value='/usr/bin/trusted'), \
                 patch('scripts.check_executioner_setup.importlib.util.find_spec', return_value=object()):
                rows = check_setup('reviewed', cfg)
            self.assertTrue(any(x['check'] == 'approved_snapshots' and x['status'] == 'FAIL' for x in rows))

    def test_cli_refuses_reviewed_without_config(self):
        self.assertEqual(main(['--mode', 'reviewed', '--json']), 2)


if __name__ == '__main__':
    unittest.main()
