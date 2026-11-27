"""Validate/copy prior inert Python wheelhouse before reusing it. No wheel parsing on host."""
import hashlib,stat,zipfile,shutil,re
from pathlib import Path
from packaging.specifiers import SpecifierSet
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require
from ..phase8c_final.pypi import wheel_rank,NAME,SHA,VER,file_url,MAX_TOTAL,MAX_WHEEL


def validate_lock(lock,requirements,version):
    require(type(lock) is dict and lock.get('lock_sha256')==digest({k:v for k,v in lock.items() if k!='lock_sha256'}) and lock.get('target_python')==version,'PYTHON_LOCK_MISMATCH')
    pins=lock.get('direct_pins');rows=lock.get('wheels')
    require(type(pins) is dict and set(pins)==set(requirements) and type(rows) is list and 1<=len(rows)<=100,'PYTHON_LOCK_MISMATCH')
    require(all(isinstance(pins[n],str) and SpecifierSet(s).contains(pins[n],prereleases=False) for n,s in requirements.items()),'PYTHON_LOCK_REQUIREMENT_MISMATCH')
    if 'direct_requirements' in lock:require(lock['direct_requirements']==requirements,'PYTHON_LOCK_REQUIREMENT_MISMATCH')
    found={};names=set();total=0
    for row in rows:
        require(type(row) is dict,'PYTHON_LOCK_MISMATCH');n=row.get('name');v=row.get('version');f=row.get('filename');sha=row.get('sha256');size=row.get('bytes')
        require(isinstance(n,str) and NAME.fullmatch(n) and n not in found and isinstance(v,str) and VER.fullmatch(v) and isinstance(f,str) and f not in names and wheel_rank(f,n,v) is not None and isinstance(sha,str) and SHA.fullmatch(sha) and type(size) is int and 0<size<=MAX_WHEEL,'PYTHON_LOCK_MISMATCH')
        file_url(row.get('url'),f);found[n]=v;names.add(f);total+=size
    require(total<=MAX_TOTAL and all(found.get(n)==v for n,v in pins.items()),'PYTHON_LOCK_MISMATCH')


def copy_verified_python(directory,out,requirements,version):
    directory=Path(directory);out=Path(out);lp=directory/'python-lock.json';bundle=directory/'python-wheelhouse.zip'
    checked_file(lp,1_000_000);lock=load_object(lp.read_text('utf-8'));validate_lock(lock,requirements,version)
    before=checked_file(bundle,120_000_000);expected={'wheels/'+r['filename']:r for r in lock['wheels']}
    with zipfile.ZipFile(bundle) as z:
        members=z.infolist();names=[e.filename for e in members]
        require(len(names)==len(expected) and set(names)==set(expected),'PYTHON_WHEELHOUSE_MEMBER_MISMATCH')
        for e in members:
            row=expected[e.filename];require(e.compress_type==zipfile.ZIP_STORED and not e.is_dir() and not e.flag_bits&1 and stat.S_IFMT(e.external_attr>>16) in (0,stat.S_IFREG) and e.file_size==row['bytes'],'PYTHON_WHEELHOUSE_MEMBER_MISMATCH')
            with z.open(e) as f:require(hashlib.file_digest(f,'sha256').hexdigest()==row['sha256'],'PYTHON_WHEELHOUSE_HASH_MISMATCH')
    require(checked_file(bundle,120_000_000)==before,'PYTHON_WHEELHOUSE_CHANGED')
    dest=out/'python-wheelhouse.zip';created=False
    try:
        with bundle.open('rb') as a,dest.open('xb') as b:created=True;shutil.copyfileobj(a,b,1024*1024)
        require(checked_file(dest,120_000_000)==before,'PYTHON_WHEELHOUSE_CHANGED')
    except BaseException:
        if created:dest.unlink(missing_ok=True)
        raise
    return dest,lock,{'acquisition':'REUSED_HASH_VERIFIED_WHEELHOUSE','wheel_count':len(expected),'new_registry_downloads':0}
