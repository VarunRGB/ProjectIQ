"""D5e: read-only, fail-closed closeout for the reviewed-fixture VM pilot.

This is deliberately NOT a general-purpose student sandbox approval. The only
possible positive decision is for already approved, SHA-pinned reviewed fixtures.
No VM, student code, installation, cleanup or host security-policy mutation is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .d5c_gate4_acceptance import SUCCESS as REGRESSION_OK, validate as validate_regression
from .d5d_host_acl_audit import SUCCESS as ACL_OK, audit as audit_current_acl
from .d5d_host_runtime_acceptance import CHECKS as GATE4_CHECKS, SUCCESS as GATE4_OK
from .d5d_process_acceptance import FIELDS as GATE2_CHECKS, STATUS_OBSERVED as GATE2_OK
from .d5d_boundary_acceptance import ALL_CHECKS as GATE3_CHECKS, STATUS_PASS as GATE3_OK

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
SUCCESS = 'D5E_REVIEWED_FIXTURE_PILOT_CLOSED_NOT_ATTESTED'
INCOMPLETE = 'D5E_EVIDENCE_INCOMPLETE_NO_GO'
MAX_BYTES = 100_000
CHECKS = (
    'current_host_acl_verified',
    'gate2_synthetic_process_and_mysql_verified',
    'gate3_tested_storage_and_network_verified',
    'gate4_independent_windows_qemu_verified',
    'three_pinned_reviewed_fixtures_verified',
    'operator_reviewed_post_gate3_regression_provenance',
    'all_diagnostic_vm_cleanups_verified',
    'all_diagnostic_vms_source_free',
    'untrusted_execution_attestation_not_promoted',
)
# Do not let a passing report remove these unresolved limitations.
OPEN_BLOCKERS = (
    'REAL_BUILD_DESCENDANT_LIFECYCLE_NOT_FULLY_INDEPENDENTLY_VERIFIED',
    'ALL_GUEST_ROOT_FILESYSTEM_WRITES_AND_QCOW2_GROWTH_NOT_BOUNDED',
    'GUEST_TO_HOST_AND_HYPERVISOR_ESCAPE_RESISTANCE_NOT_ATTESTED',
    'SSH_HOST_KEY_BOOTSTRAP_IS_TRUST_ON_FIRST_USE',
    'SAME_WINDOWS_OPERATOR_COMPROMISE_OUTSIDE_THREAT_MODEL',
    'QEMU_BINARY_PUBLISHER_PROVENANCE_NOT_ESTABLISHED_BY_SELF_PIN',
    'SAVED_ACCEPTANCE_JSON_NOT_TAMPER_PROOF_OR_LIVE_MONITORING',
    'LEGACY_REGRESSION_SUMMARY_DOES_NOT_BIND_CODE_REVISION',
)

EXPECTED = {
    'gate2': (GATE2_OK, 'TRUSTED_D5D_PROBE_IN_CLEAN_VM', GATE2_CHECKS),
    'gate3': (GATE3_OK, 'TRUSTED_STORAGE_NETWORK_PROBE_IN_CLEAN_VM', GATE3_CHECKS),
    'gate4': (GATE4_OK, 'TRUSTED_LIVE_WINDOWS_QEMU_POSTURE_OBSERVED', GATE4_CHECKS),
}


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('DUPLICATE_JSON_KEY')
        obj[key] = value
    return obj


def _read_evidence(path: Path, root: Path) -> tuple[dict, str]:
    """Only bounded ordinary JSON files below the operator's evidence root."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('EVIDENCE_NOT_REGULAR_FILE')
    real = path.resolve(strict=True)
    if not real.is_relative_to(root):
        raise ValueError('EVIDENCE_OUTSIDE_ROOT')
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError('EVIDENCE_BAD_SIZE')
    data = path.read_bytes()
    if len(data) != size:
        raise ValueError('EVIDENCE_RACED')
    def reject_nonfinite(_: str) -> None:
        raise ValueError('NONFINITE_JSON')

    obj = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_no_duplicate_keys,
                     parse_constant=reject_nonfinite)
    if type(obj) is not dict:
        raise ValueError('EVIDENCE_NOT_OBJECT')
    return obj, hashlib.sha256(data).hexdigest()


def _verify_diagnostic(stage: str, obj: dict) -> bool:
    status, execution, expected_checks = EXPECTED[stage]
    if (obj.get('status') != status or obj.get('execution') != execution or
            obj.get('cleanup') != CLEAN or obj.get('security_attestation') != ATTESTATION or
            obj.get('student_code_executed') is not False or
            obj.get('failure_code') not in (None, '') or
            type(obj.get('checks')) is not dict or
            set(obj['checks']) != set(expected_checks) or
            any(v != 'PASS' for v in obj['checks'].values())):
        return False
    if stage == 'gate2':
        d = obj.get('kernel_diagnostics')
        return (type(d) is dict and
                d.get('child_cgroup_location') in ('DIRECT', 'DESCENDANT') and
                d.get('final_scope_state') in ('DRAINED', 'ABSENT') and
                all(d.get(k) is True for k in (
                    'child_cgroup_member_verified', 'child_parent_verified',
                    'child_uid_verified', 'parent_verified', 'marker_ready',
                    'stop_marker_published')))
    if stage == 'gate3':
        d = obj.get('guest_diagnostics')
        return (type(d) is dict and
                d.get('storage_guard_bootstrap') == 'READY' and
                d.get('workspace_write') == 'CREATED_CLEANED' and
                d.get('gateway_connect') == 'DENIED' and
                all(d.get(k) == 'DENIED' for k in (
                    'root_tmp_write', 'var_tmp_write', 'root_home_write')))
    d = obj.get('host_diagnostics')
    return (type(d) is dict and d.get('argv_policy') == 'EXACT' and
            d.get('binary_digest') == 'MATCH' and
            d.get('image_identity') == 'MATCH' and
            d.get('process_identity') == 'MATCH' and
            d.get('ssh_listener') == 'LOOPBACK_QEMU_OWNED')


def _base() -> dict:
    return {
        'schema_version': '0.1',
        'stage': 'd5e_phase2_reviewed_fixture_pilot_closeout',
        'status': INCOMPLETE,
        'security_attestation': ATTESTATION,
        'student_code_executed_by_closeout': False,
        'vm_launched_by_closeout': False,
        'host_security_controls_modified_by_closeout': False,
        'checks': {name: 'NOT_VERIFIED' for name in CHECKS},
        'decision': {
            'reviewed_pinned_fixture_pilot': 'NO_GO',
            'arbitrary_untrusted_student_execution': 'NO_GO',
            'phase3_untrusted_execution_integration': 'BLOCKED',
        },
        'permitted_scope_if_passed': 'EXISTING_OPERATOR_APPROVED_PINNED_FIXTURES_ONLY',
        'outstanding_untrusted_execution_blockers': list(OPEN_BLOCKERS),
        'evidence_sha256': {},
        'failure_codes': [],
        'operator_regression_review': 'NOT_CONFIRMED',
    }


def evaluate(*, vm_config: Path, gate2: Path, gate3: Path,
             gate4: Path, regression_summary: Path, evidence_root: Path,
             post_gate3_regression_reviewed: bool = False) -> dict:
    """Reconcile saved observations; independently re-audit current host ACLs."""
    report = _base()
    if post_gate3_regression_reviewed is True:
        report['operator_regression_review'] = 'POST_GATE3_FIXTURE_RUN_CONFIRMED_BY_OPERATOR'
    try:
        root = Path(evidence_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('INVALID_EVIDENCE_ROOT')
    except (OSError, ValueError, RuntimeError):
        report['failure_code'] = 'EVIDENCE_ROOT_UNAVAILABLE'
        report['failure_codes'].append('EVIDENCE_ROOT_UNAVAILABLE')
        return report

    valid = {}
    for stage, path in (('gate2', gate2), ('gate3', gate3), ('gate4', gate4)):
        try:
            record, digest = _read_evidence(path, root)
            if _verify_diagnostic(stage, record):
                valid[stage] = record
                report['evidence_sha256'][stage] = digest
                check = {'gate2': CHECKS[1], 'gate3': CHECKS[2], 'gate4': CHECKS[3]}[stage]
                report['checks'][check] = 'PASS'
        except (OSError, ValueError, UnicodeError, TypeError, RuntimeError):
            pass  # Invalid evidence stays NOT_VERIFIED, never a PASS.
        if stage not in valid:
            report['failure_codes'].append(stage.upper() + '_EVIDENCE_INVALID_OR_MISSING')

    try:
        acl = audit_current_acl(Path(vm_config))
        if (type(acl) is dict and acl.get('status') == ACL_OK and
                acl.get('security_attestation') == ATTESTATION and
                acl.get('acl_modified') is False and acl.get('vm_launched') is False and
                type(acl.get('checks')) is dict and len(acl['checks']) == 5 and
                all(v == 'PASS' for v in acl['checks'].values())):
            report['checks'][CHECKS[0]] = 'PASS'
    except Exception:
        pass  # Host audit failures never enable the pilot.
    if report['checks'][CHECKS[0]] != 'PASS':
        report['failure_codes'].append('CURRENT_HOST_ACL_NOT_VERIFIED')

    try:
        summary, digest = _read_evidence(regression_summary, root)
        # Bind the summary's actual three per-fixture reports, approved SHAs,
        # cgroup placement, cleanup and signed run workspace using D5c Gate 4.
        regression = validate_regression(Path(regression_summary), Path(vm_config))
        if (summary.get('Passed') is True and
                summary.get('SecurityAttestation') == ATTESTATION and
                regression.get('status') == REGRESSION_OK and
                type(regression.get('checks')) is dict and
                all(v == 'PASS' for v in regression['checks'].values())):
            report['checks'][CHECKS[4]] = 'PASS'
            report['evidence_sha256']['regression_summary'] = digest
    except Exception:
        pass  # Existing validator may reject configuration or managed-root state.
    if report['checks'][CHECKS[4]] != 'PASS':
        report['failure_codes'].append('REVIEWED_FIXTURE_REGRESSION_NOT_VERIFIED')

    if (report['checks'][CHECKS[4]] == 'PASS' and
            post_gate3_regression_reviewed is True):
        report['checks'][CHECKS[5]] = 'PASS'
    elif post_gate3_regression_reviewed is not True:
        report['failure_codes'].append('OPERATOR_REGRESSION_PROVENANCE_NOT_CONFIRMED')
    if len(valid) == 3:
        report['checks'][CHECKS[6]] = 'PASS'
        report['checks'][CHECKS[7]] = 'PASS'
        report['checks'][CHECKS[8]] = 'PASS'
    if all(value == 'PASS' for value in report['checks'].values()):
        report['status'] = SUCCESS
        report['decision']['reviewed_pinned_fixture_pilot'] = 'GO'
    elif report['failure_codes']:
        report['failure_code'] = report['failure_codes'][0]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='D5e read-only reviewed-fixture pilot closeout')
    parser.add_argument('--vm-config', required=True, type=Path)
    parser.add_argument('--evidence-root', default=Path('outputs/executioner'), type=Path)
    parser.add_argument('--gate2', required=True, type=Path)
    parser.add_argument('--gate3', required=True, type=Path)
    parser.add_argument('--gate4', required=True, type=Path)
    parser.add_argument('--regression-summary', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--confirm-post-gate3-regression', action='store_true',
                        help='Operator reviewed the supplied fixture regression and confirms it ran after Gate 3 production changes; this is not cryptographic provenance')
    args = parser.parse_args(argv)
    source_files = (args.vm_config, args.gate2, args.gate3,
                    args.gate4, args.regression_summary)
    if (args.out.is_symlink() or args.out.resolve() in {
            path.resolve() for path in source_files}):
        parser.error('--out must not overwrite an input or be a symlink')
    report = evaluate(vm_config=args.vm_config, gate2=args.gate2,
                      gate3=args.gate3, gate4=args.gate4,
                      regression_summary=args.regression_summary,
                      evidence_root=args.evidence_root,
                      post_gate3_regression_reviewed=args.confirm_post_gate3_regression)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('ProjectIQ D5e - ' + report['status'])
    print('  Pilot:', report['decision']['reviewed_pinned_fixture_pilot'])
    print('  Untrusted:', report['decision']['arbitrary_untrusted_student_execution'])
    print('  Report:', str(args.out))
    return 0 if report['status'] == SUCCESS else 1


if __name__ == '__main__':
    raise SystemExit(main())
