"""Local Phase 2E workflow: collect once, grade once, revise only changed policy.

No new grading algorithm, server, network authorization or VM execution path.
The caller supplies trusted local paths and an explicit rubric policy. All output
contains private submission data. The Phase 2 engine remains authoritative.
"""
from __future__ import annotations
import argparse
import hashlib
import math
import sys
from pathlib import Path
from time import perf_counter
from .contracts import AGENTS, Evaluation, Receipt
from .collector import (MAX_JSON, MAX_REPORT, collect, json_bytes, live_sources,
                        load_json, make_submission, read_bounded, validate_identity, write_new)
from .adapters import normalize, skipped
from .phase2.contracts import Rubric, DecisionSet, Assessment
from .phase2.evidence import build_evidence, digest, safe_child
from .phase2.intelligence import focus, runtime_summary
from .phase2.scoring import assess, empty_decisions
from .phase2.research_policy import is_research

DEFAULT_RUBRIC = Path(__file__).parent / 'phase2' / 'software_project_unified.rubric.json'



def research_needed(rubric):
    return any(c.applicability != 'EXCLUDED' and
               (is_research(c) or 'RESEARCH_RECORD' in c.required_evidence)
               for c in rubric.criteria)


def configure_rubric(rubric: Rubric, *, weights=None, criterion_weights=None,
                     total_points=None, research_required=None) -> Rubric:
    """Public configuration seam for a future local frontend/backend.

    Weights are positive relative contributions, not hard-coded percentages.
    For a grouped rubric `weights` addresses groups; otherwise criteria.
    Exclusion, not zero weight, represents non-applicability.
    """
    data = rubric.model_dump(mode='json')
    def checked(mapping, keys):
        if mapping is None:
            return {}
        if not isinstance(mapping, dict) or set(mapping) - set(keys):
            raise ValueError('UNKNOWN_WEIGHT_KEY')
        for value in mapping.values():
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1_000_000:
                raise ValueError('INVALID_WEIGHT')
        return mapping
    rows = {c['id']: c for c in data['criteria']}
    if data['group_weights']:
        data['group_weights'].update(checked(weights, data['group_weights']))
    else:
        for key, value in checked(weights, rows).items():
            rows[key]['weight'] = value
    for key, value in checked(criterion_weights, rows).items():
        rows[key]['weight'] = value
    if total_points is not None:
        if type(total_points) not in (int, float) or not math.isfinite(total_points) or not 0 < total_points <= 1_000_000:
            raise ValueError('INVALID_TOTAL_POINTS')
        data['total_points'] = total_points
    if research_required is not None:
        if type(research_required) is not bool:
            raise ValueError('INVALID_RESEARCH_APPLICABILITY')
        matching = [c.id for c in rubric.criteria if is_research(c)]
        if research_required and not matching:
            raise ValueError('NO_RESEARCH_CRITERION_IN_RUBRIC')
        for key in matching:
            rows[key]['applicability'] = 'REQUIRED' if research_required else 'EXCLUDED'
        if not research_required and any(c['applicability'] != 'EXCLUDED' and 'RESEARCH_RECORD' in c['required_evidence'] for c in rows.values()):
            raise ValueError('ACTIVE_CRITERION_REQUIRES_RESEARCH')
    for c in rubric.criteria:
        if is_research(c):
            row=rows[c.id]
            for key in ('allowed_evidence','required_evidence'):
                row[key]=list(dict.fromkeys([*row[key],'RESEARCH_RECORD']))
    return Rubric.model_validate(data)


def selected_agents(rubric, *, repository_url):
    requested = {'summarizer', 'integrity'}
    if repository_url:
        requested.add('executioner')
    if research_needed(rubric):
        requested.add('research')
    return tuple(a for a in AGENTS if a in requested)


def _policy(c):
    data = c.model_dump(mode='json')
    data.pop('weight')
    return data


def _projection(bundle, c):
    # All eligible evidence of allowed kinds, not just previous citations or
    # the shortlisted top K. Any relevant change requires a fresh judgment.
    items = [e.model_dump(mode='json') for e in bundle.items
             if e.eligible_for_review and e.kind in c.allowed_evidence]
    runtime = runtime_summary(bundle) if (focus(c) == 'FUNCTIONAL' or c.method != 'JUDGMENT') else None
    return digest({'items': items, 'runtime': runtime})


def reuse_decisions(old_bundle, old_rubric, old_decisions, new_bundle, new_rubric,
                    *, retry_deferred=False):
    """Rebind only unchanged criterion policies with unchanged relevant evidence.

    Never carry teacher/model marks across another report or repository revision.
    Model deferrals can be explicitly retried; teacher decisions are never
    silently replaced when their policy and evidence remain unchanged.
    """
    assess(old_bundle, old_rubric, old_decisions, include_details=False)
    identity = ('submission_id', 'report_sha256', 'extracted_text_sha256',
                'report_extraction', 'repository_commit')
    if any(getattr(old_bundle, k) != getattr(new_bundle, k) for k in identity):
        raise ValueError('REUSE_SUBMISSION_OR_REPORT_CHANGED')
    if any(v == 'RECEIPT_OR_CONTRACT_MISMATCH' for v in new_bundle.agent_checks.values()):
        raise ValueError('REUSE_NATIVE_VERIFICATION_FAILED')
    old = {c.id: c for c in old_rubric.criteria}
    new = {c.id: c for c in new_rubric.criteria}
    kept, dropped = [], {}
    for d in old_decisions.decisions:
        cid = d.criterion_id
        if cid not in new or new[cid].applicability == 'EXCLUDED':
            dropped[cid] = 'CRITERION_EXCLUDED_OR_REMOVED'
        elif _policy(old[cid]) != _policy(new[cid]):
            dropped[cid] = 'CRITERION_POLICY_CHANGED'
        elif _projection(old_bundle, old[cid]) != _projection(new_bundle, new[cid]):
            dropped[cid] = 'RELEVANT_EVIDENCE_CHANGED'
        elif retry_deferred and d.source == 'MODEL' and d.action == 'DEFER':
            dropped[cid] = 'EXPLICIT_MODEL_DEFER_RETRY'
        else:
            kept.append(d)
    result = DecisionSet(evidence_sha256=digest(new_bundle), rubric_sha256=digest(new_rubric), decisions=kept)
    # Includes global test overlap and consistency checks. Fail closed on new
    # policy conflicts instead of dropping an arbitrary decision to get a grade.
    assess(new_bundle, new_rubric, result, include_details=False)
    return result, {'preserved_criterion_ids': [d.criterion_id for d in kept],
                    'dropped_decisions': dropped}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot_evaluation(source: Path, destination: Path):
    """Copy only the report and referenced receipts, with exact bytes preserved."""
    source = Path(source).absolute()
    root = source.parent
    if source.is_symlink() or getattr(source, 'is_junction', lambda: False)():
        raise ValueError('LINKED_EVALUATION')
    ev_bytes = read_bounded(source, MAX_JSON)
    ev = Evaluation.model_validate(load_json(source))
    if read_bounded(source, MAX_JSON) != ev_bytes:
        raise ValueError('EVALUATION_CHANGED')
    report_rel = 'input/' + ev.submission.report_name
    if Path(ev.submission.report_name).name != ev.submission.report_name or '\\' in ev.submission.report_name:
        raise ValueError('INVALID_REPORT_NAME')
    report = read_bounded(safe_child(root, report_rel), MAX_REPORT)
    if _sha(report) != ev.submission.report_sha256:
        raise ValueError('REPORT_DIGEST_MISMATCH')
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'input').mkdir()
    (destination/'native').mkdir()
    write_new(destination/report_rel, report)
    for name, result in ev.agents.items():
        if result.native is None:
            continue
        receipt = result.native
        if receipt.path != f'native/{name}.json' or receipt.origin != ev.mode:
            raise ValueError('INVALID_RECEIPT')
        data = read_bounded(safe_child(root, receipt.path), MAX_JSON)
        if _sha(data) != receipt.sha256:
            raise ValueError('NATIVE_DIGEST_MISMATCH')
        write_new(destination/receipt.path, data)
    write_new(destination/'evaluation.json', ev_bytes)
    return ev


def _supplement_research(collection, ev, *, verify_research=False, research_artifact=None):
    """Add only a missing research result to a private new collection snapshot.

    Existing live evidence remains LIVE; imported evidence remains IMPORTED.
    A missing historical imported research result needs explicit import, never
    fabricated live provenance. Existing source run is not modified.
    """
    report = collection/'input'/ev.submission.report_name
    if ev.agents['research'].status not in ('NOT_RUN', 'FAILED'):
        if research_artifact is not None or verify_research:
            raise ValueError('RESEARCH_ALREADY_COLLECTED_REFRESH_NOT_SUPPORTED')
        return ev, False
    if ev.mode == 'IMPORTED' and research_artifact is None:
        raise ValueError('IMPORTED_RESEARCH_MISSING_SUPPLY_RESEARCH_ARTIFACT')
    if ev.mode == 'LIVE' and research_artifact is not None:
        raise ValueError('CANNOT_MIX_IMPORTED_RESEARCH_WITH_LIVE_COLLECTION')
    print('research: collecting only missing research evidence', flush=True)
    data = None
    try:
        if research_artifact is not None:
            raw = load_json(research_artifact)
        else:
            fn = live_sources(ev.submission, agents=('research',), summary_only=True,
                              verify_research=verify_research)['research']
            raw = fn(report, collection)
        validate_identity('research', raw, ev.submission, imported=ev.mode == 'IMPORTED')
        data = json_bytes(raw)
        if len(data) > MAX_JSON:
            raise ValueError('NATIVE_RESULT_SIZE_LIMIT')
        receipt = Receipt(path='native/research.json', sha256=_sha(data), origin=ev.mode)
        result = normalize('research', raw, receipt)
    except Exception as exc:
        if research_artifact is not None:
            raise  # Invalid historical evidence is never silently accepted.
        # Match the existing collector's failure semantics. Other validated
        # evidence and marks survive an unavailable live Research Agent.
        data = None
        result = skipped('research', 'FAILED', type(exc).__name__,
                         'Research collection failed; required evidence may be unresolved.')
    if _sha(read_bounded(report, MAX_REPORT)) != ev.submission.report_sha256:
        raise ValueError('REPORT_CHANGED')
    updated = ev.model_dump(mode='json')
    updated['agents']['research'] = result.model_dump(mode='json')
    updated['limitations'].append('Research result added in a separate collection step; other agent receipts preserved.')
    ev = Evaluation.model_validate(updated)
    # This is a newly created private snapshot, not the original source run.
    path = collection/'native/research.json'
    if path.exists():
        path.unlink()
    if data is not None:
        write_new(path, data)
    p = collection/'evaluation.json'
    tmp = collection/'evaluation.revision.json'
    write_new(tmp, json_bytes(ev.model_dump(mode='json')))
    tmp.replace(p)
    return ev, True


def _frontend_agents(ev, bundle, rubric, *, acknowledge_imported=False):
    """Legacy grade never enters the frontend-facing result, even on import."""
    cards = {}
    for name, agent in ev.agents.items():
        check = bundle.agent_checks.get(name, 'NOT_RUN')
        row = agent.model_dump(mode='json')
        row.pop('native', None)
        row['evidence_check'] = check
        row['receipt_provenance'] = ('NATIVE_RECEIPT_MATCHED' if ev.mode == 'LIVE' else
            'IMPORTED_ACKNOWLEDGED' if acknowledge_imported else 'IMPORTED_UNCONFIRMED') if check == 'MATCHED' else None
        if check != 'MATCHED':
            row['metrics'] = []
            row['details'] = {}
            if name == 'integrity':
                row['ai_detection'] = skipped('integrity').ai_detection.model_dump(mode='json')
        if name == 'summarizer':
            row['metrics'] = [m for m in row['metrics'] if m['id'] == 'word_count']
            row['details'] = {k: v for k, v in row['details'].items()
                              if k in ('summary', 'result_kind', 'model', 'word_count', 'chunks', 'elapsed_s')}
            row['limitations'] = ['Summary-only overview; generated content is advisory and never contributes to the project grade. A missing historical summary is unavailable until separately collected.']
        if name == 'research':
            row['required_by_rubric'] = research_needed(rubric)
        cards[name] = row
    return cards


def evaluate(*, out: Path, rubric: Rubric, report=None, title=None, submission_id=None,
             repository_url=None, ref=None, references=None, evaluation=None,
             previous_assessment=None, acknowledge_imported=False,
             verify_research=False, research_artifact=None, preview=False,
             objective_only=False, retry_deferred=False):
    """Synchronous local orchestration API; result is a private versioned JSON.

    Caller must provide a new output directory; no old artifacts are modified.
    A complete proposed score is never substituted for an unresolved score.
    """
    from .phase2.__main__ import main as grade_main
    start = perf_counter()
    if (report is None) == (evaluation is None):
        raise ValueError('CHOOSE_REPORT_OR_SAVED_EVALUATION')
    if preview and objective_only:
        raise ValueError('CHOOSE_PREVIEW_OR_OBJECTIVE_ONLY')
    if previous_assessment is not None and evaluation is None:
        raise ValueError('REUSE_REQUIRES_SAVED_EVALUATION')
    if retry_deferred and previous_assessment is None:
        raise ValueError('RETRY_REQUIRES_PREVIOUS_ASSESSMENT')
    if verify_research and not research_needed(rubric):
        raise ValueError('RESEARCH_VERIFICATION_REQUIRES_RESEARCH')
    if evaluation is not None and any(x is not None for x in (title, submission_id, repository_url, ref, references)):
        raise ValueError('SAVED_EVALUATION_IDENTITY_MUST_NOT_BE_OVERRIDDEN')
    if report is not None and (research_artifact is not None or acknowledge_imported):
        raise ValueError('IMPORT_OPTIONS_REQUIRE_SAVED_EVALUATION')
    if research_artifact is not None and not research_needed(rubric):
        raise ValueError('RESEARCH_ARTIFACT_REQUIRES_ACTIVE_RESEARCH')
    out = Path(out).absolute()
    if out.exists():
        raise ValueError('OUTPUT_ALREADY_EXISTS')
    old_bundle = old_rubric = old_decisions = None
    migrated_research = {}
    if evaluation is not None:
        evaluation = Path(evaluation).absolute()
        if out.resolve().is_relative_to(evaluation.parent.resolve()):
            raise ValueError('OUTPUT_MUST_NOT_BE_INSIDE_SOURCE_COLLECTION')
        if previous_assessment is not None:
            previous_assessment = Path(previous_assessment)
            old_rubric = Rubric.model_validate(load_json(previous_assessment/'rubric.json'))
            old_decisions = DecisionSet.model_validate(load_json(previous_assessment/'decisions.json'))
    else:
        if not title or not submission_id:
            raise ValueError('FRESH_EVALUATION_REQUIRES_TITLE_AND_SUBMISSION_ID')
        if references is not None and not Path(references).is_dir():
            raise ValueError('REFERENCE_DIRECTORY_NOT_FOUND')
        sub, data = make_submission(Path(report), title, submission_id, repository_url, ref)
    out.mkdir(parents=True, exist_ok=False)
    collection = out/'collection'
    actions = {}
    bundle = None
    if evaluation is None:
        requested = selected_agents(rubric, repository_url=repository_url)
        funcs = live_sources(sub, references=references, agents=requested, summary_only=True,
                             verify_research=verify_research)
        ev = collect(submission=sub, report_bytes=data, out=collection, mode='LIVE', sources=funcs)
        actions = {a: 'COLLECTED' if a in funcs else 'SKIPPED' for a in AGENTS}
    else:
        ev = snapshot_evaluation(evaluation, collection)
        old_bundle = build_evidence(collection/'evaluation.json', acknowledge_imported=acknowledge_imported)
        if any(v == 'RECEIPT_OR_CONTRACT_MISMATCH' for v in old_bundle.agent_checks.values()):
            raise ValueError('SAVED_NATIVE_VERIFICATION_FAILED')
        bundle = old_bundle
        if old_decisions is not None:
            if old_decisions.evidence_sha256 != digest(old_bundle):
                # Rebuild the exact previous normalization from verified receipts.
                # No arbitrary digest rewrite or trust in a saved evidence JSON.
                legacy = build_evidence(collection/'evaluation.json', acknowledge_imported=acknowledge_imported, legacy_research=True)
                if old_decisions.evidence_sha256 != digest(legacy) or old_decisions.rubric_sha256 != digest(old_rubric):
                    raise ValueError('STALE_DECISIONS')
                # Old research judgments lacked the new policy; invalidate them,
                # preserving only independently revalidated unrelated decisions.
                research_ids={c.id for c in old_rubric.criteria if is_research(c)}
                migrated_research={d.criterion_id:'RESEARCH_POLICY_UPGRADED' for d in old_decisions.decisions if d.criterion_id in research_ids}
                old_decisions=DecisionSet(evidence_sha256=digest(legacy),rubric_sha256=digest(old_rubric),decisions=[d for d in old_decisions.decisions if d.criterion_id not in research_ids])
                old_bundle=legacy
            assess(old_bundle, old_rubric, old_decisions, include_details=False)
        actions = {a: 'REUSED' if ev.agents[a].status not in ('NOT_RUN', 'FAILED') else 'NOT_AVAILABLE' for a in AGENTS}
        if research_needed(rubric):
            try:
                if ev.mode == 'IMPORTED' and research_artifact is not None and not acknowledge_imported:
                    raise ValueError('IMPORTED_RESEARCH_REQUIRES_ACKNOWLEDGEMENT')
                ev, added = _supplement_research(collection, ev, verify_research=verify_research,
                                                 research_artifact=research_artifact)
                if added:
                    bundle = None
                    actions['research'] = 'IMPORTED' if ev.mode == 'IMPORTED' else 'COLLECTED'
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                # Do not manufacture an absent or failed research grade. Retain
                # collection for inspection, mark orchestration failure explicitly.
                write_new(out/'workflow_error.json', json_bytes({'stage':'research','error_type':type(exc).__name__,
                    'code':str(exc) if str(exc).isupper() and len(str(exc))<120 else 'RESEARCH_SUPPLEMENT_FAILED'}))
                raise
    eval_path = collection/'evaluation.json'
    if bundle is None:
        bundle = build_evidence(eval_path, acknowledge_imported=acknowledge_imported)
    if old_decisions is not None:
        decisions, reuse = reuse_decisions(old_bundle, old_rubric, old_decisions, bundle, rubric,
                                           retry_deferred=retry_deferred)
    else:
        decisions = empty_decisions(bundle, rubric)
        reuse = {'preserved_criterion_ids': [], 'dropped_decisions': {}}
    reuse['dropped_decisions'].update(migrated_research)
    initial = assess(bundle, rubric, decisions, include_details=False)
    # Preserve a complete, validated configuration before any inference.
    write_new(out/'configured_rubric.json', json_bytes(rubric.model_dump(mode='json')))
    write_new(out/'initial_decisions.json', json_bytes(decisions.model_dump(mode='json')))
    grade_out = out/'rubric'
    argv = ['assess' if objective_only or (not preview and not initial.unresolved_criterion_ids) else 'propose-all',
            '--evaluation', str(eval_path), '--rubric', str(out/'configured_rubric.json'),
            '--decisions', str(out/'initial_decisions.json'), '--out', str(grade_out)]
    if acknowledge_imported:
        argv.append('--acknowledge-imported')
    if preview:
        argv.append('--preview')
    # Exactly the established Phase 2 runner: bounded batches, recovery, model cleanup.
    grade_code = grade_main(argv)
    if not (grade_out/'assessment.json').is_file():
        write_new(out/'workflow_error.json', json_bytes({'stage':'rubric','code':'ASSESSMENT_NOT_SAVED','exit_code':grade_code}))
        return 1
    final = Assessment.model_validate(load_json(grade_out/'assessment.json'))
    final_decisions = DecisionSet.model_validate(load_json(grade_out/'decisions.json'))
    expected = assess(bundle, rubric, final_decisions)
    if final.model_dump(mode='json') != expected.model_dump(mode='json'):
        raise ValueError('ASSESSMENT_REVALIDATION_FAILED')
    failures = [a for a in AGENTS if ev.agents[a].status == 'FAILED' or bundle.agent_checks.get(a) == 'RECEIPT_OR_CONTRACT_MISMATCH']
    result = {'schema_version':'2E.1', 'submission':ev.submission.model_dump(mode='json'),
              'collection_mode':ev.mode, 'agent_actions':actions,
              'agents':_frontend_agents(ev, bundle, rubric, acknowledge_imported=acknowledge_imported),
              'rubric':rubric.model_dump(mode='json'), 'assessment':final.model_dump(mode='json'),
              'evidence_intelligence':load_json(grade_out/'evidence_intelligence.json'),
              'decision_reuse':reuse, 'research_required':research_needed(rubric),
              'legacy_judge_executed':False, 'rubric_mode':'PREVIEW' if preview else 'OBJECTIVE_ONLY' if objective_only else 'AUTOMATIC',
              'workflow_status':'PARTIAL_ERROR' if grade_code or failures else 'COMPLETE' if not final.unresolved_criterion_ids else 'INCOMPLETE_ASSESSMENT',
              'agent_failures':failures, 'rubric_exit_code':grade_code,
              'elapsed_seconds':round(perf_counter()-start,4),
              'artifacts':{'evaluation':'collection/evaluation.json','assessment':'rubric/assessment.json','rubric':'rubric/rubric.json',
                           'decisions':'rubric/decisions.json','evidence':'rubric/evidence.json','intelligence':'rubric/evidence_intelligence.json'},
              'limitations':list(dict.fromkeys([*[x for x in ev.limitations if x != 'No final project grade is calculated in Phase 1.'],*bundle.limitations,*final.limitations,
                  'Private local evaluation; no unrestricted execution or authenticated faculty approval.',
                  'Live Executioner collection is STATIC; runtime marks require eligible reviewed evidence.',
                  'Legacy judge never executed by this workflow. Imported historical summaries may have been produced by that judge.']))}
    write_new(out/'result.json', json_bytes(result))
    print('Unified result:', out/'result.json', flush=True)
    return 1 if grade_code else 2 if failures else 0


def add_evaluate_parser(sub):
    p = sub.add_parser('evaluate', help='Collect necessary agents and automatically run the hybrid rubric')
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--report', type=Path)
    src.add_argument('--evaluation', type=Path, help='Reuse a saved collection; no agent rerun except missing required research')
    p.add_argument('--title'); p.add_argument('--submission-id'); p.add_argument('--repo'); p.add_argument('--ref')
    p.add_argument('--references', type=Path); p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rubric', type=Path, help='Full faculty rubric JSON; defaults to generic software rubric or previous rubric')
    p.add_argument('--weights', type=Path, help='JSON object of positive group weights (criterion IDs for flat rubrics)')
    p.add_argument('--criterion-weights', type=Path, help='JSON object of positive subcriterion weights')
    p.add_argument('--total-points', type=float)
    research = p.add_mutually_exclusive_group()
    research.add_argument('--research-required', dest='research_required', action='store_true')
    research.add_argument('--no-research', dest='research_required', action='store_false')
    p.set_defaults(research_required=None)
    p.add_argument('--summary', action='store_true', help=argparse.SUPPRESS)  # compatibility; summary is always collected on fresh runs
    p.add_argument('--verify-research', action='store_true', help='Opt into online verification for newly collected research')
    p.add_argument('--previous-assessment', type=Path, help='Directory with old rubric.json and decisions.json for same saved evaluation')
    p.add_argument('--acknowledge-imported', action='store_true')
    p.add_argument('--research-artifact', type=Path, help='Explicit research import for a historical imported collection missing research')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--preview', action='store_true', help='No rubric LLM calls; fresh collection can still run selected agents')
    mode.add_argument('--objective-only', action='store_true', help='Calculate objective and preserved marks without rubric LLM')
    p.add_argument('--retry-deferred', action='store_true', help='Explicitly retry preserved MODEL deferrals only')


def evaluate_args(args):
    base = args.rubric or (args.previous_assessment/'rubric.json' if args.previous_assessment else DEFAULT_RUBRIC)
    rubric = configure_rubric(Rubric.model_validate(load_json(base)),
        weights=load_json(args.weights) if args.weights else None,
        criterion_weights=load_json(args.criterion_weights) if args.criterion_weights else None,
        total_points=args.total_points, research_required=args.research_required)
    return evaluate(out=args.out, rubric=rubric, report=args.report, title=args.title,
        submission_id=args.submission_id, repository_url=args.repo, ref=args.ref,
        references=args.references, evaluation=args.evaluation, previous_assessment=args.previous_assessment,
        acknowledge_imported=args.acknowledge_imported,
        verify_research=args.verify_research, research_artifact=args.research_artifact,
        preview=args.preview, objective_only=args.objective_only, retry_deferred=args.retry_deferred)
