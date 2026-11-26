"""Bounded ranged direct dependencies using the existing restricted PyPI broker.
The selected direct pins and transitive wheels are subsequently verified offline.
"""
import re
from packaging.specifiers import SpecifierSet
from packaging.version import Version,InvalidVersion
from packaging.utils import canonicalize_name
from ..phase8a.planner import digest
from ..phase8c2.broker import require
from ..phase8c_final.pypi import (target,NAME,VER,MAX_STATES,MAX_NODES,MAX_TOTAL,_eligible,_dependencies)

def resolve_requirements(requirements,python_version,metadata):
    """Limited deterministic backtracking; no source execution or pip on host."""
    env=target(python_version);states=0;versions={};releases={}
    require(type(requirements) is dict and 0<len(requirements)<=25,'PYTHON_DIRECT_PIN_LIMIT')
    for n,v in requirements.items():
        require(isinstance(n,str) and NAME.fullmatch(n) and isinstance(v,str) and len(v)<=500,'PYPI_REQUIREMENT_INVALID')
        SpecifierSet(v)
    constraints={n:[v] for n,v in requirements.items()}
    pins={n:v[2:] for n,v in requirements.items() if re.fullmatch(r'==[0-9][A-Za-z0-9.!+_-]{0,79}',v)}
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
        todo=sorted(set(needed)-set(chosen),key=lambda n:(n not in requirements,n))
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
    obj={'schema_version':'8C.python.lock.1','target_python':python_version,'target_platform':'LINUX_X64_GLIBC_2_17_OR_NEWER','direct_pins':{n:rows[n]['version'] for n in sorted(requirements)},'direct_requirements':requirements,'wheels':[rows[n] for n in sorted(rows)],'metadata_scope':'PYPI_RELEASE_METADATA_PENDING_ACTUAL_WHEEL_VERIFICATION'}
    obj['lock_sha256']=digest(obj);return obj

