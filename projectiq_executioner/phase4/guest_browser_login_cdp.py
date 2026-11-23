"""4D-D1/D2 fixed, guest-only CDP login/session probe for pinned MiniCollabApp.

No external navigation, dynamic operator JS, screenshots, cookies, or raw DOM
are emitted. Uses the existing hardened private CDP-pipe implementation.
Fixture credentials are public samples from the pinned SQL dump, not user input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

# Known public sample test fixture; never include these values in output.
_SAMPLE_USER = 'user@example.com'
_SAMPLE_PASSWORD = 'user123'
_START_URL = 'http://127.0.0.1:8765/public/login.html'
_WAIT_SECONDS = 32.0
_CHECKS = ('login_form_ready', 'unauthenticated_tasks_denied',
           'login_redirected', 'authenticated_tasks_readable')

# Fixed JS expressions, never supplied by the checkout, operator or catalog.
_FORM_READY = """(() => location.pathname === '/public/login.html' &&
    !!document.getElementById('loginForm') &&
    !!document.getElementById('email') &&
    !!document.getElementById('password') &&
    !!document.querySelector('#loginForm button[type=submit]'))()"""
_UNAUTHENTICATED_DENIAL = """(async () => {
    const r = await fetch('/logic/get_user_tasks.php', {credentials:'same-origin'});
    const j = await r.json();
    return !!j && !Array.isArray(j) && j.error === 'Unauthorized';
})()"""
_FOCUS_EMAIL = "document.getElementById('email').focus(); true"
_FOCUS_PASSWORD = "document.getElementById('password').focus(); true"
_SUBMIT = "document.querySelector('#loginForm button[type=submit]').click(); true"
_DASHBOARD = """(() => location.pathname === '/public/user_dashboard.html' &&
  !!document.getElementById('taskForm') &&
  !!document.getElementById('userTaskList'))()"""
_AUTHENTICATED_TASKS = """(async () => {
    if (location.pathname !== '/public/user_dashboard.html') return false;
    const r = await fetch('/logic/get_user_tasks.php', {credentials:'same-origin'});
    const j = await r.json();
    return r.ok && Array.isArray(j);
})()"""



# Phase 4D-D: fixed, bounded, non-sensitive helper diagnostics.
_DIAG_STAGES = frozenset(('ENVIRONMENT', 'HELPER_IMPORT', 'CHROME_START', 'TARGET_CREATE', 'TARGET_ATTACH', 'DOMAIN_ENABLE', 'PAGE_NAVIGATE', 'FORM_WAIT', 'PREAUTH_CHECK', 'EMAIL_FOCUS', 'EMAIL_INSERT', 'PASSWORD_FOCUS', 'PASSWORD_INSERT', 'FORM_SUBMIT', 'DASHBOARD_WAIT', 'AUTH_TASKS', 'REPORT_ENCODING', 'INTERNAL'))
_DIAG_REASONS = frozenset(('ENV_REJECTED', 'OS_FAILURE', 'TIMEOUT_FAILURE', 'VALUE_FAILURE', 'INTERNAL_FAILURE', 'RESULT_OVERSIZE', 'CDP_REPLY_TIMEOUT', 'CDP_PIPE_CLOSED', 'CDP_PROTOCOL_ERROR', 'CDP_PIPE_WRITE_FAILED', 'CDP_INVALID_JSON', 'CDP_INVALID_MESSAGE', 'CDP_MISSING_RESULT', 'CDP_TOO_MANY_PENDING', 'CDP_PENDING_TOO_LARGE', 'CDP_FRAME_TOO_LARGE', 'CDP_NAVIGATION_ERROR', 'CDP_NO_SESSION', 'CDP_NO_PAGE_TARGET', 'CDP_NETWORK_ERROR', 'CDP_JAVASCRIPT_CONTEXT_ERROR', 'CDP_COMMAND_TOO_LARGE', 'CDP_UNKNOWN_FAILURE'))
_DIAG_STAGE = 'ENVIRONMENT'
_DIAG_PREFIX = 'PIQ4DD_DIAG '

def _emit_diagnostic(reason: str) -> None:
    stage = _DIAG_STAGE if _DIAG_STAGE in _DIAG_STAGES else 'INTERNAL'
    code = reason if reason in _DIAG_REASONS else 'INTERNAL_FAILURE'
    try:
        os.write(2, (_DIAG_PREFIX + stage + ' ' + code + '\n').encode('ascii'))
    except OSError:
        pass


def _failure_category(exc: Exception) -> str:
    # A CDP exception may carry an arbitrary string; only known constants survive.
    if type(exc).__name__ == 'CdpFailure':
        code = exc.args[0] if len(exc.args) == 1 else None
        if type(code) is str and code in _DIAG_REASONS and code.startswith('CDP_'):
            return code
        return 'CDP_UNKNOWN_FAILURE'
    if isinstance(exc, TimeoutError):
        return 'TIMEOUT_FAILURE'
    if isinstance(exc, OSError):
        return 'OS_FAILURE'
    if isinstance(exc, ValueError):
        return 'VALUE_FAILURE'
    return 'INTERNAL_FAILURE'


def _emit_stdout_diagnostic(reason: str) -> None:
    """Transmit only fixed stage/reason on the existing bounded stdout pipe.

    The scoped launcher may not return stderr. The guest verifies a strict
    marker on nonzero exit; neither this marker nor a failure can yield PASS.
    """
    stage = _DIAG_STAGE if _DIAG_STAGE in _DIAG_STAGES else 'INTERNAL'
    code = reason if reason in _DIAG_REASONS else 'INTERNAL_FAILURE'
    try:
        os.write(1, (_DIAG_PREFIX + stage + ' ' + code + '\n').encode('ascii'))
    except OSError:
        pass


def _truth(cdp, session: str, expression: str, deadline: float,
           *, promise: bool = False) -> bool:
    record = cdp.call('Runtime.evaluate',
                      {'expression': expression, 'returnByValue': True,
                       'awaitPromise': promise}, session=session, deadline=deadline)
    if record.get('exceptionDetails') is not None:
        return False
    result = record.get('result')
    return type(result) is dict and result.get('type') == 'boolean' and result.get('value') is True


def _eventually(cdp, session: str, expression: str, deadline: float,
                *, promise: bool = False) -> bool:
    while time.monotonic() < deadline:
        if getattr(cdp, 'navigation_error', None):
            return False
        if _truth(cdp, session, expression, deadline, promise=promise):
            return True
        time.sleep(0.2)
    return False


def _checks_result(values: list[bool]) -> dict:
    if len(values) != len(_CHECKS) or any(type(x) is not bool for x in values):
        raise ValueError('Invalid bounded check state')
    passed = all(values)
    return {'status': 'PASS' if passed else 'FAIL',
            'issue_code': None if passed else 'LOGIN_FLOW_DID_NOT_MATCH',
            'checks': [{'id': key, 'matched': value}
                       for key, value in zip(_CHECKS, values)]}


def run_flow(cdp, deadline: float) -> dict:
    """One fresh browser target, pre-auth denial, form submit, post-auth API.

    All actions are fixed for the exact source SHA approved by the host.
    In an early mismatch, do not submit credentials or claim a partial pass.
    """
    global _DIAG_STAGE
    values = [False] * len(_CHECKS)
    _DIAG_STAGE = 'TARGET_CREATE'
    target = cdp.call('Target.createTarget', {'url': 'about:blank'}, deadline=deadline)
    page_id = target.get('targetId')
    if type(page_id) is not str or not page_id:
        raise ValueError('Cannot allocate browser target')
    _DIAG_STAGE = 'TARGET_ATTACH'
    attached = cdp.call('Target.attachToTarget',
                        {'targetId': page_id, 'flatten': True}, deadline=deadline)
    session = attached.get('sessionId')
    if type(session) is not str or not session:
        raise ValueError('Cannot attach private CDP session')
    cdp.session_id = session
    _DIAG_STAGE = 'DOMAIN_ENABLE'
    for method in ('Page.enable', 'Network.enable', 'Runtime.enable'):
        cdp.call(method, session=session, deadline=deadline)
    _DIAG_STAGE = 'PAGE_NAVIGATE'
    nav = cdp.call('Page.navigate', {'url': _START_URL},
                   session=session, deadline=deadline)
    if nav.get('errorText') is not None:
        return _checks_result(values)
    _DIAG_STAGE = 'FORM_WAIT'
    values[0] = _eventually(cdp, session, _FORM_READY, deadline)
    if not values[0]:
        return _checks_result(values)
    _DIAG_STAGE = 'PREAUTH_CHECK'
    values[1] = _truth(cdp, session, _UNAUTHENTICATED_DENIAL,
                       deadline, promise=True)
    if not values[1]:
        return _checks_result(values)
    _DIAG_STAGE = 'EMAIL_FOCUS'
    if not _truth(cdp, session, _FOCUS_EMAIL, deadline):
        return _checks_result(values)
    _DIAG_STAGE = 'EMAIL_INSERT'
    cdp.call('Input.insertText', {'text': _SAMPLE_USER}, session=session, deadline=deadline)
    _DIAG_STAGE = 'PASSWORD_FOCUS'
    if not _truth(cdp, session, _FOCUS_PASSWORD, deadline):
        return _checks_result(values)
    _DIAG_STAGE = 'PASSWORD_INSERT'
    cdp.call('Input.insertText', {'text': _SAMPLE_PASSWORD}, session=session, deadline=deadline)
    _DIAG_STAGE = 'FORM_SUBMIT'
    if not _truth(cdp, session, _SUBMIT, deadline):
        return _checks_result(values)
    _DIAG_STAGE = 'DASHBOARD_WAIT'
    values[2] = _eventually(cdp, session, _DASHBOARD, deadline)
    if not values[2]:
        return _checks_result(values)
    _DIAG_STAGE = 'AUTH_TASKS'
    values[3] = _truth(cdp, session, _AUTHENTICATED_TASKS,
                       deadline, promise=True)
    return _checks_result(values)


_TRUSTED_CDP_MODULE = '/home/projectiq/guest_browser_cdp.py'


def _load_trusted_cdp_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('guest_browser_cdp',
                                                  _TRUSTED_CDP_MODULE)
    if spec is None or spec.loader is None:
        raise ImportError('Trusted CDP module unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    global _DIAG_STAGE
    _DIAG_STAGE = 'ENVIRONMENT'
    if sys.platform != 'linux' or os.geteuid() == 0 or len(sys.argv) != 1:
        _emit_diagnostic('ENV_REJECTED')
        return 118
    quota_text = os.environ.get('HOME', '')
    quota = Path(quota_text)
    if not quota.is_dir() or not quota.resolve().is_relative_to(Path('/srv/projectiq-workspace')):
        _emit_diagnostic('ENV_REJECTED')
        return 118
    if os.environ.get('PROJECTIQ_BROWSER_PROFILE') != '1':
        _emit_diagnostic('ENV_REJECTED')
        return 118
    # Only a controller-copied CDP module may be imported. No checkout code.
    # Import from an exact trusted file: /home/projectiq is mode 0711 and
    # unprivileged FileFinder cannot list it to resolve a module name.
    # Do not widen directory permissions or search student-controlled paths.
    # The fixed path is chosen by this trusted helper, not by the project.
    profile = quota / 'phase4dd-login-cdp-profile'
    proc = None
    fds = ()
    try:
        _DIAG_STAGE = 'HELPER_IMPORT'
        pipe = _load_trusted_cdp_module()
        _DIAG_STAGE = 'CHROME_START'
        proc, tx, rx = pipe._start_chrome(profile)
        fds = (tx, rx)
        cdp = pipe.PipeCdp(tx, rx)
        report = run_flow(cdp, time.monotonic() + _WAIT_SECONDS)
        _DIAG_STAGE = 'REPORT_ENCODING'
        raw = json.dumps(report, separators=(',', ':'), sort_keys=True,
                         allow_nan=False).encode('ascii')
        if len(raw) > 512:
            _emit_diagnostic('RESULT_OVERSIZE')
            _emit_stdout_diagnostic('RESULT_OVERSIZE')
            return 119
        os.write(1, raw + b'\n')
        return 0
    except Exception as exc:
        # Never send exception messages, JS, DOM, cookies or arguments.
        reason = _failure_category(exc)
        _emit_diagnostic(reason)
        _emit_stdout_diagnostic(reason)
        return 119
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        if proc is not None:
            pipe._stop(proc)


if __name__ == '__main__':
    raise SystemExit(main())
