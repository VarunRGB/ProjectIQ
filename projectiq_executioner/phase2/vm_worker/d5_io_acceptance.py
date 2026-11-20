"""D5b/B3b Gate 2B.3: actual workspace-device write bandwidth acceptance.

Trusted opt-in clean-VM probe. No student code, network access, or host mounts.
Not sufficient for security attestation of arbitrary submissions.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import uuid
from pathlib import Path

from .d5_io_guest_probe import (
    CHECKS, WRITE_BPS, WORKER_BYTES, IO_MIN_ACCOUNTED,
    BASELINE_MIN_BPS, THROTTLED_MAX_BPS, _valid_io_evidence,
)
from .orchestrator import load_config
from .qemu import (QemuWorker, SSHControl, WorkerFailure, _free_loopback_port,
                   effective_vm_vcpus, make_nocloud_iso, make_ssh_key,
                   verify_host_toolchain)

ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
VM_RAM_MIB = 1536
WORKSPACE_MIB = 256
WALL_SECONDS = 370
REMAINING = (
    'CRASH_CANCEL_REBOOT_ORPHAN_RECOVERY_NOT_FULLY_VERIFIED',
    'COMPLETE_BUILD_DESCENDANT_CONFINEMENT_NOT_VERIFIED',
    'MYSQL_SERVICE_RESOURCES_NOT_IN_EVALUATION_SLICE',
    'GUEST_ROOT_QCOW2_AND_OTHER_WRITES_NOT_CONFINED',
    'INDEPENDENT_HOST_AND_HYPERVISOR_ISOLATION_ATTESTATION_PENDING',
)


def _base_report() -> dict:
    return {
        'schema_version': '0.1', 'phase': 2,
        'stage': 'd5b_b3b_gate2b3_eval_io_throttle',
        'status': 'NOT_EXECUTED', 'execution': 'NOT_PERFORMED',
        'student_code_executed': False,
        'security_attestation': ATTESTATION, 'cleanup': 'NOT_STARTED',
        'guest_ram_mib': VM_RAM_MIB, 'workspace_disk_mib': WORKSPACE_MIB,
        'expected_write_bytes_per_second': WRITE_BPS,
        'checks': {c: 'NOT_VERIFIED' for c in CHECKS},
        'remaining_security_gates': list(REMAINING),
    }


def _parse_guest(stdout: bytes) -> tuple[dict,dict] | None:
    """Typed, bounded kernel evidence; successful flags alone are never proof."""
    if type(stdout) is not bytes or len(stdout)>7500:return None
    try:
        raw=json.loads(stdout.decode('utf-8','strict'))
        if (type(raw) is not dict or set(raw)!={'schema_version','checks','diagnostics'}
                or raw['schema_version']!='0.1'):return None
        checks=raw['checks'];diag=raw['diagnostics']
        if (type(checks) is not dict or set(checks)!=set(CHECKS)
                or any(type(v) is not str or v not in ('PASS','FAIL','NOT_VERIFIED')
                       for v in checks.values())
                or type(diag) is not dict or len(diag)>34):return None
        ints={
          'baseline_wbytes_delta','baseline_scope_wbytes','baseline_elapsed_ms',
          'baseline_bps','eval_parent_wbytes_delta','eval_elapsed_ms','eval_aggregate_bps',
          'first_scope_wbytes','second_scope_wbytes','controller_home_mode_before',
          'controller_home_mode_after','verified_worker_count',
          'cooperative_stop_markers_published','workspace_free_before',
          'workspace_free_after','baseline_cleanup_remaining_tasks',
          'baseline_cleanup_wrapper_exit_code',
        }
        booleans={
          'baseline_launch_verified','baseline_scope_cleaned',
          'release_markers_visible_to_app','concurrent_scope_io_observed',
          'scope_cleanup_observed','workspace_files_removed','baseline_cleanup_stop_marker_published',
        }
        enums={
          'probe_stage':{
            'BASELINE_POLICY_NOT_UNBOUNDED','BASELINE_STARTING',
            'BASELINE_IOSTAT_PREFLIGHT','BASELINE_IOSTAT_INVALID',
            'BASELINE_IOSTAT_UNREADABLE','BASELINE_WORKER_LAUNCHING',
            'BASELINE_LAUNCH_FAILED','BASELINE_SCOPE_LIMITED',
            'BASELINE_RELEASE_UNREADABLE','UNEXPECTED_EXISTING_EVAL_WRITES',
            'EVAL_WORKERS_STARTING','WORKER_LAUNCH_FAILED',
            'EVAL_RELEASE_VISIBILITY','EVAL_IO_RUNNING',
            'KERNEL_COUNTERS_COLLECTED',
          },
          'baseline_outcome':{'FASTER_THAN_EVAL_LIMIT','INCONCLUSIVE'},
          'baseline_cleanup_scope_state':{'ABSENT','DRAINED','POPULATED','UNKNOWN'},
          'stress_monitor_outcome':{
            'BLOCK_IO_WRITES_ACCOUNTED','WORKER_EXITED_EARLY','MONITOR_TIMEOUT',
          },
          'baseline_io_stat_problem':{
            'OVERSIZED_IOSTAT','MALFORMED_FIELD',
            'INVALID_REQUIRED_COUNTER','MISSING_REQUIRED_COUNTER',
            'DUPLICATE_DEVICE','UNEXPECTED_BARE_TOKEN','DUPLICATE_FIELD',
            'INVALID_OPTIONAL_KEY','INVALID_OPTIONAL_VALUE',
          },
          'launch_failure_reason':{
            'SCOPE_NOT_OBSERVED','APP_UID_NOT_OBSERVED',
            'CGROUP_MEMBERSHIP_NOT_OBSERVED','EXACT_COMMAND_NOT_OBSERVED',
            'LAUNCH_VERIFICATION_TIMEOUT','WRAPPER_EXITED','WRAPPER_SIGNALLED',
            *('WORKER_EXIT_'+str(i) for i in range(111,120)),
          },
        }
        text_keys={'probe_error','scope_cleanup_failure'}
        lists={'worker_launch_observations','scope_cleanup_observations'}
        if not set(diag)<=ints|booleans|set(enums)|text_keys|lists:return None
        for k,v in diag.items():
            if k in ints and (type(v) is not int or not 0<=v<=2**48):return None
            if k in booleans and type(v) is not bool:return None
            if k in enums and (type(v) is not str or v not in enums[k]):return None
            if k in text_keys and (type(v) is not str or len(v)>90
                                   or not v.isidentifier()):return None
        launches=diag.get('worker_launch_observations')
        if launches is not None:
            if type(launches) is not list or len(launches)>2:return None
            for i,r in enumerate(launches,1):
                if type(r) is not dict or set(r)!={
                    'worker','scope_seen','max_cgroup_procs','app_uid_seen',
                    'cgroup_seen','argv_seen','wrapper_exit_code','verified'}:return None
                if (type(r['worker']) is not int or r['worker']!=i or
                    type(r['max_cgroup_procs']) is not int or not 0<=r['max_cgroup_procs']<=30 or
                    type(r['wrapper_exit_code']) is not int or not -64<=r['wrapper_exit_code']<=255):return None
                if any(type(r[k]) is not bool for k in ('scope_seen','app_uid_seen',
                       'cgroup_seen','argv_seen','verified')):return None
                if r['verified'] and not all(r[k] for k in (
                    'scope_seen','app_uid_seen','cgroup_seen','argv_seen')):return None
        cleanup=diag.get('scope_cleanup_observations')
        if cleanup is not None:
            if type(cleanup) is not list or len(cleanup)>2:return None
            for i,r in enumerate(cleanup,1):
                if type(r) is not dict or set(r)!={
                    'worker','kill_returncode','stop_returncode','scope_state',
                    'remaining_tasks','wrapper_exit_code_after',
                    'stop_marker_published','kill_failure_reason'}:return None
                if (type(r['worker']) is not int or r['worker']!=i or
                    type(r['scope_state']) is not str or r['scope_state'] not in
                     ('ABSENT','DRAINED','POPULATED','UNKNOWN') or
                    type(r['remaining_tasks']) is not int or not 0<=r['remaining_tasks']<=30 or
                    any(type(r[k]) is not int or not -64<=r[k]<=255 for k in
                        ('kill_returncode','stop_returncode','wrapper_exit_code_after')) or
                    type(r['stop_marker_published']) is not bool or
                    r['kill_failure_reason'] not in {
                        'NOT_ATTEMPTED','NONE','UNSUPPORTED_OPTION','UNIT_NOT_FOUND',
                        'PERMISSION_DENIED','NO_SUCH_PROCESS','OTHER_ERROR',
                        'COMMAND_TIMEOUT','COMMAND_UNAVAILABLE'}):return None
                if (r['kill_returncode']==-2 and r['kill_failure_reason']!='NOT_ATTEMPTED'):
                    return None
        if checks['unthrottled_baseline_exceeds_limit']=='PASS' and (
                diag.get('baseline_launch_verified') is not True or
                diag.get('baseline_outcome')!='FASTER_THAN_EVAL_LIMIT' or
                type(diag.get('baseline_wbytes_delta')) is not int or
                diag['baseline_wbytes_delta']<IO_MIN_ACCOUNTED or
                type(diag.get('baseline_scope_wbytes')) is not int or
                diag['baseline_scope_wbytes']<IO_MIN_ACCOUNTED or
                type(diag.get('baseline_elapsed_ms')) is not int or
                diag['baseline_elapsed_ms']<=0 or
                type(diag.get('baseline_bps')) is not int or
                diag['baseline_bps']<BASELINE_MIN_BPS or
                diag['baseline_bps']!=diag['baseline_wbytes_delta']*1000//diag['baseline_elapsed_ms']):return None
        if checks['baseline_scope_cleaned']=='PASS' and (
                diag.get('baseline_scope_cleaned') is not True or
                diag.get('baseline_cleanup_scope_state') not in ('ABSENT','DRAINED') or
                diag.get('baseline_cleanup_remaining_tasks')!=0 or
                diag.get('baseline_cleanup_wrapper_exit_code')==-1 or
                diag.get('baseline_cleanup_stop_marker_published') is not True):
            return None
        if checks['two_unprivileged_scoped_workers']=='PASS' and (
                checks['baseline_scope_cleaned']!='PASS' or
                launches is None or len(launches)!=2 or
                not all(r['verified'] for r in launches) or
                diag.get('verified_worker_count')!=2):return None
        if checks['both_worker_io_accounted']=='PASS' and (
                checks['two_unprivileged_scoped_workers']!='PASS' or
                diag.get('first_scope_wbytes',0)<IO_MIN_ACCOUNTED or
                diag.get('second_scope_wbytes',0)<IO_MIN_ACCOUNTED or
                diag.get('concurrent_scope_io_observed') is not True):return None
        if checks['kernel_workspace_writes_observed']=='PASS' and (
                checks['both_worker_io_accounted']!='PASS' or
                diag.get('eval_parent_wbytes_delta',0)<2*WORKER_BYTES or
                diag.get('stress_monitor_outcome')!='BLOCK_IO_WRITES_ACCOUNTED'):return None
        if checks['aggregate_write_rate_throttled']=='PASS' and (
                checks['unthrottled_baseline_exceeds_limit']!='PASS' or
                checks['kernel_workspace_writes_observed']!='PASS' or
                type(diag.get('eval_elapsed_ms')) is not int or
                diag['eval_elapsed_ms']<=0 or
                diag.get('eval_aggregate_bps')!=
                     diag['eval_parent_wbytes_delta']*1000//diag['eval_elapsed_ms'] or
                not _valid_io_evidence(diag)):return None
        if diag.get('release_markers_visible_to_app') is True and (
                diag.get('controller_home_mode_after')!=0o711):return None
        if checks['worker_scopes_cleaned']=='PASS' and (
                checks['two_unprivileged_scoped_workers']!='PASS' or
                cleanup is None or len(cleanup)!=2 or
                diag.get('scope_cleanup_observed') is not True or
                diag.get('cooperative_stop_markers_published')!=2 or
                any(r['scope_state'] not in ('ABSENT','DRAINED') or
                    r['remaining_tasks']!=0 or r['wrapper_exit_code_after']==-1 or
                    r['stop_marker_published'] is not True for r in cleanup)):
            return None
        if checks['workspace_write_recovery']=='PASS' and (
                checks['worker_scopes_cleaned']!='PASS' or
                diag.get('workspace_files_removed') is not True or
                type(diag.get('workspace_free_before')) is not int or
                type(diag.get('workspace_free_after')) is not int or
                diag['workspace_free_after']+8*1024*1024<diag['workspace_free_before']):
            return None
        return checks,diag
    except (ValueError,TypeError,KeyError,UnicodeError,OverflowError,ZeroDivisionError):
        return None


def run(config: dict, *, run_clean_vm: bool=False, acknowledge_io_stress: bool=False,
        work_root: Path|None=None) -> dict:
    result=_base_report()
    host=verify_host_toolchain(config)
    result['host_tools_and_image']='PASS' if host['status']==(
        'TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED') else 'FAIL'
    if result['host_tools_and_image']!='PASS':
        result['status']='HOST_GATES_BLOCKED';return result
    if not run_clean_vm:
        result['status']='INSPECTION_ONLY_NOT_ATTESTED';return result
    if not acknowledge_io_stress:
        result['status']='IO_STRESS_CONFIRMATION_REQUIRED';return result
    parent=Path(work_root) if work_root is not None else Path(tempfile.gettempdir())
    parent.mkdir(parents=True,exist_ok=True)
    work_path=None;worker=None;proc=None;stopped=False
    expired=threading.Event();watchdog=None;error=None
    try:
        with tempfile.TemporaryDirectory(prefix='projectiq_d5b3b_io_',dir=parent) as temporary:
            work_path=Path(temporary)
            try:
                key,pub=make_ssh_key(config,work_path)
                seed=make_nocloud_iso(work_path,pub,
                    'projectiq-b3b-io-'+uuid.uuid4().hex,
                    workspace_disk_mib=WORKSPACE_MIB,app_cgroup=True)
                port=_free_loopback_port()
                worker=QemuWorker({**config,'workspace_disk_mib':WORKSPACE_MIB},
                                  work_path,port,ram_mib=VM_RAM_MIB,
                                  cpus=effective_vm_vcpus(config,2))
                expected=f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22'
                if worker.network_spec()!=expected:
                    raise WorkerFailure('Unexpected IO probe network policy')
                worker.start(seed);proc=worker.proc
                if proc is None or proc.poll() is not None:
                    raise WorkerFailure('Clean IO VM did not start')
                result['execution']='CLEAN_VM_BOOT_REQUESTED'
                def expire():
                    expired.set()
                    if worker and worker.proc and worker.proc.poll() is None:
                        worker.proc.kill()
                watchdog=threading.Timer(WALL_SECONDS,expire)
                watchdog.daemon=True;watchdog.start()
                ssh=SSHControl(config,work_path,port,key)
                ssh.wait_ssh(worker,seconds=110)
                source=Path(__file__).with_name('d5_io_guest_probe.py').read_bytes()
                if len(source)>100_000:
                    raise WorkerFailure('Trusted guest IO probe exceeds size budget')
                reply=ssh.ssh('python3','-',input=source,timeout=235)
                parsed=_parse_guest(reply.stdout) if reply.returncode==0 else None
                if parsed is None:
                    error='INVALID_OR_FAILED_IO_GUEST_PROBE'
                    result['guest_probe_returncode']=reply.returncode
                else:
                    result['checks'],result['kernel_observations']=parsed
                    result['execution']='TRUSTED_IO_PROBE_IN_CLEAN_VM'
                    if proc.poll() is not None:
                        error='VM_EXITED_EARLY'
            except Exception as exc:
                error='IO_PROBE_'+type(exc).__name__.upper()
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
        error='IO_TEMPORARY_STORAGE_CLEANUP_FAILED'
    result['cleanup']=('CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED'
                       if (work_path is not None and work_path.exists()) or
                          (worker is not None and not stopped)
                       else 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED')
    if expired.is_set():error='IO_VM_WALL_TIMEOUT'
    if error:
        result['error_code']=error;result['status']='IO_PROBE_INCONCLUSIVE_OR_FAILED'
    elif any(v=='FAIL' for v in result['checks'].values()):
        result['status']='IO_THROTTLE_ENFORCEMENT_FAILED'
    elif any(v=='NOT_VERIFIED' for v in result['checks'].values()):
        result['status']='IO_THROTTLE_NOT_VERIFIED'
    else:
        result['status']='EVALUATION_SLICE_IO_THROTTLED_NOT_ATTESTED'
    if result['cleanup']=='CLEANUP_FAILED_MANUAL_INSPECTION_REQUIRED':
        result['status']='CLEANUP_FAILED'
    return result


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description='ProjectIQ D5b/B3b Gate 2B.3: bounded direct-I/O write throttle')
    parser.add_argument('--vm-config',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--run-clean-vm',action='store_true')
    parser.add_argument('--acknowledge-io-stress',action='store_true')
    parser.add_argument('--work-root',type=Path)
    args=parser.parse_args(argv)
    try:
        record=run(load_config(args.vm_config),run_clean_vm=args.run_clean_vm,
                   acknowledge_io_stress=args.acknowledge_io_stress,
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
                       'EVALUATION_SLICE_IO_THROTTLED_NOT_ATTESTED') else 1


if __name__=='__main__':
    raise SystemExit(main())
