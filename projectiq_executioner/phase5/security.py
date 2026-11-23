"""Read-only, privacy-preserving credential exposure *indicators*.

Never opens .env/private key files; reports only fixed codes, safe relative paths,
line numbers and confidence. Findings are NOT proof a credential is active.
"""
from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

from .source import read_bounded

# Exact tracked secret files are suspicious even if their values are unknown.
_STRONG_NAMES = {'id_rsa', 'id_ed25519', 'id_ecdsa', '.env', '.npmrc', '.pypirc',
                 'service-account.json', 'service_account.json',
                 'credentials.json', 'secrets.yaml', 'secrets.yml'}
_ENV_EXAMPLE = re.compile(r'(?i)\.(?:example|sample|template|dist)(?:\.|$)')
_PATTERN = [
    ('GITHUB_TOKEN_PATTERN', re.compile(r'\bgh(?:p|o|u|s|r)_[A-Za-z0-9_]{30,}\b')),
    ('AWS_ACCESS_KEY_PATTERN', re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('GOOGLE_API_KEY_PATTERN', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('STRIPE_SECRET_KEY_PATTERN', re.compile(r'\bsk_live_[0-9A-Za-z]{20,}\b')),
    ('SLACK_TOKEN_PATTERN', re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{20,}\b')),
    ('PRIVATE_KEY_HEADER', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
]
_ASSIGN = re.compile(
    r'''(?ix)(?:\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|db[_-]?password|database[_-]?password|private[_-]?key)\b)\s*[:=]\s*['\"]([^'\"\r\n]{20,180})['\"]'''
)
_PLACEHOLDER = re.compile(r'(?i)(?:example|sample|placeholder|dummy|changeme|your.?key|test.?key|xxxxx|<[^>]+>)')

def _entropy(value: str) -> float:
    from collections import Counter
    counts=Counter(value)
    return -sum((v/len(value))*math.log2(v/len(value)) for v in counts.values())

def secret_file_type(path: str) -> str | None:
    name=PurePosixPath(path).name.lower()
    if (name == '.env' or name.startswith('.env.')) and not _ENV_EXAMPLE.search(name):
        return 'TRACKED_ENV_FILE'
    if name in _STRONG_NAMES or name.endswith(('.pem', '.p12', '.pfx', '.jks')) or (
        name.endswith('.key') and not name.endswith('.pub')):
        return 'POTENTIAL_SECRET_FILE'
    if 'firebase-adminsdk' in name and name.endswith('.json'):
        return 'POTENTIAL_SECRET_FILE'
    return None

def _finding(code: str, path: str, line: int | None, confidence: str) -> dict:
    return {'code':code, 'path':path, 'line':line, 'confidence':confidence}

def scan_exposure(index, kept: list, skipped: dict) -> dict:
    """No contents of credential-named files read, ever; bounded source text only."""
    records=[]
    for path in sorted(index.files):
        code=secret_file_type(path)
        if code:
            records.append(_finding(code,path,None,'HIGH' if code=='TRACKED_ENV_FILE' else 'MEDIUM'))
    extra=[]
    seen={f.path for f in kept}
    extra_limit=False
    total_extra=0
    # Explicitly bounded, non-executable documentation/config scan. Never read
    # credential-named files, even if their contents appear UTF-8.
    for path in sorted(index.files):
        if path in seen or secret_file_type(path):continue
        if PurePosixPath(path).suffix.lower() not in {'.md','.txt','.json','.yaml','.yml','.toml','.ini','.xml'}:
            continue
        if index.files[path]>100_000:continue
        if len(extra)>=64 or total_extra+index.files[path]>1_000_000:
            extra_limit=True;break
        value,_,error=read_bounded(index,path)
        if error is not None:continue
        total_extra+=index.files[path]
        extra.append((path,value,False))
    files=[(f.path,f.text,True) for f in kept]+extra
    for path,contents,is_source in sorted(files,key=lambda x:x[0]):
        # Every readable source language can contain credentials, including
        # embedded JavaScript in HTML, CSS URLs and SQL inserts/dumps. The
        # source has already passed the read/byte budgets; this scanner only
        # emits fixed identifiers and locations, never matching source text.
        for line_no,line in enumerate(contents.splitlines(),1):
            if not line.strip():
                continue
            # Don't let unrelated words like "example" hide a genuine-looking
            # provider token elsewhere on the same source/documentation line.
            matches=[code for code,pattern in _PATTERN if pattern.search(line)]
            if not matches:
                assignment=_ASSIGN.search(line)
                if assignment:
                    literal=assignment.group(1)
                    # Placeholders are evaluated on the captured value only,
                    # not on unrelated comments, markup or filenames.
                    if not _PLACEHOLDER.search(literal) and \
                            len(set(literal)) >= 10 and _entropy(literal)>=3.5:
                        matches.append('HIGH_ENTROPY_SECRET_ASSIGNMENT')
            for code in sorted(set(matches)):
                records.append(_finding(code,path,line_no,
                                       'HIGH' if is_source and code!='HIGH_ENTROPY_SECRET_ASSIGNMENT' else 'MEDIUM'))
    # Bound and signal truncation. Do not return raw matches, hashes, excerpts or entropy.
    records.sort(key=lambda x:(x['path'],x['line'] or 0,x['code']))
    count=len(records)
    capped=count>200 or extra_limit
    records=records[:200]
    return {'status':'PARTIAL' if capped or skipped else ('INDICATORS_FOUND' if records else 'NO_INDICATORS_FOUND'),
            'findings':records,'scanned_source_files':len(kept),
            'scanned_additional_files':len(extra),
            'tracked_sensitive_files':sum(x['line'] is None for x in records),
            'truncated':capped,
            'scope':'BOUNDED_PATTERNS_NO_SECRET_VALUES_OR_VALIDITY_CHECK'}
