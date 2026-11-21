"""D5c Gate 5: real Windows cross-controller lease and crash-retry acceptance.

Runs NO VM, project, dependency install, or hostile guest payload. Bind this
acceptance to a fresh post-patch three-fixture regression via Gate 4 validation.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from . import d5c_orphan_registry as registry
from .d5c_gate4_acceptance import SUCCESS as GATE4_SUCCESS, validate as validate_gate4

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
SUCCESS = 'D5C_GATE5_CONTROLLER_RACES_AND_RECOVERY_VERIFIED_NOT_ATTESTED'
CHECKS = (
    'post_patch_real_fixture_evidence_bound',
    'root_lease_blocks_second_controller',
    'live_run_lease_blocks_second_controller_sweep',
    'killed_controller_orphan_recovered',
    'crash_after_identity_file_removal_recovered',
    'tampered_journal_refused_without_deleting',
    'recovery_idempotent_no_leftovers',
    'security_attestation_disabled',
)


def _child(mode: str, root: Path, ready: Path, image: str) -> int:
    if mode == 'root':
        with registry.managed_root_lease(root):
            ready.write_text('LOCKED', encoding='ascii')
            time.sleep(20)
    elif mode == 'run':
        folder, lease = registry.create_leased_run(root)
        try:
            ready.write_text(folder.name, encoding='ascii')
            time.sleep(20)
        finally:
            lease.__exit__(None, None, None)
    elif mode == 'crash':
        folder = registry.create_run(root)
        (folder / 'snapshot.zip').write_bytes(b'GATE5_TRUSTED_TEST')
        original = Path.rmdir
        def crash_at_final_remove(path, *args, **kwargs):
            if path == folder:
                os._exit(41)  # After the signed per-run identity was deleted.
            return original(path, *args, **kwargs)
        with patch.object(Path, 'rmdir', crash_at_final_remove):
            registry.recover_managed_root(root, image)
        return 3  # Failure: injected crash wasn't reached.
    return 0


def _wait_ready(proc: subprocess.Popen, marker: Path, *, seconds: float = 8.0) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.is_file():
            return marker.read_text(encoding='ascii')
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    raise registry.RecoveryError('INDEPENDENT_CONTROLLER_NOT_READY')


def _start_child(mode: str, root: Path, ready: Path, image: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, '-m',
        'projectiq_executioner.phase2.vm_worker.d5c_gate5_acceptance',
        '--_child', mode, '--_root', str(root), '--_ready', str(ready),
        '--_image', image], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _halt(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()  # Only this opt-in acceptance's own trusted Python child.
    proc.wait(timeout=10)


def run(summary: Path, config: Path) -> dict:
    report = {'schema_version': '0.1', 'stage': 'd5c_gate5_concurrency_recovery',
              'status': 'NOT_VERIFIED', 'security_attestation': ATTESTATION,
              'student_code_executed': False, 'vm_launched_by_acceptance': False,
              'checks': {name: 'NOT_VERIFIED' for name in CHECKS}}
    if os.name != 'nt':
        report['failure_code'] = 'WINDOWS_LOCK_ACCEPTANCE_REQUIRED'
        return report
    previous = validate_gate4(summary, config)
    if previous.get('status') != GATE4_SUCCESS or not all(
            x == 'PASS' for x in previous.get('checks', {}).values()):
        report['failure_code'] = 'FRESH_GATE4_RUNTIME_REGRESSION_NOT_VERIFIED'
        return report
    report['checks']['post_patch_real_fixture_evidence_bound'] = 'PASS'
    # All destructive tests below affect ONLY our newly created temp root.
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5c5_accept_') as td:
            base = Path(td)
            root = registry.initialize_root(base / 'managed')
            image = str(base / 'trusted-qemu.exe')  # No VM is launched.
            ready = base / 'ready'
            proc = _start_child('root', root, ready, image)
            try:
                if _wait_ready(proc, ready) != 'LOCKED':
                    raise registry.RecoveryError('ROOT_CONTROLLER_NOT_LOCKED')
                try:
                    with registry.managed_root_lease(root, timeout_seconds=0):
                        raise registry.RecoveryError('SECOND_CONTROLLER_ENTERED_ROOT')
                except registry.RecoveryError as exc:
                    if str(exc) != 'ACTIVE_OR_UNREADABLE_LEASE':
                        raise
                report['checks']['root_lease_blocks_second_controller'] = 'PASS'
            finally:
                _halt(proc)
            ready.unlink(missing_ok=True)

            proc = _start_child('run', root, ready, image)
            try:
                live_id = _wait_ready(proc, ready)
                if not registry.ID_PATTERN.fullmatch(live_id):
                    raise registry.RecoveryError('INVALID_CHILD_RUN_ID')
                blocked = registry.recover_managed_root(root, image)
                if not any(x['run'] == live_id and x['status'] == 'SKIPPED_ACTIVE'
                           for x in blocked) or not (root / live_id).is_dir():
                    raise registry.RecoveryError('ACTIVE_RUN_WAS_NOT_PRESERVED')
                report['checks']['live_run_lease_blocks_second_controller_sweep'] = 'PASS'
            finally:
                _halt(proc)
            ready.unlink(missing_ok=True)
            recovered = registry.recover_managed_root(root, image)
            if not any(x['run'] == live_id and x['status'] == 'RECOVERED'
                       for x in recovered) or (root / live_id).exists():
                raise registry.RecoveryError('KILLED_CONTROLLER_ORPHAN_NOT_RECOVERED')
            report['checks']['killed_controller_orphan_recovered'] = 'PASS'

            proc = _start_child('crash', root, ready, image)
            try:
                if proc.wait(timeout=12) != 41:
                    raise registry.RecoveryError('TERMINAL_DELETION_CRASH_NOT_OBSERVED')
            finally:
                _halt(proc)
            journal = root / registry.ROOT_SWEEP
            if not journal.is_file():
                raise registry.RecoveryError('INTERRUPTED_SWEEP_MISSING_JOURNAL')
            orphan = registry._read_sweep_journal(root)['id']
            if not (root / orphan).is_dir() or (root / orphan / registry.RECORD).exists():
                raise registry.RecoveryError('WRONG_CRASH_INJECTION_BOUNDARY')
            retry = registry.recover_managed_root(root, image)
            if not any(x['run'] == orphan and x['status'] == 'RECOVERED' for x in retry):
                raise registry.RecoveryError('TERMINAL_DELETION_RETRY_FAILED')
            report['checks']['crash_after_identity_file_removal_recovered'] = 'PASS'

            # Deliberate HMAC tamper against a separate signed interrupted run.
            folder = registry.create_run(root)
            original = Path.rmdir
            def fail_at_end(path, *args, **kwargs):
                if path == folder:
                    raise PermissionError('INJECTED_FINAL_DELETE_FAILURE')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'rmdir', fail_at_end):
                attempted = registry.recover_managed_root(root, image)
            if attempted[0]['status'] != 'MANUAL_REVIEW' or not journal.is_file():
                raise registry.RecoveryError('JOURNAL_TEST_SETUP_FAILED')
            authentic = journal.read_bytes()
            forged = json.loads(authentic)
            forged['id'] = registry.PREFIX + 'f' * 20
            journal.write_text(json.dumps(forged), encoding='ascii')
            denied = registry.recover_managed_root(root, image)
            if denied[0]['status'] != 'MANUAL_REVIEW' or not folder.is_dir():
                raise registry.RecoveryError('FORGED_SWEEP_JOURNAL_WAS_ACCEPTED')
            report['checks']['tampered_journal_refused_without_deleting'] = 'PASS'
            journal.write_bytes(authentic)  # Trusted test operator restores it.
            restored = registry.recover_managed_root(root, image)
            if restored[0]['status'] != 'RECOVERED':
                raise registry.RecoveryError('ORIGINAL_SWEEP_JOURNAL_RETRY_FAILED')
            if registry.recover_managed_root(root, image) != [] or (
                    {p.name for p in root.iterdir()} != {registry.ROOT_KEY, registry.ROOT_LEASE}):
                raise registry.RecoveryError('NONIDEMPOTENT_OR_LEFTOVER_MANAGED_RUN')
            report['checks']['recovery_idempotent_no_leftovers'] = 'PASS'
            report['checks']['security_attestation_disabled'] = 'PASS'
    except (OSError, ValueError, registry.RecoveryError,
            subprocess.SubprocessError, TimeoutError) as exc:
        report['failure_code'] = str(exc) if isinstance(exc, registry.RecoveryError) else type(exc).__name__
    if all(value == 'PASS' for value in report['checks'].values()):
        report['status'] = SUCCESS
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Gate 5: Windows controller race/recovery acceptance')
    ap.add_argument('--vm-config', type=Path)
    ap.add_argument('--regression-summary', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--_child', choices=['root', 'run', 'crash'], help=argparse.SUPPRESS)
    ap.add_argument('--_root', type=Path, help=argparse.SUPPRESS)
    ap.add_argument('--_ready', type=Path, help=argparse.SUPPRESS)
    ap.add_argument('--_image', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args._child:
        if not (args._root and args._ready and args._image):
            ap.error('Incomplete internal child arguments')
        return _child(args._child, args._root, args._ready, args._image)
    if not (args.vm_config and args.regression_summary and args.out):
        ap.error('--vm-config, --regression-summary, and --out are required')
    report = run(args.regression_summary, args.vm_config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('ProjectIQ D5c Gate 5 - ' + report['status'])
    print('  Report:', args.out)
    return 0 if report['status'] == SUCCESS else 1


if __name__ == '__main__':
    raise SystemExit(main())
