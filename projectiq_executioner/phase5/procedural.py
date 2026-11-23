"""Conservative *advisory* control-flow marker inventory for procedural PHP.

No claim of complete parsing or cyclomatic complexity: embedded HTML,
PHP alternative syntax and dynamic constructs make regex-only counts partial.
"""
from __future__ import annotations

import re
from .lexical import mask_cstyle

_DECISION = re.compile(r'\b(?:if|elseif|switch|case|catch|for|foreach|while)\b')
_LOOP = re.compile(r'\b(?:for|foreach|while|do)\b')

def procedural_markers(text:str,language:str,has_named_functions:bool)->tuple[int,int]:
    if language!='PHP' or has_named_functions:return 0,0
    masked,_=mask_cstyle(text,php=True)
    return len(_DECISION.findall(masked)),len(_LOOP.findall(masked))
