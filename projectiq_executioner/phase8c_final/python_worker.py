"""Managed reviewed-only, offline Python preparation. No automatic VM grant."""
import json, shutil, socket, threading, uuid
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c2.broker import require
from .storage import DiskWatchdog,inspect_volume,GIB


def validate_receipt(guest,req):
    return (type(guest) is dict and guest.get('status')=='PYTHON_OFFLINE_LOCK_VERIFIED'
            and all(guest.get(k)==req[k] for k in ('bundle_sha256','pip_sha256','lock_sha256','python_version'))
            and type(guest.get('wheel_count')) is int and guest['wheel_count']==len(req['wheels'])
            and all(guest.get(k) is True for k in ('offline_resolution_verified','offline_install_verified','host_gateway_denied'))
            and type(guest.get('pip_preexec_handoffs')) is int and guest['pip_preexec_handoffs']==2
            and guest.get('workspace_mib')==1536 and guest.get('repository_code_executed') is False)


def assemble_reviewed(*,source,lock,wheelhouse,vm_config,pip_wheel,pip_sha256,require_bounded_volume=False):
    from ..phase2.vm_worker.orchestrator import load_config
    from ..phase2.vm_worker.qemu import (QemuWorker,SSHControl,verify_host_toolchain,make_ssh_key,make_nocloud_iso,_free_loopback_port,effective_vm_vcpus)
    from ..phase2.vm_worker.d5c_runtime_workspace import ManagedRuntimeRun
    from ..phase2.vm_worker import d5d_host_acl_audit as acl
    from ..phase2.vm_worker.d5d_boundary_acceptance import _sentinel_loop
    config_receipt=checked_file(vm_config,100_000);config=load_config(Path(vm_config))
    require(checked_file(vm_config,100_000)==config_receipt,'CONFIG_CHANGED')
    require(source['repository']+'@'+source['commit_sha'] in config['approved_snapshots'],'EXISTING_OPERATOR_APPROVAL_REQUIRED')
    require(config.get('accelerator','whpx')=='whpx','WINDOWS_WHPX_REQUIRED')
    require(Path(pip_wheel).name=='pip-25.2-py3-none-any.whl' and checked_file(pip_wheel,5_000_000)['sha256']==pip_sha256,'PIP_ASSET_MISMATCH')
    require(lock['lock_sha256']==digest({k:v for k,v in lock.items() if k!='lock_sha256'}),'LOCK_CHANGED')
    receipt=checked_file(wheelhouse,120_000_000)
    info=verify_host_toolchain(config);require(info.get('status')=='TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED','HOST_TOOLCHAIN_UNVERIFIED')
    audit=acl.audit(Path(vm_config));require(audit.get('status')==acl.SUCCESS and len(audit.get('checks',{}))==5 and all(v=='PASS' for v in audit['checks'].values()),'MANAGED_ROOT_ACL_UNVERIFIED')
    require(checked_file(vm_config,100_000)==config_receipt,'CONFIG_CHANGED')
    managed=ManagedRuntimeRun(config);worker=proc=timer=watch=work=listener=sentinel=None;marked=False;expired=threading.Event();stopped=False;contact=threading.Event();stop_listener=threading.Event()
    result={'status':'PYTHON_OFFLINE_ASSEMBLY_FAILED','stage':'MANAGED_WORKSPACE','vm_start_attempts':0,'application_vms_started':0,'cleanup':'UNVERIFIED','runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'}
    try:
        work=managed.open();volume=inspect_volume(work);result['storage_volume']=volume
        require(volume['free_bytes']>=24*GIB,'HOST_DISK_HEADROOM_INSUFFICIENT')
        if require_bounded_volume:require(volume['volume_capacity_bounded'],'SEPARATE_BOUNDED_VOLUME_REQUIRED')
        result['stage']='VM_SETUP'
        key,pub=make_ssh_key(config,work);seed=make_nocloud_iso(work,pub,'projectiq-8c-final-'+uuid.uuid4().hex,workspace_disk_mib=1536,app_cgroup=True)
        port=_free_loopback_port();worker=QemuWorker({**config,'workspace_disk_mib':1536},work,port,ram_mib=3072,cpus=effective_vm_vcpus(config,2))
        require(worker.workspace_disk_mib==1536 and worker.network_spec()==f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22','OFFLINE_WORKER_POLICY_MISMATCH')
        sentinel=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sentinel.bind(('127.0.0.1',0));sentinel.listen(2);sentinel.settimeout(.2)
        sentinel_port=sentinel.getsockname()[1];listener=threading.Thread(target=_sentinel_loop,args=(sentinel,stop_listener,contact),daemon=True);listener.start()
        with socket.create_connection(('127.0.0.1',sentinel_port),timeout=2) as control:control.sendall(b'projectiq-8c-final-control\n')
        require(contact.wait(timeout=2),'HOST_SENTINEL_POSITIVE_CONTROL_FAILED');contact.clear()
        managed.mark_launching();result['stage']='VM_START';result['vm_start_attempts']=1;worker.start(seed);proc=worker.proc;managed.mark_launched(proc);marked=True
        def expire():
            expired.set()
            try:
                if proc is not None and proc.poll() is None:proc.kill()
            except Exception:pass
        timer=threading.Timer(630,expire);timer.daemon=True;timer.start()
        watch=DiskWatchdog(work,proc);watch.start()
        result['stage']='SSH_BOOT';ssh=SSHControl(config,work,port,key);ssh.wait_ssh(worker,seconds=110);managed.mark_booted()
        # Recheck the exact trusted asset and input before copying.
        require(checked_file(wheelhouse,120_000_000)==receipt and checked_file(pip_wheel,5_000_000)['sha256']==pip_sha256,'INPUT_CHANGED')
        req={'bundle_sha256':receipt['sha256'],'pip_sha256':pip_sha256,'lock_sha256':lock['lock_sha256'],'python_version':lock['target_python'],'direct_pins':lock['direct_pins'],'wheels':[{k:r[k] for k in ('name','version','filename','sha256')} for r in lock['wheels']],'sentinel_port':sentinel_port}
        result['stage']='TRUSTED_INPUT_TRANSFER';request=work/'request.json';request.write_text(json.dumps(req),'utf-8')
        ssh.copy(request,'request.json');ssh.copy(Path(__file__).resolve().parents[1]/'phase2/vm_worker/guest_agent.py','guest_agent.py')
        ssh.copy_offline_asset(wheelhouse,'python_wheelhouse.zip');ssh.copy_offline_asset(pip_wheel,'python_pip.whl')
        result['stage']='OFFLINE_WHEEL_VERIFICATION';cmd=ssh.ssh('python3','-I','-',input=Path(__file__).with_name('python_guest.py').read_bytes(),timeout=380)
        guest_path=work/'result.json';ssh.receive('result.json',guest_path);require(0<guest_path.stat().st_size<=8192,'GUEST_RESULT_LIMIT');guest=json.loads(guest_path.read_text('utf-8'))
        if cmd.returncode or not validate_receipt(guest,req):
            code=guest.get('error_code');result['error_code']=code if code in {'PYTHON_TARGET_MISMATCH','NETWORK_PROBE_INVALID','HOST_GATEWAY_NOT_DENIED','INPUT_HASH_MISMATCH','LOCK_INVALID','WHEEL_HASH_MISMATCH','WHEEL_SET_MISMATCH','PIP_REPORT_INVALID','PIP_RESOLUTION_MISMATCH','PIP_OFFLINE_RESOLUTION_FAILED','PIP_OFFLINE_INSTALL_FAILED','PREEXEC_HANDOFF_UNVERIFIED','PYTHON_GUEST_PREPARATION_FAILED'} else 'GUEST_RECEIPT_MISMATCH';raise ValueError('GUEST_VERIFICATION_FAILED')
        require(not contact.is_set() and not watch.tripped.is_set() and checked_file(wheelhouse,120_000_000)==receipt,'WORKER_OR_INPUT_CHANGED')
        result.update(status='WHEELHOUSE_CANDIDATE',guest=guest,artifact=receipt,network='QEMU_RESTRICT_ON')
    except (Exception,KeyboardInterrupt) as exc:
        known={'HOST_DISK_HEADROOM_INSUFFICIENT','SEPARATE_BOUNDED_VOLUME_REQUIRED','OFFLINE_WORKER_POLICY_MISMATCH','HOST_SENTINEL_POSITIVE_CONTROL_FAILED','INPUT_CHANGED','GUEST_RESULT_LIMIT','WORKER_OR_INPUT_CHANGED','STORAGE_GUARD_TRIPPED'}
        result.setdefault('error_code',str(exc) if type(exc) is ValueError and str(exc) in known else 'WORKER_STAGE_FAILED')
    finally:
        if timer is not None:timer.cancel();timer.join(timeout=3)
        if worker is not None:
            try:
                actual=proc if proc is not None else worker.proc;worker.stop();stopped=actual is None or actual.poll() is not None
                if marked and stopped:managed.mark_stopped()
            except Exception:stopped=False
        else:stopped=True
        if watch is not None:
            if stopped and not watch.tripped.is_set():watch.sample()
            watch.stop();result['storage_watchdog']=watch.report()
        if stopped:
            try:managed.close()
            except Exception:pass
        if stopped and managed.cleanup_verified:result['cleanup']=CLEAN
        stop_listener.set()
        if listener is not None:listener.join(timeout=2)
        if sentinel is not None:sentinel.close()
        if contact.is_set():result['error_code']='HOST_SENTINEL_CONTACTED'
        if expired.is_set():result['error_code']='WORKER_WALL_TIMEOUT'
        if watch is not None and watch.tripped.is_set():result['error_code']=watch.reason
    if (result['status']=='WHEELHOUSE_CANDIDATE' and result['cleanup']==CLEAN and not result.get('error_code')
            and watch is not None and watch.samples>0 and not watch.tripped.is_set()):
        result.update(status='PYTHON_WHEELHOUSE_VERIFIED_REVIEWED_NOT_AUTHORIZED',stage='COMPLETE')
    else:
        result.pop('artifact',None);result['status']='PYTHON_OFFLINE_ASSEMBLY_FAILED'
    return result
