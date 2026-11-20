"""D5b/B3b Gate 2B.2: trusted aggregate PID/task-limit acceptance.

An opt-in disposable VM test, NOT an attestation for untrusted students.
No project source, dependency cache or host mount enters the VM.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_pid_guest_probe import (CHECKS, TASK_LIMIT, FIRST_MIN_HELD,
                                 SECOND_MIN_HELD, _valid_kernel_pid_evidence)
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
VM_RAM_MIB = 1536
WORKSPACE_MIB = 96
WALL_SECONDS = 340
REMAINING = (
    'DEVICE_IO_THROTTLE_GATE_2B3_PENDING',
    'CRASH_AND_ORPHAN_RECOVERY_PENDING',
    'COMPLETE_BUILD_DESCENDANT_CONFINEMENT_NOT_VERIFIED',
    'MYSQL_SERVICE_RESOURCES_NOT_IN_EVALUATION_SLICE',
    'GUEST_ROOT_QCOW2_AND_OTHER_GUEST_WRITES_NOT_CONFINED',
    'INDEPENDENT_HOST_AND_HYPERVISOR_ISOLATION_ATTESTATION_PENDING',
)


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2,
        'stage': 'd5b_b3b_gate2b2_eval_pids',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'student_code_executed': False,
        'security_attestation': ATTESTATION, 'cleanup': 'NOT_STARTED',
        'guest_ram_mib': VM_RAM_MIB,
        'expected_slice_tasks_max': TASK_LIMIT,
        'checks': {c: 'NOT_VERIFIED' for c in CHECKS},
        'remaining_security_gates': list(REMAINING),
    }


def _parse_guest(stdout: bytes) -> tuple[dict, dict] | None:
    """Bounded typed evidence only. Never accept a bare PASS as proof."""
    if type(stdout) is not bytes or len(stdout) > 6500:
        return None
    try:
        raw = json.loads(stdout.decode('utf-8', 'strict'))
        if (type(raw) is not dict or set(raw) != {'schema_version','checks','diagnostics'}
                or raw['schema_version'] != '0.1'):
            return None
        checks = raw['checks']; diag = raw['diagnostics']
        if (type(checks) is not dict or set(checks) != set(CHECKS)
                or any(type(v) is not str or v not in {'PASS','FAIL','NOT_VERIFIED'}
                       for v in checks.values())
                or type(diag) is not dict or len(diag) > 24):
            return None
        number_keys = {
            'parent_pids_before', 'parent_pids_after', 'first_scope_tasks_held',
            'second_scope_tasks_held', 'max_parent_pids_current',
            'pids_events_max_delta', 'second_scope_events_max_delta',
            'controller_home_mode_before',
            'controller_home_mode_after', 'verified_worker_count',
            'cooperative_stop_markers_published',
        }
        bool_keys = {'scope_cleanup_observed','release_markers_visible_to_app',
                     'scope_limits_above_parent','concurrent_scopes_at_limit'}
        simple_strings = {'probe_error','scope_cleanup_failure'}
        enums = {
            'probe_stage': {
                'STARTING_WORKERS','WORKER_LAUNCH_FAILED','UNEXPECTED_EXISTING_TASKS',
                'RELEASE_VISIBILITY_CHECK','SCOPE_LIMIT_CONFLICT',
                'FIRST_WORKER_THREADS_NOT_HELD','RELEASE_VISIBILITY_FAILED',
                'PID_STRESS_RUNNING','KERNEL_COUNTERS_COLLECTED',
            },
            'stress_monitor_outcome': {
                'KERNEL_PID_DENIAL','ALL_WRAPPERS_EXITED','MONITOR_TIMEOUT',
            },
            'launch_failure_reason': {
                'SCOPE_NOT_OBSERVED','APP_UID_NOT_OBSERVED',
                'CGROUP_MEMBERSHIP_NOT_OBSERVED','EXACT_COMMAND_NOT_OBSERVED',
                'LAUNCH_VERIFICATION_TIMEOUT','WRAPPER_EXITED','WRAPPER_SIGNALLED',
                *('WORKER_EXIT_'+str(c) for c in (111,112,113,114,115,116,117,118)),
            },
        }
        collections = {'worker_launch_observations','scope_cleanup_observations',
                       'wrapper_exit_codes_before_cleanup'}
        if not set(diag) <= number_keys | bool_keys | simple_strings | set(enums) | collections:
            return None
        for key, value in diag.items():
            if key in number_keys and (type(value) is not int or not 0 <= value <= 2**45):
                return None
            if key in bool_keys and type(value) is not bool:
                return None
            if key in simple_strings and (type(value) is not str or
                                          len(value) > 90 or not value.isidentifier()):
                return None
            if key in enums and (type(value) is not str or value not in enums[key]):
                return None
        codes = diag.get('wrapper_exit_codes_before_cleanup')
        if codes is not None and (type(codes) is not list or len(codes)!=2 or
                                  any(type(x) is not int or not -64<=x<=255 for x in codes)):
            return None
        launches = diag.get('worker_launch_observations')
        if launches is not None:
            if type(launches) is not list or len(launches)>2:
                return None
            for index, row in enumerate(launches,1):
                if type(row) is not dict or set(row) != {
                    'worker','scope_seen','max_cgroup_procs','app_uid_seen',
                    'cgroup_seen','argv_seen','wrapper_exit_code','verified'}:
                    return None
                if (type(row['worker']) is not int or row['worker']!=index or
                        type(row['max_cgroup_procs']) is not int or not 0<=row['max_cgroup_procs']<=30 or
                        type(row['wrapper_exit_code']) is not int or
                        not -64<=row['wrapper_exit_code']<=255):
                    return None
                if any(type(row[k]) is not bool for k in
                       ('scope_seen','app_uid_seen','cgroup_seen','argv_seen','verified')):
                    return None
                if row['verified'] and not all(row[k] for k in
                        ('scope_seen','app_uid_seen','cgroup_seen','argv_seen')):
                    return None
        cleanup = diag.get('scope_cleanup_observations')
        if cleanup is not None:
            if type(cleanup) is not list or len(cleanup)>2:
                return None
            for index,row in enumerate(cleanup,1):
                if type(row) is not dict or set(row) != {
                    'worker','kill_returncode','stop_returncode','scope_state',
                    'remaining_tasks','wrapper_exit_code_after',
                    'stop_marker_published','kill_failure_reason'}:
                    return None
                if (type(row['worker']) is not int or row['worker']!=index or
                    type(row['scope_state']) is not str or row['scope_state'] not in
                    {'ABSENT','DRAINED','POPULATED','UNKNOWN'} or
                    type(row['remaining_tasks']) is not int or not 0<=row['remaining_tasks']<=30 or
                    any(type(row[k]) is not int or not -64<=row[k]<=255 for k in
                        ('kill_returncode','stop_returncode','wrapper_exit_code_after')) or
                    type(row['stop_marker_published']) is not bool or
                    row['kill_failure_reason'] not in {
                        'NOT_ATTEMPTED','NONE','UNSUPPORTED_OPTION','UNIT_NOT_FOUND',
                        'PERMISSION_DENIED','NO_SUCH_PROCESS','OTHER_ERROR',
                        'COMMAND_TIMEOUT','COMMAND_UNAVAILABLE'}):
                    return None
                if (row['kill_returncode']==-2 and
                        row['kill_failure_reason']!='NOT_ATTEMPTED'):
                    return None
        if checks['two_unprivileged_scoped_workers']=='PASS' and (
                launches is None or len(launches)!=2 or
                not all(r['verified'] for r in launches) or diag.get('verified_worker_count')!=2):
            return None
        if checks['first_worker_threads_held']=='PASS' and (
                checks['two_unprivileged_scoped_workers']!='PASS' or
                diag.get('first_scope_tasks_held',0)<FIRST_MIN_HELD):
            return None
        if checks['aggregate_tasks_reached_limit']=='PASS' and (
                checks['first_worker_threads_held']!='PASS' or
                diag.get('max_parent_pids_current',0) not in (TASK_LIMIT-1,TASK_LIMIT) or
                diag.get('first_scope_tasks_held',0)<FIRST_MIN_HELD or
                diag.get('second_scope_tasks_held',0)<SECOND_MIN_HELD or
                diag.get('scope_limits_above_parent') is not True or
                diag.get('concurrent_scopes_at_limit') is not True):
            return None
        if checks['kernel_pids_limit_denied']=='PASS' and (
                checks['aggregate_tasks_reached_limit']!='PASS' or
                diag.get('stress_monitor_outcome')!='KERNEL_PID_DENIAL' or
                not _valid_kernel_pid_evidence(diag)):
            return None
        if diag.get('release_markers_visible_to_app') is True and (
                diag.get('controller_home_mode_after')!=0o711):
            return None
        if checks['worker_scopes_cleaned']=='PASS' and (
                cleanup is None or len(cleanup)!=2 or
                diag.get('scope_cleanup_observed') is not True or
                diag.get('cooperative_stop_markers_published')!=2 or
                any(r['scope_state'] not in {'ABSENT','DRAINED'} or
                    r['remaining_tasks']!=0 or r['wrapper_exit_code_after']==-1 or
                    r['stop_marker_published'] is not True for r in cleanup)):
            return None
        if checks['guest_pid_capacity_recovers']=='PASS' and (
                checks['worker_scopes_cleaned']!='PASS' or
                type(diag.get('parent_pids_after')) is not int or
                type(diag.get('parent_pids_before')) is not int or
                diag['parent_pids_after']>diag['parent_pids_before']):
            return None
        return checks,diag
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError):
        return None


def run(config: dict, *, run_clean_vm: bool=False, acknowledge_pid_stress: bool=False,
        work_root: Path|None=None) -> dict:
    result=_base_report()
    host=verify_host_toolchain(config)
    result['host_tools_and_image']='PASS' if host['status']==(
        'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED') else 'FAIL'
    if result['host_tools_and_image']!='PASS':
        result['status']='HOST_GATES_BLOCKED';return result
    if not run_clean_vm:
        result['status']='INSPECTION_ONLY_NOT_ATTESTED';return result
    if not acknowledge_pid_stress:
        result['status']='PID_STRESS_CONFIRMATION_REQUIRED';return result
    parent=Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True,exist_ok=True)
    work_path=None;worker=None;proc=None;stopped=False
    expired=threading.Event();watchdog=None;error=None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b3b_pids_',dir=parent) as temporary:
            work_path=Path(temporary)
            try:
                key,pub=make_ssh_key(config,work_path)
                seed=make_nocloud_iso(work_path,pub,
                    'projectiq-b3b-pids-'+uuid.uuid4().hex,
                    workspace_disk_mib=WORKSPACE_MIB,app_cgroup=True)
                port=_free_loopback_port()
                worker=QemuWorker({**config,'workspace_disk_mib':WORKSPACE_MIB},
                                  work_path,port,ram_mib=VM_RAM_MIB,
                                  cpus=effective_vm_vcpus(config,2))
                expected=f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
                if worker.network_spec()!=expected:
                    raise WorkerFailure('Unexpected PID probe network policy')
                worker.start(seed);proc=worker.proc
                if proc is None or proc.poll() is not None:
                    raise WorkerFailure('Clean PID VM did not start')
                result['execution']='CLEAN_VM_BOOT_REQUESTED'
                def expire():
                    expired.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog=threading.Timer(WALL_SECONDS,expire)
                watchdog.daemon=True;watchdog.start()
                ssh=SSHControl(config,work_path,port,key)
                ssh.wait_ssh(worker,seconds=110)
                source=Path(__file__).with_name('d5_pid_guest_probe.py').read_bytes()
                if len(source)>90_000:
                    raise WorkerFailure('Trusted guest PID probe exceeds size budget')
                reply=ssh.ssh('python3','-',input=source,timeout=200)
                parsed=_parse_guest(reply.stdout) if reply.returncode==0 else None
                if parsed is None:
                    error='INVALID_OR_FAILED_PID_GUEST_PROBE'
                    result['guest_probe_returncode']=reply.returncode
                else:
                    result['checks'],result['kernel_observations']=parsed
                    result['execution']='TRUSTED_PID_PROBE_IN_CLEAN_VM'
                    if proc.poll() is not None:
                        error='VM_EXITED_EARLY'
            except Exception as exc:
                error='PID_PROBE_'+type(exc).__name__.upper()
                if isinstance(exc,WorkerFailure):
                    result['operator_hint']=str(exc)[:160]
            finally:
                if watchdog:watchdog.cancel()
                if worker:
                    try:
                        worker.stop();stopped=proc is None or proc.poll() is not None
                    except Exception:
                        stopped=False
    except OSError:
        error='PID_TEMPORARY_STORAGE_CLEANUP_FAILED'
    result['cleanup']=('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                       if (work_path is not None and work_path.exists()) or
                          (worker is not None and not stopped)
                       else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if expired.is_set():error='PID_VM_WALL_TIMEOUT'
    if error:
        result['error_code']=error;result['status']='PID_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(v=='FAIL' for v in result['checks'].values()):
        result['status']='PID_ENFORCEMENT_FAILED'
    elif any(v=='NOT_VERIFIED' for v in result['checks'].values()):
        result['status']='PID_ENFORCEMENT_NOT_VERIFIED'
    else:
        result['status']='EVALUATION_SLICE_PIDS_ENFORCED_NOT_ATTESTED'
    if result['cleanup']=='CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        result['status']='CLEANUP_FAILED'
    return result


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description='ProjectIQ D5b/B3b Gate 2B.2: bounded PID stress')
    parser.add_argument('--vm-config',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--run-clean-vm',action='store_true')
    parser.add_argument('--acknowledge-pid-stress',action='store_true')
    parser.add_argument('--work-root',type=Path)
    args=parser.parse_args(argv)
    try:
        record=run(load_config(args.vm_config),run_clean_vm=args.run_clean_vm,
                   acknowledge_pid_stress=args.acknowledge_pid_stress,
                   work_root=args.work_root)
    except Exception as exc:
        record=_base_report();record['status']='HOST_GATES_BLOCKED'
        record['error_code']='INSPECTION_'+type(exc).__name__.upper()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('ProjectIQ D5b/B3b Gate 2B.2 - '+record['status'])
    print('  VM RAM:       '+str(record['guest_ram_mib'])+' MiB')
    print('  Attestation:  '+record['security_attestation'])
    print('  Cleanup:      '+record['cleanup'])
    print('  Report:       '+str(args.out))
    return 0 if record['status'] in ('INSPECTION_ONLY_NOT_ATTESTED',
                       'EVALUATION_SLICE_PIDS_ENFORCED_NOT_ATTESTED') else 1


if __name__=='__main__':
    raise SystemExit(main())
