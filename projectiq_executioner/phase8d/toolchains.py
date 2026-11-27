"""Operator-owned provisioned Node catalog. No runtime downloads on host."""
import re
from pathlib import Path
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require


def load_node_catalog(path):
    path=Path(path).absolute();before=checked_file(path,64_000);obj=load_object(path.read_text('utf-8'))
    require(set(obj)=={'runtimes'} and isinstance(obj['runtimes'],list) and 1<=len(obj['runtimes'])<=8,'NODE_CATALOG_INVALID')
    rows={}
    for r in obj['runtimes']:
        require(isinstance(r,dict) and set(r)=={'version','tarball','sha256'} and isinstance(r['version'],str) and re.fullmatch(r'v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',r['version']) and r['version'] not in rows and isinstance(r['sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',r['sha256']) and isinstance(r['tarball'],str),'NODE_CATALOG_INVALID')
        p=Path(r['tarball']).expanduser();p=p if p.is_absolute() else path.parent/p
        require(p.name==f"node-{r['version']}-linux-x64.tar.xz" and checked_file(p,65_000_000)['sha256']==r['sha256'],'NODE_ASSET_MISMATCH');rows[r['version']]=(p.absolute(),r['sha256'])
    require(checked_file(path,64_000)==before,'NODE_CATALOG_CHANGED');return rows
