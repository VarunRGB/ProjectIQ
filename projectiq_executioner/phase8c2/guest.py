"""Copied as trusted operator code over SSH. Never imported from submitted source."""
import base64, hashlib, json, os, stat, sys, zipfile
from pathlib import Path


def assemble(g,base,req):
    g._verify_execution_identity(base)
    work=g._stage_bounded_workspace({'workspace_disk_mib':1536},base=base)
    g._verify_evaluation_cgroup(work);g._APP_CGROUP_ACTIVE=True
    g._APP_BUILD_HANDOFFS.clear()
    port=req['sentinel_port']
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('NETWORK_PROBE_INVALID')
    probe='import socket,sys\ntry:\n s=socket.create_connection(("10.0.2.2",int(sys.argv[1])),timeout=3);s.close()\nexcept OSError: sys.exit(0)\nsys.exit(2)'
    observed=g._trusted_process(['/usr/bin/python3','-I','-c',probe,str(port)],timeout=15,env={},cwd=work,project_code=True)
    if observed.returncode:raise ValueError('HOST_GATEWAY_NOT_DENIED')
    archive=work/'npm_cache.zip';node_archive=work/'node_runtime.tar.xz'
    if g._sha256(archive)!=req['bundle_sha256'] or g._sha256(node_archive)!=req['node_sha256']:raise ValueError('INPUT_HASH_MISMATCH')
    inputs=g.safe_extract(archive,work/'inputs',max_total=145_000_000);archive.unlink()
    project=work/'manifest-project';project.mkdir(mode=0o700)
    package=json.loads((inputs/'package.json').read_text());lock=json.loads((inputs/'package-lock.json').read_text())
    if 'scripts' in package or lock.get('lockfileVersion')!=3:raise ValueError('MANIFEST_UNSAFE')
    # Source-free manifests and verified tarballs; no submitted JS or other code.
    local=json.loads(json.dumps(lock));used=set()
    for path,row in local['packages'].items():
        if not path:continue
        name=base64.b64decode(row['integrity'].split('-',1)[1],validate=True).hex()+'.tgz'
        tar=inputs/'tarballs'/name
        if tar.is_file() and not tar.is_symlink():
            with tar.open('rb') as f:
                if hashlib.file_digest(f,'sha512').hexdigest()!=name[:-4]:raise ValueError('PACKAGE_DIGEST_MISMATCH')
            row['resolved']='file:'+str(tar);used.add(name)
    if not used:raise ValueError('EMPTY_INPUTS')
    (project/'package.json').write_text(json.dumps(package));(project/'package-lock.json').write_text(json.dumps(local))
    g._share_tree(inputs,writable=False);g._share_tree(project,writable=True)
    runtime=work/'node_runtime';runtime.mkdir(mode=0o700)
    node,npm=g._node_tar_extract(node_archive,req['node_version'],runtime);node_archive.unlink();g._share_tree(runtime,writable=False)
    cache=work/'npm-cache';cache.mkdir(mode=0o700);g._share_tree(cache,writable=True)
    env={'PATH':str(node.parent)+':/usr/bin:/bin',**g._isolated_npm_config_paths(work),'NPM_CONFIG_CACHE':str(cache),'NPM_CONFIG_OFFLINE':'true','NPM_CONFIG_IGNORE_SCRIPTS':'true','NPM_CONFIG_AUDIT':'false','NPM_CONFIG_FUND':'false','NPM_CONFIG_UPDATE_NOTIFIER':'false','CI':'true'}
    command=[str(npm),'ci','--offline','--ignore-scripts','--no-audit','--no-fund','--no-progress','--cache',str(cache)]
    first=g._trusted_process(command,timeout=185,env=env,cwd=project,project_code=True,audit_kind='npm_ci')
    if first.returncode:raise ValueError('LOCAL_ARCHIVE_INSTALL_FAILED')
    # Restore original registry lock and demonstrate offline reuse, no local
    # tarball paths in the second install. Keep trusted file owner unchanged.
    # The application UID had write access to the first project; create a fresh
    # project instead of trusting the prior node_modules or manifest paths.
    verify=work/'verify-project';verify.mkdir(mode=0o700)
    (verify/'package.json').write_text(json.dumps(package));(verify/'package-lock.json').write_text(json.dumps(lock));g._share_tree(verify,writable=True)
    second=g._trusted_process(command,timeout=185,env=env,cwd=verify,project_code=True,audit_kind='npm_ci')
    if second.returncode:raise ValueError('REGISTRY_LOCK_OFFLINE_REUSE_FAILED')
    if g._APP_BUILD_HANDOFFS!=['npm_ci','npm_ci']:raise ValueError('PREEXEC_HANDOFF_UNVERIFIED')
    dest=base/'npm_cache.zip';count=total=0
    with zipfile.ZipFile(dest,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=5,allowZip64=False) as z:
        for p in sorted(cache.rglob('*')):
            if p.is_symlink():raise ValueError('UNSAFE_CACHE')
            if p.is_dir():continue
            rel=p.relative_to(cache)
            if '_logs' in rel.parts:continue
            if not p.is_file() or p.stat().st_size>20_000_000:raise ValueError('UNSAFE_CACHE')
            count+=1;total+=p.stat().st_size
            if count>12000 or total>250_000_000:raise ValueError('CACHE_LIMIT')
            z.write(p,'npm-cache/'+rel.as_posix())
    if not count or dest.stat().st_size>170_000_000:raise ValueError('CACHE_LIMIT')
    return {'status':'OFFLINE_CACHE_ASSEMBLED','bundle_sha256':req['bundle_sha256'],'npm_cache_sha256':g._sha256(dest),'node_sha256':req['node_sha256'],'offline_reuse':True,'host_gateway_denied':True,'npm_ci_preexec_handoffs':2,'repository_scripts_executed':False,'workspace_mib':1536}


def main():
    base=Path('/home/projectiq');sys.path.insert(0,str(base))
    import guest_agent as g
    try:result=assemble(g,base,json.loads((base/'request.json').read_text()))
    except Exception as exc:
        allowed={'INPUT_HASH_MISMATCH','MANIFEST_UNSAFE','PACKAGE_DIGEST_MISMATCH','EMPTY_INPUTS','LOCAL_ARCHIVE_INSTALL_FAILED','MANIFEST_CHANGED','REGISTRY_LOCK_OFFLINE_REUSE_FAILED','UNSAFE_CACHE','CACHE_LIMIT','NETWORK_PROBE_INVALID','HOST_GATEWAY_NOT_DENIED','PREEXEC_HANDOFF_UNVERIFIED'}
        result={'status':'FAILED','error_code':str(exc) if type(exc) is ValueError and str(exc) in allowed else 'GUEST_ASSEMBLY_FAILED'}
    (base/'result.json').write_text(json.dumps(result))
    return 0 if result['status']=='OFFLINE_CACHE_ASSEMBLED' else 2

if __name__=='__main__':raise SystemExit(main())
