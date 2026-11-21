"""D5d Gate 3: POSIX ACL emission and negative root-disk write evidence."""
from __future__ import annotations

import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5d_storage_guard as guard


class RootStorageAclTests(unittest.TestCase):
    def test_new_acl_blocks_only_named_app_writes(self):
        raw = guard._acl_with_denied_write(None, 0o41777, 1402)
        rows = guard._entries(raw)
        self.assertEqual(rows[0], (guard.USER_OBJ, 7, guard.NO_ID))
        self.assertEqual(rows[1], (guard.USER, 5, 1402))
        self.assertEqual(rows[-1], (guard.OTHER, 7, guard.NO_ID))
        self.assertEqual([r[1] for r in rows if r[0] == guard.MASK], [7])

    def test_existing_acl_entries_preserved(self):
        original = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *row)
            for row in ((guard.USER_OBJ,7,guard.NO_ID),
                        (guard.USER,7,2001), (guard.GROUP_OBJ,5,guard.NO_ID),
                        (guard.GROUP,6,2010), (guard.MASK,7,guard.NO_ID),
                        (guard.OTHER,7,guard.NO_ID)))
        raw = guard._acl_with_denied_write(original, 0o41777, 1402)
        rows = guard._entries(raw)
        self.assertIn((guard.USER,7,2001), rows)
        self.assertIn((guard.GROUP,6,2010), rows)
        self.assertIn((guard.USER,5,1402), rows)
        self.assertIn((guard.MASK,7,guard.NO_ID), rows)

    def test_existing_app_entry_drops_write_without_mask_relaxation(self):
        raw = guard._acl_with_denied_write(None, 0o41777, 1402)
        raw = guard._acl_with_denied_write(raw, 0o41777, 1402)
        self.assertEqual([x for x in guard._entries(raw) if x[0] == guard.USER],
                         [(guard.USER,5,1402)])

    def test_invalid_acl_and_uid_fail_closed(self):
        for raw in (b'bad', b'\x02\x00\x00\x00', b'\x01\x00\x00\x00' + b'0' * 24):
            with self.subTest(raw=raw), self.assertRaises(guard.StorageGuardError):
                guard._acl_with_denied_write(raw, 0o41777, 1402)
        for uid in (0, -1, guard.NO_ID, 'abc'):
            with self.subTest(uid=uid), self.assertRaises(guard.StorageGuardError):
                guard._acl_with_denied_write(None, 0o41777, uid)

    def test_reject_nonroot_or_nonsticky_root_tmp_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'tmp'
            p.mkdir()
            with self.assertRaises(guard.StorageGuardError), \
                 patch.object(guard.os, 'setxattr', side_effect=AssertionError('MUST NOT SET'), create=True):
                guard._deny_app_tmp_write(p, 1402)

    @unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                         'Requires Linux root to exercise kernel ACL enforcement')
    def test_real_kernel_denies_named_uid_but_preserves_mode(self):
        import pwd
        import subprocess
        uid = pwd.getpwnam('nobody').pw_uid
        with tempfile.TemporaryDirectory() as td:
            os.chmod(td, 0o755)
            p = Path(td) / 'tmp'
            p.mkdir(mode=0o777)
            p.chmod(0o1777)
            try:
                guard._deny_app_tmp_write(p, uid)
            except guard.StorageGuardError as exc:
                self.skipTest('Test filesystem has no POSIX ACL support: ' + str(exc))
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o1777)
            denied = subprocess.run(['su', '-s', '/bin/sh', 'nobody', '-c',
                                      'touch ' + str(p / 'forbidden')], capture_output=True)
            self.assertNotEqual(denied.returncode, 0)
            self.assertFalse((p / 'forbidden').exists())

    def test_home_lock_refuses_wrong_path(self):
        with self.assertRaisesRegex(guard.StorageGuardError, 'UNEXPECTED_APP_HOME'):
            guard._lock_app_home(Path('/tmp'), 1402)

    def test_main_never_runs_on_windows(self):
        with patch.object(guard.sys, 'platform', 'win32'):
            self.assertEqual(guard.main(), 2)


if __name__ == '__main__':
    unittest.main()
