"""D5e closeout must remain fail-closed, including when files are tampered."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from projectiq_executioner.phase2.vm_worker import d5e_closeout as gate


class D5eCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'evidence'
        self.root.mkdir()
        self.vm_config = Path(self.temp.name) / 'vm_config.json'
        self.vm_config.write_text('{}', encoding='utf-8')
        self.paths = {name: self.root / (name + '.json') for name in ('gate2', 'gate3', 'gate4')}
        self.summary_path = self.root / 'regression_summary.json'
        self.good = {
            'gate2': {
                'kernel_diagnostics': {
                    'child_cgroup_location': 'DIRECT', 'final_scope_state': 'ABSENT',
                    'child_cgroup_member_verified': True, 'child_parent_verified': True,
                    'child_uid_verified': True, 'parent_verified': True,
                    'marker_ready': True, 'stop_marker_published': True,
                },
            },
            'gate3': {
                'guest_diagnostics': {
                    'storage_guard_bootstrap': 'READY', 'workspace_write': 'CREATED_CLEANED',
                    'root_tmp_write': 'DENIED', 'var_tmp_write': 'DENIED',
                    'root_home_write': 'DENIED', 'gateway_connect': 'DENIED',
                },
            },
            'gate4': {
                'host_diagnostics': {
                    'argv_policy': 'EXACT', 'binary_digest': 'MATCH',
                    'image_identity': 'MATCH', 'process_identity': 'MATCH',
                    'ssh_listener': 'LOOPBACK_QEMU_OWNED',
                },
            },
        }
        for name, extra in self.good.items():
            status, execution, checks = gate.EXPECTED[name]
            body = {
                'status': status, 'execution': execution, 'cleanup': gate.CLEAN,
                'security_attestation': gate.ATTESTATION, 'student_code_executed': False,
                'checks': {k: 'PASS' for k in checks}, **extra,
            }
            self._write(name, body)
        self.summary_path.write_text(json.dumps({
            'Passed': True, 'SecurityAttestation': gate.ATTESTATION,
        }), encoding='utf-8')
        self.good_acl = {
            'status': gate.ACL_OK, 'security_attestation': gate.ATTESTATION,
            'acl_modified': False, 'vm_launched': False,
            'checks': {k: 'PASS' for k in (
                'parent_acl', 'root_acl', 'key_acl', 'lease_acl', 'registered_root_valid')},
        }
        self.good_regression = {
            'status': gate.REGRESSION_OK,
            'checks': {k: 'PASS' for k in ('run_registration', 'http_200', 'cleanup')},
        }

    def _write(self, name, body):
        self.paths[name].write_text(json.dumps(body), encoding='utf-8')

    def _read(self, name):
        return json.loads(self.paths[name].read_text('utf-8'))

    def _run(self):
        with patch.object(gate, 'audit_current_acl', return_value=self.good_acl), \
             patch.object(gate, 'validate_regression', return_value=self.good_regression):
            return gate.evaluate(vm_config=self.vm_config, evidence_root=self.root,
                                 gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                                 gate4=self.paths['gate4'], regression_summary=self.summary_path,
                                 post_gate3_regression_reviewed=True)

    def test_reviewed_fixture_pilot_go_but_untrusted_remains_no_go(self):
        result = self._run()
        self.assertEqual(result['status'], gate.SUCCESS)
        self.assertTrue(all(v == 'PASS' for v in result['checks'].values()))
        self.assertEqual(result['decision']['reviewed_pinned_fixture_pilot'], 'GO')
        self.assertEqual(result['decision']['arbitrary_untrusted_student_execution'], 'NO_GO')
        self.assertEqual(result['decision']['phase3_untrusted_execution_integration'], 'BLOCKED')
        self.assertEqual(set(result['evidence_sha256']), {'gate2', 'gate3', 'gate4', 'regression_summary'})
        self.assertEqual(len(result['outstanding_untrusted_execution_blockers']), 8)
        self.assertFalse(result['vm_launched_by_closeout'])

    def test_operator_regression_provenance_is_required(self):
        with patch.object(gate, 'audit_current_acl', return_value=self.good_acl), \
             patch.object(gate, 'validate_regression', return_value=self.good_regression):
            result = gate.evaluate(vm_config=self.vm_config, evidence_root=self.root,
                                   gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                                   gate4=self.paths['gate4'], regression_summary=self.summary_path)
        self.assertEqual(result['status'], gate.INCOMPLETE)
        self.assertEqual(result['checks']['operator_reviewed_post_gate3_regression_provenance'], 'NOT_VERIFIED')

    def test_report_hash_matches_bytes(self):
        import hashlib
        report = self._run()
        self.assertEqual(report['evidence_sha256']['gate2'], hashlib.sha256(self.paths['gate2'].read_bytes()).hexdigest())

    def test_missing_gate_fails_closed(self):
        self.paths['gate3'].unlink()
        result = self._run()
        self.assertEqual(result['status'], gate.INCOMPLETE)
        self.assertEqual(result['checks'][gate.CHECKS[2]], 'NOT_VERIFIED')
        self.assertEqual(result['decision']['reviewed_pinned_fixture_pilot'], 'NO_GO')

    def test_wrong_status_or_cleanup_fails_closed(self):
        for change in ({'status': 'READY'}, {'cleanup': 'CLEANUP_FAILED'},
                       {'execution': 'NOT_PERFORMED'}, {'failure_code': 'FAILURE'},
                       {'student_code_executed': True},
                       {'security_attestation': 'ATTESTED_FOR_UNTRUSTED_STUDENTS'}):
            with self.subTest(change=change):
                original = self._read('gate4')
                self._write('gate4', {**original, **change})
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)
                self._write('gate4', original)

    def test_missing_extra_or_failed_checks_denied(self):
        original = self._read('gate2')
        for checks in (
            {**original['checks'], 'new_unreviewed_claim': 'PASS'},
            {k: v for k, v in original['checks'].items() if k != 'mysql_pids_bounded'},
            {**original['checks'], 'mysql_pids_bounded': 'NOT_VERIFIED'},
        ):
            with self.subTest(checks=checks):
                self._write('gate2', {**original, 'checks': checks})
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)
        self._write('gate2', original)

    def test_gate2_child_or_drain_diagnostics_cannot_be_forged(self):
        original = self._read('gate2')
        for delta in (
            {'child_cgroup_location': 'OUTSIDE'}, {'final_scope_state': 'POPULATED'},
            {'child_parent_verified': False}, {'stop_marker_published': False},
        ):
            with self.subTest(delta=delta):
                self._write('gate2', {**original, 'kernel_diagnostics':
                    {**original['kernel_diagnostics'], **delta}})
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)
        self._write('gate2', original)

    def test_gate3_diagnostic_write_escape_denied(self):
        original = self._read('gate3')
        for field, bad in (
            ('root_tmp_write', 'CREATED_CLEANED'),
            ('var_tmp_write', 'CREATED_CLEANED'),
            ('root_home_write', 'CREATED_CLEANED'),
            ('workspace_write', 'DENIED'),
            ('gateway_connect', 'CONNECTED'),
            ('storage_guard_bootstrap', 'MISSING'),
        ):
            with self.subTest(field=field):
                self._write('gate3', {**original, 'guest_diagnostics':
                    {**original['guest_diagnostics'], field: bad}})
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)
        self._write('gate3', original)

    def test_gate4_runtime_mismatch_denied(self):
        original = self._read('gate4')
        for field, bad in (
            ('argv_policy', 'MISMATCH'), ('binary_digest', 'MISMATCH'),
            ('image_identity', 'MISMATCH'), ('process_identity', 'MISMATCH'),
            ('ssh_listener', 'NOT_VERIFIED'),
        ):
            with self.subTest(field=field):
                self._write('gate4', {**original, 'host_diagnostics':
                    {**original['host_diagnostics'], field: bad}})
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)
        self._write('gate4', original)

    def test_acl_failure_fails_closed(self):
        for acl in ({'status': 'D5D_HOST_ACL_REQUIRES_REVIEW'},
                    {**self.good_acl, 'acl_modified': True},
                    {**self.good_acl, 'checks': {'registered_root_valid': 'PASS'}}):
            with self.subTest(acl=acl):
                with patch.object(gate, 'audit_current_acl', return_value=acl), \
                     patch.object(gate, 'validate_regression', return_value=self.good_regression):
                    result = gate.evaluate(vm_config=self.vm_config,
                                           gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                                           gate4=self.paths['gate4'],
                                           regression_summary=self.summary_path, evidence_root=self.root,
                                           post_gate3_regression_reviewed=True)
                self.assertEqual(result['status'], gate.INCOMPLETE)

    def test_regression_failure_fails_closed(self):
        original = self.summary_path.read_text('utf-8')
        for reg in ({'status': 'FAILED'}, {'status': gate.REGRESSION_OK,
                                        'checks': {'ok': 'NOT_VERIFIED'}}):
            with self.subTest(reg=reg):
                with patch.object(gate, 'audit_current_acl', return_value=self.good_acl), \
                     patch.object(gate, 'validate_regression', return_value=reg):
                    result = gate.evaluate(vm_config=self.vm_config,
                                           gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                                           gate4=self.paths['gate4'],
                                           regression_summary=self.summary_path, evidence_root=self.root,
                                           post_gate3_regression_reviewed=True)
                self.assertEqual(result['status'], gate.INCOMPLETE)
        self.summary_path.write_text(json.dumps({'Passed': False,
            'SecurityAttestation': gate.ATTESTATION}), encoding='utf-8')
        self.assertEqual(self._run()['status'], gate.INCOMPLETE)
        self.summary_path.write_text(original, encoding='utf-8')

    def test_evidence_outside_root_fails_closed(self):
        external = Path(self.temp.name) / 'external.json'
        external.write_bytes(self.paths['gate3'].read_bytes())
        with patch.object(gate, 'audit_current_acl', return_value=self.good_acl), \
             patch.object(gate, 'validate_regression', return_value=self.good_regression):
            result = gate.evaluate(vm_config=self.vm_config, evidence_root=self.root,
                                   gate2=self.paths['gate2'], gate3=external,
                                   gate4=self.paths['gate4'], regression_summary=self.summary_path,
                                 post_gate3_regression_reviewed=True)
        self.assertEqual(result['status'], gate.INCOMPLETE)

    def test_symlink_denied_even_if_target_inside_root(self):
        link = self.root / 'linked.json'
        try:
            link.symlink_to(self.paths['gate3'])
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not permitted')
        with self.assertRaises(ValueError):
            gate._read_evidence(link, self.root.resolve())

    def test_duplicate_json_keys_rejected(self):
        self.paths['gate3'].write_bytes(b'{"status":"PASS","status":"PASS"}')
        self.assertEqual(self._run()['status'], gate.INCOMPLETE)

    def test_empty_and_oversized_json_rejected(self):
        for bytes_ in (b'', b'x' * (gate.MAX_BYTES + 1), b'[]', b'{"n":NaN}'):
            with self.subTest(length=len(bytes_)):
                self.paths['gate3'].write_bytes(bytes_)
                self.assertEqual(self._run()['status'], gate.INCOMPLETE)

    def test_cli_refuses_to_overwrite_evidence(self):
        import contextlib
        import io
        args = [
            '--vm-config', str(self.vm_config),
            '--evidence-root', str(self.root),
            '--gate2', str(self.paths['gate2']),
            '--gate3', str(self.paths['gate3']),
            '--gate4', str(self.paths['gate4']),
            '--regression-summary', str(self.summary_path),
            '--out', str(self.paths['gate2']),
        ]
        original = self.paths['gate2'].read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                gate.main(args)
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.paths['gate2'].read_bytes(), original)

    def test_external_validator_exception_is_fail_closed(self):
        with patch.object(gate, 'audit_current_acl', side_effect=RuntimeError('host read failed')), \
             patch.object(gate, 'validate_regression', side_effect=RuntimeError('invalid root')):
            result = gate.evaluate(vm_config=self.vm_config, evidence_root=self.root,
                                   gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                                   gate4=self.paths['gate4'], regression_summary=self.summary_path,
                                   post_gate3_regression_reviewed=True)
        self.assertEqual(result['status'], gate.INCOMPLETE)
        self.assertIn('CURRENT_HOST_ACL_NOT_VERIFIED', result['failure_codes'])
        self.assertIn('REVIEWED_FIXTURE_REGRESSION_NOT_VERIFIED', result['failure_codes'])
        self.assertEqual(result['decision']['arbitrary_untrusted_student_execution'], 'NO_GO')

    def test_missing_root_fails_closed(self):
        report = gate.evaluate(vm_config=self.vm_config, evidence_root=self.root / 'missing',
                               gate2=self.paths['gate2'], gate3=self.paths['gate3'],
                               gate4=self.paths['gate4'], regression_summary=self.summary_path,
                               post_gate3_regression_reviewed=True)
        self.assertEqual(report['failure_code'], 'EVIDENCE_ROOT_UNAVAILABLE')
        self.assertEqual(report['status'], gate.INCOMPLETE)

    def test_attestation_never_promoted_even_if_pilot_go(self):
        report = self._run()
        self.assertEqual(report['security_attestation'], gate.ATTESTATION)
        self.assertNotIn('ATTESTED', report['status'].replace('NOT_ATTESTED', ''))
        self.assertIn('QEMU_BINARY_PUBLISHER_PROVENANCE_NOT_ESTABLISHED_BY_SELF_PIN',
                      report['outstanding_untrusted_execution_blockers'])


if __name__ == '__main__':
    unittest.main()
