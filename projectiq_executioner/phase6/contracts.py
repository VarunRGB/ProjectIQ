"""Bounded, strict Phase 6 inputs/outputs. Never export arbitrary raw logs or exceptions."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

VERSION = '0.1'
MAX_JSON_BYTES = 8_000_000
MAX_TOTAL_INPUT_BYTES = 24_000_000
MAX_RUNS = 24
MAX_EVENTS = 2000
MAX_DIAGNOSES = 2000
MAX_TESTS = 400
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
EXEC = 'PERFORMED_IN_DISPOSABLE_VM'
NOT_ATTESTED = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,89}$')
SHA = re.compile(r'^[0-9a-f]{40}$')
REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
LOCAL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_. -]{0,159}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
SAFE_PATH = re.compile(r'^[A-Za-z0-9_./-]{1,230}$')

class EvidenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def fail(code: str) -> None:
    raise EvidenceError(code)


def read_json(path: Path) -> tuple[dict, str, int]:
    """Read bounded JSON; reject duplicate keys, symlinks, special files and non-finite numbers."""
    p = Path(path)
    if p.is_symlink() or any(parent.is_symlink() for parent in p.parents):
        fail('UNSAFE_EVIDENCE_PATH')
    try:
        if not p.is_file():
            fail('EVIDENCE_FILE_NOT_REGULAR')
        n = p.stat().st_size
        if not 0 < n <= MAX_JSON_BYTES:
            fail('EVIDENCE_FILE_SIZE_LIMIT')
        b = p.read_bytes()
        if len(b) != n:
            fail('EVIDENCE_FILE_CHANGED')
        def pairs(items):
            d = {}
            for k, v in items:
                if k in d:
                    fail('DUPLICATE_JSON_KEY')
                d[k] = v
            return d
        result = json.loads(b.decode('utf-8-sig'), object_pairs_hook=pairs,
                            parse_constant=lambda _: fail('NONFINITE_JSON_NUMBER'))
    except EvidenceError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        fail('INVALID_EVIDENCE_JSON')
    if type(result) is not dict:
        fail('INVALID_EVIDENCE_ROOT')
    return result, hashlib.sha256(b).hexdigest(), n


def source_key(source: Any) -> tuple:
    if type(source) is not dict:
        fail('MISSING_SOURCE_IDENTITY')
    kind, repo, sha = source.get('kind'), source.get('repository'), source.get('commit_sha')
    if type(repo) is not str or not 1 <= len(repo) <= 240:
        fail('INVALID_SOURCE_IDENTITY')
    if kind == 'github':
        if not REPO.fullmatch(repo) or '..' in repo.split('/'):
            fail('INVALID_SOURCE_IDENTITY')
        if type(sha) is not str or not SHA.fullmatch(sha):
            fail('UNPINNED_GITHUB_SOURCE')
        return kind, repo, sha
    if kind == 'local':
        if not LOCAL.fullmatch(repo):
            fail('INVALID_SOURCE_IDENTITY')
        if sha is not None:
            fail('UNVERIFIED_LOCAL_SOURCE_SHA')
        return kind, repo, None
    fail('UNSUPPORTED_SOURCE_KIND')


def safe_id(value: object) -> str | None:
    return value if type(value) is str and ID.fullmatch(value) else None


def safe_path(value: object) -> str | None:
    return value if type(value) is str and SAFE_PATH.fullmatch(value) and '..' not in value.split('/') and not value.startswith('/') else None


def stable_hash(value: Any) -> str:
    try:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=True,
                          allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        fail('NONCANONICAL_EVIDENCE')
    return hashlib.sha256(blob).hexdigest()


def write_json_atomic(data: dict, path: Path) -> None:
    dest = Path(path)
    if dest.is_symlink() or any(p.is_symlink() for p in dest.parents):
        fail('UNSAFE_OUTPUT_PATH')
    if dest.exists():
        fail('OUTPUT_ALREADY_EXISTS')
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create means an existing report is never silently destroyed.
    with dest.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        f.write('\n')
