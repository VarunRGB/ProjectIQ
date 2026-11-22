"""4C-D5: exact-input, independently approved, two-repository VM pilot.

Only inspect operator-owned JSON. Do not fetch, execute student code, provision
credentials, authorize new SHA, or claim full-app coverage from a passing route.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .oracle_contracts import digest
from .oracle_stage import _load_strict_json

_STRATEGY = 'SEQUENTIAL_DISTINCT_DISPOSABLE_VMS'
_ID = re.compile(r'[a-z][a-z0-9_-]{1,39}\Z', re.ASCII)
_PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}\Z', re.ASCII)
_SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_SHA40 = re.compile(r'[0-9a-f]{40}\Z', re.ASCII)
_REPOSITORY = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z', re.ASCII)
_WORKFLOWS = {
    'D3_REGISTRATION': frozenset(('plan', 'candidate', 'auth', 'oracles', 'postconditions')),
    'REVIEWED_JSON_API': frozenset(('plan', 'candidate', 'oracles')),
}
_LIMITS = {'plan': 2_000_000, 'candidate': 200_000,
           'auth': 64_000, 'oracles': 256_000, 'postconditions': 32_768}
_ATTESTATION = 'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS'


class CrossRepositoryReviewError(ValueError):
    """Malformed or insufficiently reviewed multi-repository pilot."""


def _deny(code: str) -> None:
    raise CrossRepositoryReviewError(code)


def _path(root: Path, relative: str) -> Path:
    """Confine inputs to a non-symlinked operator staging tree."""
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 240
            or '\\' in relative or ':' in relative or relative.startswith('/')
            or any(not _PART.fullmatch(part) or part in ('.', '..')
                   for part in relative.split('/'))):
        _deny('INVALID_D5_INPUT_RELATIVE_PATH')
    location = root
    for part in relative.split('/'):
        location = location / part
        try:
            info = location.lstat()
        except OSError as exc:
            raise CrossRepositoryReviewError('D5_INPUT_NOT_AVAILABLE') from exc
        if location.is_symlink() or (getattr(info, 'st_file_attributes', 0) & 0x400):
            _deny('D5_SYMLINK_OR_REPARSE_INPUT_REJECTED')
    if not location.is_file() or not location.resolve().is_relative_to(root.resolve()):
        _deny('D5_INPUT_OUTSIDE_STAGING_ROOT')
    return location


def validate_manifest(manifest: Any) -> list[dict]:
    """Two roles, two distinct IDs and sources, bounded operator-owned paths."""
    if (not isinstance(manifest, dict) or set(manifest) != {
            'schema_version', 'strategy', 'cases'}
            or manifest.get('schema_version') != '0.1'
            or manifest.get('strategy') != _STRATEGY):
        _deny('INVALID_D5_MANIFEST')
    cases = manifest['cases']
    if not isinstance(cases, list) or len(cases) != 2:
        _deny('D5_REQUIRES_EXACTLY_TWO_CASES')
    ids, workflows, sources, adapters = [], [], [], []
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {
                'id', 'workflow', 'source', 'adapter', 'files', 'input_sha256'}):
            _deny('INVALID_D5_CASE')
        if not isinstance(case['id'], str) or not _ID.fullmatch(case['id']):
            _deny('INVALID_D5_CASE_ID')
        workflow = case['workflow']
        if workflow not in _WORKFLOWS:
            _deny('UNSUPPORTED_D5_WORKFLOW')
        source = case['source']
        if (not isinstance(source, dict) or set(source) != {'kind', 'repository', 'commit_sha'}
                or source.get('kind') != 'github'
                or not isinstance(source.get('repository'), str)
                or not _REPOSITORY.fullmatch(source['repository'])
                or not isinstance(source.get('commit_sha'), str)
                or not _SHA40.fullmatch(source['commit_sha'])):
            _deny('INVALID_D5_PINNED_SOURCE')
        adapter = case['adapter']
        if (workflow == 'D3_REGISTRATION' and adapter != 'php_mysql_v1'
                or workflow == 'REVIEWED_JSON_API' and adapter not in {
                    'node_fullstack_offline_v1', 'node_express_offline_v1',
                    'python_web_offline_v1'}):
            _deny('UNSUPPORTED_D5_ADAPTER')
        if (not isinstance(case['files'], dict)
                or set(case['files']) != _WORKFLOWS[workflow]
                or any(not isinstance(x, str) for x in case['files'].values())
                or not isinstance(case['input_sha256'], str)
                or not _SHA.fullmatch(case['input_sha256'])):
            _deny('INVALID_D5_INPUT_BINDING')
        ids.append(case['id'])
        workflows.append(workflow)
        sources.append(source['repository'].casefold())
        adapters.append(adapter)
    if (len(set(ids)) != 2 or len(set(workflows)) != 2
            or len(set(sources)) != 2 or len(set(adapters)) != 2):
        _deny('D5_REQUIRES_DISTINCT_REPOSITORIES_AND_ADAPTERS')
    return cases


def load_case_documents(case: Mapping[str, Any], root: Path) -> dict:
    """Read exact bounded files without following symlink/reparse components."""
    if not root.is_dir() or root.is_symlink():
        _deny('INVALID_D5_INPUT_STAGING_ROOT')
    result = {}
    for label, filename in case['files'].items():
        try:
            result[label] = _load_strict_json(_path(root, filename), _LIMITS[label])
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise CrossRepositoryReviewError('D5_INVALID_OR_UNAVAILABLE_INPUT') from exc
    if digest(result) != case['input_sha256']:
        _deny('D5_INPUT_CONTENT_DIGEST_MISMATCH')
    plan, candidate = result['plan'], result['candidate']
    if (not isinstance(plan, dict) or not isinstance(candidate, dict)
            or plan.get('source') != case['source']
            or candidate.get('source') != case['source']
            or candidate.get('adapter') != case['adapter']):
        _deny('D5_SOURCE_OR_ADAPTER_BINDING_MISMATCH')
    return result


def compile_reviewed_suite(manifest: Mapping[str, Any], config: Mapping[str, Any],
                           inputs_root: Path) -> list[dict]:
    """ALL existing operator approvals must pass before FIRST VM launch."""
    cases = validate_manifest(manifest)
    if not isinstance(config, Mapping):
        _deny('INVALID_D5_OPERATOR_CONFIG')
    allowed = config.get('approved_cross_repository_suites')
    required = 'cross-repo#' + digest(manifest)
    if (not isinstance(allowed, list) or len(allowed) > 256
            or any(not isinstance(x, str) or len(x) > 260 for x in allowed)
            or required not in allowed):
        _deny('D5_SUITE_NOT_OPERATOR_APPROVED')
    approved = config.get('approved_snapshots')
    if not isinstance(approved, list):
        _deny('D5_MISSING_PINNED_SHA_APPROVALS')
    compiled = []
    for case in cases:
        source = case['source']
        if source['repository'] + '@' + source['commit_sha'] not in approved:
            _deny('D5_PINNED_REPOSITORY_NOT_APPROVED')
        docs = load_case_documents(case, inputs_root)
        try:
            if case['workflow'] == 'D3_REGISTRATION':
                from .db_postcondition_contracts import compile_reviewed_postconditions
                proof = compile_reviewed_postconditions(
                    docs['plan'], docs['auth'], docs['oracles'],
                    docs['postconditions'], docs['candidate'], config)
                checks = proof['postconditions']['checks']
                if not (len(checks) == 1 and checks[0]['table'] == 'users'
                        and checks[0]['lookup_column'] == 'email'):
                    _deny('D5_REQUIRES_POSITIVE_REGISTRATION_WITNESS')
                count = 1
            else:
                from .reviewed_api import compile_reviewed_api_bundle
                proof = compile_reviewed_api_bundle(docs['plan'], docs['oracles'],
                                                     docs['candidate'], config)
                if not any(a['kind'] == 'JSON_BODY_SUBSET_EQUALS'
                           for test in proof['tests'] for a in test['assertions']):
                    _deny('D5_REQUIRES_BEHAVIORAL_JSON_ORACLE')
                count = len(proof['tests'])
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise CrossRepositoryReviewError('D5_CASE_ORACLE_OR_APPROVAL_INVALID') from exc
        compiled.append({'case': case, 'docs': docs, 'proof': proof,
                         'test_count': count})
    return compiled
