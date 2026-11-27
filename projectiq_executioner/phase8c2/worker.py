"""Source-free offline cache assembly using the existing managed Windows VM.
Still reviewed-only: no admission grant and no general sandbox attestation.
"""
from pathlib import Path
import json, shutil, threading, uuid, re, socket
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c.artifacts import _zip_inventory
from .broker import require


def assemble_reviewed(*,source,bundle,out,vm_config,node_tarball,node_sha256,node_version='v24.22.3',storage_guard=False,require_bounded_volume=False):
    from ..phase2.vm_worker.orchestrator import load_config
    from ..phase2.vm_worker.qemu import (QemuWorker,SSHControl,verify_host_toolchain,make_ssh_key,make_nocloud_iso,_free_loopback_port,effective_vm_vcpus)
    from ..phase2.vm_worker.d5c_runtime_workspace import ManagedRuntimeRun
    from ..phase2.vm_worker import d5d_host_acl_audit as acl
    require(not require_bounded_volume or storage_guard,'BOUNDED_VOLUME_REQUIRES_STORAGE_GUARD')
    config=load_config(vm_config)
    identity=source['repository']+'@'+source['commit_sha']
    require(identity in config['approved_snapshots'],'EXISTING_OPERATOR_APPROVAL_REQUIRED')
    require(config.get('accelerator','whpx')=='whpx','WINDOWS_WHPX_REQUIRED')
    require(re.fullmatch(r'v24\.[0-9]{1,3}\.[0-9]{1,3}',node_version) and Path(node_tarball).name==f'node-{node_version}-linux-x64.tar.xz','NODE_VERSION_UNSUPPORTED')
    require(checked_file(node_tarball,65_000_000)['sha256']==node_sha256,'NODE_ASSET_MISMATCH')
    receipt=checked_file(bundle,145_000_000)
    info=verify_host_toolchain(config);require(info.get('status')=='TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED','HOST_TOOLCHAIN_UNVERIFIED')
    audit=acl.audit(vm_config);require(audit.get('status')==acl.SUCCESS and len(audit.get('checks',{}))==5 and all(v=='PASS' for v in audit['checks'].values()),'MANAGED_ROOT_ACL_UNVERIFIED')
    managed=ManagedRuntimeRun(config);worker=proc=timer=work=listener=sentinel=None;marked=False;expired=threading.Event();stopped=False
    from ..phase2.vm_worker.d5d_boundary_acceptance import _sentinel_loop
    contact=threading.Event();stop_listener=threading.Event()
    result={'status':'OFFLINE_ASSEMBLY_FAILED','cleanup':'UNVERIFIED','vm_start_attempts':0,'application_vms_started':0,'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','stage':'MANAGED_WORKSPACE'}
    # Keep output private, and publish cache only after the VM stops and managed
    # storage is recovered. A failed candidate is removed from the output.
    watch=None
    pending=Path(out)/'cache.pending';final=Path(out)/'npm-cache.zip'
    try:
        work=managed.open()
        require(shutil.disk_usage(work).free>=24*1024**3,'HOST_DISK_HEADROOM_INSUFFICIENT')
        if storage_guard:
            from ..phase8c_final.storage import inspect_volume,DiskWatchdog
            volume=inspect_volume(work);result['storage_volume']=volume
            if require_bounded_volume:require(volume['volume_capacity_bounded'],'SEPARATE_BOUNDED_VOLUME_REQUIRED')
        result['stage']='VM_SETUP'
        key,pub=make_ssh_key(config,work);seed=make_nocloud_iso(work,pub,'projectiq-8c2-'+uuid.uuid4().hex,workspace_disk_mib=1536,app_cgroup=True)
        port=_free_loopback_port();worker=QemuWorker({**config,'workspace_disk_mib':1536},work,port,ram_mib=3072,cpus=effective_vm_vcpus(config,2))
        require(worker.workspace_disk_mib==1536 and worker.network_spec()==f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22','OFFLINE_WORKER_POLICY_MISMATCH')
        sentinel=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sentinel.bind(('127.0.0.1',0));sentinel.listen(2);sentinel.settimeout(0.2)
        sentinel_port=sentinel.getsockname()[1];listener=threading.Thread(target=_sentinel_loop,args=(sentinel,stop_listener,contact),daemon=True);listener.start()
        with socket.create_connection(('127.0.0.1',sentinel_port),timeout=2) as control:control.sendall(b'projectiq-8c2-control\n')
        require(contact.wait(timeout=2),'HOST_SENTINEL_POSITIVE_CONTROL_FAILED');contact.clear()
        managed.mark_launching();result['stage']='VM_START';result['vm_start_attempts']=1;worker.start(seed);proc=worker.proc;managed.mark_launched(proc);marked=True
        def expire():
            expired.set()
            if proc is not None and proc.poll() is None:proc.kill()
        timer=threading.Timer(630,expire);timer.daemon=True;timer.start()
        if storage_guard:
            watch=DiskWatchdog(work,proc);watch.start()
        result['stage']='SSH_BOOT';ssh=SSHControl(config,work,port,key);ssh.wait_ssh(worker,seconds=110);managed.mark_booted()
        result['stage']='TRUSTED_INPUT_TRANSFER'
        req={'bundle_sha256':receipt['sha256'],'node_sha256':node_sha256,'node_version':node_version,'sentinel_port':sentinel_port}
        request=work/'request.json';request.write_text(json.dumps(req),encoding='utf-8')
        ssh.copy(request,'request.json');ssh.copy(Path(__file__).resolve().parents[1]/'phase2/vm_worker/guest_agent.py','guest_agent.py')
        ssh.copy_offline_asset(bundle,'npm_cache.zip');ssh.copy_offline_asset(node_tarball,'node_runtime.tar.xz')
        result['stage']='OFFLINE_ASSEMBLY';cmd=ssh.ssh('python3','-I','-',input=Path(__file__).with_name('guest.py').read_bytes(),timeout=440)
        guest_path=work/'result.json';ssh.receive('result.json',guest_path)
        require(guest_path.stat().st_size<=8192,'GUEST_RESULT_LIMIT');guest=json.loads(guest_path.read_text(encoding='utf-8'))
        if cmd.returncode or guest.get('status')!='OFFLINE_CACHE_ASSEMBLED':
            code=guest.get('error_code');result['error_code']=code if code in {'INPUT_HASH_MISMATCH','MANIFEST_UNSAFE','PACKAGE_DIGEST_MISMATCH','EMPTY_INPUTS','LOCAL_ARCHIVE_INSTALL_FAILED','REGISTRY_LOCK_OFFLINE_REUSE_FAILED','UNSAFE_CACHE','CACHE_LIMIT','GUEST_ASSEMBLY_FAILED','NETWORK_PROBE_INVALID','HOST_GATEWAY_NOT_DENIED','PREEXEC_HANDOFF_UNVERIFIED'} else 'GUEST_ASSEMBLY_FAILED';raise ValueError('GUEST_ASSEMBLY_FAILED')
        require(all(guest.get(k)==v for k,v in req.items() if k not in {'node_version','sentinel_port'}) and guest.get('offline_reuse') is True and guest.get('repository_scripts_executed') is False and guest.get('workspace_mib')==1536 and guest.get('host_gateway_denied') is True and type(guest.get('npm_ci_preexec_handoffs')) is int and guest['npm_ci_preexec_handoffs']==2 and not contact.is_set(),'GUEST_RECEIPT_MISMATCH')
        result['stage']='CACHE_TRANSFER';received=work/'npm_cache.zip';ssh.receive_offline_cache(received)
        artifact=checked_file(received,170_000_000);require(artifact['sha256']==guest.get('npm_cache_sha256'),'CACHE_HASH_MISMATCH');inventory=_zip_inventory(received,False)
        with received.open('rb') as src,pending.open('xb') as dest:shutil.copyfileobj(src,dest,1024*1024)
        require(checked_file(pending,170_000_000)==artifact,'CACHE_COPY_MISMATCH')
        result.update(status='CACHE_CANDIDATE',artifact=artifact,inventory=inventory,offline_reuse_observed=True,workspace_mib=1536,network='QEMU_RESTRICT_ON',host_gateway_sentinel_denied=True,npm_ci_preexec_handoffs=2,repository_scripts_executed=False)
    except (Exception,KeyboardInterrupt) as exc:
        known={'HOST_DISK_HEADROOM_INSUFFICIENT','SEPARATE_BOUNDED_VOLUME_REQUIRED','STORAGE_GUARD_TRIPPED','OFFLINE_WORKER_POLICY_MISMATCH','HOST_SENTINEL_POSITIVE_CONTROL_FAILED','GUEST_RESULT_LIMIT','GUEST_RECEIPT_MISMATCH','CACHE_HASH_MISMATCH','CACHE_COPY_MISMATCH'}
        result.setdefault('error_code',str(exc) if type(exc) is ValueError and str(exc) in known else 'WORKER_STAGE_FAILED')
    finally:
        if timer is not None:timer.cancel();timer.join(timeout=3)
        if worker is not None:
            try:
                actual=proc if proc is not None else worker.proc
                worker.stop();stopped=actual is None or actual.poll() is not None
                if marked and stopped:managed.mark_stopped()
            except Exception:stopped=False
        else:stopped=True
        if watch is not None:
            if stopped and not watch.tripped.is_set():watch.sample()
            watch.stop();result['storage_watchdog']=watch.report()
            if watch.tripped.is_set():result['error_code']=watch.reason;result['status']='OFFLINE_ASSEMBLY_FAILED'
        if stopped:
            try:managed.close()
            except Exception:pass
        if stopped and managed.cleanup_verified:result['cleanup']=CLEAN
        stop_listener.set()
        if listener is not None:listener.join(timeout=2)
        if sentinel is not None:sentinel.close()
        if contact.is_set():result['error_code']='HOST_SENTINEL_CONTACTED';result['status']='OFFLINE_ASSEMBLY_FAILED'
        if expired.is_set():result['error_code']='WORKER_WALL_TIMEOUT'
    if result['status']=='CACHE_CANDIDATE' and result['cleanup']==CLEAN and not expired.is_set() and (not storage_guard or (watch is not None and watch.samples>0 and not watch.tripped.is_set())):
        pending.rename(final);result['status']='OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED';result['stage']='COMPLETE'
    else:
        pending.unlink(missing_ok=True);result.pop('artifact',None);result.pop('inventory',None);result['status']='OFFLINE_ASSEMBLY_FAILED'
    return result
