"""Reviewed-only managed runtime. Reuse 8C storage, lifecycle and SSH boundaries."""
import json,socket,threading,uuid,re,shutil,inspect
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file,CLEAN
from ..phase8c2.broker import require
from ..phase8c_final.storage import DiskWatchdog,inspect_volume,GIB
from .guest import ERRORS,PROBE_PATHS
from .planner import SUPPORTED,OPTIONAL


CLI={'NODE_CLI','PY_CLI'}|OPTIONAL
BUILDS={'NODE_NEXT','NODE_VITE','NODE_CRA','NODE_ASTRO','NODE_NUXT','NODE_SVELTEKIT','NODE_ANGULAR','NATIVE_C','NATIVE_CPP','GO_SINGLE','RUST_SINGLE'}


def validate_receipt(guest,req):
    if type(guest) is not dict:return False
    if any(guest.get(k)!=req[k] for k in ('source','component_id','adapter','snapshot_sha256','plan_sha256','dependency_kind')):return False
    if guest.get('dependency_sha256')!=req.get('bundle_sha256') or guest.get('host_gateway_denied') is not True or guest.get('workspace_mib')!=1536 or type(guest.get('functional_tests_executed')) is not int or guest['functional_tests_executed']!=0:return False
    kind=req['adapter'];dep=req['dependency_kind']
    expected={'build_handoffs':int(kind in BUILDS),'npm_install_handoffs':int(dep=='NPM'),'pip_install_handoffs':2 if dep=='PYTHON' else 0}
    if any(type(guest.get(k)) is not int or guest[k]!=v for k,v in expected.items()):return False
    if kind in CLI:return guest.get('status')=='CLI_EXIT_ZERO_OBSERVED' and type(guest.get('cli_exit_code')) is int and guest['cli_exit_code']==0 and guest.get('cli_preexec_handoff') is True
    h=guest.get('http',{});paths=set(PROBE_PATHS) if kind in {'NODE_HTTP','PY_FLASK','PY_ASGI','PY_DJANGO'} else {req['probe_path']}
    return (guest.get('status')=='HTTP_READINESS_OBSERVED' and guest.get('server_placement_observed') is True and guest.get('application_uid_listener_observed') is True and type(h) is dict and h.get('status')=='HTTP_2XX_OBSERVED' and isinstance(h.get('path'),str) and h['path'] in paths and guest.get('service_port',8765)==req.get('service_port',8765) and type(h.get('http_status')) is int and 200<=h['http_status']<300)


def trusted_guest_payload():
    # Fixed operator-owned Python verification module, never submitted source.
    p=Path(__file__).resolve().parents[1]/'phase8c_final/python_guest.py'
    py=p.read_text('utf-8');g=Path(__file__).with_name('guest.py').read_text('utf-8')
    from .versions import node_constraint
    ver='import re\n'+inspect.getsource(node_constraint)
    return ("_piq_version={'__name__':'piq_trusted_versions'}\nexec(compile("+repr(ver)+",'piq_trusted_versions','exec'),_piq_version)\n"+"_piq_verify={'__name__':'piq_trusted_python_verify'}\nexec(compile("+repr(py)+",'piq_trusted_python_verify','exec'),_piq_verify)\n"+g+"\nraise SystemExit(main(_piq_verify['assemble'],_piq_version['node_constraint']))\n").encode('utf-8')


def execute_reviewed(*,source,plan,component,snapshot,vm_config,expected_config,bundle=None,lock=None,node_tarball=None,node_sha256=None,pip_wheel=None,pip_sha256=None,require_bounded_volume=False):
    from ..phase2.vm_worker.orchestrator import load_config
    from ..phase2.vm_worker.qemu import (QemuWorker,SSHControl,verify_host_toolchain,make_ssh_key,make_nocloud_iso,_free_loopback_port,effective_vm_vcpus)
    from ..phase2.vm_worker.d5c_runtime_workspace import ManagedRuntimeRun
    from ..phase2.vm_worker import d5d_host_acl_audit as acl
    from ..phase2.vm_worker.d5d_boundary_acceptance import _sentinel_loop
    config_receipt=checked_file(vm_config,100_000);require(config_receipt==expected_config,'CONFIG_CHANGED');config=load_config(Path(vm_config))
    require(checked_file(vm_config,100_000)==config_receipt,'CONFIG_CHANGED')
    require(source['repository']+'@'+source['commit_sha'] in config['approved_snapshots'],'EXISTING_OPERATOR_APPROVAL_REQUIRED')
    require(config.get('accelerator','whpx')=='whpx','WINDOWS_WHPX_REQUIRED')
    require(plan['source']==source and plan['plan_sha256']==digest({k:v for k,v in plan.items() if k!='plan_sha256'}) and component in plan['components'] and component['status']=='REVIEWED_CANDIDATE' and not component['blockers'],'PLAN_CHANGED_OR_BLOCKED')
    kind=component['adapter'];dep=component['dependency']['kind'];require(kind in SUPPORTED and dep in {'NONE','NPM','PYTHON'},'RUNTIME_ADAPTER_UNSUPPORTED')
    inputs=[(Path(snapshot),'snapshot.zip',30_000_000,checked_file(snapshot,30_000_000))]
    req={'source':source,'component_id':component['id'],'root':component['root'],'adapter':kind,'entrypoint':component['entrypoint'],'application_symbol':component['application_symbol'],'probe_path':component['probe_path'],'service_port':component['service_port'],'plan_sha256':plan['plan_sha256'],'dependency_kind':dep,'snapshot_sha256':inputs[0][3]['sha256']}
    if kind.startswith('NODE_'):
        version=component['toolchain'];require(node_tarball is not None and Path(node_tarball).name==f'node-{version}-linux-x64.tar.xz','NODE_ASSET_REQUIRED')
        asset=checked_file(node_tarball,65_000_000);require(asset['sha256']==node_sha256,'NODE_ASSET_MISMATCH');inputs.append((Path(node_tarball),'node_runtime.tar.xz',65_000_000,asset));req.update(node_version=version,node_sha256=node_sha256,npm_constraints=component['dependency'].get('npm_constraints',[]))
    if kind.startswith('PY_'):req['python_version']=component['toolchain']
    if dep=='NPM':
        require(bundle is not None,'DEPENDENCY_BUNDLE_REQUIRED');receipt=checked_file(bundle,145_000_000);inputs.append((Path(bundle),'npm_cache.zip',145_000_000,receipt));req['bundle_sha256']=receipt['sha256']
    if dep=='PYTHON':
        require(pip_wheel is not None and Path(pip_wheel).name=='pip-25.2-py3-none-any.whl','PIP_ASSET_REQUIRED');asset=checked_file(pip_wheel,5_000_000);require(asset['sha256']==pip_sha256,'PIP_ASSET_MISMATCH');inputs.append((Path(pip_wheel),'python_pip.whl',5_000_000,asset))
        require(lock is not None and lock['lock_sha256']==digest({k:v for k,v in lock.items() if k!='lock_sha256'}) and lock['target_python']==component['toolchain'],'LOCK_CHANGED')
        receipt=checked_file(bundle,120_000_000);inputs.append((Path(bundle),'python_wheelhouse.zip',120_000_000,receipt));req.update(bundle_sha256=receipt['sha256'],pip_sha256=pip_sha256,lock_sha256=lock['lock_sha256'],direct_pins=lock['direct_pins'],wheels=[{k:r[k] for k in ('name','version','filename','sha256')} for r in lock['wheels']])
    info=verify_host_toolchain(config);require(info.get('status')=='TOOLCHAIN_AND_IMAGE_VERIFIED_NOT_SANDBOX_ATTESTED','HOST_TOOLCHAIN_UNVERIFIED')
    audit=acl.audit(Path(vm_config));require(audit.get('status')==acl.SUCCESS and len(audit.get('checks',{}))==5 and all(v=='PASS' for v in audit['checks'].values()),'MANAGED_ROOT_ACL_UNVERIFIED')
    require(checked_file(vm_config,100_000)==config_receipt,'CONFIG_CHANGED')
    managed=ManagedRuntimeRun(config);worker=proc=timer=watch=work=listener=sentinel=None;marked=False;expired=threading.Event();stopped=False;contact=threading.Event();stop_listener=threading.Event()
    result={'status':'REVIEWED_RUNTIME_FAILED_OR_UNVERIFIED','stage':'MANAGED_WORKSPACE','vm_start_attempts':0,'cleanup':'UNVERIFIED','source':source,'component_id':component['id'],'adapter':kind,'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','functional_tests_executed':0}
    try:
        work=managed.open();volume=inspect_volume(work);result['storage_volume']=volume
        require(volume['free_bytes']>=24*GIB,'HOST_DISK_HEADROOM_INSUFFICIENT')
        if require_bounded_volume:require(volume['volume_capacity_bounded'],'SEPARATE_BOUNDED_VOLUME_REQUIRED')
        result['stage']='VM_SETUP';key,pub=make_ssh_key(config,work);seed=make_nocloud_iso(work,pub,'projectiq-8d-'+uuid.uuid4().hex,workspace_disk_mib=1536,app_cgroup=True)
        port=_free_loopback_port();worker=QemuWorker({**config,'workspace_disk_mib':1536},work,port,ram_mib=3072,cpus=effective_vm_vcpus(config,2))
        require(worker.workspace_disk_mib==1536 and worker.network_spec()==f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22','OFFLINE_WORKER_POLICY_MISMATCH')
        sentinel=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sentinel.bind(('127.0.0.1',0));sentinel.listen(2);sentinel.settimeout(.2)
        req['sentinel_port']=sentinel.getsockname()[1];listener=threading.Thread(target=_sentinel_loop,args=(sentinel,stop_listener,contact),daemon=True);listener.start()
        with socket.create_connection(('127.0.0.1',req['sentinel_port']),timeout=2) as control:control.sendall(b'projectiq-8d-control\n')
        require(contact.wait(timeout=2),'HOST_SENTINEL_POSITIVE_CONTROL_FAILED');contact.clear()
        # No automatic retry and no configuration modification.
        require(checked_file(vm_config,100_000)==config_receipt,'CONFIG_CHANGED')
        managed.mark_launching();result['stage']='VM_START';result['vm_start_attempts']=1;worker.start(seed);proc=worker.proc;managed.mark_launched(proc);marked=True
        def expire():
            expired.set()
            try:
                if proc is not None and proc.poll() is None:proc.kill()
            except Exception:pass
        timer=threading.Timer(850,expire);timer.daemon=True;timer.start();watch=DiskWatchdog(work,proc);watch.start()
        result['stage']='SSH_BOOT';ssh=SSHControl(config,work,port,key);ssh.wait_ssh(worker,seconds=110);managed.mark_booted()
        result['stage']='TRUSTED_INPUT_TRANSFER';request=work/'request.json';request.write_text(json.dumps(req),'utf-8');require(request.stat().st_size<=64_000,'REQUEST_SIZE_LIMIT')
        ssh.copy(request,'request.json');ssh.copy(Path(__file__).resolve().parents[1]/'phase2/vm_worker/guest_agent.py','guest_agent.py')
        for p,remote,limit,receipt in inputs:
            require(checked_file(p,limit)==receipt,'INPUT_CHANGED')
            if remote=='snapshot.zip':ssh.copy(p,remote,timeout=90)
            else:ssh.copy_offline_asset(p,remote)
        result['stage']='OFFLINE_RUNTIME';cmd=ssh.ssh('python3','-I','-',input=trusted_guest_payload(),timeout=650)
        guest_path=work/'result.json';ssh.receive('result.json',guest_path);require(0<guest_path.stat().st_size<=8192,'GUEST_RESULT_LIMIT');guest=json.loads(guest_path.read_text('utf-8'))
        if cmd.returncode or not validate_receipt(guest,req):
            code=guest.get('error_code') if type(guest) is dict else None;result['error_code']=code if isinstance(code,str) and code in ERRORS else 'GUEST_RECEIPT_OR_RUNTIME_UNVERIFIED';raise ValueError('GUEST_RECEIPT_OR_RUNTIME_UNVERIFIED')
        require(not contact.is_set() and not watch.tripped.is_set() and all(checked_file(p,limit)==receipt for p,_,limit,receipt in inputs),'WORKER_OR_INPUT_CHANGED')
        # Keep only checked fields. Never echo extra guest-controlled data.
        evidence={k:guest[k] for k in ('status','source','component_id','adapter','snapshot_sha256','plan_sha256','dependency_kind','dependency_sha256','host_gateway_denied','workspace_mib','build_handoffs','npm_install_handoffs','pip_install_handoffs','functional_tests_executed')}
        if kind in CLI:evidence.update(cli_exit_code=0,cli_preexec_handoff=True)
        else:evidence.update(service_port=req['service_port'],server_placement_observed=True,application_uid_listener_observed=True,http={k:guest['http'][k] for k in ('status','path','http_status')})
        result.update(status='RUNTIME_CANDIDATE',evidence=evidence,network='QEMU_RESTRICT_ON')
    except (Exception,KeyboardInterrupt) as exc:
        known={'HOST_DISK_HEADROOM_INSUFFICIENT','SEPARATE_BOUNDED_VOLUME_REQUIRED','OFFLINE_WORKER_POLICY_MISMATCH','HOST_SENTINEL_POSITIVE_CONTROL_FAILED','CONFIG_CHANGED','INPUT_CHANGED','GUEST_RESULT_LIMIT','WORKER_OR_INPUT_CHANGED','STORAGE_GUARD_TRIPPED','REQUEST_SIZE_LIMIT'}
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
    if result['status']=='RUNTIME_CANDIDATE' and result['cleanup']==CLEAN and not result.get('error_code') and watch is not None and watch.samples>0 and not watch.tripped.is_set():
        result.update(status='REVIEWED_HTTP_READINESS_VERIFIED' if kind not in CLI else 'REVIEWED_CLI_EXIT_ZERO_VERIFIED',stage='COMPLETE')
    else:result.pop('evidence',None);result['status']='REVIEWED_RUNTIME_FAILED_OR_UNVERIFIED'
    return result
