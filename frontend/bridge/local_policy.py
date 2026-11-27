"""Local operator admission for the existing bridge; no runtime or rubric engine."""
from __future__ import annotations
import hmac
import re
import threading
from pathlib import Path

MODES = {'STATIC', 'REVIEWED', 'RESUME', 'TRUSTED_DEMO'}
SHA = re.compile(r'^[0-9a-f]{40}$')
REPO = re.compile(r'^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/)?$')
TOKEN = re.compile(r'^[A-Za-z0-9_-]{32,128}$')
STATES = {'COMPLETED':'done', 'PARTIAL':'partial', 'REVIEW_REQUIRED':'partial', 'FAILED':'failed', 'NOT_RUN':'skipped'}
AGENTS = ('summarizer', 'integrity', 'research', 'executioner')


class BridgeBusy(ValueError):
    pass


class Admission:
    """One mutating operation/run per bridge process. No queuing or retry."""
    def __init__(self):
        self.lock = threading.Lock()

    def acquire(self):
        if not self.lock.acquire(blocking=False):
            raise BridgeBusy('BRIDGE_BUSY_WAIT_FOR_CURRENT_OPERATION')

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


def valid_token(token):
    return isinstance(token, str) and bool(TOKEN.fullmatch(token))


def authorized(headers, *, port, origin, token, preflight=False):
    # DNS rebinding and cross-origin requests are rejected BEFORE reading bodies.
    if headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return False
    actual = headers.get('Origin')
    if actual is not None and actual != origin:
        return False
    if preflight:
        return actual == origin
    supplied = headers.get('X-ProjectIQ-Token', '')
    return valid_token(token) and valid_token(supplied) and hmac.compare_digest(token, supplied)


def available_modes(vm_config, demo_enabled):
    modes = ['STATIC']
    if vm_config is not None and Path(vm_config).is_file():
        modes += ['REVIEWED', 'RESUME']
        if demo_enabled:
            modes.append('TRUSTED_DEMO')
    return modes


def request_mode(req, payload, *, vm_config, demo_enabled):
    """Validate admission intent; existing engines still verify approvals and receipts."""
    if not isinstance(req, dict) or not isinstance(payload, dict):
        raise ValueError('INVALID_EVALUATION_REQUEST')
    exe = req.get('executioner')
    if exe is None:
        exe = {}
    if not isinstance(exe, dict):
        raise ValueError('INVALID_EXECUTIONER_REQUEST')
    mode = exe.get('mode', 'STATIC')
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('INVALID_EXECUTIONER_MODE')
    repo, ref = req.get('repository_url'), req.get('ref')
    imported = bool(payload.get('executioner_import'))
    resume_upload = bool(payload.get('resume_from'))
    trust = exe.get('trust_demo_source', False)
    disposable = exe.get('confirm_disposable_state', False)
    if type(trust) is not bool or type(disposable) is not bool:
        raise ValueError('INVALID_DEMO_CONFIRMATION')
    if not repo and (mode != 'STATIC' or imported or resume_upload):
        raise ValueError('REPOSITORY_REQUIRED_FOR_EXECUTION')
    if repo and (not isinstance(repo, str) or not REPO.fullmatch(repo)):
        raise ValueError('INVALID_PUBLIC_GITHUB_REPOSITORY')
    if mode == 'TRUSTED_DEMO':
        if mode not in available_modes(vm_config, demo_enabled):
            raise ValueError('TRUSTED_DEMO_NOT_ENABLED_BY_OPERATOR')
        if not trust:
            raise ValueError('EXPLICIT_DEMO_TRUST_REQUIRED')
        if imported or resume_upload or exe.get('suites') or exe.get('resume_from') or exe.get('resume_target'):
            raise ValueError('DEMO_CANNOT_COMBINE_REVIEWED_OR_IMPORTED_INPUTS')
        if not isinstance(ref, str) or not SHA.fullmatch(ref):
            raise ValueError('PINNED_40_CHARACTER_SHA_REQUIRED')
        return mode, False
    if trust or disposable:
        raise ValueError('DEMO_CONFIRMATION_REQUIRES_DEMO_MODE')
    composed = bool(repo) and (mode != 'STATIC' or imported)
    if composed:
        if payload.get('acknowledge_imported') is not True:
            raise ValueError('ACKNOWLEDGEMENT_REQUIRED_FOR_COMPOSED_EXECUTIONER_EVIDENCE')
        if not isinstance(ref, str) or not SHA.fullmatch(ref):
            raise ValueError('PINNED_40_CHARACTER_SHA_REQUIRED')
        if not imported and mode not in available_modes(vm_config, demo_enabled):
            raise ValueError('REVIEWED_AND_RESUME_REQUIRE_OPERATOR_VM_CONFIG')
    if resume_upload and mode != 'RESUME':
        raise ValueError('RESUME_UPLOAD_REQUIRES_RESUME_MODE')
    return mode, composed


def demo_args(exe, vm_config):
    # Caller has already checked request_mode. No paths or arbitrary flags from HTTP.
    if exe.get('mode') != 'TRUSTED_DEMO':
        return []
    if exe.get('trust_demo_source') is not True or vm_config is None:
        raise ValueError('EXPLICIT_DEMO_TRUST_REQUIRED')
    args = ['--trust-demo-source', '--demo-vm-config', str(vm_config)]
    if exe.get('confirm_disposable_state') is True:
        args.append('--confirm-disposable-state')
    return args


def final_states(result):
    """Read actual result, not progress guesses. Partial assessment is not failure."""
    if not isinstance(result, dict) or not isinstance(result.get('assessment'), dict) or not isinstance(result.get('rubric'), dict):
        raise ValueError('INVALID_UNIFIED_RESULT')
    if result.get('workflow_status') not in ('COMPLETE','INCOMPLETE_ASSESSMENT','PARTIAL_ERROR','CANCELLED') or type(result.get('rubric_exit_code')) is not int:
        raise ValueError('INVALID_UNIFIED_WORKFLOW_STATUS')
    agents = result.get('agents')
    if not isinstance(agents, dict) or set(agents) != set(AGENTS):
        raise ValueError('INVALID_UNIFIED_AGENTS')
    states = {}
    for name in AGENTS:
        status = agents[name].get('status') if isinstance(agents[name], dict) else None
        if status not in STATES:
            raise ValueError('INVALID_UNIFIED_AGENT_STATUS')
        states[name] = STATES[status]
    fmt = (result.get('ieee_format') or {}).get('status')
    states['format'] = {'COMPLETED':'done','REVIEW_REQUIRED':'partial','FAILED':'failed'}.get(fmt,'skipped')
    if result['rubric_exit_code'] != 0:
        states['rubric'] = 'failed'
    elif result['workflow_status'] == 'INCOMPLETE_ASSESSMENT':
        states['rubric'] = 'partial'
    else:
        states['rubric'] = 'done'
    return states


def job_agent_actions(result, *, composed, imported, mode):
    """Supplement receipt provenance; never modify native receipt or agent_actions."""
    actions = {}
    for name, row in result['agents'].items():
        if row.get('status') == 'NOT_RUN':
            actions[name] = 'SKIPPED'
        elif row.get('status') == 'FAILED' or row.get('evidence_check') != 'MATCHED':
            actions[name] = 'NOT_AVAILABLE'
        elif composed and name == 'executioner' and imported:
            actions[name] = 'IMPORTED_FILE'
        elif composed and name == 'executioner' and mode == 'RESUME':
            actions[name] = 'RESUME_THIS_RUN'
        elif composed:
            actions[name] = 'COLLECTED_THIS_RUN'
        else:
            actions[name] = result.get('agent_actions', {}).get(name, 'NOT_AVAILABLE')
    return actions
