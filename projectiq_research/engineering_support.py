"""Conservative support-role evidence. No semantic claim is proven by this rule."""
from __future__ import annotations
import re

# Explicit engineering concerns, not paper titles or project-specific vocabulary.
# All three sides (independent report, verified source, linked citation claim)
# must express the same concern. Broad topic words like 'system' never qualify.
CONCERNS={
 'authorization':r'\b(?:access[ -]control|authori[sz]ation|authori[sz]e[ds]?|unauthori[sz]ed|role[ -]based|role[ -]guard|role[ -]checks?|administrator permissions?|wrong[ -]role|ownership checks?|per[ -]record ownership|object[ -]level|access permissions?)\b',
 'authentication':r'\b(?:authentication|password hash(?:ing)?|credential verification|invalid credentials|session cookie)\b',
 'database integrity':r'\b(?:database transactions?|transaction isolation|data consistency|referential integrity|database postconditions?)\b',
 'input/output safety':r'\b(?:input validation|injection prevention|output encoding|cross[ -]site scripting|sql injection)\b',
 'concurrency':r'\b(?:concurrency control|race conditions?|mutual exclusion)\b',
 'query optimization':r'\b(?:query optimi[sz]ation|database index(?:ing|es)?)\b',
 'reliability':r'\b(?:fault tolerance|failure recovery|distributed consensus)\b',
 'testing method':r'\b(?:unit testing|regression testing|property[ -]based testing)\b',
}
_MARKER=re.compile(r'\[\s*\d+(?:\s*[,;\-–]\s*\d+)*\s*\]|\b[A-Z][a-z]+(?:\s+et\s+al\.)?\s*\(\s*(?:19|20)\d{2}\s*\)')

def supporting_concerns(document,extraction,source_text,claims):
    if not claims or not source_text:return []
    lines=document.text.splitlines()
    if len(document.text)>1_000_000:return []  # bounded advisory policy
    # Stop at bibliography; exclude entire detected LR and any citation-bearing
    # sentences. If a detected section has unusable offsets, fail closed.
    excluded=[]
    for section in (extraction.references,extraction.literature_review):
        if section.found:
            a,b=section.start_line,section.end_line
            if type(a) is not int or type(b) is not int or not 1<=a<=b<=len(lines)+1:return []
            excluded.append((a,b))
    pieces=[];part=[]
    for i,line in enumerate(lines,1):
        if any(a<=i<=b for a,b in excluded):
            if part:pieces.append(' '.join(part));part=[]
        else:part.append(line)
    if part:pieces.append(' '.join(part))
    independent=' '.join(sentence for p in pieces for sentence in re.split(r'(?<=[.!?])\s+',p) if not _MARKER.search(sentence))
    linked=' '.join(claims)
    return [name for name,pattern in CONCERNS.items() if all(re.search(pattern,text,re.I) for text in (independent,source_text,linked))]
