"""Static classification of production and test source, independent of project name."""
from __future__ import annotations
import re

def file_scope(path:str)->str:
    p=path.lower().replace('\\','/')
    bits=p.split('/')
    name=bits[-1]
    if any(b in {'test','tests','__tests__','spec','specs','fixtures','cypress','e2e'} for b in bits[:-1]):
        return 'TEST'
    if re.match(r'test_[^/]+\.py$',name) or re.search(r'(?:_test\.py|\.(?:test|spec)\.[cm]?[jt]sx?|_test\.go)$',name):
        return 'TEST'
    return 'PRODUCTION'
