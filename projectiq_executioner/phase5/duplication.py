"""Conservative exact line-sequence duplication, never raw source disclosure."""
from __future__ import annotations

import hashlib

from .lexical import mask_cstyle
from .source import SourceFile

MIN_LINES=8
MIN_CHARS=145


def _sequence(file:SourceFile):
    if file.language=='Python':
        # Keep Python indentation because it affects semantics.
        rows=file.text.splitlines()
        normalized=[(' '.join(x.strip().split())) if x.strip() and not x.lstrip().startswith('#')
                    else '' for x in rows]
    else:
        masked,_=mask_cstyle(file.text,php=(file.language=='PHP'))
        normalized=[' '.join(row.strip().split()) for row in masked.splitlines()]
    return [(i,s) for i,s in enumerate(normalized,1) if s]


def find_duplications(files:list[SourceFile],max_groups:int=50, *, with_truncation:bool=False):
    seen={};groups=[];duplicates=set()
    for file in sorted(files,key=lambda x:x.path):
        if file.language not in {'Python','JavaScript','TypeScript','PHP','C','C++','C/C++'}:
            continue
        lines=_sequence(file)
        for k in range(0,max(0,len(lines)-MIN_LINES+1)):
            seq=lines[k:k+MIN_LINES]
            if len(seq)!=MIN_LINES or sum(len(x[1]) for x in seq)<MIN_CHARS:
                continue
            # Skip windows with huge gaps: sparse identical lines not true blocks.
            if seq[-1][0]-seq[0][0]>MIN_LINES+3:continue
            digest=hashlib.sha256('\n'.join(x[1] for x in seq).encode('utf8')).digest()
            previous=seen.get(digest)
            if previous is None:
                seen[digest]=(file.path,seq[0][0]);continue
            other,line=previous
            if other==file.path:continue  # Cross-file clones only in this MVP.
            key=(other,file.path,line,seq[0][0])
            # Overlapping windows from same clone region are collapsed.
            bucket=(other,file.path,line-seq[0][0])
            if bucket in duplicates:continue
            duplicates.add(bucket)
            groups.append({'path_a':other,'line_a':line,'path_b':file.path,
                           'line_b':seq[0][0],'lines':MIN_LINES,'confidence':'MEDIUM'})
            if len(groups)>max_groups:
                result=sorted(groups[:max_groups],key=lambda x:(x['path_a'],x['line_a'],x['path_b'],x['line_b']))
                return (result,True) if with_truncation else result
    result=sorted(groups,key=lambda x:(x['path_a'],x['line_a'],x['path_b'],x['line_b']))
    return (result,False) if with_truncation else result
