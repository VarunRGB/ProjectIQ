"""Trusted guest runtime adapter implementation; no arbitrary command templates.
The host appends only its own existing python_guest.py implementation.
"""
import base64,hashlib,http.client,json,os,re,shutil,sys,time,socket
from pathlib import Path

PORT=8765
PROBE_PATHS=('/', '/health', '/healthz', '/api/health')
ERRORS={'APP_CGROUP_BUILD_PLACEMENT_NOT_VERIFIED','APP_CGROUP_BUILD_EXEC_TIMEOUT','APP_CGROUP_SERVER_PLACEMENT_NOT_VERIFIED','APP_CGROUP_ENFORCEMENT_UNAVAILABLE','APP_CGROUP_OTHER_TRUSTED_COMMAND_TIMEOUT','WORKSPACE_DISK_QUOTA_UNAVAILABLE','WORKSPACE_DISK_QUOTA_EXCEEDED','WORKSPACE_BOOTSTRAP_NOT_READY','RUNTIME_PRIVILEGE_BOUNDARY_UNAVAILABLE','NODE_VITE_RUNTIME_EXTRACTION_FAILED','NODE_VITE_NODE_ARCHIVE_INVALID','PYTHON_WEB_PIP_BOOTSTRAP_INVALID','PYTHON_WEB_PIP_VERSION_MISMATCH','PYTHON_WEB_WHEELHOUSE_INVALID','INPUT_HASH_MISMATCH','NETWORK_PROBE_INVALID','HOST_GATEWAY_NOT_DENIED','COMPONENT_ROOT_UNSAFE','ENTRYPOINT_UNSAFE','NODE_RUNTIME_MISMATCH','NPM_RUNTIME_VERSION_MISMATCH','PYTHON_TARGET_MISMATCH','HTTP_PORT_ALREADY_OCCUPIED','HTTP_LISTENER_UID_UNVERIFIED','TOOLCHAIN_UNAVAILABLE','NPM_INPUT_INVALID','NPM_OFFLINE_INSTALL_FAILED','NPM_INSTALL_HANDOFF_UNVERIFIED','BUILD_FAILED','BUILD_OUTPUT_MISSING','BUILD_HANDOFF_UNVERIFIED','CLI_FAILED','CLI_HANDOFF_UNVERIFIED','SERVER_PLACEMENT_UNVERIFIED','HTTP_READINESS_UNVERIFIED','RUNTIME_ADAPTER_UNSUPPORTED','PIP_OFFLINE_RESOLUTION_FAILED','PIP_OFFLINE_INSTALL_FAILED','PIP_RESOLUTION_MISMATCH','PIP_REPORT_INVALID','LOCK_INVALID','WHEEL_HASH_MISMATCH','WHEEL_SET_MISMATCH','PREEXEC_HANDOFF_UNVERIFIED'}


def require(ok,code):
    if not ok:raise ValueError(code)


def within(root,rel,*,directory=False):
    require(isinstance(rel,str) and len(rel)<=240 and (rel=='.' or re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',rel) and all(x not in ('.','..') for x in rel.split('/'))),'ENTRYPOINT_UNSAFE')
    p=root if rel=='.' else root.joinpath(*rel.split('/'))
    require(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()) and (p.is_dir() if directory else p.is_file()),'ENTRYPOINT_UNSAFE')
    return p


def init(g,base,req):
    g._verify_execution_identity(base)
    work=g._stage_bounded_workspace({'workspace_disk_mib':1536},base=base)
    g._verify_evaluation_cgroup(work);g._APP_CGROUP_ACTIVE=True;g._APP_BUILD_HANDOFFS.clear()
    port=req['sentinel_port'];require(type(port) is int and 1024<=port<=65535,'NETWORK_PROBE_INVALID')
    probe='import socket,sys\ntry:\n s=socket.create_connection(("10.0.2.2",int(sys.argv[1])),timeout=3);s.close()\nexcept OSError: sys.exit(0)\nsys.exit(2)'
    observed=g._trusted_process(['/usr/bin/python3','-I','-c',probe,str(port)],timeout=15,env={},cwd=work,project_code=True)
    require(observed.returncode==0,'HOST_GATEWAY_NOT_DENIED')
    return work


def install_node(g,work,req,root,npm_constraint):
    tar=work/'node_runtime.tar.xz';require(g._sha256(tar)==req['node_sha256'],'INPUT_HASH_MISMATCH')
    runtime=work/'node-runtime';runtime.mkdir(mode=0o700)
    node,npm=g._node_tar_extract(tar,req['node_version'],runtime);tar.unlink();g._share_tree(runtime,writable=False)
    info=g._trusted_process([str(node),'--version'],timeout=15)
    require(info.returncode==0 and info.stdout.decode('utf-8','replace').strip()==req['node_version'],'NODE_RUNTIME_MISMATCH')
    if req.get('npm_constraints'):
        np=g._trusted_process([str(npm),'--version'],timeout=15,env={'PATH':str(node.parent)+':/usr/bin:/bin'})
        v=np.stdout.decode('utf-8','replace').strip()
        require(np.returncode==0 and all(npm_constraint(v,c) is True for c in req['npm_constraints']),'NPM_RUNTIME_VERSION_MISMATCH')
    env={'PATH':str(node.parent)+':/usr/bin:/bin','CI':'true','PORT':str(req.get('service_port',PORT)),'HOST':'127.0.0.1','NODE_ENV':'production'}
    if req['dependency_kind']=='NPM':
        archive=work/'npm_cache.zip';require(g._sha256(archive)==req['bundle_sha256'],'INPUT_HASH_MISMATCH')
        inputs=g.safe_extract(archive,work/'dependency-inputs',max_total=145_000_000);archive.unlink()
        manifest=json.loads((inputs/'package.json').read_text('utf-8'));lock=json.loads((inputs/'package-lock.json').read_text('utf-8'))
        require('scripts' not in manifest and lock.get('lockfileVersion')==3,'NPM_INPUT_INVALID')
        # The host verified the bundle against the fresh original manifests.
        install=work/'manifest-install';install.mkdir(mode=0o700);used=set()
        for path,row in lock['packages'].items():
            if not path:continue
            name=base64.b64decode(row['integrity'].split('-',1)[1],validate=True).hex()+'.tgz'
            p=inputs/'tarballs'/name
            if p.is_file() and not p.is_symlink():
                with p.open('rb') as f:require(hashlib.file_digest(f,'sha512').hexdigest()==name[:-4],'INPUT_HASH_MISMATCH')
                row['resolved']='file:'+str(p);used.add(name)
        require(bool(used),'NPM_INPUT_INVALID')
        (install/'package.json').write_text(json.dumps(manifest),'utf-8');(install/'package-lock.json').write_text(json.dumps(lock),'utf-8')
        g._share_tree(inputs,writable=False);g._share_tree(install,writable=True)
        cache=work/'npm-cache';cache.mkdir(mode=0o700);g._share_tree(cache,writable=True)
        ienv={**env,'NODE_ENV':'development',**g._isolated_npm_config_paths(work),'NPM_CONFIG_CACHE':str(cache),'NPM_CONFIG_OFFLINE':'true','NPM_CONFIG_IGNORE_SCRIPTS':'true','NPM_CONFIG_AUDIT':'false','NPM_CONFIG_FUND':'false','NPM_CONFIG_UPDATE_NOTIFIER':'false'}
        command=[str(npm),'ci','--offline','--ignore-scripts','--include=dev','--no-audit','--no-fund','--no-progress','--cache',str(cache)]
        cp=g._trusted_process(command,timeout=200,env=ienv,cwd=install,project_code=True,audit_kind='npm_ci')
        require(cp.returncode==0,'NPM_OFFLINE_INSTALL_FAILED');require(g._APP_BUILD_HANDOFFS==['npm_ci'],'NPM_INSTALL_HANDOFF_UNVERIFIED')
        src=install/'node_modules';dst=root/'node_modules';require(src.is_dir() and not src.is_symlink() and not dst.exists() and not dst.is_symlink(),'NPM_INPUT_INVALID')
        src.rename(dst)
    return str(node),env


# Fixed wrapper: suppress unbounded stdout/stderr from reviewed project commands.
# The operator-created command array remains the only launch argument source.
QUIET='import os,sys\nf=os.open("/dev/null",os.O_WRONLY);os.dup2(f,1);os.dup2(f,2);os.close(f);os.execv(sys.argv[1],sys.argv[1:])'


def audited(g,cmd,root,env,kind,timeout):
    g._BUILD_KINDS=g._BUILD_KINDS|{kind}
    previous=list(g._APP_BUILD_HANDOFFS)
    result=g._trusted_process(['/usr/bin/python3','-I','-c',QUIET,*cmd],cwd=root,env=env,timeout=timeout,project_code=True,audit_kind=kind)
    return result.returncode==0 and g._APP_BUILD_HANDOFFS==previous+[kind]


def binary(path):
    p=Path(path);require(p.is_file(),'TOOLCHAIN_UNAVAILABLE');return path


def npm_bin(root,package,command):
    require(re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+',package) and all(x not in ('.','..','@.','@..') for x in package.split('/')),'NPM_INPUT_INVALID')
    folder=root.joinpath('node_modules',*package.split('/'));require(folder.is_dir() and not folder.is_symlink() and folder.resolve().is_relative_to(root.resolve()),'NPM_INPUT_INVALID')
    manifest=folder/'package.json'
    require(manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size<=512*1024,'NPM_INPUT_INVALID')
    obj=json.loads(manifest.read_text('utf-8'));require(obj.get('name')==package,'NPM_INPUT_INVALID');bins=obj.get('bin')
    rel=bins if isinstance(bins,str) and command==package.split('/')[-1] else bins.get(command) if isinstance(bins,dict) else None
    require(isinstance(rel,str),'BUILD_OUTPUT_MISSING');return str(within(folder,rel.removeprefix('./')))


def launch_recipe(g,work,root,req,node,env):
    """Return (server command, optional CLI completion, expected build count).
    All commands assembled from a fixed adapter table, not package scripts."""
    kind=req['adapter'];entry=req.get('entrypoint');ep=str(within(root,entry)) if entry is not None else None
    py='/usr/bin/python3';host='127.0.0.1';port=str(req.get('service_port',PORT))
    def serve(directory):return [py,'-I','-m','http.server',port,'--bind',host,'--directory',str(directory)]
    if kind=='STATIC_HTTP':return serve(Path(ep).parent),False,0
    if kind=='PHP_HTTP':return [binary('/usr/bin/php'),'-S',host+':'+port,'-t',str(Path(ep).parent)],False,0
    if kind=='NODE_HTTP':return [node,ep],False,0
    if kind=='NODE_CLI':return [node,ep],True,0
    if kind in {'NODE_NEXT','NODE_VITE','NODE_CRA','NODE_ASTRO','NODE_NUXT','NODE_SVELTEKIT','NODE_ANGULAR'}:
        build={'NODE_NEXT':('next','next',['build']),
               'NODE_VITE':('vite','vite',['build','--outDir','dist']),
               'NODE_CRA':('react-scripts','react-scripts',['build']),
               'NODE_ASTRO':('astro','astro',['build']),
               'NODE_NUXT':('nuxt','nuxt',['build']),
               'NODE_SVELTEKIT':('vite','vite',['build']),
               'NODE_ANGULAR':('@angular/cli','ng',['build','--configuration','production','--output-path','dist/projectiq'])}
        package,binname,args=build[kind];tool=npm_bin(root,package,binname);cmd=[node,tool,*args]
        require(audited(g,cmd,root,env,'phase8d_build',240),'BUILD_FAILED')
        if kind=='NODE_NEXT':return [node,tool,'start','--hostname',host,'--port',port],False,1
        if kind=='NODE_NUXT':return [node,str(within(root,'.output/server/index.mjs'))],False,1
        if kind=='NODE_SVELTEKIT':return [node,str(within(root,'build/index.js'))],False,1
        if kind=='NODE_ANGULAR':
            # Known static Angular output shapes, not arbitrary build-output commands.
            candidates=[p for p in ('dist/projectiq/browser','dist/projectiq') if (root/p/'index.html').is_file()]
            require(len(candidates)==1,'BUILD_OUTPUT_MISSING');dist=candidates[0]
        else:dist='build' if kind=='NODE_CRA' else 'dist'
        folder=within(root,dist,directory=True)
        require((folder/'index.html').is_file() and not (folder/'index.html').is_symlink(),'BUILD_OUTPUT_MISSING')
        return serve(folder),False,1
    if kind in {'PY_FLASK','PY_ASGI','PY_DJANGO','PY_STREAMLIT','PY_CLI'}:
        if kind in {'PY_FLASK','PY_ASGI'}:
            require(entry.endswith('.py') and all(p.isidentifier() for p in entry[:-3].split('/')) and isinstance(req.get('application_symbol'),str) and req['application_symbol'].isidentifier(),'ENTRYPOINT_UNSAFE')
            app=entry[:-3].replace('/','.')+':'+req['application_symbol']
            if kind=='PY_FLASK':return [py,'-m','flask','--app',app,'run','--host',host,'--port',port,'--no-reload','--no-debugger'],False,0
            return [py,'-m','uvicorn',app,'--host',host,'--port',port],False,0
        if kind=='PY_DJANGO':return [py,ep,'runserver',host+':'+port,'--noreload'],False,0
        if kind=='PY_STREAMLIT':return [py,'-m','streamlit','run',ep,'--server.address',host,'--server.port',port,'--server.headless','true','--browser.gatherUsageStats','false'],False,0
        return [py,ep],True,0
    if kind=='JAVA_SINGLE':return [binary('/usr/bin/java'),ep],True,0
    output=str(work/'program-bin')
    if kind in {'NATIVE_C','NATIVE_CPP','RUST_SINGLE','GO_SINGLE'}:
        # Single-file programs only, no package managers, build scripts or Makefiles.
        if kind=='NATIVE_C':cmd=[binary('/usr/bin/gcc'),'-std=c17',ep,'-o',output]
        elif kind=='NATIVE_CPP':cmd=[binary('/usr/bin/g++'),'-std=c++17',ep,'-o',output]
        elif kind=='RUST_SINGLE':cmd=[binary('/usr/bin/rustc'),ep,'-o',output]
        else:cmd=[binary('/usr/bin/go'),'build','-o',output,ep]
        # Output path is under app-owned workspace directory, not controller root.
        folder=work/'compiled';folder.mkdir(mode=0o700);g._share_tree(folder,writable=True);output=str(folder/'program-bin')
        cmd=[output if x==str(work/'program-bin') else x for x in cmd]
        require(audited(g,cmd,root,env,'phase8d_build',150),'BUILD_FAILED');require(Path(output).is_file() and not Path(output).is_symlink(),'BUILD_OUTPUT_MISSING')
        return [output],True,1
    raise ValueError('RUNTIME_ADAPTER_UNSUPPORTED')


def listener_uid(port,uid,tables=(Path('/proc/net/tcp'),Path('/proc/net/tcp6'))):
    try:
        for table in tables:
            if not table.exists():continue
            with table.open('rb') as f:data=f.read(256_001)
            if len(data)>256_000:return False
            for row in data.decode('ascii').splitlines()[1:]:
                cols=row.split()
                if len(cols)>9 and cols[3]=='0A' and int(cols[1].rsplit(':',1)[1],16)==port and int(cols[7])==uid:return True
        return False
    except (OSError,ValueError,UnicodeError):return False


def probe_http(proc,paths,*,seconds=45,port=PORT):
    """No redirects, proxy, response text export, or guessed arbitrary URLs."""
    until=time.monotonic()+seconds;observed=[]
    while time.monotonic()<until and proc.poll() is None:
        for path in paths:
            con=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
            try:
                con.request('GET',path,headers={'User-Agent':'ProjectIQ-8D-Readiness','Connection':'close'})
                response=con.getresponse();status=response.status
                if status not in observed:observed.append(status)
                if 200<=status<300 and proc.poll() is None:
                    return {'status':'HTTP_2XX_OBSERVED','path':path,'http_status':status}
            except (OSError,http.client.HTTPException):pass
            finally:con.close()
        time.sleep(.5)
    return {'status':'HTTP_READINESS_UNVERIFIED','observed_statuses':sorted(observed)[:20]}


def run(g,base,req,python_verify,npm_constraint):
    port=req.get('service_port',PORT);require(type(port) is int and 1024<=port<=65535,'NETWORK_PROBE_INVALID')
    if req['adapter'].startswith('PY_'):
        require('.'.join(map(str,sys.version_info[:3]))==req['python_version'],'PYTHON_TARGET_MISMATCH')
    if req['dependency_kind']=='PYTHON':
        proof=python_verify(g,base,req)
        require(proof.get('status')=='PYTHON_OFFLINE_LOCK_VERIFIED','PIP_OFFLINE_INSTALL_FAILED')
        work=g.WORKSPACE_PATH
    else:work=init(g,base,req)
    snapshot=work/'snapshot.zip';require(g._sha256(snapshot)==req['snapshot_sha256'],'INPUT_HASH_MISMATCH')
    checkout=g.safe_extract(snapshot,work/'checkout');snapshot.unlink()
    root=within(checkout,req['root'],directory=True);g._share_tree(checkout,writable=True)
    env={'PATH':'/usr/bin:/bin','PORT':str(req.get('service_port',PORT)),'HOST':'127.0.0.1','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','CI':'true'}
    node=None
    if req['adapter'].startswith('NODE_'):node,env=install_node(g,work,req,root,npm_constraint)
    if req['dependency_kind']=='PYTHON':env['PYTHONPATH']=str(work/'python-site')+':'+str(root)
    # No student source has been intentionally launched before this stage.
    command,cli,built=launch_recipe(g,work,root,req,node,env)
    evidence={'source':req['source'],'component_id':req['component_id'],'adapter':req['adapter'],'snapshot_sha256':req['snapshot_sha256'],'plan_sha256':req['plan_sha256'],'dependency_kind':req['dependency_kind'],'dependency_sha256':req.get('bundle_sha256'),'host_gateway_denied':True,'workspace_mib':1536,'build_handoffs':built,'npm_install_handoffs':g._APP_BUILD_HANDOFFS.count('npm_ci'),'pip_install_handoffs':g._APP_BUILD_HANDOFFS.count('pip_install'),'functional_tests_executed':0}
    if cli:
        require(audited(g,command,root,env,'phase8d_cli',45),'CLI_FAILED')
        evidence.update(status='CLI_EXIT_ZERO_OBSERVED',cli_exit_code=0,cli_preexec_handoff=True)
    else:
        g._APP_SERVER_PLACEMENTS.clear();proc=None
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as check:
            try:check.bind(('127.0.0.1',port))
            except OSError:raise ValueError('HTTP_PORT_ALREADY_OCCUPIED')
        try:
            with open('/dev/null','wb') as log:proc=g._project_popen(command,cwd=root,env=env,log=log)
            require(len(g._APP_SERVER_PLACEMENTS)==1,'SERVER_PLACEMENT_UNVERIFIED')
            paths=(req['probe_path'],) if req['adapter'] in {'STATIC_HTTP','PHP_HTTP','NODE_NEXT','NODE_VITE','NODE_CRA','NODE_ASTRO','NODE_NUXT','NODE_SVELTEKIT','NODE_ANGULAR','PY_STREAMLIT'} else PROBE_PATHS
            result=probe_http(proc,paths,port=port)
            require(result['status']=='HTTP_2XX_OBSERVED','HTTP_READINESS_UNVERIFIED')
            require(listener_uid(port,g.pwd.getpwnam(g.APP_USER).pw_uid),'HTTP_LISTENER_UID_UNVERIFIED')
            evidence.update(status='HTTP_READINESS_OBSERVED',service_port=port,http=result,server_placement_observed=True,application_uid_listener_observed=True)
        finally:
            if proc is not None:g._d4_stop_process(proc)
    return evidence


def main(python_verify,npm_constraint):
    base=Path('/home/projectiq');sys.path.insert(0,str(base));import guest_agent as g
    try:result=run(g,base,json.loads((base/'request.json').read_text('utf-8')),python_verify,npm_constraint)
    except Exception as exc:
        # Never return source, raw stdout, stack traces or arbitrary exception text.
        code=str(exc);result={'status':'FAILED_OR_INCONCLUSIVE','error_code':code if (type(exc) is ValueError or type(exc).__name__=='GuestFailure') and code in ERRORS else 'GUEST_RUNTIME_STAGE_FAILED'}
    (base/'result.json').write_text(json.dumps(result),'utf-8');return 0 if result['status'] in {'HTTP_READINESS_OBSERVED','CLI_EXIT_ZERO_OBSERVED'} else 2
