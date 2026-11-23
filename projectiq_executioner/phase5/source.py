"""Conservative source selection on top of Phase 1's read-only SourceIndex."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from ..indexer import SourceIndex
from .contracts import (LANGUAGES, MAX_ANALYZED_FILES,
                        MAX_TEXT_BYTES, MAX_TOTAL_BYTES)

# Known generated/minified sources are not evaluated as student-authored code.
_GENERATED_NAMES = frozenset({'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml'})
_GENERATED_MARKERS = ('/generated/', '/gen/', '/.generated/', '/__generated__/',
                      '/minified/', '/dist/', '/vendor/', '/node_modules/')


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str
    text: str
    sha256: str


def eligible(path: str) -> bool:
    name = path.rsplit('/',1)[-1].lower()
    lower = '/' + path.lower()
    return (Path(name).suffix.lower() in LANGUAGES
            and not (name == '.env' or name.startswith('.env.') or name in {'id_rsa','id_ed25519'})
            and name not in _GENERATED_NAMES and not name.endswith(('.min.js','.min.css',
            '.generated.js','.generated.ts','.g.cs','.designer.cs'))
            and not any(x in lower for x in _GENERATED_MARKERS))


def read_bounded(index: SourceIndex, path: str) -> tuple[str | None,str | None,str | None]:
    """Bounded file-descriptor reads; never imports or executes student code.

    SourceIndex supplies the Phase 1 inventory. Parent-path symlink races on
    Windows remain outside the guarantees of this static local scanner.
    """
    if path not in index.files:
        return None,None,'UNREADABLE'
    if index.files[path]>MAX_TEXT_BYTES:
        return None,None,'FILE_TOO_LARGE'
    current=index.root
    for bit in path.split('/'):
        current=current / bit
        if current.is_symlink():
            return None,None,'SYMLINK_OR_SPECIAL'
    try:
        if not current.resolve().is_relative_to(index.root.resolve()):
            return None,None,'SYMLINK_OR_SPECIAL'
        st=current.stat()
        if not stat.S_ISREG(st.st_mode):
            return None,None,'SYMLINK_OR_SPECIAL'
        if st.st_size>MAX_TEXT_BYTES:
            return None,None,'FILE_TOO_LARGE'
        flags=os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0)
        fd=os.open(current,flags)
        with os.fdopen(fd,'rb') as handle:
            fd_stat=os.fstat(handle.fileno())
            if not stat.S_ISREG(fd_stat.st_mode):
                return None,None,'SYMLINK_OR_SPECIAL'
            if fd_stat.st_size>MAX_TEXT_BYTES:
                return None,None,'FILE_TOO_LARGE'
            data=handle.read(MAX_TEXT_BYTES+1)
        if len(data)>MAX_TEXT_BYTES:return None,None,'FILE_TOO_LARGE'
        if path.lower().endswith('.sql') and data.startswith((b'\xff\xfe',b'\xfe\xff')):
            # Some SQL exports use BOM-marked UTF-16. Accept only an explicit
            # BOM and strict decoding, not a guess or lossy replacement.
            content=data.decode('utf-16',errors='strict')
        else:
            if b'\x00' in data[:8192]:return None,None,'NON_UTF8'
            content=data.decode('utf-8-sig',errors='strict')
    except UnicodeDecodeError:
        return None,None,'NON_UTF8'
    except (OSError,ValueError,RuntimeError):
        return None,None,'UNREADABLE'
    # Preserve original bytes in the hash but normalize line breaks for locations.
    # CR-only and CRLF files must have the same line numbering as LF files.
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    return content,hashlib.sha256(data).hexdigest(),None


def collect(index: SourceIndex):
    selected=sorted(p for p in index.files if eligible(p))
    kept=[]; skipped={}; total=0
    for i,p in enumerate(selected):
        if i>=MAX_ANALYZED_FILES:
            skipped[p]='FILE_LIMIT';continue
        if index.files[p]>MAX_TEXT_BYTES:
            skipped[p]='FILE_TOO_LARGE';continue
        if total+index.files[p]>MAX_TOTAL_BYTES:
            skipped[p]='TOTAL_BYTE_LIMIT';continue
        text,sha,why=read_bounded(index,p)
        if why:
            skipped[p]=why;continue
        total+=index.files[p]
        kept.append(SourceFile(p,LANGUAGES[Path(p).suffix.lower()],text,sha))
    return selected,kept,skipped
