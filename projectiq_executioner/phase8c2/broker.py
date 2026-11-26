"""Trusted, bounded byte acquisition. Never unpack or execute registry content on host."""
from __future__ import annotations
import base64, hashlib, http.client, ipaddress, json, re, socket, ssl, time, zipfile
from pathlib import Path
from ..phase8b.planner import load_object, npm_check
from ..phase8a.planner import digest

HOST='registry.npmjs.org'
MAX_FILE=20_000_000
MAX_TOTAL=140_000_000
MAX_RECORDS=1000
MAX_SECONDS=600
NAME=r'(?:@[a-z0-9][a-z0-9_.-]*/)?[a-z0-9][a-z0-9_.-]*'
VERSION=r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?'
FIELDS=('dependencies','devDependencies','optionalDependencies','peerDependencies','peerDependenciesMeta')
ROW_FIELDS=('version','resolved','integrity','dev','optional','devOptional','peer','dependencies','optionalDependencies','peerDependencies','peerDependenciesMeta','engines','os','cpu','libc','hasInstallScript','bin','hasShrinkwrap')


def require(ok,code):
    if not ok:raise ValueError(code)


def exact_url(name,version):
    require(isinstance(name,str) and re.fullmatch(NAME,name) and all(x not in ('.','..') for x in name.split('/')),'INVALID_PACKAGE_NAME')
    require(isinstance(version,str) and re.fullmatch(VERSION,version),'INVALID_PACKAGE_VERSION')
    return 'https://'+HOST+'/'+name+'/-/'+name.split('/')[-1]+'-'+version+'.tgz'


def _platform(row):
    for key,target in [('os','linux'),('cpu','x64'),('libc','glibc')]:
        values=row.get(key,[])
        require(isinstance(values,list) and len(values)<=40 and all(isinstance(x,str) and re.fullmatch(r'!?[A-Za-z0-9_-]{1,30}',x) for x in values),'INVALID_PLATFORM_SELECTOR')
        positive=[x for x in values if not x.startswith('!')]
        if '!'+target in values or (positive and target not in positive and 'any' not in positive):return False
    return True


def compile_inputs(manifest,lock):
    issues,_=npm_check({'package.json':json.dumps(manifest),'package-lock.json':json.dumps(lock)},'.')
    require(not issues,'NPM_PREFLIGHT_BLOCKED')
    require(lock.get('lockfileVersion')==3,'ONLY_LOCK_V3_SUPPORTED')
    records=lock['packages'];require(1<len(records)<=MAX_RECORDS+1,'LOCK_RECORD_LIMIT')
    # No student scripts, config files, workspace code or package.json hooks.
    name=manifest.get('name','projectiq-input');version=manifest.get('version','1.0.0')
    exact_url(name,version)
    clean={'name':name,'version':version,'private':True,**{k:manifest[k] for k in FIELDS if k in manifest}}
    cleanlock={'name':name,'version':version,'lockfileVersion':3,'requires':True,'packages':{'':{k:v for k,v in clean.items() if k!='private'}}}
    jobs={}; skipped=0
    for path,row in sorted(records.items()):
        if not path:continue
        # Each nested node_modules segment must contain exactly one normal package name.
        require(isinstance(path,str) and len(path)<=350 and path.startswith('node_modules/'),'INVALID_LOCK_PATH')
        names=path[len('node_modules/'):].split('/node_modules/')
        require(all(re.fullmatch(NAME,n) and all(part not in ('.','..') for part in n.split('/')) for n in names),'INVALID_LOCK_PATH')
        for field in ('dependencies','optionalDependencies','peerDependencies'):
            deps=row.get(field,{})
            require(isinstance(deps,dict) and len(deps)<=1000 and all(re.fullmatch(NAME,k) and isinstance(v,str) and len(v)<=200 and re.fullmatch(r'[0-9A-Za-z.*^~<>=| +\-]+',v) and not v.startswith(('git','http','file','link','workspace','npm')) for k,v in deps.items()),'NONREGISTRY_TRANSITIVE_SPEC')
        pkg=names[-1]; url=exact_url(pkg,row.get('version'));require(row.get('resolved')==url,'REGISTRY_URL_NOT_EXACT_PACKAGE')
        integrity=row['integrity']; raw=base64.b64decode(integrity.split('-',1)[1],validate=True)
        require(len(raw)==64,'INVALID_INTEGRITY')
        key=raw.hex(); cleanlock['packages'][path]={k:row[k] for k in ROW_FIELDS if k in row}
        if not _platform(row):
            require(row.get('optional') is True or row.get('devOptional') is True,'MANDATORY_PACKAGE_PLATFORM_MISMATCH');skipped+=1;continue
        if key in jobs:require(jobs[key]['url']==url,'DIGEST_URL_CONFLICT')
        else:jobs[key]={'url':url,'sha512':key,'filename':key+'.tgz'}
    require(bool(jobs),'NO_TARGET_PACKAGES')
    return {'manifest':clean,'lock':cleanlock,'downloads':list(jobs.values()),'platform_skipped':skipped}


def public_address(value):
    try:
        a=ipaddress.ip_address(value)
        return a.version==4 and a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback or a.is_link_local or a.is_unspecified)
    except ValueError:return False


class RegistryConnection(http.client.HTTPSConnection):
    """No proxy, redirect, caller-selected host/port, or DNS re-resolution."""
    def __init__(self):super().__init__(HOST,443,timeout=15,context=ssl.create_default_context())
    def connect(self):
        addresses=sorted({a[4][0] for a in socket.getaddrinfo(HOST,443,socket.AF_INET,socket.SOCK_STREAM)})
        require(bool(addresses) and all(public_address(a) for a in addresses),'REGISTRY_DNS_NOT_PUBLIC')
        raw=socket.create_connection((addresses[0],443),timeout=self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=HOST)
        except BaseException:raw.close();raise


def fetch_one(job,path,remaining,deadline,connection=RegistryConnection):
    # Defense in depth: the job is generated from a checked lock, not CLI URLs.
    url=job['url'];require(re.fullmatch(r'https://registry\.npmjs\.org/'+NAME+r'/-/[a-z0-9_.-]+-'+VERSION+r'\.tgz',url) is not None,'REGISTRY_URL_DENIED')
    require(re.fullmatch('[0-9a-f]{128}',job['sha512']) and job['filename']==job['sha512']+'.tgz','INVALID_DIGEST')
    cap=min(remaining,MAX_FILE);require(cap>0,'DOWNLOAD_BUDGET_EXCEEDED')
    con=connection()
    try:
        con.request('GET',url[len('https://'+HOST):],headers={'Accept-Encoding':'identity','User-Agent':'ProjectIQ-Locked-Artifact-Fetch/1','Connection':'close'})
        res=con.getresponse();require(res.status==200,'REGISTRY_HTTP_NOT_200')
        require(res.getheader('Content-Encoding','identity').lower()=='identity','ENCODING_DENIED')
        declared=res.getheader('Content-Length');require(declared is None or (declared.isdecimal() and 0<int(declared)<=cap),'DOWNLOAD_SIZE_LIMIT')
        size=0;h=hashlib.sha512()
        with path.open('xb') as f:
            while True:
                require(time.monotonic()<deadline,'DOWNLOAD_DEADLINE')
                data=res.read(min(65536,cap-size+1))
                if not data:break
                size+=len(data);require(size<=cap,'DOWNLOAD_SIZE_LIMIT');h.update(data);f.write(data)
        require(size>0 and (declared is None or size==int(declared)) and h.hexdigest()==job['sha512'],'DOWNLOAD_INTEGRITY_MISMATCH')
        return size
    finally:con.close()


def build_bundle(inputs,out,*,fetch=fetch_one):
    """All downloaded bytes remain inert. No tar parser or package manager here."""
    import shutil
    out=Path(out);deadline=time.monotonic()+MAX_SECONDS;total=0
    require(not out.exists(),'OUTPUT_COLLISION')
    root=out.parent/'.registry-staging'
    root.mkdir(exist_ok=False)
    try:
        with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_STORED,allowZip64=False) as z:
            for key in ('manifest','lock'):
                z.writestr('inputs/'+('package.json' if key=='manifest' else 'package-lock.json'),json.dumps(inputs[key],separators=(',',':')))
            for job in inputs['downloads']:
                p=root/job['filename'];size=fetch(job,p,MAX_TOTAL-total,deadline);total+=size
                require(total<=MAX_TOTAL and p.stat().st_size==size,'DOWNLOAD_BUDGET_EXCEEDED')
                z.write(p,'inputs/tarballs/'+job['filename']);p.unlink()
        require(out.stat().st_size<=145_000_000,'BUNDLE_SIZE_LIMIT')
        return {'download_count':len(inputs['downloads']),'package_bytes':total,'platform_skipped':inputs['platform_skipped']}
    except BaseException:
        out.unlink(missing_ok=True);raise
    finally:
        shutil.rmtree(root)
