import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { backend, type BackendCapabilities } from '../../api';
import type { Criterion, Evidence, Feedback, Rubric, TeacherDecisionEntry, TeacherReview } from '../../api/types';
import { EvidenceCard } from '../../components/EvidenceCard';
import { CoverageMeter, GradeHeadline, SectionRings, segClass } from '../../components/Ledger';
import { Badge, Callout, CriterionStatusBadge, Drawer, More, Tip } from '../../components/ui';
import { EVIDENCE_KIND, FLAG_TEXT, engineMessage, humanize, reasonText } from '../../lib/labels';
import { clone, isResearch, validateRubric } from '../../lib/rubric';
import { policyChanges, recompute, type RecomputedCriterion } from '../../lib/scoring';
import { fmtPoints, type ViewModel } from '../../lib/viewModel';
import RubricEditor from '../configure/RubricEditor';

function LevelStrip({ crit, c }: { crit: Criterion; c: RecomputedCriterion }) {
  if (!crit.levels.length) return null;
  return (
    <div className="levels" role="list" aria-label="Grading levels">
      {crit.levels.map((l) => {
        const sel = c.level_id === l.id && c.fraction !== null;
        const cls = sel ? (c.status === 'TEACHER_REVIEWED' ? 'sel-reviewed' : 'sel-proposed') : c.origin === 'TEACHER' && c.proposal?.level_id === l.id ? 'was-proposed' : '';
        return (
          <Tip key={l.id} text={<><strong>{l.label} ({Math.round(l.fraction * 100)}%)</strong><br />{l.descriptor}</>}>
            <span className={`lvl ${cls}`} role="listitem" style={{ display: 'block', width: '100%' }}>{l.label}</span>
          </Tip>
        );
      })}
    </div>
  );
}

function FeedbackList({ f }: { f: Feedback | null }) {
  if (!f) return null;
  return (
    <dl className="fb">
      <dt>Strength</dt><dd>{f.strength}</dd>
      <dt>Gap</dt><dd>{f.limitation}</dd>
      <dt>Improve by</dt><dd>{f.improvement}</dd>
    </dl>
  );
}

function CriterionCard({ c, crit, vm, onOpen, onQuickApprove }: { c: RecomputedCriterion; crit: Criterion; vm: ViewModel; onOpen: () => void; onQuickApprove: () => void }) {
  const t = vm.rec.review.decisions[c.id];
  const pending = vm.rec.review.pendingReassessment.includes(c.id);
  const kinds = [...new Set(c.evidence_ids.map((id) => vm.evidenceById.get(id)?.kind).filter(Boolean))] as Evidence['kind'][];
  const cls = c.status === 'EXCLUDED' ? 'excluded' : segClass(c);
  const pointsCls = c.status === 'TEACHER_REVIEWED' ? 'ink' : c.status === 'PROPOSED' ? 'pencil' : '';
  return (
    <article className={`crit ${cls}`} id={`crit-${c.id}`}>
      <div style={{ minWidth: 0 }}>
        <div className="row small muted" style={{ gap: 8 }}><span>{humanize(c.group)}</span><span aria-hidden>/</span><span>{c.effective_weight_percent}% of the grade</span>
          {crit.applicability === 'OPTIONAL' && <Badge tone="muted">Optional</Badge>}{isResearch(crit) && <Badge tone="info">Research</Badge>}</div>
        <h3 style={{ marginTop: 4 }}>{c.title}</h3>
        <p className="small muted" style={{ margin: '2px 0 0' }}>{crit.description}</p>
        {c.status !== 'EXCLUDED' && <LevelStrip crit={crit} c={c} />}
        {c.status === 'UNASSESSED' && (
          <Callout tone="warn" title={pending ? 'Needs reassessment after your rubric change' : 'Not assessed yet'}>
            {pending ? 'The policy for this criterion changed, so its earlier mark no longer applies. Reassess it with the rubric model or score it yourself.' : reasonText(c.reason)}
            {' '}It is not counted as zero.
          </Callout>
        )}
        {c.status !== 'UNASSESSED' && c.status !== 'EXCLUDED' && (c.feedback ? <FeedbackList f={c.feedback} /> : <p className="small mt">{reasonText(c.reason)}</p>)}
        {c.status === 'EXCLUDED' && <p className="small muted mt">{c.reason} Its weight is shared among the applicable criteria.</p>}
        {c.review_flags.map((f) => <p key={f} className="small c-warn" style={{ margin: '8px 0 0' }}>{FLAG_TEXT[f] ?? humanize(f)}</p>)}
        {t && (
          <div className="remark">
            <div className="who">{t.basis === 'APPROVED_PROPOSAL' ? 'You approved the AI proposal' : t.basis === 'OVERRIDE' ? `You changed the AI proposal${c.proposal?.level_id ? ` from ${crit.levels.find((l) => l.id === c.proposal?.level_id)?.label}` : ''}` : 'You scored this criterion'}</div>
            {t.remarks || <span className="muted">No remarks.</span>}
          </div>
        )}
        {c.evidence_ids.length > 0 && (
          <div className="f3-evidence">
            <div className="row tiny muted" style={{ gap: 6 }}>Cites {c.evidence_ids.length} evidence item{c.evidence_ids.length > 1 ? 's' : ''}: {kinds.map((k) => <Badge key={k} tone="muted">{EVIDENCE_KIND[k].label}</Badge>)}</div>
            <More summary="Inspect cited evidence before approving"><div className="stack">{c.evidence_ids.map((id) => {const ev=vm.evidenceById.get(id); return ev ? <EvidenceCard key={id} e={ev} /> : <p className="small muted" key={id}>Evidence {id} is unavailable in the saved bundle.</p>;})}</div><p className="tiny muted">A citation provides traceability, not proof that the model interpreted it correctly. Review the underlying observation or excerpt.</p></More>
          </div>
        )}
      </div>
      <div className="crit-mark">
        <CriterionStatusBadge status={c.status} />
        {c.status === 'EXCLUDED' ? <span className="small muted">Not applicable</span> : c.earned_points === null
          ? <span className="crit-points none">Not assessed</span>
          : <span className={`crit-points ${pointsCls}`}>{fmtPoints(c.earned_points)}<span className="of"> / {fmtPoints(c.max_points)}</span></span>}
        {c.status !== 'EXCLUDED' && c.earned_points === null && <span className="tiny muted">out of {fmtPoints(c.max_points)}</span>}
        <div className="stack" style={{ gap: 6, marginTop: 'auto', alignItems: 'flex-end' }}>
          {(c.status === 'PROPOSED' || c.status === 'OBJECTIVE_PROVISIONAL') && <button className="btn approve sm" onClick={onQuickApprove}>Approve</button>}
          {c.status !== 'EXCLUDED' && <button className="btn sm" onClick={onOpen}>{c.status === 'UNASSESSED' ? 'Assess' : c.status === 'TEACHER_REVIEWED' ? 'Edit decision' : 'Review or change'}</button>}
        </div>
      </div>
    </article>
  );
}

function approveEntry(c: RecomputedCriterion, crit: Criterion, remarks = ''): TeacherDecisionEntry {
  const judgment = crit.method === 'JUDGMENT';
  return {
    criterion_id: c.id, source: 'TEACHER', action: judgment ? 'SCORE' : 'CONFIRM_TEST_RATIO', level_id: judgment ? c.level_id : null,
    evidence_ids: judgment ? c.evidence_ids : [], rationale: remarks || 'Approved the proposed level after reviewing the cited evidence.',
    feedback: judgment ? c.feedback : null, remarks, basis: 'APPROVED_PROPOSAL', decided_utc: new Date().toISOString(),
  };
}

function ReviewDrawer({ vm, c, crit, onClose, onSaved, caps, reload }: { vm: ViewModel; c: RecomputedCriterion; crit: Criterion; onClose: () => void; onSaved: (r: TeacherReview, msg?: string) => void; caps: BackendCapabilities | null; reload: () => void }) {
  const existing = vm.rec.review.decisions[c.id];
  const proposalLevel = c.proposal?.level_id ?? (c.origin !== 'TEACHER' ? c.level_id : null);
  const eligible = useMemo(() => (vm.rec.evidence?.items ?? []).filter((e) => e.eligible_for_review && (crit.allowed_evidence as string[]).includes(e.kind)), [vm, crit]);
  const [level, setLevel] = useState<string | null>(existing?.level_id ?? c.level_id ?? null);
  const [picked, setPicked] = useState<string[]>(existing?.evidence_ids ?? (c.evidence_ids.length ? c.evidence_ids : []));
  const [remarks, setRemarks] = useState(existing?.remarks ?? '');
  const [fb, setFb] = useState<Feedback>(existing?.feedback ?? c.feedback ?? { strength: '', limitation: '', improvement: '', observed_failure: false });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // With nothing cited yet (an unassessed criterion), start by listing all eligible evidence.
  const [showAll, setShowAll] = useState(!(existing?.evidence_ids?.length || c.evidence_ids.length));
  const modelDecision = vm.rec.decisions?.decisions.find((d) => d.criterion_id === c.id);
  const pending = vm.rec.review.pendingReassessment.includes(c.id);
  const judgment = crit.method === 'JUDGMENT';
  const isOverride = !!proposalLevel && level !== proposalLevel;

  const pickedEv = picked.map((id) => vm.evidenceById.get(id)).filter(Boolean) as Evidence[];
  const coverageProblems: string[] = [];
  if (judgment) {
    if (!level) coverageProblems.push('Choose a level.');
    if (pickedEv.length < crit.minimum_evidence) coverageProblems.push(`Cite at least ${crit.minimum_evidence} evidence item${crit.minimum_evidence > 1 ? 's' : ''}.`);
    const missingKinds = crit.required_evidence.filter((k) => !pickedEv.some((e) => e.kind === k));
    if (missingKinds.length) coverageProblems.push(`This criterion requires: ${missingKinds.map((k) => EVIDENCE_KIND[k].label.toLowerCase()).join(', ')}.`);
    if ((isOverride || !proposalLevel) && remarks.trim().length < 8) coverageProblems.push('Add a short remark explaining your decision.');
  }
  const preview = useMemo(() => {
    if (!judgment || !level) return null;
    const lv = crit.levels.find((l) => l.id === level);
    return lv ? lv.fraction * c.max_points : null;
  }, [level, crit, c, judgment]);

  const [saveErr, setSaveErr] = useState<string | null>(null);
  const save = async (entry: TeacherDecisionEntry) => {
    setBusy(true); setSaveErr(null);
    try { onSaved(await backend.saveTeacherDecision(vm.rec.id, entry), 'Decision saved and validated.'); onClose(); }
    catch (e) { setSaveErr(engineMessage(e)); } finally { setBusy(false); }
  };
  const submit = () => save({
    criterion_id: c.id, source: 'TEACHER', action: 'SCORE', level_id: level, evidence_ids: picked,
    rationale: remarks.trim() || 'Approved the proposed level after reviewing the cited evidence.', feedback: fb.strength.trim() && fb.limitation.trim() && fb.improvement.trim() ? fb : null,
    remarks: remarks.trim(), basis: !proposalLevel ? 'TEACHER_SCORED' : isOverride ? 'OVERRIDE' : 'APPROVED_PROPOSAL', decided_utc: new Date().toISOString(),
  });
  const reassess = async (retry: boolean) => {
    setBusy(true); setMsg(null);
    try { const r = await backend.reassess(vm.rec.id, [c.id], { retryDeferred: retry }); onSaved(r.updated); setMsg(r.messages.join(' ')); if (r.reload) reload(); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };

  const missingRequired = crit.required_evidence.filter((k) => !pickedEv.some((e) => e.kind === k));
  // Put evidence of a still-missing required kind first, so it is easy to find and tick.
  // Rarer missing kinds (for example research records) come before plentiful ones like report excerpts.
  const kindCount = (k: string) => eligible.filter((e) => e.kind === k).length;
  const rank = (k: string) => ((missingRequired as string[]).includes(k) ? kindCount(k) : 1e9);
  // Fixed when the drawer opens, so items do not jump while the teacher ticks them.
  const [ordered] = useState(() => [...eligible].sort((a, b) => rank(a.kind) - rank(b.kind)));
  const shown = showAll ? ordered : ordered.filter((e) => picked.includes(e.id) || c.evidence_ids.includes(e.id));
  return (
    <Drawer label={`Review ${c.title}`} onClose={onClose}
      head={<>
        <div className="row between"><span className="small muted">{humanize(c.group)}, {c.effective_weight_percent}% of the grade, {fmtPoints(c.max_points)} marks</span><button className="btn ghost sm" onClick={onClose}>Close</button></div>
        <h2 style={{ marginTop: 4 }}>{c.title}</h2>
        <p className="small muted" style={{ margin: '4px 0 0' }}>{crit.description}</p>
      </>}
      foot={<>
        {(saveErr || (judgment && coverageProblems.length > 0)) && (
          <span role="status" className={`small ${saveErr ? 'c-bad' : 'c-warn'}`} style={{ flex: 1, alignSelf: 'center' }}>
            {saveErr ?? <>To save: {coverageProblems.join(' ')}{missingRequired.length > 0 && !showAll && <> <button className="btn ghost sm" onClick={() => setShowAll(true)}>Show all evidence</button></>}</>}
          </span>
        )}
        {existing && <button className="btn ghost" disabled={busy} onClick={async () => { onSaved(await backend.clearTeacherDecision(vm.rec.id, c.id), 'Returned to the AI proposal.'); onClose(); }}>Undo my decision</button>}
        {crit.applicability === 'OPTIONAL' && <button className="btn" disabled={busy || remarks.trim().length < 8} title="Add a remark first" onClick={() => save({ criterion_id: c.id, source: 'TEACHER', action: 'EXCLUDE', evidence_ids: [], rationale: remarks.trim(), remarks: remarks.trim(), basis: 'TEACHER_SCORED', decided_utc: new Date().toISOString() })}>Mark not applicable</button>}
        {!judgment && c.fraction !== null && <button className="btn approve" disabled={busy} onClick={() => save(approveEntry(c, crit, remarks.trim()))}>Confirm objective result</button>}
        {judgment && <button className={`btn ${isOverride || !proposalLevel ? 'primary' : 'approve'}`} disabled={busy || coverageProblems.length > 0} onClick={submit}>
          {!proposalLevel ? 'Save my mark' : isOverride ? 'Save override' : 'Approve proposal'}</button>}
      </>}>
      {c.status === 'UNASSESSED' && (
        <Callout tone="warn" title="Why there is no proposal">
          {pending ? 'You changed this criterion after evaluation, so the earlier mark was discarded.' : reasonText(c.reason)}
        </Callout>
      )}
      {msg && <div className="mt"><Callout tone="info">{msg}</Callout></div>}
      {saveErr && <div className="mt"><Callout tone="bad" title="Not saved">{saveErr}</Callout></div>}
      {(pending || modelDecision?.action === 'DEFER') && (
        <div className="row mt">
          {pending && <button className="btn" disabled={busy || !caps?.rubricReassessment} onClick={() => reassess(false)}>Reassess with the rubric model</button>}
          {!pending && modelDecision?.action === 'DEFER' && <button className="btn" disabled={busy || !caps?.rubricReassessment} onClick={() => reassess(true)}>Retry the AI judgment</button>}
          <span className="tiny muted">Runs the rubric model for this criterion only. No agents are rerun.</span>
        </div>
      )}

      {proposalLevel && c.proposal && (
        <section className="mt-l">
          <h3>AI proposal</h3>
          <div className="sub mt" style={{ borderStyle: 'dashed', borderColor: 'var(--pencil)', background: 'var(--pencil-bg)' }}>
            <div className="row between">
              <span><Badge tone="pencil">{crit.levels.find((l) => l.id === proposalLevel)?.label ?? proposalLevel}</Badge> <span className="small">{fmtPoints((c.proposal.fraction ?? 0) * c.max_points)} of {fmtPoints(c.max_points)} marks</span></span>
              <span className="tiny muted">Proposed from cited evidence; not a mark until approved</span>
            </div>
            <FeedbackList f={vm.rec.result.assessment.criteria.find((x) => x.id === c.id)?.feedback ?? null} />
            {c.review_flags.map((f) => <p key={f} className="small c-warn" style={{ margin: '8px 0 0' }}>{FLAG_TEXT[f] ?? humanize(f)}</p>)}
          </div>
        </section>
      )}

      {judgment && (
        <section className="mt-l">
          <h3>Your decision</h3>
          <div className="choice-cards mt">
            {crit.levels.map((l) => (
              <label key={l.id} className={`choice ${level === l.id ? 'on' : ''}`}>
                <input type="radio" name="level" checked={level === l.id} onChange={() => setLevel(l.id)} />
                <div style={{ flex: 1 }}>
                  <div className="row between"><strong>{l.label}</strong><span className="small muted">{Math.round(l.fraction * 100)}%, {fmtPoints(l.fraction * c.max_points)} marks{proposalLevel === l.id && <> <Badge tone="pencil">AI proposal</Badge></>}</span></div>
                  <div className="small muted">{l.descriptor}</div>
                </div>
              </label>
            ))}
          </div>
          {preview !== null && <p className="small mt">This criterion would contribute <strong>{fmtPoints(preview)}</strong> of {fmtPoints(c.max_points)} marks.</p>}
          <label className="f mt">Remarks{(isOverride || !proposalLevel) ? ' (required)' : ' (optional)'}<span className="hint">Shown in the report next to the mark. Explain what you saw in the evidence.</span>
            <textarea className="in" maxLength={2000} value={remarks} onChange={(e) => setRemarks(e.target.value)} placeholder={isOverride ? 'Why does a different level fit better?' : 'For example: confirmed against the test table in section 8.'} /></label>
          <More summary="Edit the feedback the student will see" open={!proposalLevel}>
            <div className="stack">
              <label className="f">Strength<textarea className="in" maxLength={220} value={fb.strength} onChange={(e) => setFb({ ...fb, strength: e.target.value })} /></label>
              <label className="f">Gap<textarea className="in" maxLength={220} value={fb.limitation} onChange={(e) => setFb({ ...fb, limitation: e.target.value })} /></label>
              <label className="f">Improvement<textarea className="in" maxLength={220} value={fb.improvement} onChange={(e) => setFb({ ...fb, improvement: e.target.value })} /></label>
            </div>
          </More>
          {saveErr && <div className="mt"><Callout tone="bad" title="Not saved">{saveErr}</Callout></div>}
        </section>
      )}
      {!judgment && (
        <section className="mt-l"><h3>Objective result</h3><p className="small">{reasonText(c.reason)}</p>
          <pre className="sub small" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(c.objective_details, null, 2)}</pre>
          <label className="f mt">Remarks (optional)<textarea className="in" value={remarks} onChange={(e) => setRemarks(e.target.value)} /></label></section>
      )}

      <section className="mt-l">
        <div className="row between"><h3>Evidence</h3>
          {judgment && <button className="btn ghost sm" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show cited evidence only' : `Show all ${eligible.length} eligible items`}</button>}</div>
        <p className="small muted">Allowed for this criterion: {crit.allowed_evidence.map((k) => EVIDENCE_KIND[k].label.toLowerCase()).join(', ')}. Required: {crit.required_evidence.map((k) => EVIDENCE_KIND[k].label.toLowerCase()).join(', ') || 'none'}.</p>
        {!shown.length && <Callout tone="muted">{eligible.length ? 'No evidence cited yet. Show all eligible items to choose some.' : 'No eligible evidence of the allowed kinds exists in this evaluation. The criterion cannot be scored until the evidence is collected.'}</Callout>}
        {shown.map((e) => (
          <div key={e.id} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap', gap: 10, marginTop: 10 }}>
            {judgment && <input type="checkbox" style={{ marginTop: 16, width: 16, height: 16, accentColor: 'var(--ink)' }} aria-label={`Cite ${e.label}`} checked={picked.includes(e.id)}
              onChange={(ev) => setPicked(ev.target.checked ? [...picked, e.id] : picked.filter((x) => x !== e.id))} />}
            <div style={{ flex: 1, minWidth: 0 }}><EvidenceCard e={e} /></div>
          </div>
        ))}
      </section>
    </Drawer>
  );
}

function RubricDrawer({ vm, onClose, onSaved }: { vm: ViewModel; onClose: () => void; onSaved: (r: TeacherReview, msg: string) => void }) {
  const original = vm.rec.result.rubric;
  const [draft, setDraft] = useState<Rubric>(clone(vm.rubric));
  const [err, setErr] = useState<string | null>(null);
  const issues = validateRubric(draft);
  const changes = policyChanges(original, draft);
  const assessed = vm.assessment.criteria.filter((c) => c.fraction !== null).map((c) => c.id);
  const invalidated = [...new Set([...changes.invalidated.filter((id) => assessed.includes(id) || vm.rec.review.decisions[id]), ...changes.includedNow])];
  const researchAgentMissing = draft.criteria.some((c) => isResearch(c) && c.applicability !== 'EXCLUDED') && ['NOT_RUN', 'FAILED'].includes(vm.rec.result.agents.research.status);
  let projected: ReturnType<typeof recompute> | null = null;
  try { if (!issues.length) projected = recompute({ base: vm.rec.result.assessment, rubric: draft, teacher: Object.fromEntries(Object.entries(vm.rec.review.decisions).filter(([k]) => !invalidated.includes(k))), invalidated: [...vm.rec.review.pendingReassessment, ...invalidated] }); } catch { projected = null; }
  return (
    <Drawer label="Edit rubric after evaluation" onClose={onClose}
      head={<>
        <div className="row between"><h2>Adjust rubric, weights and marks</h2><button className="btn ghost sm" onClick={onClose}>Close</button></div>
        <p className="small muted" style={{ margin: '4px 0 0' }}>Weights, total marks and applicability update the grade instantly. No agent is rerun.</p>
      </>}
      foot={<>
        <button className="btn ghost" onClick={() => setDraft(clone(original))}>Restore the rubric used in evaluation</button>
        <button className="btn primary" disabled={issues.length > 0} onClick={async () => {
          try {
            const r = await backend.updateRubric(vm.rec.id, draft, invalidated);
            onSaved(r, invalidated.length ? `Rubric updated. ${invalidated.length} criterion(s) need reassessment.` : 'Rubric updated. Marks recalculated without rerunning any agent.'); onClose();
          } catch (e) { setErr(engineMessage(e)); }
        }}>Apply changes</button>
      </>}>
      <div className="sub">
        <h4>Impact of these changes</h4>
        <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
          <li>No agents rerun. {invalidated.length ? `${invalidated.length} criterion mark${invalidated.length > 1 ? 's' : ''} will be discarded and need a rubric-model reassessment: ${invalidated.join(', ')}.` : 'All existing marks and your decisions are kept.'}</li>
          {changes.excludedNow.length > 0 && <li>Now not applicable: {changes.excludedNow.join(', ')}. Their weight is shared among the others.</li>}
          {researchAgentMissing && <li className="c-warn">Research is now graded, but the Research agent has no result for this submission. Only the Research agent would need to run; the criterion stays unassessed until then.</li>}
          {projected && <li>Projected: {projected.proposed_points !== null ? <strong>{fmtPoints(projected.proposed_points)} / {fmtPoints(projected.max_points)}</strong> : <>grade withheld, {fmtPoints(projected.assessed_subtotal_points)} of {fmtPoints(projected.assessed_max_points)} assessed so far</>}.</li>}
        </ul>
      </div>
      {err && <div className="mt"><Callout tone="bad" title="Not applied">{err}</Callout></div>}
      <div className="mt-l"><RubricEditor rubric={draft} onChange={setDraft} assessedIds={assessed} /></div>
    </Drawer>
  );
}

export default function Grading({ vm, setReview, caps, reload }: { vm: ViewModel; setReview: (r: TeacherReview) => void; caps: BackendCapabilities | null; reload: () => void }) {
  const [params, setParams] = useSearchParams();
  const [editRubric, setEditRubric] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const openId = params.get('criterion');
  const a = vm.assessment;
  const critById = new Map(vm.rubric.criteria.map((c) => [c.id, c]));
  const open = openId ? a.criteria.find((c) => c.id === openId) : null;
  const saved = (r: TeacherReview, msg?: string) => { setReview(r); if (msg) { setToast(msg); setTimeout(() => setToast(null), 4000); } };
  const proposals = a.criteria.filter((c) => c.status === 'PROPOSED' || c.status === 'OBJECTIVE_PROVISIONAL');
  const [err, setErr] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const approveAll = async () => {
    if (bulkBusy || !confirm(`Approve ${proposals.length} proposed or provisional criteria? Review their cited evidence first.`)) return;
    setBulkBusy(true);
    let r: TeacherReview | null = null; let n = 0; setErr(null);
    try {
      for (const c of proposals) { r = await backend.saveTeacherDecision(vm.rec.id, approveEntry(c, critById.get(c.id)!)); n++; }
    } catch (e) { setErr(engineMessage(e)); }
    if (r) saved(r, `Approved ${n} proposal${n === 1 ? '' : 's'}.`);
    setBulkBusy(false);
  };
  return (
    <>
      <section className="panel">
        <div className="ledger">
          <div className="grade-block"><GradeHeadline a={a} /></div>
          <div className="stack">
            <CoverageMeter a={a} />
            <div className="row">
              <button className="btn" onClick={() => setEditRubric(true)}>Adjust rubric and weights</button>
              {proposals.length > 0 && <button className="btn approve" disabled={bulkBusy} onClick={approveAll}>{bulkBusy ? 'Saving…' : `Approve all ${proposals.length} proposals`}</button>}
              {Object.keys(vm.rec.review.decisions).length > 0 && <button className="btn ghost danger" onClick={async () => { if (confirm('Remove all your decisions and rubric changes for this evaluation?')) saved(await backend.resetReview(vm.rec.id), 'Review reset.'); }}>Reset my review</button>}
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              AI proposals are drawn in dashed violet; your approved marks in solid teal. Approving accepts the proposed level and its cited evidence. Changing a level needs a short remark.
            </p>
            {vm.rec.review.rubricOverride && <Callout tone="info">You changed the rubric after evaluation (total {vm.rubric.total_points} marks). Marks were recalculated without rerunning agents.</Callout>}
          </div>
        </div>
      </section>
      <section className="panel"><div className="panel-head"><h2>Score by section</h2></div><SectionRings a={a} size={84} /></section>
      {toast && <div className="mt" role="status"><Callout tone="good">{toast}</Callout></div>}
      {err && <div className="mt" role="alert"><Callout tone="bad" title="Not saved">{err}</Callout></div>}
      <div className="mt-l">
        {a.criteria.map((c) => (
          <CriterionCard key={c.id} c={c} crit={critById.get(c.id)!} vm={vm}
            onOpen={() => setParams({ criterion: c.id })}
            onQuickApprove={async () => { setErr(null); try { saved(await backend.saveTeacherDecision(vm.rec.id, approveEntry(c, critById.get(c.id)!)), `Approved ${c.title}.`); } catch (e) { setErr(`${c.title}: ${engineMessage(e)}`); } }} />
        ))}
      </div>
      <More summary="How the engine calculates marks">
        <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{a.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
      </More>
      {open && critById.get(open.id) && <ReviewDrawer key={open.id} vm={vm} c={open} crit={critById.get(open.id)!} caps={caps} reload={reload} onClose={() => setParams({})} onSaved={saved} />}
      {editRubric && <RubricDrawer vm={vm} onClose={() => setEditRubric(false)} onSaved={saved} />}
    </>
  );
}
