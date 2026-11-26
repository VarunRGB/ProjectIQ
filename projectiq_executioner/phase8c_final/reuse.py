"""Verify a previously acquired npm bundle against freshly compiled manifest inputs.
Only the outer inert ZIP is read; package tarballs are hashed, never extracted.
"""
import hashlib, stat, zipfile, shutil
from pathlib import Path
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require,MAX_FILE,MAX_TOTAL


def copy_verified_npm(bundle,dest,inputs):
    bundle=Path(bundle);dest=Path(dest);before=checked_file(bundle,145_000_000)
    jobs={f"inputs/tarballs/{x['filename']}":x for x in inputs['downloads']};expected=set(jobs)|{'inputs/package.json','inputs/package-lock.json'}
    require(not dest.exists() and not dest.is_symlink(),'OUTPUT_COLLISION')
    count=0;total=0;created=False
    try:
        with zipfile.ZipFile(bundle) as z:
            members=z.infolist();names=[e.filename for e in members]
            require(len(names)==len(expected) and set(names)==expected,'NPM_BUNDLE_MEMBER_SET_MISMATCH')
            for e in members:
                require(e.compress_type==zipfile.ZIP_STORED and not e.is_dir() and not e.flag_bits&1 and stat.S_IFMT(e.external_attr>>16) in (0,stat.S_IFREG),'NPM_BUNDLE_MEMBER_UNSAFE')
                if e.filename in jobs:
                    require(0<e.file_size<=MAX_FILE and total+e.file_size<=MAX_TOTAL,'NPM_BUNDLE_LIMIT');h=hashlib.sha512();size=0
                    with z.open(e) as f:
                        while block:=f.read(65536):
                            size+=len(block);require(size<=e.file_size,'NPM_BUNDLE_LIMIT');h.update(block)
                    require(size==e.file_size and h.hexdigest()==jobs[e.filename]['sha512'],'NPM_BUNDLE_INTEGRITY_MISMATCH');count+=1;total+=size
                else:
                    require(0<e.file_size<=4_000_000,'NPM_BUNDLE_LIMIT')
                    key='manifest' if e.filename.endswith('/package.json') else 'lock'
                    require(load_object(z.read(e).decode('utf-8'))==inputs[key],'NPM_BUNDLE_MANIFEST_MISMATCH')
        require(checked_file(bundle,145_000_000)==before,'NPM_BUNDLE_CHANGED')
        with bundle.open('rb') as src,dest.open('xb') as dst:
            created=True;shutil.copyfileobj(src,dst,1024*1024)
        require(checked_file(dest,145_000_000)==before,'NPM_BUNDLE_COPY_MISMATCH')
    except BaseException:
        # Caller supplies a fresh output directory; do not overwrite a prior artifact.
        if created and dest.exists() and not dest.is_symlink():dest.unlink()
        raise
    return {'download_count':count,'package_bytes':total,'platform_skipped':inputs['platform_skipped'],'acquisition':'REUSED_EXACT_MANIFEST_AND_SHA512_VERIFIED','new_registry_downloads':0}
