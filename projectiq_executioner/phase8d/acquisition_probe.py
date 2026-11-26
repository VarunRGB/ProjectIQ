"""Operator-only, byte acquisition diagnostic for the two Phase 8E fixtures.
No checkout, VM, package installation, application execution or approval change.
"""
import argparse,json,tempfile
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8c_final.dispatch import write
from ..phase8c_final.pypi import SAFE_ERRORS,diagnostic_error
from .dispatch import fresh,_python_fetch
from .assets import copy_verified_python

CASES={'fixture-asgi':{'fastapi':'<1,>=0.115','uvicorn':'<1,>=0.30'},'fixture-django':{'django':'<6,>=5.1'}}
VERIFY_CODES={'PYTHON_LOCK_MISMATCH','PYTHON_LOCK_REQUIREMENT_MISMATCH','PYTHON_WHEELHOUSE_MEMBER_MISMATCH','PYTHON_WHEELHOUSE_HASH_MISMATCH','PYTHON_WHEELHOUSE_CHANGED','HOST_DISK_HEADROOM_INSUFFICIENT','REGISTRY_RESULT_LIMIT','REGISTRY_ACQUISITION_FAILED','ARTIFACT_SIZE_OR_TYPE','ARTIFACT_SIZE_LIMIT','LINKED_ARTIFACT','ARTIFACT_CHANGED'}

def probe(out,cases):
    out=fresh(out);rows=[]
    for case in cases:
        dest=out/case;dest.mkdir();requirements=CASES[case];stage='ACQUISITION'
        row={'case':case,'requirements':requirements,'target_python':'3.12.3','vm_start_attempts':0,'runtime_permission':False}
        try:
            bundle,lock,stats=_python_fetch(requirements,dest,'3.12.3')
            stage='VERIFY'
            # Reuse the existing independent member/hash verification path; the
            # temporary copy is removed and no package is installed or imported.
            with tempfile.TemporaryDirectory(prefix='verified-',dir=dest) as tmp:
                copy_verified_python(dest,Path(tmp),requirements,'3.12.3')
            row.update(status='WHEELHOUSE_VERIFIED_NOT_EXECUTED',wheel_count=len(lock['wheels']),wheel_bytes=sum(w['bytes'] for w in lock['wheels']),lock_sha256=lock['lock_sha256'])
        except Exception as exc:
            code=str(exc) if type(exc) is ValueError and str(exc) in (SAFE_ERRORS|VERIFY_CODES) else ('PYPI_PROBE_VERIFICATION_FAILED' if stage=='VERIFY' else diagnostic_error(exc,'RESOLUTION'))
            row.update(status='BLOCKED',stage=stage,error_code=code)
        row['result_sha256']=digest(row);write(dest/'acquisition_result.json',row);rows.append(row)
        print(case+': '+row['status']+(' '+row['error_code'] if 'error_code' in row else ''),flush=True)
    result={'schema_version':'8E.acquisition-probe.1','vm_start_attempts':0,'automatic_untrusted_execution':False,'cases':rows,'status':'ACQUISITION_VERIFIED' if all(r['status']=='WHEELHOUSE_VERIFIED_NOT_EXECUTED' for r in rows) else 'ACQUISITION_BLOCKED'}
    result['result_sha256']=digest(result);write(out/'probe_result.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--case',choices=tuple(CASES),action='append');args=p.parse_args(argv)
    cases=args.case or list(CASES)
    if len(set(cases))!=len(cases):p.error('duplicate cases')
    result=probe(args.out,cases);return 0 if result['status']=='ACQUISITION_VERIFIED' else 2

if __name__=='__main__':raise SystemExit(main())
