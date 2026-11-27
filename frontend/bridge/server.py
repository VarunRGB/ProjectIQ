"""ProjectIQ local bridge: the faculty UI's API over the existing backend.

    python frontend/bridge/server.py --root outputs/unified
    python frontend/bridge/server.py --root outputs/unified --vm-config vm_config.json --review-root reviewed_catalogs
    # then in frontend/:  $env:VITE_BACKEND="http"; npm run dev

It only composes public entry points that already exist; no agent logic is changed:

  * New evaluation (STATIC or report-only): `python -m projectiq_core evaluate ...`
  * REVIEWED / RESUME: `python -m projectiq_executioner.orchestration` (operator VM
    configuration required), then `projectiq_core collect` for the other agents,
    `projectiq_core bundle` of all native results, and
    `projectiq_core evaluate --evaluation ... --acknowledge-imported`. The teacher's
    acknowledgment is required and the result is labelled as imported evidence.
  * Importing an existing Executioner result: the same bundle path, without a VM.
  * Teacher decisions, weights and marks: validated with the real Phase 2 engine
    (`orchestrator.reuse_decisions`, `scoring.assess`) and saved beside the run in
    `faculty/`. Original artifacts are never modified.
  * Reassessing criteria / retrying deferrals: `python -m projectiq_core.phase2 propose`
    (local Ollama rubric model), written to a new `faculty/reassess_*` directory.

Binds to 127.0.0.1 only. Outputs contain confidential student work. This is a trusted
local operator tool, not a hardened multi-user service.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from local_policy import (Admission, BridgeBusy, authorized, available_modes,
                          demo_args, final_states, job_agent_actions, request_mode, valid_token)

ADMISSION = Admission()

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$')
SHA = re.compile(r'^[0-9a-f]{40}$')
SAFE_NAME = re.compile(r'[^A-Za-z0-9_.-]+')
MAX_BODY = 200 * 1024 * 1024
AGENTS = ('summarizer', 'integrity', 'research', 'executioner')


def now():
    return datetime.now(timezone.utc).isoformat()


def load(p: Path):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def dump(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(p)


def safe_file_name(name: str) -> str:
    base = Path(str(name).replace('\\', '/')).name
    base = SAFE_NAME.sub('_', base).strip('._') or 'file'
    return base[:120]


class Config:
    root = Path('outputs/unified')
    python = sys.executable
    origin = 'http://127.0.0.1:5173'
    vm_config: Path | None = None
    review_root: Path | None = None
    review_registry: Path | None = None
    work_root: Path | None = None
    port = 8765
    token = ''
    demo_enabled = False


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, run_id, title, eval_id):
        self.run_id = run_id
        self.title = title
        self.eval_id = eval_id
        self.started = time.time()
        self.finished = None
        self.state = 'RUNNING'
        self.stage = 'queued'
        self.events = []
        self.agents = {a: {'state': 'waiting'} for a in ('format', *AGENTS, 'rubric')}
        self.started_at = {}
        self.proc = None
        self.cancelled = False
        self.lock = threading.Lock()

    def event(self, message, level='info', stage=None, agent=None):
        with self.lock:
            if stage:
                self.stage = stage
            self.events.append({'at_ms': int((time.time() - self.started) * 1000), 'stage': self.stage,
                                'agent': agent, 'message': message, 'level': level})

    def agent(self, name, state, detail=None):
        with self.lock:
            if name not in self.started_at and state == 'running':
                self.started_at[name] = time.time()
            a = self.agents[name]
            a['state'] = state
            if detail:
                a['detail'] = detail
            if name in self.started_at:
                a['elapsed_ms'] = int((time.time() - self.started_at[name]) * 1000)

    def status(self):
        with self.lock:
            agents = json.loads(json.dumps(self.agents))
            for k, v in agents.items():
                if v['state'] == 'running' and k in self.started_at:
                    v['elapsed_ms'] = int((time.time() - self.started_at[k]) * 1000)
            end = self.finished or time.time()
            return {'run_id': self.run_id, 'evaluation_id': self.eval_id if self.state == 'COMPLETED' else None,
                    'title': self.title, 'started_at': datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
                    'elapsed_ms': int((end - self.started) * 1000), 'stage': self.stage, 'state': self.state,
                    'agents': agents, 'events': list(self.events), 'cancel_requested': self.cancelled,
                     'executioner_mode': getattr(self, 'executioner_mode', 'STATIC')}


JOBS: dict[str, Job] = {}

AGENT_LINE = re.compile(r'^(summarizer|integrity|research|executioner):\s*(.*)$')
STATE_MAP = {'COMPLETED': 'done', 'PARTIAL': 'partial', 'REVIEW_REQUIRED': 'partial', 'FAILED': 'failed', 'NOT_RUN': 'skipped'}


def interpret(job: Job, line: str):
    """Turn the backend's own progress lines into run events."""
    line = line.rstrip()
    if not line:
        return
    m = AGENT_LINE.match(line)
    if m:
        agent, rest = m.groups()
        if job.agents['format']['state'] == 'running':
            # The collector runs the IEEE format check before the first agent starts.
            job.agent('format', 'done', 'Format check finished')
        if rest == 'starting':
            job.agent(agent, 'running', 'Running')
            job.event(f'{agent.capitalize()} started.', stage=agent, agent=agent)
        elif rest == 'not requested':
            job.agent(agent, 'skipped', 'Not needed for this evaluation')
        elif rest in STATE_MAP:
            job.agent(agent, STATE_MAP[rest], rest.replace('_', ' ').capitalize())
            job.event(f'{agent.capitalize()}: {rest.replace("_", " ").lower()}.', 'error' if rest == 'FAILED' else 'warn' if rest != 'COMPLETED' else 'success', agent=agent)
        else:
            job.agent(agent, job.agents[agent]['state'] if job.agents[agent]['state'] != 'waiting' else 'running', rest[:160])
            job.event(f'{agent.capitalize()}: {rest[:200]}', agent=agent)
        return
    if line.startswith('Rubric') or line.startswith('Assessment') or line.startswith('Proposed total') or line.startswith('Unresolved'):
        if job.agents['rubric']['state'] == 'waiting':
            job.agent('rubric', 'running', 'Proposing levels from evidence')
            job.event('Rubric engine started.', stage='rubric', agent='rubric')
        mb = re.search(r'batch (\d+)/(\d+)', line)
        if mb:
            with job.lock:
                job.agents['rubric']['progress'] = round(100 * (int(mb.group(1)) - 1) / int(mb.group(2)))
        job.agent('rubric', 'running', line[:160])
        job.event(line[:220], 'warn' if 'WITHHELD' in line or 'DEFER' in line else 'info', agent='rubric')
        return
    if line.startswith('Unified result:'):
        job.agent('rubric', 'done', 'Assessment saved')
        return
    job.event(line[:220])


def run_cmd(job: Job, args, cwd=REPO_ROOT, label='backend'):
    if job.cancelled:
        raise RuntimeError('CANCELLED')
    env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    env.pop('PROJECTIQ_BRIDGE_TOKEN', None)
    env.pop('VITE_BRIDGE_TOKEN', None)
    job.event(f'Running {label}.')
    with job.lock:
        if job.cancelled:
            raise RuntimeError('CANCELLED')
        job.proc = subprocess.Popen([Config.python, '-u', *map(str, args)], cwd=str(cwd), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    tail = []
    for line in job.proc.stdout:
        tail = (tail + [line.rstrip()])[-30:]
        interpret(job, line)
    code = job.proc.wait()
    job.proc = None
    # Cancellation is checked before the next command. Never terminate the VM owner.
    return code, tail


def evaluate_job(job: Job, req: dict, files: dict, jobdir: Path):
    try:
        out = Config.root / job.eval_id
        report = jobdir / 'input' / files['report']
        rubric_path = jobdir / 'rubric.json'
        refs_dir = jobdir / 'references' if files['references'] else None
        exe = req.get('executioner') or None
        repo = req.get('repository_url')
        ref = req.get('ref')
        mode = (exe or {}).get('mode', 'STATIC')
        composed = bool(repo) and mode != 'TRUSTED_DEMO' and (mode != 'STATIC' or bool(files.get('executioner_import')))
        rubric_mode = req.get('rubric_mode', 'AUTOMATIC')
        tail_args = ['--preview'] if rubric_mode == 'PREVIEW' else ['--objective-only'] if rubric_mode == 'OBJECTIVE_ONLY' else []
        from projectiq_core.phase2.contracts import Rubric
        from projectiq_core.orchestrator import research_needed
        research = research_needed(Rubric.model_validate(load(rubric_path)))
        verify = bool(req.get('verify_research')) and research
        if not composed:
            job.event('Submission received; the backend saves a private report snapshot with its SHA-256.', stage='queued')
            args = ['-m', 'projectiq_core', 'evaluate', '--report', report, '--title', req['title'], '--submission-id', req['submission_id'],
                    '--rubric', rubric_path, '--out', out, *tail_args]
            if repo:
                args += ['--repo', repo] + (['--ref', ref] if ref else [])
            if refs_dir:
                args += ['--references', refs_dir]
            if verify:
                args += ['--verify-research']
            args += demo_args(exe or {}, Config.vm_config)
            job.agent('format', 'running', 'Checking IEEE formatting')
            code, tail = run_cmd(job, args, label='projectiq_core evaluate')
        else:
            if not req.get('acknowledge_imported'):
                raise ValueError('ACKNOWLEDGEMENT_REQUIRED_FOR_COMPOSED_EXECUTIONER_EVIDENCE')
            if not ref or not SHA.match(ref):
                raise ValueError('PINNED_40_CHARACTER_SHA_REQUIRED')
            # 1. Executioner: import or run the reviewed/resume mode.
            if files.get('executioner_import'):
                exec_json = jobdir / 'input' / files['executioner_import']
                job.agent('executioner', 'done', 'Imported existing result')
                job.event('Using the supplied Executioner result; it will be re-verified when bundled.', agent='executioner')
            else:
                if not Config.vm_config:
                    raise ValueError('REVIEWED_AND_RESUME_REQUIRE_OPERATOR_VM_CONFIG')
                request = {'submission_id': req['submission_id'], 'repository_url': repo, 'ref': ref, 'mode': mode,
                           'run_readiness': exe.get('run_readiness', 'AUTO'), 'max_vm_launches': int(exe.get('max_vm_launches', 0))}
                if mode == 'REVIEWED':
                    request['suites'] = exe.get('suites', [])
                if mode == 'RESUME':
                    request['resume_target'] = exe.get('resume_target', 'REVIEWED')
                    request['resume_from'] = str(jobdir / 'input' / files['resume_from']) if files.get('resume_from') else exe.get('resume_from')
                dump(jobdir / 'executioner_request.json', request)
                job.agent('executioner', 'running', f'{mode} run in progress')
                job.event(f'Executioner {mode}: only operator-approved suites can run, in disposable VMs.', stage='executioner', agent='executioner')
                args = ['-m', 'projectiq_executioner.orchestration', '--request', jobdir / 'executioner_request.json', '--out', jobdir / 'executioner', '--vm-config', Config.vm_config]
                for flag, val in (('--review-root', Config.review_root), ('--review-registry', Config.review_registry), ('--work-root', Config.work_root)):
                    if val:
                        args += [flag, val]
                code, tail = run_cmd(job, args, label=f'Executioner {mode}')
                exec_json = jobdir / 'executioner' / 'executioner_result.json'
                if not exec_json.is_file():
                    job.agent('executioner', 'failed', 'No result saved')
                    raise RuntimeError('EXECUTIONER_RESULT_NOT_SAVED')
                status = load(exec_json).get('status')
                job.agent('executioner', 'done' if status == 'COMPLETED' else 'partial', str(status).replace('_', ' ').capitalize())
            # 2. Other agents, live.
            agents = ['summarizer', 'integrity'] + (['research'] if research else [])
            args = ['-m', 'projectiq_core', 'collect', '--report', report, '--title', req['title'], '--submission-id', req['submission_id'],
                    '--repo', repo, '--ref', ref, '--agents', *agents, '--out', jobdir / 'collect']
            if refs_dir:
                args += ['--references', refs_dir]
            if verify:
                args += ['--verify-research']
            job.agent('format', 'running', 'Checking IEEE formatting')
            run_cmd(job, args, label='projectiq_core collect')
            if not (jobdir / 'collect' / 'evaluation.json').is_file():
                raise RuntimeError('COLLECTION_RESULT_NOT_SAVED')
            native = jobdir / 'collect' / 'native'
            # 3. Bundle every native result as one acknowledged imported evaluation.
            args = ['-m', 'projectiq_core', 'bundle', '--report', report, '--title', req['title'], '--submission-id', req['submission_id'],
                    '--repo', repo, '--ref', ref, '--executioner', exec_json, '--out', jobdir / 'bundle']
            for a in agents:
                if (native / f'{a}.json').is_file():
                    args += [f'--{a}', native / f'{a}.json']
            job.event('Bundling native results; receipts are re-checked against the report and commit.', stage='evidence')
            code, tail = run_cmd(job, args, label='projectiq_core bundle')
            if not (jobdir / 'bundle' / 'evaluation.json').is_file():
                raise RuntimeError('BUNDLE_FAILED')
            args = ['-m', 'projectiq_core', 'evaluate', '--evaluation', jobdir / 'bundle' / 'evaluation.json', '--acknowledge-imported',
                    '--rubric', rubric_path, '--out', out, *tail_args]
            code, tail = run_cmd(job, args, label='projectiq_core evaluate (saved evaluation)')
        if not (out / 'result.json').is_file():
            msg = next((t for t in reversed(tail) if 'failed' in t.lower() or t.isupper()), tail[-1] if tail else 'UNKNOWN')
            raise RuntimeError(msg[:200])
        result = load(out / 'result.json')
        states = final_states(result)
        meta = {'students': req.get('students') or [], 'course': req.get('course'), 'submitted_utc': now(), 'evaluated_utc': now(),
                'bridge_job_actions': job_agent_actions(result, composed=composed, imported=bool(files.get('executioner_import')), mode=mode),
                'executioner_mode_requested': mode if repo else None, 'references_uploaded': len(files['references']),
                'verify_research': verify, 'elapsed_seconds': round(time.time() - job.started, 1),
                'composition': 'IMPORTED_ACKNOWLEDGED' if composed else 'LIVE'}
        dump(out / 'faculty_meta.json', meta)
        for a, state in states.items():
            job.agent(a, state, 'Final saved result')
        if result['workflow_status'] == 'CANCELLED':
            job.state = 'CANCELLED'
            job.event('Backend reported cancellation. Saved artifacts retained.', 'warn', stage='failed')
        else:
            job.state = 'COMPLETED'
            if job.cancelled:
                job.event('Current command finished and saved the evaluation before cancellation could take effect.', 'info')
            partial = result['workflow_status'] != 'COMPLETE' or code != 0 or any(s in ('partial', 'failed') for s in states.values())
            job.event('Evaluation saved with limitations; review the actual results.' if partial else 'Evaluation ready for review.', 'warn' if partial else 'success', stage='done')
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, run directory kept
        job.state = 'CANCELLED' if str(exc) == 'CANCELLED' else 'FAILED'
        job.event(f'Stopped: {exc}', 'error', stage='failed')
        (jobdir / 'bridge_error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        job.finished = time.time()
        ADMISSION.release()


# --------------------------------------------------------------------------- #
# Review: validated by the real engine
# --------------------------------------------------------------------------- #
def run_dir(eval_id):
    if not ID.match(eval_id):
        raise FileNotFoundError(eval_id)
    d = (Config.root / eval_id).resolve()
    if d.parent != Config.root.resolve() or not (d / 'result.json').is_file():
        raise FileNotFoundError(eval_id)
    return d


def current_base(d: Path):
    """Model base after any reassessment: (rubric, decisions, assessment)."""
    cur = d / 'faculty' / 'current.json'
    if cur.is_file():
        c = load(cur)
        return c['rubric'], c['decisions'], c['assessment']
    return load(d / 'rubric' / 'rubric.json'), load(d / 'rubric' / 'decisions.json'), load(d / 'rubric' / 'assessment.json')


def validate_review(d: Path, review: dict):
    from projectiq_core.phase2.contracts import EvidenceBundle, Rubric, DecisionSet, Decision
    from projectiq_core.phase2.evidence import digest
    from projectiq_core.phase2.scoring import assess
    from projectiq_core.orchestrator import reuse_decisions
    bundle = EvidenceBundle.model_validate(load(d / 'rubric' / 'evidence.json'))
    base_rubric_raw, base_dec_raw, _ = current_base(d)
    base_rubric = Rubric.model_validate(base_rubric_raw)
    model = DecisionSet.model_validate(base_dec_raw)
    rubric = Rubric.model_validate(review['rubricOverride']) if review.get('rubricOverride') else base_rubric
    if rubric.model_dump(mode='json') != base_rubric.model_dump(mode='json'):
        # Same rule the orchestrator applies: weights keep decisions, policy changes drop them.
        model, _info = reuse_decisions(bundle, base_rubric, model, bundle, rubric)
    teacher = review.get('decisions') or {}
    keep = [x for x in model.decisions if x.criterion_id not in teacher]
    fields = ('criterion_id', 'action', 'source', 'level_id', 'evidence_ids', 'rationale', 'feedback')
    if not isinstance(teacher, dict) or any(not isinstance(t, dict) or t.get('source') != 'TEACHER' or t.get('criterion_id') != key for key, t in teacher.items()):
        raise ValueError('INVALID_TEACHER_DECISION_IDENTITY')
    teacher_decisions = [Decision.model_validate({k: v for k, v in t.items() if k in fields}) for t in teacher.values()]
    ds = DecisionSet(evidence_sha256=digest(bundle), rubric_sha256=digest(rubric), decisions=[*keep, *teacher_decisions])
    assessment = assess(bundle, rubric, ds)
    return rubric, ds, assessment


def save_review(d: Path, review: dict):
    rubric, ds, assessment = validate_review(d, review)
    f = d / 'faculty'
    dump(f / 'review.json', review)
    dump(f / 'rubric.json', rubric.model_dump(mode='json'))
    dump(f / 'decisions.json', ds.model_dump(mode='json'))
    dump(f / 'assessment.json', assessment.model_dump(mode='json'))
    return assessment.model_dump(mode='json')


def reassess(d: Path, criteria: list, retry: bool, review: dict):
    from projectiq_core.phase2.contracts import Rubric
    rubric, ds, _ = validate_review(d, {**review, 'decisions': {}})
    work = d / 'faculty' / f'reassess_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    work.mkdir(parents=True)
    dump(work / 'input_rubric.json', rubric.model_dump(mode='json'))
    dump(work / 'input_decisions.json', ds.model_dump(mode='json'))
    ev = d / 'collection' / 'evaluation.json'
    imported = load(d / 'result.json').get('collection_mode') == 'IMPORTED'
    args = [Config.python, '-u', '-m', 'projectiq_core.phase2', 'propose', '--evaluation', str(ev), '--rubric', str(work / 'input_rubric.json'),
            '--decisions', str(work / 'input_decisions.json'), '--criteria', *criteria, '--out', str(work / 'out')]
    if retry:
        args.append('--retry-deferred')
    if imported:
        args.append('--acknowledge-imported')
    env = {k: v for k, v in os.environ.items() if k not in ('PROJECTIQ_BRIDGE_TOKEN', 'VITE_BRIDGE_TOKEN')}
    p = subprocess.run(args, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
    (work / 'log.txt').write_text(p.stdout + p.stderr, encoding='utf-8')
    if not (work / 'out' / 'assessment.json').is_file():
        tail = [x for x in (p.stdout + p.stderr).splitlines() if x.strip()][-3:]
        raise RuntimeError('REASSESSMENT_FAILED: ' + ' | '.join(tail)[:300])
    new = {'rubric': load(work / 'out' / 'rubric.json'), 'decisions': load(work / 'out' / 'decisions.json'),
           'assessment': load(work / 'out' / 'assessment.json'), 'source': work.name, 'at': now()}
    Rubric.model_validate(new['rubric'])
    dump(d / 'faculty' / 'current.json', new)
    messages = []
    log = (work / 'log.txt').read_text(encoding='utf-8')
    if 'Rubric inference failed' in log:
        messages.append('The local rubric model could not be used (' + (log.split('Rubric inference failed', 1)[1].split(':')[-1].strip()[:60] or 'error') + '). Check that Ollama is running, then retry. You can also score the criterion yourself.')
    for c in new['assessment']['criteria']:
        if c['id'] in criteria:
            reason = re.sub(r'\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b', lambda m: m.group(0).replace('_', ' ').lower(), c['reason'])[:160]
            messages.append(f"{c['title']}: " + ('a new proposal was returned.' if c['status'] in ('PROPOSED', 'OBJECTIVE_PROVISIONAL') else f"still {c['status'].lower().replace('_', ' ')}. {reason}"))
    return messages


def record(d: Path):
    result = load(d / 'result.json')
    cur = d / 'faculty' / 'current.json'
    if cur.is_file():
        c = load(cur)
        result['rubric'], result['assessment'] = c['rubric'], c['assessment']
        decisions = c['decisions']
    else:
        decisions = load(d / 'rubric' / 'decisions.json') if (d / 'rubric' / 'decisions.json').is_file() else None
    review_p = d / 'faculty' / 'review.json'
    return {'result': result,
            'evidence': load(d / 'rubric' / 'evidence.json') if (d / 'rubric' / 'evidence.json').is_file() else None,
            'decisions': decisions,
            'meta': load(d / 'faculty_meta.json') if (d / 'faculty_meta.json').is_file() else {},
            'review': load(review_p) if review_p.is_file() else None,
            'engine_assessment': load(d / 'faculty' / 'assessment.json') if (d / 'faculty' / 'assessment.json').is_file() else None}


def summary(d: Path):
    r = record(d)
    a = r['engine_assessment'] or r['result']['assessment']
    meta = r['meta']
    return {'id': d.name, 'title': r['result']['submission']['project_title'], 'submission_id': r['result']['submission']['id'],
            'students': meta.get('students') or [], 'course': meta.get('course'), 'evaluated_utc': meta.get('evaluated_utc'),
            'workflow_status': r['result']['workflow_status'], 'assessment_status': a['status'], 'max_points': a['max_points'],
            'proposed_points': a['proposed_points'], 'reviewed_points': a['reviewed_points'],
            'assessed_weight_percent': a['assessed_weight_percent'], 'reviewed_weight_percent': a['reviewed_weight_percent'],
            'has_repository': bool(r['result']['submission'].get('repository_url')),
            'agent_statuses': {k: v['status'] for k, v in r['result']['agents'].items()}, 'source': 'BACKEND'}


def capabilities():
    try:
        from projectiq_integrity.config import DEFAULT, MODELS_DIR
        ai = 'AVAILABLE' if (Path(MODELS_DIR) / DEFAULT.classifier_model).exists() else 'NOT_INSTALLED'
    except Exception:  # noqa: BLE001
        ai = 'NOT_INSTALLED'
    modes = available_modes(Config.vm_config, Config.demo_enabled)
    notes = ['Evaluations run on this machine with the agents and local models already installed.',
             'One active evaluation or grading operation per bridge. Cancellation stops only at a safe command boundary.']
    if 'TRUSTED_DEMO' in modes:
        notes.append('Automatic testing is enabled for explicitly trusted exact commits only; unsupported projects retain static evidence. No automatic grading credit.')
    if not Config.vm_config:
        notes.append('REVIEWED and RESUME need an operator VM configuration (start the bridge with --vm-config). Existing Executioner results can still be imported.')
    else:
        notes.append('REVIEWED and RESUME run the Executioner separately, then bundle all agent results as acknowledged imported evidence.')
    if ai != 'AVAILABLE':
        notes.append('AI-writing detector model not found; run python scripts/setup_offline.py.')
    return {'name': 'Local ProjectIQ', 'kind': 'http', 'liveExecutionerModes': modes, 'importExecutionerResults': True,
            'onlineResearchVerification': True, 'integrityReferenceUpload': True, 'aiDetection': ai, 'rubricReassessment': True,
            'persistsTeacherDecisions': 'BACKEND', 'notes': notes}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write('bridge: ' + (fmt % args) + '\n')

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', Config.origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-ProjectIQ-Token')
        self.send_header('Vary', 'Origin')
        self.send_header('Cache-Control', 'no-store')

    def permitted(self, *, preflight=False):
        if not authorized(self.headers, port=Config.port, origin=Config.origin, token=Config.token, preflight=preflight):
            self.send_json(403, {'error': 'LOCAL_OPERATOR_AUTHORIZATION_REQUIRED'})
            return False
        return True

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n < 0 or n > MAX_BODY:
            raise ValueError('REQUEST_TOO_LARGE')
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ValueError('JSON_CONTENT_TYPE_REQUIRED')
        obj = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
        if not isinstance(obj, dict):
            raise ValueError('JSON_OBJECT_REQUIRED')
        return obj

    def do_OPTIONS(self):  # noqa: N802
        if not self.permitted(preflight=True):
            return
        self.send_response(204)
        self.cors()
        self.end_headers()

    def parts(self):
        return [p for p in self.path.split('?')[0].split('/') if p]

    def do_GET(self):  # noqa: N802
        if not self.permitted():
            return
        p = self.parts()
        try:
            if p == ['api', 'capabilities']:
                return self.send_json(200, capabilities())
            if p == ['api', 'evaluations']:
                active = {j.eval_id for j in list(JOBS.values()) if j.state != 'COMPLETED'}
                runs = sorted((d for d in Config.root.iterdir() if d.is_dir() and d.name not in active and (d / 'result.json').is_file()),
                              key=lambda d: d.stat().st_mtime, reverse=True) if Config.root.is_dir() else []
                return self.send_json(200, [summary(d) for d in runs])
            if len(p) == 3 and p[:2] == ['api', 'evaluations']:
                return self.send_json(200, record(run_dir(p[2])))
            if len(p) == 3 and p[:2] == ['api', 'runs'] and p[2] in JOBS:
                return self.send_json(200, JOBS[p[2]].status())
            return self.send_json(404, {'error': 'NOT_FOUND'})
        except FileNotFoundError:
            return self.send_json(404, {'error': 'EVALUATION_NOT_FOUND'})
        except Exception as exc:  # noqa: BLE001
            return self.send_json(500, {'error': type(exc).__name__, 'detail': str(exc)[:300]})

    def do_PUT(self):  # noqa: N802
        if not self.permitted():
            return
        p = self.parts()
        try:
            if len(p) == 4 and p[:2] == ['api', 'evaluations'] and p[3] == 'review':
                d = run_dir(p[2])
                review = self.body().get('review') or {}
                with ADMISSION:
                    assessment = save_review(d, review)
                return self.send_json(200, {'review': review, 'assessment': assessment})
            return self.send_json(404, {'error': 'NOT_FOUND'})
        except FileNotFoundError:
            return self.send_json(404, {'error': 'EVALUATION_NOT_FOUND'})
        except BridgeBusy as exc:
            return self.send_json(409, {'error': str(exc)})
        except ValueError as exc:
            # Engine validation codes such as EVIDENCE_COVERAGE_NOT_MET or INELIGIBLE_EVIDENCE.
            return self.send_json(422, {'error': str(exc).split('\n')[0][:300]})
        except Exception as exc:  # noqa: BLE001
            return self.send_json(500, {'error': type(exc).__name__, 'detail': str(exc)[:300]})

    def do_POST(self):  # noqa: N802
        if not self.permitted():
            return
        p = self.parts()
        try:
            if p == ['api', 'evaluations']:
                return self.send_json(202, start(self.body()))
            if len(p) == 4 and p[:2] == ['api', 'runs'] and p[3] == 'cancel' and p[2] in JOBS:
                job = JOBS[p[2]]
                with job.lock:
                    if job.state != 'RUNNING':
                        return self.send_json(409, {'error': 'RUN_ALREADY_FINISHED'})
                    already = job.cancelled
                    job.cancelled = True
                if not already:
                    job.event('Stop requested. The current command will finish safely; no new backend command will start.', 'warn')
                return self.send_json(200, {'ok': True, 'cancel_requested': True})
            if len(p) == 4 and p[:2] == ['api', 'evaluations'] and p[3] == 'reassess':
                d = run_dir(p[2])
                b = self.body()
                criteria = [c for c in b.get('criteria', []) if ID.match(c)]
                if not criteria:
                    raise ValueError('NO_CRITERIA')
                with ADMISSION:
                    review = b.get('review') or (load(d / 'faculty' / 'review.json') if (d / 'faculty' / 'review.json').is_file() else {})
                    messages = reassess(d, criteria, bool(b.get('retry_deferred')), review)
                    review['pendingReassessment'] = [c for c in review.get('pendingReassessment', []) if c not in criteria]
                    review['rubricOverride'] = None  # the reassessed rubric is now the base
                    assessment = save_review(d, review)
                return self.send_json(200, {'review': review, 'assessment': assessment, 'messages': messages})
            return self.send_json(404, {'error': 'NOT_FOUND'})
        except FileNotFoundError:
            return self.send_json(404, {'error': 'EVALUATION_NOT_FOUND'})
        except BridgeBusy as exc:
            return self.send_json(409, {'error': str(exc)})
        except ValueError as exc:
            return self.send_json(422, {'error': str(exc).split('\n')[0][:300]})
        except Exception as exc:  # noqa: BLE001
            return self.send_json(500, {'error': type(exc).__name__, 'detail': str(exc)[:300]})


def write_upload(dest: Path, item: dict) -> str:
    if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('b64'), str):
        raise ValueError('INVALID_UPLOAD')
    name = safe_file_name(item['name'])
    if Path(name).suffix.lower() not in ('.pdf', '.docx', '.txt', '.md', '.json') or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', name.split('.')[0], re.I):
        raise ValueError('INVALID_UPLOAD_FILENAME')
    dest.mkdir(parents=True, exist_ok=True)
    # Exclusive create rejects names that collide after sanitization (including Windows).
    data = base64.b64decode(item['b64'], validate=True)
    try:
        with (dest / name).open('xb') as f:
            f.write(data)
    except FileExistsError:
        raise ValueError('DUPLICATE_UPLOAD_FILENAME') from None
    return name


def start(payload: dict):
    ADMISSION.acquire()
    try:
        return _start_admitted(payload)
    except BaseException:
        ADMISSION.release()
        raise


def _start_admitted(payload: dict):
    req = payload.get('request') or {}
    mode, _ = request_mode(req, payload, vm_config=Config.vm_config, demo_enabled=Config.demo_enabled)
    if not isinstance(req.get('submission_id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', req['submission_id']) or not isinstance(req.get('title'), str) or not req['title'].strip():
        raise ValueError('INVALID_TITLE_OR_SUBMISSION_ID')
    if not payload.get('report'):
        raise ValueError('REPORT_REQUIRED')
    from projectiq_core.phase2.contracts import Rubric
    rubric = Rubric.model_validate(req.get('rubric'))
    run_id = uuid.uuid4().hex[:12]
    eval_id = f"{req['submission_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_id}"
    jobdir = Config.root / '_jobs' / run_id
    files = {'report': write_upload(jobdir / 'input', payload['report']), 'references': []}
    for r in payload.get('references') or []:
        files['references'].append(write_upload(jobdir / 'references', r))
    if payload.get('executioner_import'):
        files['executioner_import'] = write_upload(jobdir / 'input', payload['executioner_import'])
    if payload.get('resume_from'):
        files['resume_from'] = write_upload(jobdir / 'input', payload['resume_from'])
    dump(jobdir / 'rubric.json', rubric.model_dump(mode='json'))
    dump(jobdir / 'request.json', {k: v for k, v in req.items() if k != 'rubric'})
    req['acknowledge_imported'] = bool(payload.get('acknowledge_imported'))
    job = Job(run_id, req['title'], eval_id)
    job.executioner_mode = mode
    JOBS[run_id] = job
    try:
        # Non-daemon: normal Python shutdown waits for the active operation.
        threading.Thread(target=evaluate_job, args=(job, req, files, jobdir), daemon=False).start()
    except BaseException:
        JOBS.pop(run_id, None)
        raise
    return {'run_id': run_id, 'evaluation_id': eval_id}


def main():
    ap = argparse.ArgumentParser(description='ProjectIQ local bridge for the faculty UI')
    ap.add_argument('--root', type=Path, default=REPO_ROOT / 'outputs' / 'unified', help='Directory of unified evaluation outputs')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--allow-origin', default='http://127.0.0.1:5173')
    ap.add_argument('--python', default=sys.executable, help='Python interpreter of the ProjectIQ environment')
    ap.add_argument('--vm-config', type=Path, help='Operator-owned Executioner VM configuration (enables REVIEWED/RESUME)')
    ap.add_argument('--enable-trusted-demo', action='store_true', help='Enable explicit local-operator trusted demo requests only')
    ap.add_argument('--review-root', type=Path)
    ap.add_argument('--review-registry', type=Path)
    ap.add_argument('--work-root', type=Path)
    a = ap.parse_args()
    Config.root = a.root.absolute()
    Config.root.mkdir(parents=True, exist_ok=True)
    Config.python, Config.origin = a.python, a.allow_origin
    Config.port = a.port
    Config.token = os.environ.get('PROJECTIQ_BRIDGE_TOKEN', '')
    if not valid_token(Config.token):
        ap.error('Set PROJECTIQ_BRIDGE_TOKEN to a random 32–128 character URL-safe token.')
    if not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):[0-9]{1,5}', Config.origin):
        ap.error('UI origin must be an explicit HTTP loopback origin.')
    if a.vm_config is not None:
        a.vm_config = a.vm_config.resolve()
        if not a.vm_config.is_file():
            ap.error('VM configuration file does not exist.')
    if a.enable_trusted_demo and a.vm_config is None:
        ap.error('--enable-trusted-demo requires --vm-config.')
    Config.demo_enabled = a.enable_trusted_demo
    Config.vm_config, Config.review_root, Config.review_registry, Config.work_root = a.vm_config, a.review_root, a.review_registry, a.work_root
    print(f'ProjectIQ bridge: {Config.root} at http://127.0.0.1:{a.port} (UI origin {Config.origin})')
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
