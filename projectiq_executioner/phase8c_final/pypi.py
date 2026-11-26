"""Bounded PyPI JSON resolution and byte-only wheel fetch; no host package execution.
Release metadata is advisory until the offline guest validates the actual wheels.
Only exact direct pins without extras and stable compatible wheels are supported.
"""
from __future__ import annotations
import hashlib, http.client, json, re, socket, ssl, time, zipfile, shutil
from pathlib import Path
from urllib.parse import urlsplit
from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.specifiers import SpecifierSet
from ..phase8a.planner import digest
from ..phase8b.planner import load_object
from ..phase8c2.broker import require, public_address

MAX_METADATA=12_000_000
MAX_WHEEL=35_000_000
MAX_TOTAL=110_000_000
MAX_NODES=100
MAX_QUERIES=220
MAX_STATES=350
NAME=re.compile(r'[a-z0-9][a-z0-9-]{0,100}\Z')
VER=re.compile(r'[0-9][A-Za-z0-9.!+_-]{0,79}\Z')
WHEEL=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,180}\.whl\Z')
SHA=re.compile(r'[0-9a-f]{64}\Z')
FILE_PATH=re.compile(r'/packages/(?:[a-f0-9]{2}/){2}[a-f0-9]{40,128}/[A-Za-z0-9_.+\-]+\.whl\Z')


def target(version):
    require(isinstance(version,str) and re.fullmatch(r'3\.12\.[0-9]{1,3}',version),'PYTHON_TARGET_UNSUPPORTED')
    return {'implementation_name':'cpython','implementation_version':version,'os_name':'posix',
            'platform_machine':'x86_64','platform_python_implementation':'CPython','platform_release':'',
            'platform_system':'Linux','platform_version':'','python_full_version':version,
            'python_version':'3.12','sys_platform':'linux','extra':''}


def direct_pins(text):
    require(isinstance(text,str) and len(text.encode('utf-8'))<=16000,'PYTHON_REQUIREMENTS_LIMIT')
    pins={}
    for raw in text.splitlines():
        line=raw.strip().lstrip('\ufeff')
        if not line or line.startswith('#'):continue
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}==[0-9][A-Za-z0-9.!+_-]{0,79}',line),'PYTHON_EXACT_DIRECT_PINS_REQUIRED')
        n,v=line.split('==');n=canonicalize_name(n);parsed=Version(v)
        require(NAME.fullmatch(n) and not parsed.is_prerelease and not parsed.is_devrelease and n not in pins,'PYTHON_PIN_INVALID_OR_DUPLICATE')
        pins[n]=str(parsed)
    require(0<len(pins)<=25,'PYTHON_DIRECT_PIN_LIMIT')
    return dict(sorted(pins.items()))


def wheel_rank(filename,name,version):
    """Conservative CPython 3.12 / Linux x86_64 / glibc >=2.17 target.
    Allow manylinux up to glibc 2.17 only, not arbitrary linux wheels.
    """
    try:
        if not WHEEL.fullmatch(filename):return None
        n,v,_,tags=parse_wheel_filename(filename)
        if n!=name or v!=Version(version):return None
        ranks=[]
        for t in tags:
            i,a,p=t.interpreter,t.abi,t.platform
            if p=='any' and a=='none' and i in {'py3','py312','cp312'}:ranks.append(100);continue
            if p not in {'manylinux2014_x86_64','manylinux2010_x86_64','manylinux1_x86_64'}:
                m=re.fullmatch(r'manylinux_2_([0-9]{1,2})_x86_64',p)
                if not m or not 5<=int(m.group(1))<=17:continue
            if i=='cp312' and a=='cp312':ranks.append(0)
            elif a=='abi3' and re.fullmatch(r'cp3[2-9]|cp31[0-2]',i):ranks.append(10)
            elif i in {'py3','py312','cp312'} and a=='none':ranks.append(20)
        return min(ranks) if ranks else None
    except (ValueError,TypeError):return None


def file_url(url,filename):
    require(isinstance(url,str) and WHEEL.fullmatch(filename) and len(url)<=512,'PYPI_FILE_URL_INVALID')
    u=urlsplit(url)
    require(u.scheme=='https' and u.netloc=='files.pythonhosted.org' and not u.query and not u.fragment
            and FILE_PATH.fullmatch(u.path) and u.path.rsplit('/',1)[1]==filename,'PYPI_FILE_URL_DENIED')
    return u.path


class PublicTLS(http.client.HTTPSConnection):
    def __init__(self,host):
        require(host in {'pypi.org','files.pythonhosted.org'},'PYPI_HOST_DENIED')
        super().__init__(host,443,timeout=15,context=ssl.create_default_context())
    def connect(self):
        ips=sorted({a[4][0] for a in socket.getaddrinfo(self.host,443,socket.AF_INET,socket.SOCK_STREAM)})
        require(ips and all(public_address(a) for a in ips),'PYPI_DNS_NOT_PUBLIC')
        raw=socket.create_connection((ips[0],443),timeout=self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:raw.close();raise


def _get(host,path,limit,deadline,consume,connection=PublicTLS):
    require(time.monotonic()<deadline and 0<limit<=MAX_WHEEL,'PYPI_BUDGET_EXCEEDED')
    if host=='pypi.org':require(re.fullmatch(r'/pypi/[a-z0-9][a-z0-9-]{0,100}(?:/[0-9][A-Za-z0-9.!+_-]{0,79})?/json',path),'PYPI_METADATA_PATH_DENIED')
    else:require(host=='files.pythonhosted.org' and FILE_PATH.fullmatch(path),'PYPI_FILE_URL_DENIED')
    con=connection(host)
    try:
        con.request('GET',path,headers={'Accept-Encoding':'identity','Connection':'close','User-Agent':'ProjectIQ-Wheel-Broker/1'})
        response=con.getresponse();require(response.status==200,'PYPI_HTTP_NOT_200')
        require(response.getheader('Content-Encoding','identity').lower()=='identity','PYPI_ENCODING_DENIED')
        length=response.getheader('Content-Length')
        require(length is None or length.isdecimal() and 0<int(length)<=limit,'PYPI_SIZE_LIMIT')
        size=0
        while True:
            require(time.monotonic()<deadline,'PYPI_DEADLINE')
            block=response.read(min(65536,limit-size+1))
            if not block:break
            size+=len(block);require(size<=limit,'PYPI_SIZE_LIMIT');consume(block)
        require(size>0 and (length is None or size==int(length)),'PYPI_SIZE_MISMATCH')
        return size
    finally:con.close()


class Metadata:
    def __init__(self,deadline):self.deadline=deadline;self.count=0;self.cache={};self.total=0
    def get(self,name,version=None):
        require(NAME.fullmatch(name) and (version is None or VER.fullmatch(version)),'PYPI_METADATA_PATH_DENIED')
        key=(name,version)
        if key not in self.cache:
            self.count+=1;require(self.count<=MAX_QUERIES,'PYPI_QUERY_BUDGET')
            path='/pypi/'+name+('/'+version if version else '')+'/json';data=bytearray()
            _get('pypi.org',path,MAX_METADATA,self.deadline,data.extend)
            self.total+=len(data);require(self.total<=80_000_000,'PYPI_METADATA_TOTAL_LIMIT')
            self.cache[key]=load_object(data.decode('utf-8'))
        return self.cache[key]


def _eligible(files,name,version,env):
    require(isinstance(files,list) and len(files)<=3000,'PYPI_RELEASE_FILE_LIMIT')
    candidates=[]
    for f in files:
        if not isinstance(f,dict) or f.get('packagetype')!='bdist_wheel' or f.get('yanked') is not False:continue
        filename=f.get('filename','');rank=wheel_rank(filename,name,version)
        if rank is None:continue
        rp=f.get('requires_python')
        if rp and not SpecifierSet(rp).contains(env['python_full_version'],prereleases=True):continue
        size=f.get('size');sha=f.get('digests',{}).get('sha256');url=f.get('url')
        if type(size) is not int or not 0<size<=MAX_WHEEL or not isinstance(sha,str) or not SHA.fullmatch(sha):continue
        file_url(url,filename)
        candidates.append((rank,filename,{'name':name,'version':version,'filename':filename,'sha256':sha,'bytes':size,'url':url}))
    return min(candidates,key=lambda x:(x[0],x[1]))[2] if candidates else None


def _dependencies(info,env):
    raw=info.get('requires_dist') or []
    require(isinstance(raw,list) and len(raw)<=200,'PYPI_METADATA_DEPENDENCY_LIMIT')
    result=[]
    for row in raw:
        require(isinstance(row,str) and len(row)<=500,'PYPI_REQUIREMENT_INVALID')
        r=Requirement(row)
        if r.marker:
            # Unknown kernel-specific marker values are not inferred.
            require(not re.search(r'\b(platform_release|platform_version)\b',str(r.marker)),'PYPI_MARKER_UNSUPPORTED')
            if not r.marker.evaluate(env):continue
        require(not r.url and not r.extras,'PYPI_TRANSITIVE_URL_OR_EXTRAS_UNSUPPORTED')
        name=canonicalize_name(r.name);require(NAME.fullmatch(name),'PYPI_NAME_INVALID')
        result.append((name,str(r.specifier)))
    return result


def resolve(pins,python_version,metadata):
    """Limited deterministic backtracking; no source execution or pip on host."""
    env=target(python_version);states=0;versions={};releases={}
    constraints={n:['=='+v] for n,v in pins.items()}
    def candidates(name,clauses):
        specs=SpecifierSet(','.join(c for c in clauses if c))
        # Direct pins avoid downloading a whole project index.
        if name in pins:return [pins[name]] if specs.contains(pins[name],prereleases=False) else []
        if name not in versions:
            obj=metadata.get(name);rows=obj.get('releases');require(isinstance(rows,dict) and len(rows)<=15000,'PYPI_RELEASE_LIMIT')
            vs=[]
            for v,files in rows.items():
                try:
                    parsed=Version(v)
                    if VER.fullmatch(v) and not parsed.is_prerelease and not parsed.is_devrelease and _eligible(files,name,v,env):vs.append((parsed,v))
                except InvalidVersion:continue
            versions[name]=[v for _,v in sorted(vs,reverse=True)]
        return [v for v in versions[name] if specs.contains(v,prereleases=False)][:40]
    def release(name,version):
        key=(name,version)
        if key not in releases:
            data=metadata.get(name,version);info=data.get('info');require(isinstance(info,dict) and canonicalize_name(info.get('name',''))==name and Version(info.get('version',''))==Version(version),'PYPI_RELEASE_IDENTITY_MISMATCH')
            rp=info.get('requires_python')
            if rp and not SpecifierSet(rp).contains(env['python_full_version'],prereleases=True):releases[key]=None
            else:
                wheel=_eligible(data.get('urls'),name,version,env)
                releases[key]=(wheel,_dependencies(info,env)) if wheel else None
        return releases[key]
    def visit(chosen,needed):
        nonlocal states
        states+=1;require(states<=MAX_STATES and len(needed)<=MAX_NODES,'PYPI_SOLVER_BUDGET')
        for n,row in chosen.items():
            if not SpecifierSet(','.join(c for c in needed[n] if c)).contains(row['version'],prereleases=False):return None
        todo=sorted(set(needed)-set(chosen),key=lambda n:(n not in pins,n))
        if not todo:return chosen
        n=todo[0]
        for v in candidates(n,needed[n]):
            release_data=release(n,v)
            if release_data is None:continue
            wheel,deps=release_data;new={k:list(val) for k,val in needed.items()}
            for k,s in deps:new.setdefault(k,[]).append(s)
            answer=visit({**chosen,n:wheel},new)
            if answer is not None:return answer
        return None
    rows=visit({},constraints);require(rows is not None,'PYPI_NO_COMPATIBLE_WHEEL_SOLUTION')
    require(sum(r['bytes'] for r in rows.values())<=MAX_TOTAL,'PYPI_TOTAL_WHEEL_LIMIT')
    obj={'schema_version':'8C.python.lock.1','target_python':python_version,'target_platform':'LINUX_X64_GLIBC_2_17_OR_NEWER','direct_pins':pins,'wheels':[rows[n] for n in sorted(rows)],'metadata_scope':'PYPI_RELEASE_METADATA_PENDING_ACTUAL_WHEEL_VERIFICATION'}
    obj['lock_sha256']=digest(obj);return obj


def fetch_wheels(lock,out,deadline):
    """Save only verified bytes in a wheelhouse, never open a wheel on host."""
    out=Path(out);require(not out.exists() and not out.is_symlink(),'OUTPUT_COLLISION');root=out.parent/'.wheel-staging';root.mkdir(exist_ok=False)
    total=0
    try:
        with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_STORED,allowZip64=False) as z:
            for row in lock['wheels']:
                path=file_url(row['url'],row['filename']);sha=hashlib.sha256();dest=root/row['filename']
                with dest.open('xb') as f:
                    def consume(b):sha.update(b);f.write(b)
                    size=_get('files.pythonhosted.org',path,min(row['bytes'],MAX_TOTAL-total),deadline,consume)
                require(size==row['bytes'] and sha.hexdigest()==row['sha256'],'PYPI_WHEEL_HASH_MISMATCH')
                total+=size;z.write(dest,'wheels/'+row['filename']);dest.unlink()
        require(out.stat().st_size<=120_000_000,'PYPI_WHEELHOUSE_LIMIT')
    except BaseException:
        out.unlink(missing_ok=True);raise
    finally:shutil.rmtree(root)
    return total

# Fixed diagnostic codes only; no metadata, URLs, paths or arbitrary exception text.
SAFE_ERRORS=frozenset(('PYPI_BUDGET_EXCEEDED', 'PYPI_DEADLINE', 'PYPI_DNS_NOT_PUBLIC', 'PYPI_ENCODING_DENIED', 'PYPI_FILE_URL_DENIED', 'PYPI_FILE_URL_INVALID', 'PYPI_HOST_DENIED', 'PYPI_HTTP_NOT_200', 'PYPI_MARKER_UNSUPPORTED', 'PYPI_METADATA_DEPENDENCY_LIMIT', 'PYPI_METADATA_PATH_DENIED', 'PYPI_METADATA_TOTAL_LIMIT', 'PYPI_NAME_INVALID', 'PYPI_NO_COMPATIBLE_WHEEL_SOLUTION', 'PYPI_QUERY_BUDGET', 'PYPI_RELEASE_FILE_LIMIT', 'PYPI_RELEASE_IDENTITY_MISMATCH', 'PYPI_RELEASE_LIMIT', 'PYPI_RELEASE_METADATA_PENDING_ACTUAL_WHEEL_VERIFICATION', 'PYPI_REQUIREMENT_INVALID', 'PYPI_SIZE_LIMIT', 'PYPI_SIZE_MISMATCH', 'PYPI_SOLVER_BUDGET', 'PYPI_TOTAL_WHEEL_LIMIT', 'PYPI_TRANSITIVE_URL_OR_EXTRAS_UNSUPPORTED', 'PYPI_WHEELHOUSE_LIMIT', 'PYPI_WHEEL_HASH_MISMATCH', 'PYTHON_DIRECT_PIN_LIMIT', 'PYTHON_EXACT_DIRECT_PINS_REQUIRED', 'PYTHON_PIN_INVALID_OR_DUPLICATE', 'PYTHON_REQUIREMENTS_LIMIT', 'PYTHON_TARGET_UNSUPPORTED'))
