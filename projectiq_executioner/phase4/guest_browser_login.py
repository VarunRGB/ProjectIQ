"""4D-D1/D2 trusted guest dispatch and strict boolean-only report adapter."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Any

# Standalone in /home/projectiq inside QEMU: no ProjectIQ package imports.
CHECKS = ('login_form_ready', 'unauthenticated_tasks_denied',
          'login_redirected', 'authenticated_tasks_readable')
PINNED_SOURCE = {
    'kind': 'github',
    'repository': 'VarunRGB/Mini-Task-Collaboration-App',
    'commit_sha': 'c4f7965d16807fa05c3d7ffeee54f86fee4d0a37',
}

def _check_bundle(bundle: dict, source: dict) -> None:
    required = {'schema_version','source','adapter','catalog_sha256','flow',
                'start_path','target_path','checks','browser_binary'}
    if (type(bundle) is not dict or set(bundle) != required
            or type(source) is not dict or source != PINNED_SOURCE
            or bundle.get('source') != source
            or bundle['schema_version'] != '0.1'
            or bundle['adapter'] != 'php_mysql_v1'
            or bundle['flow'] != 'minicollab_user_login_session_v1'
            or bundle['start_path'] != '/public/login.html'
            or bundle['target_path'] != '/public/user_dashboard.html'
            or bundle['checks'] != list(CHECKS)
            or bundle['browser_binary'] != '/usr/bin/google-chrome-stable'):
        raise ValueError('4DD_GUEST_BUNDLE_NOT_FIXED')

def _valid_payload(payload: dict) -> bool:
    if (type(payload) is not dict or set(payload) != {'status','issue_code','checks'}
            or payload['status'] not in ('PASS','FAIL')
            or type(payload['checks']) is not list
            or len(payload['checks']) != len(CHECKS)):
        return False
    for item, name in zip(payload['checks'], CHECKS):
        if type(item) is not dict or set(item) != {'id','matched'} or item['id'] != name or type(item['matched']) is not bool:
            return False
    matches = [x['matched'] for x in payload['checks']]
    if payload['status'] == 'PASS':
        return all(matches) and payload['issue_code'] is None
    return not all(matches) and payload['issue_code'] == 'LOGIN_FLOW_DID_NOT_MATCH'


def _empty(bundle: dict, issue: str = 'LOGIN_HELPER_UNAVAILABLE') -> dict:
    return {'schema_version': '0.1', 'stage': 'guest_browser_login',
            'source': bundle['source'], 'adapter': bundle['adapter'],
            'catalog_sha256': bundle['catalog_sha256'],
            'execution': 'HEADLESS_BROWSER_IN_DISPOSABLE_VM',
            'status': 'INCONCLUSIVE', 'issue_code': issue,
            'checks': [{'id': k, 'matched': None} for k in CHECKS]}



# Whitelist exactly one marker emitted by the fixed guest helper.
_DIAG_STAGES = frozenset(('ENVIRONMENT', 'HELPER_IMPORT', 'CHROME_START', 'TARGET_CREATE', 'TARGET_ATTACH', 'DOMAIN_ENABLE', 'PAGE_NAVIGATE', 'FORM_WAIT', 'PREAUTH_CHECK', 'EMAIL_FOCUS', 'EMAIL_INSERT', 'PASSWORD_FOCUS', 'PASSWORD_INSERT', 'FORM_SUBMIT', 'DASHBOARD_WAIT', 'AUTH_TASKS', 'REPORT_ENCODING', 'INTERNAL'))
_DIAG_REASONS = frozenset(('ENV_REJECTED', 'OS_FAILURE', 'TIMEOUT_FAILURE', 'VALUE_FAILURE', 'INTERNAL_FAILURE', 'RESULT_OVERSIZE', 'CDP_REPLY_TIMEOUT', 'CDP_PIPE_CLOSED', 'CDP_PROTOCOL_ERROR', 'CDP_PIPE_WRITE_FAILED', 'CDP_INVALID_JSON', 'CDP_INVALID_MESSAGE', 'CDP_MISSING_RESULT', 'CDP_TOO_MANY_PENDING', 'CDP_PENDING_TOO_LARGE', 'CDP_FRAME_TOO_LARGE', 'CDP_NAVIGATION_ERROR', 'CDP_NO_SESSION', 'CDP_NO_PAGE_TARGET', 'CDP_NETWORK_ERROR', 'CDP_JAVASCRIPT_CONTEXT_ERROR', 'CDP_COMMAND_TOO_LARGE', 'CDP_UNKNOWN_FAILURE'))
_DIAG_LINE = re.compile(rb'PIQ4DD_DIAG ([A-Z_]{2,32}) ([A-Z_]{2,40})\n\Z', re.ASCII)

def _sanitized_cdp_diagnostic(raw: object) -> str | None:
    if type(raw) is not bytes or len(raw) > 100:
        return None
    hit = _DIAG_LINE.fullmatch(raw)
    if hit is None:
        return None
    stage, reason = (value.decode('ascii') for value in hit.groups())
    if stage not in _DIAG_STAGES or reason not in _DIAG_REASONS:
        return None
    return 'LOGIN_DIAG_' + stage + '__' + reason


def run_login(bundle: dict, *, expected_source: dict, workspace: Path,
              trusted_process: Callable[..., Any]) -> dict:
    _check_bundle(bundle, expected_source)
    # Only the pinned image's fixed Python interpreter and copied helper.
    cmd = ['/usr/bin/python3', '-I', '/home/projectiq/guest_browser_login_cdp.py']
    try:
        proc = trusted_process(cmd, project_code=True, browser_profile=True,
                               timeout=65, cwd=Path(workspace))
    except (OSError, RuntimeError, ValueError):
        return _empty(bundle)
    if getattr(proc, 'returncode', None) != 0:
        # The fixed diagnostic goes to stdout because scoped SSH wrappers may
        # swallow stderr. Accept only one exact whitelisted marker on failure.
        reason = _sanitized_cdp_diagnostic(getattr(proc, 'stdout', None))
        if reason is None:
            reason = _sanitized_cdp_diagnostic(getattr(proc, 'stderr', None))
        if reason is None:
            reason = {118: 'LOGIN_DIAG_NO_MARKER_EXIT_118',
                      119: 'LOGIN_DIAG_NO_MARKER_EXIT_119'}.get(proc.returncode,
                                                       'LOGIN_CDP_UNAVAILABLE')
        return _empty(bundle, reason)
    raw = getattr(proc, 'stdout', None)
    if type(raw) is not bytes or not 0 < len(raw) < 512:
        return _empty(bundle, 'LOGIN_HELPER_INVALID_REPORT')
    try:
        payload = json.loads(raw.decode('ascii', 'strict'))
        if type(payload) is not dict or set(payload) != {'status', 'issue_code', 'checks'}:
            raise ValueError('bad payload shape')
        report = {**_empty(bundle), **payload}
        if not _valid_payload(payload):
            raise ValueError('Invalid fixed helper evidence')
    except (UnicodeError, ValueError, TypeError, KeyError):
        return _empty(bundle, 'LOGIN_HELPER_INVALID_REPORT')
    return report
