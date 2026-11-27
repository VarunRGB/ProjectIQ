"""Operator-owned asset references for local demo; never grants trust."""
from pathlib import Path
import re
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c2.broker import require
from ..phase8c_final.dispatch import write

NAME='phase8f_demo_assets.local.json'
SCHEMA='8F-D.assets.1'


def _asset(value, name, limit):
    require(isinstance(value,dict) and set(value)=={'path','sha256'},'DEMO_ASSET_PROFILE_INVALID')
    p=value['path'];h=value['sha256']
    require(type(p) is str and Path(p).is_absolute() and Path(p).name==name and type(h) is str and re.fullmatch('[0-9a-f]{64}',h),'DEMO_ASSET_PROFILE_INVALID')
    require(checked_file(p,limit)['sha256']==h,'DEMO_ASSET_HASH_MISMATCH')
    return Path(p),h


def load_profile(path):
    path=Path(path).absolute();before=checked_file(path,16_384)
    obj=load_object(path.read_text('utf-8'))
    require(set(obj)=={'schema_version','node','pip'} and obj['schema_version']==SCHEMA,'DEMO_ASSET_PROFILE_INVALID')
    n,ns=_asset(obj['node'],'node-v24.22.3-linux-x64.tar.xz',65_000_000)
    p,ps=_asset(obj['pip'],'pip-25.2-py3-none-any.whl',5_000_000)
    require(checked_file(path,16_384)==before,'DEMO_ASSET_PROFILE_CHANGED')
    return {'node_tarball':n,'node_sha256':ns,'pip_wheel':p,'pip_sha256':ps},before


def configure(path,*,node_tarball,node_sha256,pip_wheel,pip_sha256):
    path=Path(path).absolute()
    require(not path.exists() and not path.is_symlink(),'DEMO_ASSET_PROFILE_ALREADY_EXISTS')
    # Never overwrite policy or another file. Parent must already exist and be unlinked.
    from ..phase8d.dispatch import _linked
    require(path.name==NAME and path.parent.is_dir() and not any(_linked(p) for p in path.parents),'DEMO_ASSET_PROFILE_PATH_INVALID')
    obj={'schema_version':SCHEMA,'node':{'path':str(Path(node_tarball).absolute()),'sha256':node_sha256},'pip':{'path':str(Path(pip_wheel).absolute()),'sha256':pip_sha256}}
    _asset(obj['node'],'node-v24.22.3-linux-x64.tar.xz',65_000_000);_asset(obj['pip'],'pip-25.2-py3-none-any.whl',5_000_000)
    write(path,obj)
    return checked_file(path,16_384)
