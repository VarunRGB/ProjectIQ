"""D5d read-only Windows ACL precheck: classification and negative evidence."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5c_orphan_registry as registry
from projectiq_executioner.phase2.vm_worker import d5d_host_acl_audit as audit

OPERATOR = 'S-1-5-21-999-101'
ADMIN = 'S-1-5-32-544'
USERS = 'S-1-5-32-545'
UNKNOWN = 'S-1-5-21-999-102'


def acl_file(name: str, *, owner: str = OPERATOR, aces: list | None = None) -> dict:
    if aces is None:
        aces = [
            {'sid': OPERATOR, 'rights': 0x1F01FF, 'type': 'Allow'},
            {'sid': ADMIN, 'rights': 0x1F01FF, 'type': 'Allow'},
            {'sid': USERS, 'rights': 0x20089, 'type': 'Allow'},
        ]
    return {'name': name, 'owner': owner, 'dacl_present': True, 'dacl_null': False, 'aces': aces}


def valid_snapshot() -> dict:
    return {'operator': OPERATOR,
            'files': [acl_file(name) for name in audit.NAMES]}


class ACLPolicyTests(unittest.TestCase):
    def test_operator_admin_readonly_users_pass(self):
        self.assertEqual(audit._assess_snapshot(valid_snapshot()),
                         {n: 'PASS' for n in audit.NAMES})

    def test_builtin_users_modify_is_rejected(self):
        data = valid_snapshot()
        data['files'][2]['aces'].append({'sid': USERS, 'rights': 0x301BF, 'type': 'Allow'})
        self.assertEqual(audit._assess_snapshot(data)['key'], 'FAIL')

    def test_authenticated_users_write_denied(self):
        data = valid_snapshot()
        data['files'][1]['aces'].append({'sid': 'S-1-5-11', 'rights': 2, 'type': 'Allow'})
        self.assertEqual(audit._assess_snapshot(data)['root'], 'FAIL')

    def test_unknown_writable_principal_requires_review(self):
        data = valid_snapshot()
        data['files'][3]['aces'].append({'sid': UNKNOWN, 'rights': 0x40000, 'type': 'Allow'})
        self.assertEqual(audit._assess_snapshot(data)['lease'], 'MANUAL_REVIEW')

    def test_unknown_readonly_principal_is_allowed(self):
        data = valid_snapshot()
        data['files'][3]['aces'].append({'sid': UNKNOWN, 'rights': 0x20089, 'type': 'Allow'})
        self.assertEqual(audit._assess_snapshot(data)['lease'], 'PASS')

    def test_owner_unexpected_requires_review(self):
        data = valid_snapshot()
        data['files'][1]['owner'] = UNKNOWN
        self.assertEqual(audit._assess_snapshot(data)['root'], 'MANUAL_REVIEW')

    def test_deny_entry_does_not_cancel_broad_allow(self):
        data = valid_snapshot()
        data['files'][1]['aces'].extend([
            {'sid': USERS, 'rights': 2, 'type': 'Deny'},
            {'sid': USERS, 'rights': 2, 'type': 'Allow'},
        ])
        self.assertEqual(audit._assess_snapshot(data)['root'], 'FAIL')

    def test_bad_acl_objects_not_promoted(self):
        for invalid in ({}, {'operator': OPERATOR, 'files': []},
                        {'operator': OPERATOR, 'files': [acl_file('root')] * 3}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                audit._assess_snapshot(invalid)

    def test_null_dacl_denied_even_if_rules_empty(self):
        data = valid_snapshot()
        data['files'][0]['dacl_null'] = True
        data['files'][0]['aces'] = []
        self.assertEqual(audit._assess_snapshot(data)['parent'], 'FAIL')

    def test_invalid_dacl_metadata_is_not_trusted(self):
        data = valid_snapshot()
        del data['files'][0]['dacl_present']
        with self.assertRaises(ValueError):
            audit._assess_snapshot(data)

    def test_owner_rights_with_verified_owner_is_not_an_extra_writer(self):
        # OWNER RIGHTS (S-1-3-4) is evaluated as the current object owner.
        # The operator, SYSTEM and Administrators are already trusted owners.
        for owner in (OPERATOR, 'S-1-5-18', ADMIN):
            with self.subTest(owner=owner):
                snapshot = valid_snapshot()
                for record in snapshot['files']:
                    record['owner'] = owner
                    record['aces'].append({
                        'sid': 'S-1-3-4', 'rights': 0x1F01FF, 'type': 'Allow',
                    })
                findings = {}
                self.assertEqual(audit._assess_snapshot(snapshot, findings=findings),
                                 {name: 'PASS' for name in audit.NAMES})
                self.assertTrue(all(not codes for codes in findings.values()))

    def test_owner_rights_with_untrusted_owner_stays_manual_review(self):
        snapshot = valid_snapshot()
        snapshot['files'][1]['owner'] = UNKNOWN
        snapshot['files'][1]['aces'].append({
            'sid': 'S-1-3-4', 'rights': 0x1F01FF, 'type': 'Allow',
        })
        reasons = {}
        result = audit._assess_snapshot(snapshot, findings=reasons)
        self.assertEqual(result['root'], 'MANUAL_REVIEW')
        self.assertIn('UNRECOGNIZED_OBJECT_OWNER', reasons['root'])
        self.assertIn('UNRECOGNIZED_WRITABLE_OWNER_RIGHTS', reasons['root'])

    def test_owner_rights_does_not_hide_untrusted_or_broad_writer(self):
        for principal, expected in ((UNKNOWN, 'MANUAL_REVIEW'), (USERS, 'FAIL')):
            with self.subTest(principal=principal):
                snapshot = valid_snapshot()
                snapshot['files'][2]['aces'].extend([
                    {'sid': 'S-1-3-4', 'rights': 0x1F01FF, 'type': 'Allow'},
                    {'sid': principal, 'rights': 0x40000, 'type': 'Allow'},
                ])
                self.assertEqual(audit._assess_snapshot(snapshot)['key'], expected)

    def test_creator_owner_is_not_whitelisted_as_owner_rights(self):
        snapshot = valid_snapshot()
        snapshot['files'][1]['aces'].append({
            'sid': 'S-1-3-0', 'rights': 0x1F01FF, 'type': 'Allow',
        })
        self.assertEqual(audit._assess_snapshot(snapshot)['root'], 'MANUAL_REVIEW')

    def test_reason_codes_distinguish_unknown_owner_from_writer(self):
        data = valid_snapshot()
        data['files'][1]['owner'] = UNKNOWN
        data['files'][2]['aces'].append({'sid': 'S-1-3-0', 'rights': 2, 'type': 'Allow'})
        reasons = {}
        checks = audit._assess_snapshot(data, findings=reasons)
        self.assertEqual(checks['root'], 'MANUAL_REVIEW')
        self.assertEqual(reasons['root'], ['UNRECOGNIZED_OBJECT_OWNER'])
        self.assertEqual(checks['key'], 'MANUAL_REVIEW')
        self.assertEqual(reasons['key'], ['UNRECOGNIZED_WRITABLE_CREATOR_OWNER'])
        self.assertEqual(reasons['lease'], [])
        self.assertNotIn(UNKNOWN, str(reasons))

    def test_broad_writer_diagnostic_does_not_turn_fail_into_review(self):
        data = valid_snapshot()
        data['files'][0]['aces'].append({'sid': USERS, 'rights': 2, 'type': 'Allow'})
        reasons = {}
        checks = audit._assess_snapshot(data, findings=reasons)
        self.assertEqual(checks['parent'], 'FAIL')
        self.assertIn('BROAD_PRINCIPAL_HAS_WRITE_ACCESS', reasons['parent'])

    def test_bogus_rights_type_rejected(self):
        data = valid_snapshot()
        data['files'][0]['aces'][0]['rights'] = 'write'
        with self.assertRaises(ValueError):
            audit._assess_snapshot(data)


class RealRootWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = registry.initialize_root(Path(self.tmp.name) / 'managed')
        self.config = Path(self.tmp.name) / 'config.json'
        self.config.write_text(json.dumps({
            'managed_run_root': str(self.root),
            'qemu_binary': str(Path(self.tmp.name) / 'qemu-system-x86_64.exe'),
        }), encoding='utf-8')

    def _run(self, snapshot=None):
        with patch.object(audit, '_windows_supported', return_value=True), \
             patch.object(audit, '_read_acl_snapshot', return_value=snapshot or valid_snapshot()):
            return audit.audit(self.config)

    def test_clean_root_readonly_precheck_passes(self):
        report = self._run()
        self.assertEqual(report['status'], audit.SUCCESS)
        self.assertEqual(set(report['checks'].values()), {'PASS'})
        self.assertFalse(report['vm_launched'])
        self.assertFalse(report['acl_modified'])
        self.assertEqual(report['security_attestation'], audit.ATTESTATION)
        self.assertTrue(self.root.is_dir())

    def test_unresolved_run_is_preserved_and_blocks_audit(self):
        folder = registry.create_run(self.root)
        report = self._run()
        self.assertEqual(report['status'], audit.DENIED)
        self.assertEqual(report['failure_code'], 'ROOT_HAS_UNRESOLVED_ENTRIES')
        self.assertTrue(folder.exists())

    def test_real_audit_reports_redacted_diagnostic_reasons(self):
        snapshot = valid_snapshot()
        snapshot['files'][2]['aces'].append({'sid': UNKNOWN, 'rights': 0x40000, 'type': 'Allow'})
        report = self._run(snapshot)
        self.assertEqual(report['checks']['key_acl'], 'MANUAL_REVIEW')
        self.assertEqual(report['acl_findings']['key'], ['UNRECOGNIZED_WRITABLE_OTHER_PRINCIPAL'])
        self.assertNotIn(UNKNOWN, json.dumps(report))
        self.assertFalse(report['acl_modified'])

    def test_broad_root_key_write_blocks_pass(self):
        snapshot = valid_snapshot()
        snapshot['files'][2]['aces'].append({'sid': USERS, 'rights': 2, 'type': 'Allow'})
        report = self._run(snapshot)
        self.assertEqual(report['status'], audit.DENIED)
        self.assertEqual(report['checks']['key_acl'], 'FAIL')

    def test_parent_broad_writer_blocks_pass(self):
        snapshot = valid_snapshot()
        snapshot['files'][0]['aces'].append({'sid': USERS, 'rights': 0x40, 'type': 'Allow'})
        report = self._run(snapshot)
        self.assertEqual(report['status'], audit.DENIED)
        self.assertEqual(report['checks']['parent_acl'], 'FAIL')

    def test_corrupt_root_lease_blocks_audit(self):
        (self.root / registry.ROOT_LEASE).write_bytes(b'INVALID')
        report = self._run()
        self.assertEqual(report['status'], audit.DENIED)
        self.assertEqual(report['failure_code'], 'INVALID_ROOT_LEASE')

    def test_no_windows_host_evidence_is_not_a_pass(self):
        with patch.object(audit, '_windows_supported', return_value=False):
            report = audit.audit(self.config)
        self.assertEqual(report['status'], audit.DENIED)
        self.assertEqual(report['failure_code'], 'WINDOWS_REQUIRED')


if __name__ == '__main__':
    unittest.main()
