"""Fresh bounded source transfer, no checkout execution, no host extraction."""
import hashlib,zipfile,stat
from pathlib import Path
from ..phase8a.scanner import _linked,inspect
from ..phase8a.planner import digest
from ..phase8c2.broker import require
from ..phase8b.bridge import checked_file


def make_snapshot(root,out,inspection):
    root=Path(root);out=Path(out);scan=inspect(root)
    require(not scan.issues and digest(scan.digests)==inspection['inspected_content_sha256'] and digest(scan.paths)==inspection['inventory_sha256'],'SOURCE_CHANGED')
    # Filtered inventory excludes generated, secret-named and environment files.
    # The scope is recorded, never described as complete exact repository bytes.
    total=0
    try:
        with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=False) as z:
            for rel in scan.paths:
                p=root.joinpath(*rel.split('/'))
                require(not _linked(p) and not any(_linked(x) for x in p.parents if x!=root and x.is_relative_to(root)),'SOURCE_LINKED')
                st=p.stat();require(stat.S_ISREG(st.st_mode) and st.st_size<=20_000_000,'SOURCE_MEMBER_LIMIT')
                total+=st.st_size;require(total<=120_000_000,'SOURCE_TOTAL_LIMIT')
                data=p.read_bytes();require(len(data)==st.st_size and p.stat().st_mtime_ns==st.st_mtime_ns,'SOURCE_CHANGED')
                if rel in scan.digests:require(hashlib.sha256(data).hexdigest()==scan.digests[rel],'SOURCE_CHANGED')
                z.writestr('source/'+rel,data)
        require(out.stat().st_size<=30_000_000,'SOURCE_ARCHIVE_LIMIT')
    except BaseException:
        out.unlink(missing_ok=True);raise
    return {**checked_file(out,30_000_000),'scope':'FILTERED_8A_INVENTORY','file_count':len(scan.paths),'expanded_bytes':total}
