import { Fragment, useMemo, useState } from 'react';
import type { Applicability, Criterion, GradingKind, Rubric } from '../../api/types';
import { Badge, Callout, Tip } from '../../components/ui';
import { EVIDENCE_KIND, humanize } from '../../lib/labels';
import { blankCriterion, clone, effectiveShares, GRADING_KINDS, groupsOf, isResearch, setResearch, validateRubric } from '../../lib/rubric';

const pctFmt = (v: number) => `${(v * 100).toFixed(v * 100 < 10 ? 1 : 0)}%`;

function num(v: string): number { const n = Number(v); return Number.isFinite(n) ? n : 0; }

function LevelsEditor({ c, onChange, locked }: { c: Criterion; onChange: (c: Criterion) => void; locked?: boolean }) {
  const set = (i: number, patch: Partial<Criterion['levels'][number]>) => {
    const next = clone(c); next.levels[i] = { ...next.levels[i], ...patch }; onChange(next);
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row between"><h4>Grading levels and descriptors</h4>
        <span className="tiny muted">The lowest level must be 0% and the highest 100%.</span></div>
      {c.levels.map((l, i) => (
        <div key={i} className="row" style={{ alignItems: 'flex-start', gap: 8, flexWrap: 'nowrap' }}>
          <input className="in" style={{ width: 130 }} aria-label="Level name" value={l.label} disabled={locked}
            onChange={(e) => set(i, { label: e.target.value, id: e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || l.id })} />
          <span className="row" style={{ gap: 4, flexWrap: 'nowrap' }}>
            <input className="in num" style={{ width: 70 }} aria-label="Share of marks (%)" type="number" min={0} max={100} value={Math.round(l.fraction * 100)} disabled={locked}
              onChange={(e) => set(i, { fraction: Math.max(0, Math.min(1, num(e.target.value) / 100)) })} /><span className="small muted">%</span>
          </span>
          <textarea className="in" style={{ minHeight: 40 }} aria-label="Descriptor" value={l.descriptor} disabled={locked} onChange={(e) => set(i, { descriptor: e.target.value })} />
          <button className="btn ghost sm" disabled={locked || c.levels.length <= 2} onClick={() => { const n = clone(c); n.levels.splice(i, 1); onChange(n); }} aria-label={`Remove level ${l.label}`}>Remove</button>
        </div>
      ))}
      <div><button className="btn sm" disabled={locked} onClick={() => {
        const n = clone(c); const last = n.levels[n.levels.length - 1];
        n.levels.splice(n.levels.length - 1, 0, { id: `level_${n.levels.length + 1}`, label: 'New level', fraction: Math.max(0, (last?.fraction ?? 1) - 0.1), descriptor: 'Describe this level.' });
        n.levels.sort((a, b) => a.fraction - b.fraction); onChange(n);
      }}>Add level</button></div>
    </div>
  );
}

function EvidencePicker({ c, onChange }: { c: Criterion; onChange: (c: Criterion) => void }) {
  if (c.method !== 'JUDGMENT') return <p className="small muted">Objective criterion: scored only from verified test observations ({humanize(c.method)}).</p>;
  const toggle = (k: GradingKind, field: 'allowed_evidence' | 'required_evidence') => {
    const n = clone(c); const list = new Set(n[field]);
    list.has(k) ? list.delete(k) : list.add(k);
    n[field] = GRADING_KINDS.filter((x) => list.has(x));
    if (field === 'allowed_evidence') n.required_evidence = n.required_evidence.filter((x) => n.allowed_evidence.includes(x));
    if (field === 'required_evidence' && list.has(k) && !n.allowed_evidence.includes(k)) n.allowed_evidence = GRADING_KINDS.filter((x) => n.allowed_evidence.includes(x) || x === k);
    onChange(n);
  };
  return (
    <div>
      <h4>Evidence this criterion may use</h4>
      <table className="t" style={{ marginTop: 6 }}>
        <thead><tr><th>Evidence</th><th>Allowed</th><th>Required</th></tr></thead>
        <tbody>
          {GRADING_KINDS.map((k) => (
            <tr key={k}>
              <td>{EVIDENCE_KIND[k].label}<div className="tiny muted">{EVIDENCE_KIND[k].explain}</div></td>
              <td><input type="checkbox" checked={c.allowed_evidence.includes(k)} onChange={() => toggle(k, 'allowed_evidence')} aria-label={`Allow ${EVIDENCE_KIND[k].label}`} /></td>
              <td><input type="checkbox" checked={c.required_evidence.includes(k)} onChange={() => toggle(k, 'required_evidence')} aria-label={`Require ${EVIDENCE_KIND[k].label}`} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row mt">
        <label className="f" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>Minimum distinct evidence items
          <input className="in num" type="number" min={1} max={20} value={c.minimum_evidence} onChange={(e) => onChange({ ...c, minimum_evidence: Math.max(1, Math.min(20, Math.round(num(e.target.value)))) })} /></label>
        <label className="check"><input type="checkbox" checked={c.feedback_required} onChange={(e) => onChange({ ...c, feedback_required: e.target.checked })} />Require strength, limitation and improvement feedback</label>
      </div>
    </div>
  );
}

export interface RubricEditorProps {
  rubric: Rubric;
  onChange: (r: Rubric) => void;
  /** Criteria that already have marks (post-evaluation editing): policy edits will need reassessment. */
  assessedIds?: string[];
  compact?: boolean;
}

export default function RubricEditor({ rubric, onChange, assessedIds = [] }: RubricEditorProps) {
  const [open, setOpen] = useState<string | null>(null);
  const [researchErr, setResearchErr] = useState<string | null>(null);
  const issues = useMemo(() => validateRubric(rubric), [rubric]);
  const shares = useMemo(() => effectiveShares(rubric), [rubric]);
  const grouped = Object.keys(rubric.group_weights || {}).length > 0;
  const groups = groupsOf(rubric);
  const researchCriteria = rubric.criteria.filter(isResearch);
  const researchOn = researchCriteria.some((c) => c.applicability !== 'EXCLUDED');
  const groupTotal = Object.values(rubric.group_weights || {}).reduce((a, b) => a + b, 0);

  const setCrit = (id: string, c: Criterion) => onChange({ ...rubric, criteria: rubric.criteria.map((x) => (x.id === id ? c : x)) });
  const groupShare = (g: string) => rubric.criteria.filter((c) => c.group === g).reduce((a, c) => a + (shares[c.id] || 0), 0);

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="grid g3" style={{ alignItems: 'end' }}>
        <label className="f">Rubric title<input className="in" value={rubric.title} onChange={(e) => onChange({ ...rubric, title: e.target.value })} /></label>
        <label className="f">Total marks<span className="hint">Marks are scaled to this total.</span>
          <input className="in" type="number" min={1} value={rubric.total_points} onChange={(e) => onChange({ ...rubric, total_points: num(e.target.value) })} /></label>
        <div className="sub">
          <label className="check">
            <input type="checkbox" checked={researchOn} disabled={!researchCriteria.length} onChange={(e) => {
              try { onChange(setResearch(rubric, e.target.checked)); setResearchErr(null); } catch (err: any) { setResearchErr(err.message); }
            }} />
            <span><strong>Grade research and citations</strong><br /><span className="small muted">Off: the research criterion is excluded and its weight is shared among the others. The Research agent is skipped.</span></span>
          </label>
          {!researchCriteria.length && <p className="tiny muted" style={{ marginTop: 6 }}>This rubric has no research criterion.</p>}
        </div>
      </div>
      {researchErr && <Callout tone="bad">{researchErr === 'ACTIVE_CRITERION_REQUIRES_RESEARCH' ? 'Another active criterion requires research records. Remove that requirement first.' : humanize(researchErr)}</Callout>}

      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Section and criterion</th>
              <th className="num">{grouped ? 'Section weight' : ''}</th>
              <th className="num"><Tip text="Relative weight inside its section. Weights are proportions, not percentages.">Criterion weight</Tip></th>
              <th>Applies</th>
              <th className="num">Share of grade</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => {
              const crits = rubric.criteria.filter((c) => c.group === g);
              return (
                <Fragment key={g}>
                  <tr style={{ background: 'var(--surface-2)' }}>
                    <td><strong>{humanize(g)}</strong> <span className="tiny muted">{crits.length} criteri{crits.length === 1 ? 'on' : 'a'}</span></td>
                    <td className="num">{grouped ? (
                      <span className="row" style={{ justifyContent: 'flex-end', gap: 6 }}>
                        <input className="in num" type="number" min={0} aria-label={`Weight for ${g}`} value={rubric.group_weights[g] ?? 0}
                          onChange={(e) => onChange({ ...rubric, group_weights: { ...rubric.group_weights, [g]: num(e.target.value) } })} />
                        <span className="tiny muted" style={{ width: 44 }}>{groupTotal ? `${Math.round(((rubric.group_weights[g] ?? 0) / groupTotal) * 100)}% set` : ''}</span>
                      </span>) : null}</td>
                    <td /><td />
                    <td className="num"><strong>{pctFmt(groupShare(g))}</strong></td>
                    <td className="num"><button className="btn ghost sm" onClick={() => {
                      const c = blankCriterion(g, rubric.criteria.length + 1);
                      onChange({ ...rubric, criteria: [...rubric.criteria, c] }); setOpen(c.id);
                    }}>Add criterion</button></td>
                  </tr>
                  {crits.map((c) => (
                    <Fragment key={c.id}>
                      <tr>
                        <td style={{ paddingLeft: 22 }}>
                          <button className="btn ghost sm" style={{ padding: 0, fontWeight: 650 }} onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
                            {open === c.id ? '–' : '+'} {c.title}
                          </button>
                          <div className="tiny muted">{c.levels.length ? `${c.levels.length} levels` : humanize(c.method)}; evidence: {c.allowed_evidence.map((k) => EVIDENCE_KIND[k].label.toLowerCase()).join(', ')}
                            {isResearch(c) && <> <Badge tone="info">Research</Badge></>}
                            {assessedIds.includes(c.id) && <> <Tip text="Changing levels, evidence or the description of this criterion discards its current mark and needs a rubric-model reassessment. Weights and marks can change freely."><Badge tone="pencil">Has a mark</Badge></Tip></>}
                          </div>
                        </td>
                        <td />
                        <td className="num"><input className="in num" type="number" min={0} aria-label={`Weight for ${c.title}`} value={c.weight} onChange={(e) => setCrit(c.id, { ...c, weight: num(e.target.value) })} /></td>
                        <td>
                          <select className="in" style={{ width: 150 }} value={c.applicability} aria-label={`Applicability for ${c.title}`}
                            onChange={(e) => setCrit(c.id, { ...c, applicability: e.target.value as Applicability })}>
                            <option value="REQUIRED">Required</option>
                            <option value="OPTIONAL">Optional</option>
                            <option value="EXCLUDED">Not applicable</option>
                          </select>
                        </td>
                        <td className="num">{c.applicability === 'EXCLUDED' ? <span className="muted small">Excluded</span> : pctFmt(shares[c.id] || 0)}</td>
                        <td className="num"><button className="btn ghost sm danger" aria-label={`Remove ${c.title}`} disabled={rubric.criteria.length <= 1}
                          onClick={() => onChange({ ...rubric, criteria: rubric.criteria.filter((x) => x.id !== c.id) })}>Remove</button></td>
                      </tr>
                      {open === c.id && (
                        <tr><td colSpan={6} style={{ background: 'var(--surface-2)', padding: 18 }}>
                          <div className="stack" style={{ gap: 16 }}>
                            <div className="grid g3">
                              <label className="f">Title<input className="in" value={c.title} onChange={(e) => setCrit(c.id, { ...c, title: e.target.value })} /></label>
                              <label className="f">Criterion ID<span className="hint">Used in saved decisions</span><input className="in" value={c.id} onChange={(e) => { setCrit(c.id, { ...c, id: e.target.value }); setOpen(e.target.value); }} /></label>
                              <label className="f">Section
                                <select className="in" value={c.group} onChange={(e) => setCrit(c.id, { ...c, group: e.target.value })}>{groups.map((x) => <option key={x} value={x}>{humanize(x)}</option>)}</select></label>
                            </div>
                            <label className="f">What is assessed<textarea className="in" value={c.description} onChange={(e) => setCrit(c.id, { ...c, description: e.target.value })} /></label>
                            {c.method === 'JUDGMENT' && <LevelsEditor c={c} onChange={(n) => setCrit(c.id, n)} />}
                            <EvidencePicker c={c} onChange={(n) => setCrit(c.id, n)} />
                          </div>
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {grouped && (
        <div className="row">
          <button className="btn sm" onClick={() => {
            const name = prompt('Name of the new section (letters, digits, - or _)');
            if (!name) return;
            const id = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
            const c = blankCriterion(id, rubric.criteria.length + 1);
            onChange({ ...rubric, group_weights: { ...rubric.group_weights, [id]: 10 }, criteria: [...rubric.criteria, c] }); setOpen(c.id);
          }}>Add section</button>
        </div>
      )}
      {issues.length > 0 ? (
        <Callout tone="warn" title={`${issues.length} thing${issues.length > 1 ? 's' : ''} to fix before this rubric can be used`}>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{issues.slice(0, 8).map((i, k) => <li key={k}>{i.message}</li>)}</ul>
        </Callout>
      ) : <Callout tone="good">The rubric is valid for the ProjectIQ engine.</Callout>}
    </div>
  );
}
