"""Operator-owned guest program for offline validation of exact Python wheel sets."""
import json, re, sys, os
from pathlib import Path


def require(ok,code):
    if not ok:raise ValueError(code)


def verify_report(path,expected):
    require(path.is_file() and not path.is_symlink() and 0<path.stat().st_size<=2_000_000,'PIP_REPORT_INVALID')
    data=json.loads(path.read_text('utf-8'));rows=data.get('install')
    require(isinstance(rows,list) and len(rows)==len(expected),'PIP_RESOLUTION_MISMATCH')
    observed={}
    for row in rows:
        m=row['metadata'];name=re.sub(r'[-_.]+','-',m['name']).lower()
        sha=row['download_info']['archive_info']['hashes']['sha256']
        require(name not in observed,'PIP_RESOLUTION_MISMATCH');observed[name]=(m['version'],sha)
    require(observed==expected,'PIP_RESOLUTION_MISMATCH')


def assemble(g,base,req):
    require('.'.join(map(str,sys.version_info[:3]))==req['python_version'],'PYTHON_TARGET_MISMATCH')
    g._verify_execution_identity(base)
    work=g._stage_bounded_workspace({'workspace_disk_mib':1536},base=base)
    g._verify_evaluation_cgroup(work);g._APP_CGROUP_ACTIVE=True;g._APP_BUILD_HANDOFFS.clear()
    port=req['sentinel_port'];require(type(port) is int and 1024<=port<=65535,'NETWORK_PROBE_INVALID')
    probe='import socket,sys\ntry:\n s=socket.create_connection(("10.0.2.2",int(sys.argv[1])),timeout=3);s.close()\nexcept OSError: sys.exit(0)\nsys.exit(2)'
    observed=g._trusted_process(['/usr/bin/python3','-I','-c',probe,str(port)],timeout=15,env={},cwd=work,project_code=True)
    require(observed.returncode==0,'HOST_GATEWAY_NOT_DENIED')
    archive=work/'python_wheelhouse.zip';require(g._sha256(archive)==req['bundle_sha256'],'INPUT_HASH_MISMATCH')
    wheels=work/'python-wheels';files=g._unpack_python_wheelhouse(archive,wheels)
    pins=req['direct_pins'];rows=req['wheels'];require(type(pins) is dict and 1<=len(pins)<=25 and type(rows) is list and 1<=len(rows)<=100,'LOCK_INVALID')
    expected={};names=set();locked=[];constraints=[]
    for row in rows:
        n,v,f,h=row['name'],row['version'],row['filename'],row['sha256']
        require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,100}',n) and re.fullmatch(r'[0-9][A-Za-z0-9.!+_-]{0,79}',v) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,180}\.whl',f) and re.fullmatch('[0-9a-f]{64}',h) and n not in expected and f not in names,'LOCK_INVALID')
        require((wheels/f).is_file() and g._sha256(wheels/f)==h,'WHEEL_HASH_MISMATCH');names.add(f);expected[n]=(v,h);locked.append(n+'=='+v+' --hash=sha256:'+h);constraints.append(n+'=='+v)
    require({p.name for p in files}==names and all(n in expected and expected[n][0]==v for n,v in pins.items()),'WHEEL_SET_MISMATCH')
    g._share_tree(wheels,writable=False)
    pip=g._python_pip_wheel(work,req);env=g._verify_python_pip(work,pip)
    direct=work/'direct.txt';direct.write_text('\n'.join(n+'=='+v for n,v in sorted(pins.items()))+'\n');g._share_tree(direct,writable=False)
    constraint=work/'constraints.txt';constraint.write_text('\n'.join(constraints)+'\n');g._share_tree(constraint,writable=False)
    lock=work/'locked.txt';lock.write_text('\n'.join(locked)+'\n');g._share_tree(lock,writable=False)
    reports=work/'reports';reports.mkdir(mode=0o700);g._share_tree(reports,writable=True)
    common=['/usr/bin/python3','-m','pip','--isolated','install','--no-index','--find-links',str(wheels),'--only-binary=:all:','--ignore-installed','--no-cache-dir','--no-compile','--disable-pip-version-check']
    plan=reports/'resolve.json';cmd=common+['--dry-run','--report',str(plan),'-c',str(constraint),'-r',str(direct)]
    result=g._trusted_process(cmd,timeout=120,env=env,cwd=work,project_code=True,audit_kind='pip_install')
    require(result.returncode==0,'PIP_OFFLINE_RESOLUTION_FAILED');verify_report(plan,expected)
    site=work/'python-site';site.mkdir(mode=0o700);g._share_tree(site,writable=True)
    report=reports/'installed.json';cmd=common+['--require-hashes','--target',str(site),'--report',str(report),'-r',str(lock)]
    result=g._trusted_process(cmd,timeout=150,env=env,cwd=work,project_code=True,audit_kind='pip_install')
    require(result.returncode==0,'PIP_OFFLINE_INSTALL_FAILED');verify_report(report,expected)
    require(g._APP_BUILD_HANDOFFS==['pip_install','pip_install'],'PREEXEC_HANDOFF_UNVERIFIED')
    return {'status':'PYTHON_OFFLINE_LOCK_VERIFIED','bundle_sha256':req['bundle_sha256'],'pip_sha256':req['pip_sha256'],'lock_sha256':req['lock_sha256'],'python_version':req['python_version'],'wheel_count':len(rows),'offline_resolution_verified':True,'offline_install_verified':True,'host_gateway_denied':True,'pip_preexec_handoffs':2,'workspace_mib':1536,'repository_code_executed':False}


def main():
    base=Path('/home/projectiq');sys.path.insert(0,str(base));import guest_agent as g
    try:result=assemble(g,base,json.loads((base/'request.json').read_text('utf-8')))
    except Exception as exc:
        allowed={'PYTHON_TARGET_MISMATCH','NETWORK_PROBE_INVALID','HOST_GATEWAY_NOT_DENIED','INPUT_HASH_MISMATCH','LOCK_INVALID','WHEEL_HASH_MISMATCH','WHEEL_SET_MISMATCH','PIP_REPORT_INVALID','PIP_RESOLUTION_MISMATCH','PIP_OFFLINE_RESOLUTION_FAILED','PIP_OFFLINE_INSTALL_FAILED','PREEXEC_HANDOFF_UNVERIFIED'}
        result={'status':'FAILED','error_code':str(exc) if type(exc) is ValueError and str(exc) in allowed else 'PYTHON_GUEST_PREPARATION_FAILED'}
    (base/'result.json').write_text(json.dumps(result),'utf-8');return 0 if result['status']=='PYTHON_OFFLINE_LOCK_VERIFIED' else 2

if __name__=='__main__':raise SystemExit(main())
