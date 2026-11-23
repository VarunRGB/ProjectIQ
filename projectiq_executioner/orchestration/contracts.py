"""Versioned, bounded, non-authorizing Executioner orchestration contracts.

Requests describe the work to attempt. They are NEVER an execution approval.
Only the operator-owned VM configuration, reviewed catalogs and downstream
Phase 2/4 gates can authorize a particular run.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

VERSION = '0.1'
ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'
CLEAN = 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'
EXECUTED = 'PERFORMED_IN_DISPOSABLE_VM'
_BACKEND = 'QEMU_DISPOSABLE_QCOW2_VM'
_SHA = re.compile(r'^[0-9a-f]{40}$')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
_REPO = re.compile(r'^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$')
_REF = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_./-]{0,150}$')
_PATH = re.compile(r'^[A-Za-z0-9_./-]{1,230}$')
MODES = frozenset({'STATIC', 'REVIEWED', 'RESUME'})
SUITE_TYPES = frozenset({'readiness','api','auth','db_fixture','db_postconditions',
                         'scenario_isolation','dependent_workflow','crud','browser_ui','browser_login'})
SUITE_CATALOGS = {
    'readiness': (),
    'api': ('oracle',),
    'auth': ('auth','oracle'),
    'db_fixture': ('fixture',),
    'db_postconditions': ('auth','oracle','postconditions'),
    'scenario_isolation': ('auth','oracle','postconditions','isolation'),
    'dependent_workflow': ('fixture','workflow'),
    'crud': ('auth','oracle','crud'),
    'browser_ui': ('browser',),
    'browser_login': ('login',),
}
VM_COST = {x: (2 if x == 'scenario_isolation' else 1) for x in SUITE_TYPES}
RUN_STATUSES = {
    'readiness': {'TESTS_PASSED','TESTS_FAILED','TESTS_INCONCLUSIVE','NO_RUNNABLE_TESTS','BLOCKED',
                  'VM_EXECUTION_NOT_VERIFIED','GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'},
    'api': {'TESTS_PASSED','TESTS_FAILED','TESTS_INCONCLUSIVE','BLOCKED','VM_EXECUTION_NOT_VERIFIED',
            'GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'},
    'auth': {'TESTS_PASSED','TESTS_FAILED','TESTS_INCONCLUSIVE','BLOCKED','VM_EXECUTION_NOT_VERIFIED',
             'GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'},
    'db_fixture': {'FIXTURES_SEEDED','FIXTURES_FAILED','VM_EXECUTION_NOT_VERIFIED',
                    'GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'},
    'db_postconditions': {'POSTCONDITIONS_PASSED','POSTCONDITIONS_FAILED','POSTCONDITIONS_INCONCLUSIVE',
                          'VM_EXECUTION_NOT_VERIFIED','GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'},
    'scenario_isolation': {'SCENARIO_ISOLATION_VERIFIED','ISOLATION_FAILED','ISOLATION_NOT_VERIFIED','CLEANUP_FAILED'},
    'dependent_workflow': {'WORKFLOW_PASSED','WORKFLOW_FAILED','WORKFLOW_INCONCLUSIVE',
                           'WORKFLOW_NOT_VERIFIED','WORKFLOW_VM_NOT_VERIFIED','CLEANUP_FAILED'},
    'crud': {'CRUD_PASSED','CRUD_FAILED','CRUD_INCONCLUSIVE','CRUD_NOT_VERIFIED','CRUD_VM_NOT_VERIFIED','CLEANUP_FAILED'},
    'browser_ui': {'BROWSER_PASSED','BROWSER_FAILED','BROWSER_INCONCLUSIVE',
                   'BROWSER_NOT_VERIFIED','BROWSER_VM_NOT_VERIFIED','CLEANUP_FAILED'},
    'browser_login': {'LOGIN_PASSED','LOGIN_FAILED','LOGIN_INCONCLUSIVE','LOGIN_VM_NOT_VERIFIED','CLEANUP_FAILED'},
}

class OrchestrationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def reject(code: str) -> None:
    raise OrchestrationError(code)


def canonical_hash(data: Any) -> str:
    try:
        raw = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                         allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError):
        reject('NON_CANONICAL_DATA')
    return hashlib.sha256(raw).hexdigest()


def safe_output_path(value: str) -> bool:
    return (type(value) is str and _PATH.fullmatch(value) is not None and
            not value.startswith('/') and '..' not in value.split('/') and
            all(part not in ('','.','..') for part in value.split('/')))


def no_symlinks(path: Path) -> bool:
    p = Path(path)
    return not p.is_symlink() and not any(x.is_symlink() for x in p.parents)


def read_json(path: Path, *, limit: int = 8_000_000) -> dict:
    """Fail closed for ambiguous JSON, directory links and oversized input."""
    p = Path(path)
    if not no_symlinks(p):
        reject('UNSAFE_INPUT_PATH')
    try:
        if not p.is_file():
            reject('MISSING_INPUT_FILE')
        if not 0 < p.stat().st_size <= limit:
            reject('INPUT_SIZE_LIMIT')
        raw = p.read_bytes()
        if not 0 < len(raw) <= limit:
            reject('INPUT_SIZE_LIMIT')
        def pairs(items):
            result = {}
            for k, v in items:
                if k in result:
                    reject('DUPLICATE_JSON_KEY')
                result[k] = v
            return result
        result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                            parse_constant=lambda _: reject('NONFINITE_JSON_NUMBER'))
    except OrchestrationError:
        raise
    except (UnicodeError, OSError, ValueError, RecursionError):
        reject('INVALID_INPUT_JSON')
    if type(result) is not dict:
        reject('INVALID_INPUT_ROOT')
    return result


def write_json(path: Path, data: dict) -> None:
    """Exclusive evidence creation; never overwrite or follow a symlink."""
    p = Path(path)
    if not no_symlinks(p):
        reject('UNSAFE_OUTPUT_PATH')
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False)
        f.write('\n')


def validate_source(source: Any) -> dict:
    if type(source) is not dict:
        reject('INVALID_SOURCE')
    repo, sha = source.get('repository'), source.get('commit_sha')
    kind = source.get('kind')
    if (kind not in ('github','github_pinned') or type(repo) is not str
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or type(sha) is not str or not _SHA.fullmatch(sha)):
        reject('SOURCE_NOT_PINNED')
    return {'kind':'github','repository':repo,'commit_sha':sha}


def validate_request(value: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping) or set(value) - {
        'submission_id','repository_url','ref','mode','resume_target','resume_from','suites',
        'run_readiness','max_vm_launches'}:
        reject('INVALID_REQUEST_FIELDS')
    v = dict(value)
    if type(v.get('submission_id')) is not str or not _ID.fullmatch(v['submission_id']):
        reject('INVALID_SUBMISSION_ID')
    url = v.get('repository_url')
    if type(url) is not str or not _REPO.fullmatch(url) or '..' in url:
        reject('INVALID_GITHUB_URL')
    if type(v.get('mode')) is not str or v['mode'] not in MODES:
        reject('INVALID_MODE')
    ref = v.get('ref')
    if ref is not None and (type(ref) is not str or not _REF.fullmatch(ref)
                            or '..' in ref.split('/') or ref.startswith('/') or ref.endswith('/')):
        reject('INVALID_REF')
    if v['mode'] == 'RESUME':
        if v.get('resume_target') not in ('STATIC','REVIEWED'):
            reject('INVALID_RESUME_TARGET')
        if type(v.get('resume_from')) is not str or not v['resume_from']:
            reject('MISSING_RESUME_RESULT')
    elif 'resume_from' in v or 'resume_target' in v:
        reject('UNEXPECTED_RESUME_FIELDS')
    if v.get('run_readiness','AUTO') not in ('AUTO','YES','NO'):
        reject('INVALID_READINESS_POLICY')
    count = v.get('max_vm_launches', 4)
    if type(count) is not int or not 0 <= count <= 12:
        reject('INVALID_VM_BUDGET')
    suites = v.get('suites', [])
    if type(suites) is not list or len(suites) > 10:
        reject('INVALID_SUITE_LIST')
    normalized=[];seen=set()
    for suite in suites:
        if type(suite) is not dict or set(suite) != {'kind','catalogs'}:
            reject('INVALID_SUITE_FIELDS')
        kind = suite.get('kind')
        if kind not in SUITE_TYPES or kind in seen or kind == 'readiness':
            reject('INVALID_SUITE_KIND')
        seen.add(kind)
        catalogs = suite.get('catalogs')
        expected = SUITE_CATALOGS[kind]
        if type(catalogs) is not dict or set(catalogs) != set(expected) or any(
                not safe_output_path(catalogs[k]) for k in expected):
            reject('INVALID_SUITE_CATALOG_PATHS')
        normalized.append({'kind':kind,'catalogs':dict(catalogs)})
    if v['mode'] == 'STATIC' and suites:
        # An accidentally supplied reviewed suite must not trigger execution.
        reject('STATIC_MODE_WITH_REVIEWED_SUITES')
    if v['mode'] == 'RESUME' and ref is not None and not _SHA.fullmatch(ref):
        reject('RESUME_REQUIRES_PINNED_REF')
    return {'submission_id':v['submission_id'],'repository_url':url.rstrip('/'),
            'ref':ref,'mode':v['mode'],'resume_target':v.get('resume_target'),
            'resume_from':v.get('resume_from'),
            'suites':normalized,'run_readiness':v.get('run_readiness','AUTO'),
            'max_vm_launches':count}


def source_matches_url(source: dict, url: str) -> bool:
    match = _REPO.fullmatch(url)
    return bool(match and source.get('repository','').lower() ==
                (match.group(1)+'/'+match.group(2)).lower())


def verified_run_shape(kind: str, report: Any, source: dict, adapter: str | None) -> bool:
    if type(report) is not dict or report.get('status') not in RUN_STATUSES[kind]:
        return False
    stage_source = report.get('source')
    if kind == 'scenario_isolation':
        return (stage_source == source and report.get('adapter') == adapter and
                report.get('security_attestation') == ATTESTATION and
                report.get('execution') in ('ATTEMPTED_SEQUENTIAL_DISPOSABLE_VMS',
                                            'PERFORMED_IN_DISTINCT_DISPOSABLE_VMS'))
    return (stage_source == source and report.get('adapter') == adapter and
            report.get('security_attestation') == ATTESTATION and
            report.get('execution') in ('NOT_PERFORMED','NOT_VERIFIED',EXECUTED) and
            report.get('cleanup') in ('NOT_STARTED','NOT_VERIFIED',CLEAN,'CLEANUP_FAILED'))
