"""Phase 4D-D1/D2: reviewed MiniCollabApp login/session contract (offline).

A fixed, manually reviewed fixture and test sequence; NOT generic browser automation.
This module never launches Chrome or exposes credentials in catalog/evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from ..phase3.contracts import validate_test_plan
from .oracle_contracts import digest

SCHEMA = '0.1'
PINNED_SOURCE = {
    'kind': 'github',
    'repository': 'VarunRGB/Mini-Task-Collaboration-App',
    'commit_sha': 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37',
}
FLOW = 'minicollab_user_login_session_v1'
START_PATH = '/public/login.html'
TARGET_PATH = '/public/user_dashboard.html'
CHECKS = ('login_form_ready', 'unauthenticated_tasks_denied',
          'login_redirected', 'authenticated_tasks_readable')
BROWSER_BINARIES = frozenset(('/usr/bin/google-chrome-stable', '/usr/bin/chromium',
                              '/opt/google/chrome/chrome'))
_SHA = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)

_DIAG_STAGES = frozenset(('ENVIRONMENT', 'HELPER_IMPORT', 'CHROME_START', 'TARGET_CREATE', 'TARGET_ATTACH', 'DOMAIN_ENABLE', 'PAGE_NAVIGATE', 'FORM_WAIT', 'PREAUTH_CHECK', 'EMAIL_FOCUS', 'EMAIL_INSERT', 'PASSWORD_FOCUS', 'PASSWORD_INSERT', 'FORM_SUBMIT', 'DASHBOARD_WAIT', 'AUTH_TASKS', 'REPORT_ENCODING', 'INTERNAL'))
_DIAG_REASONS = frozenset(('ENV_REJECTED', 'OS_FAILURE', 'TIMEOUT_FAILURE', 'VALUE_FAILURE', 'INTERNAL_FAILURE', 'RESULT_OVERSIZE', 'CDP_REPLY_TIMEOUT', 'CDP_PIPE_CLOSED', 'CDP_PROTOCOL_ERROR', 'CDP_PIPE_WRITE_FAILED', 'CDP_INVALID_JSON', 'CDP_INVALID_MESSAGE', 'CDP_MISSING_RESULT', 'CDP_TOO_MANY_PENDING', 'CDP_PENDING_TOO_LARGE', 'CDP_FRAME_TOO_LARGE', 'CDP_NAVIGATION_ERROR', 'CDP_NO_SESSION', 'CDP_NO_PAGE_TARGET', 'CDP_NETWORK_ERROR', 'CDP_JAVASCRIPT_CONTEXT_ERROR', 'CDP_COMMAND_TOO_LARGE', 'CDP_UNKNOWN_FAILURE'))

_DIAG_ISSUE = re.compile(r'LOGIN_DIAG_([A-Z_]{2,32})__([A-Z_]{2,40})\Z', re.ASCII)

def _approved_inconclusive_issue(code: object) -> bool:
    if type(code) is not str:
        return False
    if code in {'LOGIN_HELPER_UNAVAILABLE', 'LOGIN_BROWSER_UNAVAILABLE',
                'LOGIN_CDP_UNAVAILABLE', 'LOGIN_HELPER_INVALID_REPORT',
                'LOGIN_DIAG_NO_MARKER_EXIT_118', 'LOGIN_DIAG_NO_MARKER_EXIT_119'}:
        return True
    match = _DIAG_ISSUE.fullmatch(code)
    return match is not None and match.group(1) in _DIAG_STAGES and match.group(2) in _DIAG_REASONS




class LoginReviewError(ValueError):
    """Invalid operator approval, source, or report."""


def _deny(code: str) -> None:
    raise LoginReviewError(code)


def login_catalog(source: Mapping) -> dict:
    if type(source) is not dict or source != PINNED_SOURCE:
        _deny('4DD_SOURCE_NOT_PINNED_PILOT')
    return {'schema_version': SCHEMA, 'source': dict(source),
            'flow': FLOW, 'start_path': START_PATH, 'target_path': TARGET_PATH,
            'checks': list(CHECKS)}


def validate_login_bundle(bundle: Any, *, expected_source: dict | None = None) -> None:
    if (type(bundle) is not dict or set(bundle) != {
            'schema_version', 'source', 'adapter', 'catalog_sha256',
            'flow', 'start_path', 'target_path', 'checks', 'browser_binary'}
            or bundle['schema_version'] != SCHEMA
            or bundle['source'] != PINNED_SOURCE
            or (expected_source is not None and bundle['source'] != expected_source)
            or bundle['adapter'] != 'php_mysql_v1'
            or bundle['flow'] != FLOW
            or bundle['start_path'] != START_PATH
            or bundle['target_path'] != TARGET_PATH
            or bundle['checks'] != list(CHECKS)
            or bundle['browser_binary'] not in BROWSER_BINARIES
            or type(bundle['catalog_sha256']) is not str
            or not _SHA.fullmatch(bundle['catalog_sha256'])):
        _deny('4DD_INVALID_REVIEWED_LOGIN_BUNDLE')


def compile_reviewed_login(plan: Mapping, candidate: Mapping,
                           catalog: Mapping, config: Mapping) -> dict:
    try:
        validate_test_plan(plan)
    except (TypeError, ValueError, KeyError) as exc:
        raise LoginReviewError('4DD_INVALID_PHASE3_PLAN') from exc
    source = PINNED_SOURCE
    if (type(candidate) is not dict or not isinstance(config, Mapping)
            or plan.get('source') != source
            or plan.get('execution_context', {}).get('adapter') != 'php_mysql_v1'
            or candidate.get('source') != source
            or candidate.get('adapter') != 'php_mysql_v1'
            or candidate.get('worker_provider') != 'qemu_qcow2_v1'
            or candidate.get('stage') != 'vm_execution_candidate'
            or candidate.get('schema_version') != '0.3'
            or candidate.get('status') != 'CANDIDATE_NEEDS_VM_VERIFICATION'):
        _deny('4DD_PHP_CANDIDATE_NOT_APPROVED')
    expected = login_catalog(source)
    if type(catalog) is not dict or catalog != expected:
        _deny('4DD_CATALOG_NOT_FIXED')
    identity = source['repository'] + '@' + source['commit_sha']
    if identity not in config.get('approved_snapshots', []):
        _deny('4DD_SOURCE_NOT_APPROVED')
    approval = identity + '#' + digest(catalog)
    grants = config.get('approved_browser_logins')
    if (type(grants) is not list or len(grants) > 256
            or any(type(x) is not str or len(x) > 260 for x in grants)
            or approval not in grants):
        _deny('4DD_LOGIN_CATALOG_NOT_APPROVED')
    if config.get('browser_resource_profile') != 'reviewed_browser_384_512_v1':
        _deny('4DD_REVIEWED_RESOURCE_PROFILE_REQUIRED')
    runtime = config.get('browser_runtime')
    if (type(runtime) is not dict or set(runtime) != {'binary', 'base_sha256'}
            or runtime.get('binary') != '/usr/bin/google-chrome-stable'
            or type(config.get('base_sha256')) is not str
            or not _SHA.fullmatch(config['base_sha256'])
            or runtime.get('base_sha256') != config['base_sha256']):
        _deny('4DD_BROWSER_NOT_PINNED')
    guest = {'schema_version': SCHEMA, 'source': dict(source),
             'adapter': 'php_mysql_v1', 'catalog_sha256': digest(catalog),
             'flow': FLOW, 'start_path': START_PATH,
             'target_path': TARGET_PATH, 'checks': list(CHECKS),
             'browser_binary': runtime['binary']}
    validate_login_bundle(guest, expected_source=source)
    return {'login': guest, '_review': {'plan': dict(plan), 'candidate': dict(candidate),
                                        'catalog': dict(catalog)}}


def validate_reviewed_login(proof: Any, candidate: Mapping,
                            config: Mapping) -> dict:
    if (type(proof) is not dict or set(proof) != {'login', '_review'}
            or type(proof['_review']) is not dict
            or set(proof['_review']) != {'plan', 'candidate', 'catalog'}
            or proof['_review']['candidate'] != candidate):
        _deny('4DD_REVIEW_PROOF_REQUIRED')
    rebuilt = compile_reviewed_login(proof['_review']['plan'], candidate,
                                     proof['_review']['catalog'], config)
    if rebuilt != proof:
        _deny('4DD_REVIEW_CHANGED_AFTER_APPROVAL')
    return proof['login']


def validate_login_report(bundle: dict, report: Any) -> None:
    """Strict privacy-preserving evidence: no raw page/API/credential details."""
    validate_login_bundle(bundle)
    required = {'schema_version', 'stage', 'source', 'adapter', 'catalog_sha256',
                'execution', 'status', 'issue_code', 'checks'}
    if (type(report) is not dict or set(report) != required
            or report['schema_version'] != SCHEMA
            or report['stage'] != 'guest_browser_login'
            or report['source'] != bundle['source']
            or report['adapter'] != bundle['adapter']
            or report['catalog_sha256'] != bundle['catalog_sha256']
            or report['execution'] != 'HEADLESS_BROWSER_IN_DISPOSABLE_VM'
            or report['status'] not in ('PASS', 'FAIL', 'INCONCLUSIVE')
            or type(report['checks']) is not list
            or len(report['checks']) != len(CHECKS)):
        _deny('4DD_INVALID_LOGIN_REPORT')
    for got, expected in zip(report['checks'], CHECKS):
        if (type(got) is not dict or set(got) != {'id', 'matched'}
                or got['id'] != expected
                or got['matched'] is not None and type(got['matched']) is not bool):
            _deny('4DD_INVALID_LOGIN_CHECK')
    states = [x['matched'] for x in report['checks']]
    if report['status'] == 'PASS':
        if states != [True] * len(CHECKS) or report['issue_code'] is not None:
            _deny('4DD_FALSE_PASS')
    elif report['status'] == 'FAIL':
        if (any(x is None for x in states) or all(states)
                or report['issue_code'] != 'LOGIN_FLOW_DID_NOT_MATCH'):
            _deny('4DD_INVALID_FAIL')
    else:
        if (any(x is not None for x in states)
                or not _approved_inconclusive_issue(report['issue_code'])):
            _deny('4DD_INVALID_INCONCLUSIVE')
