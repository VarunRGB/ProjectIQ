"""Observe the current reviewed worker without promoting it to untrusted status.
Optional source-free D5d qualification reuses the actual existing probe runners.
"""
from pathlib import Path
import re
import tempfile
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file, CLEAN


def _stamp(record):
    record['worker_report_sha256']=digest(record)
    return record


def inspect_worker(config_path, *, run_clean_vms=False, qemu_sha256=None):
    record={'schema_version':'8C.worker.1','status':'REVIEWED_WORKER_PREFLIGHT_UNVERIFIED',
            'automatic_admission_supported':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS',
            'checks':{},'worker_fingerprints':{},'probe_results':{},'clean_probe_attempts':0,
            'student_code_executed':False,'runtime_permission':False,'dependency_preparation_permission':False,
            'limitations':['Read-only inspection does not attest runtime isolation.', 'The network string is launcher configuration, not a measured firewall.', 'A local hash is not independent worker identity or provenance.']}
    try:
        from ..phase2.vm_worker.orchestrator import load_config
        from ..phase2.vm_worker.qemu import verify_host_toolchain,QemuWorker
        from ..phase2.vm_worker import d5d_host_acl_audit as acl
        from ..phase2.vm_worker.d5e_closeout import OPEN_BLOCKERS
        config_path=Path(config_path).absolute()
        before=checked_file(config_path,100_000)
        config=load_config(config_path)
        if checked_file(config_path,100_000)!=before:raise ValueError('CONFIG_CHANGED')
        record['config_sha256']=before['sha256']
        info=verify_host_toolchain(config)
        tools_ok=info.get('status')=='TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED'
        record['checks']['toolchain_and_image']='PASS' if tools_ok else 'NOT_VERIFIED'
        if tools_ok:
            record['worker_fingerprints']['qemu_binary_sha256']=checked_file(info['toolchain']['qemu-system-x86_64'],512_000_000)['sha256']
        record['worker_fingerprints']['base_image_expected_sha256']=config['base_sha256'].lower()
        root=Path(__file__).resolve().parents[1]
        for rel in ('phase2/vm_worker/qemu.py','phase2/vm_worker/guest_agent.py',
                    'phase2/vm_worker/orchestrator.py','phase2/vm_worker/d5d_boundary_acceptance.py',
                    'phase2/vm_worker/d5d_process_acceptance.py','phase2/vm_worker/d5d_host_runtime_acceptance.py',
                    'phase2/phase2d/cache_prep.py','phase2/phase2d/python_cache_prep.py','phase8b/bridge.py'):
            record['worker_fingerprints'][rel]=checked_file(root/rel,8_000_000)['sha256']
        # Constructor and network_spec do not create or boot a VM. Set an
        # arbitrary fixed port only to examine the two configured modes.
        port=32123; parent=Path(tempfile.gettempdir()); forward=f'hostfwd=tcp:127.0.0.1:{port}-:22'
        runtime=QemuWorker(config,parent,port)
        prep=QemuWorker(config,parent,port,trusted_dependency_fetch=True)
        record['checks']['offline_runtime_network_spec']='PASS' if runtime.network_spec()=='user,id=net0,restrict=on,'+forward else 'NOT_VERIFIED'
        record['checks']['preparation_network_spec']='NETWORK_ENABLED_NO_REGISTRY_FIREWALL_IN_LAUNCHER' if prep.network_spec()=='user,id=net0,'+forward else 'UNRECOGNIZED_REQUIRES_REVIEW'
        record['checks']['preparation_workspace_disk']='NO_BOUNDED_WORKSPACE_ATTACHED' if prep.workspace_disk_mib==0 else 'REQUIRES_VALIDATION'
        audit=acl.audit(config_path)
        acl_ok=(audit.get('status')==acl.SUCCESS and audit.get('security_attestation')=='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS' and audit.get('acl_modified') is False and audit.get('vm_launched') is False and type(audit.get('checks')) is dict and len(audit['checks'])==5 and all(v=='PASS' for v in audit['checks'].values()))
        record['checks']['current_managed_root_acl']='PASS' if acl_ok else 'NOT_VERIFIED'
        record['existing_untrusted_execution_blockers']=list(OPEN_BLOCKERS)
        if tools_ok and acl_ok and record['checks']['offline_runtime_network_spec']=='PASS':
            record['status']='REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED'
        if run_clean_vms:
            if (record['status']!='REVIEWED_WORKER_PREFLIGHT_OBSERVED_NOT_ATTESTED' or not isinstance(qemu_sha256,str) or not re.fullmatch('[0-9a-f]{64}',qemu_sha256) or qemu_sha256!=record['worker_fingerprints'].get('qemu_binary_sha256')):
                record['qualification_status']='LIVE_PROBES_BLOCKED_BY_PREFLIGHT_OR_PIN'
            else:
                _run_clean_probes(config,qemu_sha256,record)
        else:
            record['qualification_status']='LIVE_PROBES_NOT_REQUESTED'
        if checked_file(config_path,100_000)!=before:
            record['status']='REVIEWED_WORKER_PREFLIGHT_UNVERIFIED';record['error_code']='CONFIG_CHANGED_DURING_INSPECTION'
    except (Exception,KeyboardInterrupt):
        record['status']='REVIEWED_WORKER_PREFLIGHT_UNVERIFIED'
        record['error_code']='WORKER_INSPECTION_FAILED_OR_INTERRUPTED'
    return _stamp(record)


def _run_clean_probes(config,pin,record):
    from ..phase2.vm_worker import d5d_process_acceptance as proc, d5d_boundary_acceptance as boundary, d5d_host_runtime_acceptance as host
    from ..phase2.vm_worker.d5e_closeout import _verify_diagnostic
    runners=(('gate2',lambda:proc.run(config,run_clean_vm=True)),
             ('gate3',lambda:boundary.run(config,run_clean_vm=True)),
             ('gate4',lambda:host.run(config,pin,run_clean_vm=True)))
    record['qualification_status']='LIVE_PROBES_INCOMPLETE'
    for label,run in runners:
        record['clean_probe_attempts']+=1
        try:
            raw=run()
            passed=_verify_diagnostic(label,raw) and raw.get('cleanup')==CLEAN
            record['probe_results'][label]={'status':'OBSERVED_NOT_ATTESTED' if passed else 'NOT_VERIFIED',
                  'cleanup':CLEAN if raw.get('cleanup')==CLEAN else 'UNVERIFIED',
                  'report_sha256':digest(raw)}
        except (Exception,KeyboardInterrupt):
            record['probe_results'][label]={'status':'NOT_VERIFIED','cleanup':'UNVERIFIED'}
            return
        if not passed:return  # Never launch another clean probe after failure.
    record['qualification_status']='THREE_LEGACY_BASELINES_OBSERVED_NOT_ATTESTED'
