"""D5b/B4: single-command, fail-closed consolidation of *fresh* B3b evidence.

Operator-approved fixtures only. No arbitrary student repositories are run.
Four existing destructive clean-VM gates are rerun sequentially; the saved
reviewed-fixture PowerShell regression harness supplies separate real-project
acceptance. A successful checkpoint is NOT a security attestation and does not
cover every adapter, build descendant, database service, or host-failure mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

PINNED_HEAD = 'b500900f3deb9a310cefb9b4099370b73936cea4'
HARNESS_RELATIVE = 'projectiq_executioner/tools/d5_b4_regression.ps1'
HARNESS_SHA256 = '2543ace6c23545bbe33ca2f420f0b18bfaa4c775d9b689b2139bd0174edfb886'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
CHECKPOINT_OK = 'D5B_B4_REVIEWED_FIXTURE_CHECKPOINT_PASSED_NOT_ATTESTED'
OPEN_RISKS = (
    'NOT_ALL_SUPPORTED_ADAPTERS_HAVE_REAL_VM_REGRESSION',
    'FULL_BUILD_DESCENDANT_LIFECYCLE_CONFINEMENT_NOT_VERIFIED',
    'MYSQL_SYSTEM_SERVICE_NOT_CONFIRMED_IN_EVALUATION_SLICE',
    'GUEST_ROOT_DISK_AND_OTHER_WRITABLE_PATHS_NOT_CONFINED',
    'D5C_FAILURE_ORPHAN_AND_HOST_CANCELLATION_PENDING',
    'D5D_INDEPENDENT_IDENTITY_NETWORK_AND_ISOLATION_REVIEW_PENDING',
    'D5E_SECURITY_GO_NO_GO_REVIEW_PENDING',
)

# Existing, independently reviewed source files: prevent silently testing a
# different gate implementation. Git blob hashes are stable on CRLF checkouts.
SOURCE_BLOBS = {
    'projectiq_executioner/phase2/vm_worker/d5_memory_acceptance.py': '83c39fd22c56bc01081317c55a6d86ac5c488714',
    'projectiq_executioner/phase2/vm_worker/d5_pid_acceptance.py': '03126f876dfe2f19838085d667515a3f106de974',
    'projectiq_executioner/phase2/vm_worker/d5_io_acceptance.py': '3b056a4d8a8ce6c46da98af8b1552e9314930a01',
    'projectiq_executioner/phase2/vm_worker/d5_recovery_acceptance.py': '881e25c316a3563f577f09b66e2b66a15d3c03fa',
    'projectiq_executioner/phase2/vm_worker/qemu.py': 'cc6692688cd54b6ab89eea05ca52409a41199a6e',
}

# module, acknowledgement, expected stage, status, execution, checks, max run seconds
GATES = {
    'memory': (
        'd5_memory_acceptance', '--acknowledge-3584mib-vm',
        'd5b_b3b_gate2b1_eval_memory',
        'EVALUATION_SLICE_MEMORY_ENFORCED_NOT_ATTESTED',
        'TRUSTED_MEMORY_PROBE_IN_CLEAN_VM',
        ('trusted_bootstraps_complete', 'evaluation_slice_kernel_policy',
         'guest_ram_headroom', 'two_unprivileged_scoped_workers',
         'memory_events_cgroup_oom_kill', 'memory_peak_near_policy_limit',
         'guest_memory_recovers', 'worker_scopes_cleaned'), 450,
    ),
    'pid': (
        'd5_pid_acceptance', '--acknowledge-pid-stress',
        'd5b_b3b_gate2b2_eval_pids',
        'EVALUATION_SLICE_PIDS_ENFORCED_NOT_ATTESTED',
        'TRUSTED_PID_PROBE_IN_CLEAN_VM',
        ('trusted_bootstraps_complete', 'evaluation_slice_kernel_policy',
         'two_unprivileged_scoped_workers', 'first_worker_threads_held',
         'aggregate_tasks_reached_limit', 'kernel_pids_limit_denied',
         'guest_pid_capacity_recovers', 'worker_scopes_cleaned'), 500,
    ),
    'io': (
        'd5_io_acceptance', '--acknowledge-io-stress',
        'd5b_b3b_gate2b3_eval_io_throttle',
        'EVALUATION_SLICE_IO_THROTTLED_NOT_ATTESTED',
        'TRUSTED_IO_PROBE_IN_CLEAN_VM',
        ('trusted_bootstraps_complete', 'evaluation_slice_kernel_policy',
         'unthrottled_baseline_exceeds_limit', 'baseline_scope_cleaned',
         'two_unprivileged_scoped_workers', 'both_worker_io_accounted',
         'kernel_workspace_writes_observed', 'aggregate_write_rate_throttled',
         'worker_scopes_cleaned', 'workspace_write_recovery'), 520,
    ),
    'recovery': (
        'd5_recovery_acceptance', '--acknowledge-hard-vm-kill',
        'd5b_b3b_gate2b4_vm_recovery',
        'EVALUATION_VM_FAILURE_RECOVERY_VERIFIED_NOT_ATTESTED',
        'FRESH_VM_BOOTED_AFTER_FORCED_TERMINATION',
        ('trusted_bootstraps_complete', 'evaluation_slice_kernel_policy',
         'controlled_crash_scope_drained', 'controlled_cancel_scope_drained',
         'first_vm_worker_verified', 'forced_vm_termination_observed',
         'first_vm_storage_removed', 'fresh_vm_booted',
         'fresh_vm_workspace_and_slice_clean', 'fresh_vm_storage_removed'), 650,
    ),
}
FIXTURES = {
    'FSDL': ('static_html_v1', 1, False),
    'MiniCollabApp': ('php_mysql_v1', 1, False),
    'FullStack': ('node_fullstack_offline_v1', 2, True),
}


def _int(record: dict, key: str, *, low: int = 0, high: int = 2**48) -> bool:
    v = record.get(key)
    return type(v) is int and low <= v <= high


def _two_scope_clean(diag: dict) -> bool:
    rows = diag.get('scope_cleanup_observations')
    if not isinstance(rows, list) or len(rows) != 2:
        return False
    return all(type(row) is dict and row.get('worker') == i
               and row.get('scope_state') in ('ABSENT', 'DRAINED')
               and row.get('remaining_tasks') == 0
               and row.get('stop_marker_published') is True
               for i, row in enumerate(rows, 1))


def _two_launches(diag: dict) -> bool:
    rows = diag.get('worker_launch_observations')
    return (type(rows) is list and len(rows) == 2 and
            all(type(row) is dict and row.get('worker') == i and
                row.get('verified') is True and row.get('app_uid_seen') is True and
                row.get('cgroup_seen') is True and row.get('argv_seen') is True and
                row.get('scope_seen') is True for i, row in enumerate(rows, 1)))


def _kernel_gate_evidence(name: str, report: dict) -> bool:
    d = report.get('kernel_observations') if name != 'recovery' else report.get('observations')
    if type(d) is not dict:
        return False
    if name == 'memory':
        return (_two_launches(d) and _two_scope_clean(d) and
                d.get('scope_cleanup_observed') is True and
                _int(d, 'memory_peak_bytes', low=2_000_000_000, high=2**31) and
                _int(d, 'memory_oom_kill_delta', low=1) and
                d.get('verified_worker_count') == 2)
    if name == 'pid':
        return (_two_launches(d) and _two_scope_clean(d) and
                d.get('concurrent_scopes_at_limit') is True and
                d.get('scope_limits_above_parent') is True and
                d.get('max_parent_pids_current') == 80 and
                d.get('parent_pids_after') == 0 and
                _int(d, 'first_scope_tasks_held', low=1) and
                _int(d, 'second_scope_tasks_held', low=1) and
                _int(d, 'pids_events_max_delta') and
                _int(d, 'second_scope_events_max_delta') and
                d['pids_events_max_delta'] + d['second_scope_events_max_delta'] >= 1)
    if name == 'io':
        return (_two_launches(d) and _two_scope_clean(d) and
                d.get('concurrent_scope_io_observed') is True and
                d.get('workspace_files_removed') is True and
                d.get('baseline_scope_cleaned') is True and
                _int(d, 'baseline_bps', low=24*1024*1024) and
                _int(d, 'eval_aggregate_bps', low=1, high=20*1024*1024) and
                _int(d, 'eval_elapsed_ms', low=1) and
                _int(d, 'baseline_wbytes_delta', low=56*1024*1024) and
                _int(d, 'eval_parent_wbytes_delta', low=128*1024*1024) and
                _int(d, 'first_scope_wbytes', low=56*1024*1024) and
                _int(d, 'second_scope_wbytes', low=56*1024*1024) and
                d['eval_aggregate_bps'] ==
                d['eval_parent_wbytes_delta']*1000//d['eval_elapsed_ms'])
    if name == 'recovery':
        return (d.get('arm_crash_exit_code') == 42 and
                d.get('arm_cancel_exit_code') == 0 and
                _int(d, 'first_vm_evaluation_tasks', low=1) and
                d.get('fresh_vm_evaluation_tasks') == 0 and
                d.get('first_vm_worker_armed') is True and
                d.get('forced_vm_termination_observed') is True and
                d.get('first_vm_storage_removed') is True and
                d.get('fresh_vm_storage_removed') is True and
                d.get('fresh_vm_sentinel_absent') is True)
    return False


def validate_gate(name: str, report: dict) -> list[str]:
    """Reject a claimed PASS when the independent report structure disagrees."""
    if name not in GATES:
        raise ValueError('Unknown gate')
    _, _, stage, status, execution, checks, _ = GATES[name]
    errors: list[str] = []
    if type(report) is not dict:
        return ['NOT_A_JSON_OBJECT']
    for key, expected in (
        ('schema_version', '0.1'), ('phase', 2), ('stage', stage),
        ('status', status), ('execution', execution), ('cleanup', CLEAN),
        ('security_attestation', ATTESTATION), ('student_code_executed', False),
    ):
        if type(report.get(key)) is not type(expected) or report.get(key) != expected:
            errors.append('INVALID_' + key.upper())
    if report.get('error_code') not in (None, ''):
        errors.append('GUEST_ERROR_REPORTED')
    observed = report.get('checks')
    if (type(observed) is not dict or set(observed) != set(checks)
            or any(v != 'PASS' for v in observed.values())):
        errors.append('CHECKS_NOT_ALL_VERIFIED')
    if not _kernel_gate_evidence(name, report):
        errors.append('KERNEL_OR_CLEANUP_EVIDENCE_INCONSISTENT')
    return errors


def validate_fixture_run(summary: dict, fixture_reports: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    if (type(summary) is not dict or summary.get('Passed') is not True or
            summary.get('Failure') not in (None, '') or
            summary.get('SecurityAttestation') != ATTESTATION):
        return ['HARNESS_SUMMARY_FAILED']
    rows = summary.get('Results')
    if type(rows) is not list or len(rows) != len(FIXTURES):
        return ['HARNESS_FIXTURE_COUNT_MISMATCH']
    if type(fixture_reports) is not dict or set(fixture_reports) != set(FIXTURES):
        return ['FIXTURE_REPORT_COUNT_MISMATCH']
    for name, adapter, servers, build in (
            (name, *FIXTURES[name]) for name in FIXTURES):
        row = next((r for r in rows if type(r) is dict and r.get('Fixture') == name), None)
        doc = fixture_reports[name]
        if row is None or type(doc) is not dict:
            errors.append('MISSING_' + name.upper())
            continue
        if (row.get('Startup') != 'PASS' or row.get('HTTP') != '200' or
                row.get('Servers') != servers or row.get('Cleanup') != 'PASS'):
            errors.append('HARNESS_ROW_INVALID_' + name.upper())
        if (doc.get('status') != 'STARTUP_VERIFIED' or
                doc.get('execution') != 'PERFORMED_IN_DISPOSABLE_VM' or
                doc.get('cleanup') != CLEAN or
                doc.get('adapter') != adapter or
                doc.get('security_attestation') != ATTESTATION or
                type(doc.get('source')) is not dict or
                not re.fullmatch(r'[0-9a-fA-F]{40}', str(doc['source'].get('commit_sha',''))) or
                not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(doc['source'].get('repository','')))):
            errors.append('FIXTURE_TOP_LEVEL_INVALID_' + name.upper())
            continue
        guest = doc.get('guest_result')
        if type(guest) is not dict or guest.get('status') != 'STARTUP_VERIFIED' or guest.get('http_status') != 200:
            errors.append('FIXTURE_HTTP_INVALID_' + name.upper())
            continue
        p = guest.get('cgroup_placement')
        b = guest.get('build_launch_evidence')
        if (type(p) is not dict or p.get('status') != 'SERVER_PIDS_VERIFIED' or
                p.get('server_count') != servers or
                p.get('builds_verified') is not False or
                p.get('database_service_verified') is not False):
            errors.append('FIXTURE_SCOPE_CLAIM_INVALID_' + name.upper())
        if type(b) is not dict or (b.get('status') != (
                'PREEXEC_KERNEL_HANDOFF_VERIFIED' if build else 'NOT_APPLICABLE')):
            errors.append('FIXTURE_BUILD_INVALID_' + name.upper())
        elif build:
            if (b.get('npm_ci') != 2 or b.get('vite_build') != 1 or
                    b.get('pip_install') != 0 or
                    type(guest.get('service_results')) is not dict or
                    any(guest['service_results'].get(k) != 200 for k in (
                        'frontend_http','backend_http','built_asset_http','frontend_proxy_api_http')) or
                    guest['service_results'].get('sqlite_verified') is not True):
                errors.append('FULLSTACK_ASSERTIONS_INVALID')
        elif any(b.get(k) != 0 for k in ('npm_ci','vite_build','pip_install')):
            errors.append('UNEXPECTED_BUILD_ACTIVITY_' + name.upper())
    if len({r.get('Fixture') for r in rows if type(r) is dict}) != len(FIXTURES):
        errors.append('DUPLICATE_HARNESS_FIXTURE')
    return errors


def _json(path: Path, *, limit: int = 250_000) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Missing, symlinked or oversized evidence: ' + path.name)
    raw = path.read_bytes()
    obj = json.loads(raw.decode('utf-8-sig', 'strict'))
    if type(obj) is not dict:
        raise ValueError('Expected JSON object: ' + path.name)
    return obj


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(['git', *args], cwd=repo, capture_output=True, timeout=15)
    if p.returncode:
        raise ValueError('Git evidence check failed: ' + ' '.join(args[:2]))
    return p.stdout.decode('utf-8', 'strict').strip()


def check_checkout(repo: Path) -> dict:
    """Pin the source of every rerun; no tracked changes to hidden policies."""
    if Path(_git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('Run from exact project root')
    if _git(repo, 'branch', '--show-current') != 'executioner':
        raise ValueError('Not on executioner branch')
    if _git(repo, 'rev-parse', 'HEAD') != PINNED_HEAD:
        raise ValueError('Unexpected HEAD: generator must be reviewed for latest commit')
    diff = subprocess.run(['git','diff','--quiet','HEAD','--','projectiq_executioner'],cwd=repo,timeout=15)
    if diff.returncode:
        raise ValueError('Tracked Executioner source has local modifications')
    for path, expect in SOURCE_BLOBS.items():
        if _git(repo,'rev-parse','HEAD:'+path) != expect:
            raise ValueError('Pinned gate source changed: ' + path)
        if not (repo/path).is_file():
            raise ValueError('Missing gate file: ' + path)
    return {'git_head': PINNED_HEAD, 'git_branch': 'executioner',
            'pinned_source_blobs': dict(SOURCE_BLOBS)}


def _terminate_tree(proc: subprocess.Popen) -> bool:
    """Try to terminate descendants as well as launcher on timeout.

    This is best-effort only. A timeout is never considered a clean VM recovery.
    """
    try:
        if os.name == 'nt':
            done = subprocess.run(['taskkill','/F','/T','/PID',str(proc.pid)],
                                  capture_output=True,timeout=20)
            if done.returncode and proc.poll() is None:
                proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=15)
        return proc.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False


def _run_logged(command: list[str], repo: Path, log: Path, seconds: int) -> tuple[int | None, bool]:
    """Redirect logs to disk; never buffer arbitrary subprocess output in RAM."""
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    qemu = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'qemu'
    if os.name == 'nt' and qemu.is_dir():
        env['PATH'] += os.pathsep + str(qemu)
    with log.open('wb') as sink:
        proc = subprocess.Popen(command, cwd=repo, stdout=sink, stderr=subprocess.STDOUT,
                                env=env, start_new_session=(os.name != 'nt'),
                                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
        try:
            return proc.wait(timeout=seconds), False
        except subprocess.TimeoutExpired:
            _terminate_tree(proc)
            return None, True


def _record(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(path)


def _evidence(path: Path) -> dict:
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _base() -> dict:
    return {'schema_version':'0.1','phase':2,'stage':'d5b_b4_consolidated_reviewed_fixtures',
            'status':'NOT_EXECUTED','security_attestation':ATTESTATION,
            'student_code_executed':False,'all_supported_adapters_verified':False,
            'remaining_security_gates':list(OPEN_RISKS),
            'checks':{name:'NOT_VERIFIED' for name in
                      ('pinned_checkout','offline_tests','memory','pid','io','recovery',
                       'reviewed_fixture_regression','reports_structurally_verified')},
            'evidence':{},'failure_code':None}


def run(repo: Path, config: Path, harness: Path, out: Path, *, execute: bool,
        ack_resources: bool, ack_vm_kill: bool) -> dict:
    """One run, one evidence directory; never silently reuse older reports."""
    report = _base()
    try:
        repo = repo.resolve(strict=True)
        report['source'] = check_checkout(repo)
        report['checks']['pinned_checkout'] = 'PASS'
        if not execute:
            report['status'] = 'INSPECTION_ONLY_NOT_ATTESTED'
            return report
        if not (ack_resources and ack_vm_kill):
            report['status'] = 'EXPLICIT_DESTRUCTIVE_TEST_ACKNOWLEDGEMENT_REQUIRED'
            return report
        if not config.is_absolute():
            config = repo/config
        if not harness.is_absolute():
            harness = repo/harness
        if (not config.is_file() or config.is_symlink() or not harness.is_file() or
                harness.is_symlink() or harness.resolve() != (repo/HARNESS_RELATIVE).resolve()):
            raise ValueError('Missing vm config or fixed reviewed regression harness')
        body = harness.read_text(encoding='utf-8-sig').replace('\r\n','\n').encode('utf-8')
        if hashlib.sha256(body).hexdigest() != HARNESS_SHA256:
            raise ValueError('Reviewed regression harness was modified')
        # The report path is merely an output target; the run root is unique.
        run_root = out.parent/'d5b_b4_runs'/('run_'+uuid.uuid4().hex[:14])
        run_root.mkdir(parents=True, exist_ok=False)
        report['run_root'] = str(run_root)
        stages = [('offline_tests', [sys.executable, '-m','unittest','discover',
                                    '-s','projectiq_executioner/tests','-p','test_*.py','-q'], 180)]
        for label, command, seconds in stages:
            print('B4: '+label, flush=True)
            code, timeout = _run_logged(command, repo, run_root/(label+'.log'), seconds)
            if timeout or code != 0:
                report['failure_code'] = label.upper() + ('_TIMEOUT' if timeout else '_FAILED')
                report['status'] = 'D5B_B4_INCOMPLETE'
                return report
            report['checks'][label] = 'PASS'
        for name, (module, ack, _stage, _status, _execution, _checks, seconds) in GATES.items():
            dest = run_root/(name+'.json')
            command = [sys.executable,'-m','projectiq_executioner.phase2.vm_worker.'+module,
                       '--vm-config', str(config),'--run-clean-vm',ack,'--out',str(dest)]
            print('B4: running fresh '+name+' VM acceptance', flush=True)
            code, timeout = _run_logged(command, repo, run_root/(name+'.log'), seconds)
            if timeout or code != 0 or not dest.is_file():
                report['failure_code'] = name.upper() + ('_TIMEOUT' if timeout else '_EXECUTION_FAILED')
                report['status'] = 'D5B_B4_INCOMPLETE'
                return report
            try:
                obj = _json(dest)
                errs = validate_gate(name, obj)
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
                errs = ['INVALID_GATE_REPORT_' + type(exc).__name__.upper()]
            report['evidence'][name] = _evidence(dest)
            if errs:
                report['failure_code'] = name.upper() + '_' + errs[0]
                report['status'] = 'D5B_B4_INCOMPLETE'
                return report
            report['checks'][name] = 'PASS'
        if os.name != 'nt':
            report['failure_code'] = 'REVIEWED_HARNESS_REQUIRES_WINDOWS_POWERSHELL'
            report['status'] = 'D5B_B4_INCOMPLETE'
            return report
        fixture_base = run_root/'fixtures'
        fixture_base.mkdir()
        print('B4: rerunning three approved fixture VMs', flush=True)
        command = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(harness),
                   '-RepoRoot',str(repo),'-OutputBase',str(fixture_base)]
        code, timeout = _run_logged(command, repo, run_root/'fixtures.log', 2400)
        if timeout or code != 0:
            report['failure_code'] = 'FIXTURE_REGRESSION_' + ('TIMEOUT' if timeout else 'FAILED')
            report['status'] = 'D5B_B4_INCOMPLETE'
            return report
        folders = list(fixture_base.glob('gate2a3_regression_*/regression_summary.json'))
        if len(folders) != 1:
            raise ValueError('Expected exactly one new fixture regression summary')
        summary_file = folders[0]
        summary = _json(summary_file)
        fixture_docs = {}
        for name in FIXTURES:
            p = summary_file.parent/name.lower()/'vm_execution.json'
            fixture_docs[name] = _json(p)
            report['evidence']['fixture_'+name] = _evidence(p)
        errs = validate_fixture_run(summary, fixture_docs)
        report['evidence']['fixture_summary'] = _evidence(summary_file)
        if errs:
            report['failure_code'] = 'REVIEWED_FIXTURES_' + errs[0]
            report['status'] = 'D5B_B4_INCOMPLETE'
            return report
        report['checks']['reviewed_fixture_regression'] = 'PASS'
        report['checks']['reports_structurally_verified'] = 'PASS'
        # Recheck the reviewed code did not change while the VMs were running.
        check_checkout(repo)
        report['status'] = CHECKPOINT_OK
        return report
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        report['failure_code'] = 'HOST_' + type(exc).__name__.upper()
        report['status'] = 'D5B_B4_INCOMPLETE'
        return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='ProjectIQ D5b B4 consolidated reviewed-fixture acceptance')
    p.add_argument('--repo-root', type=Path, default=Path('.'))
    p.add_argument('--vm-config', type=Path, default=Path('vm_config.json'))
    p.add_argument('--regression-harness', type=Path,
                   default=Path(HARNESS_RELATIVE))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--run-consolidated-acceptance', action='store_true')
    p.add_argument('--acknowledge-resource-tests', action='store_true')
    p.add_argument('--acknowledge-hard-vm-kill', action='store_true')
    args = p.parse_args(argv)
    try:
        result = run(args.repo_root, args.vm_config, args.regression_harness,
                     args.out.resolve(), execute=args.run_consolidated_acceptance,
                     ack_resources=args.acknowledge_resource_tests,
                     ack_vm_kill=args.acknowledge_hard_vm_kill)
    except Exception as exc:
        result = _base()
        result['status'] = 'D5B_B4_INCOMPLETE'
        result['failure_code'] = 'UNEXPECTED_' + type(exc).__name__.upper()
    _record(args.out, result)
    print('ProjectIQ D5b B4: ' + result['status'], flush=True)
    print('Report: '+str(args.out), flush=True)
    if result['failure_code']:
        print('Failure: '+str(result['failure_code']), flush=True)
    return 0 if result['status'] in (CHECKPOINT_OK, 'INSPECTION_ONLY_NOT_ATTESTED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
