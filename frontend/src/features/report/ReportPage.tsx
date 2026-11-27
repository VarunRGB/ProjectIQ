import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { LedgerBar, SectionRings } from '../../components/Ledger';
import { Ring, toneFor } from '../../components/Visuals';
import { VERDICT } from '../results/FormatTab';
import { evidenceAgent } from '../../components/EvidenceCard';
import { AGENT_INFO, AGE_BAND, CRITERION_STATUS, DIAG_CATEGORY, EVIDENCE_KIND, FLAG_TEXT, INTEGRITY_CATEGORY, OWNER_INFO, PROVENANCE, RESEARCH_RELEVANCE, RESEARCH_VERIFICATION, TEST_OUTCOME, TEST_REASON, humanize, reasonText } from '../../lib/labels';
import { useEvaluation } from '../../lib/useEvaluation';
import { fmtPoints, pct, type ViewModel } from '../../lib/viewModel';
import { verifiedFindings } from '../results/SummaryTab';
import { FunctionalReport } from '../results/FunctionalEvidence';
import type { AgentName } from '../../api/types';

function Tag({ status }: { status: keyof typeof CRITERION_STATUS }) {
  const s = CRITERION_STATUS[status];
  return <span className={`r-tag ${s.tone}`}>{s.label}</span>;
}

function clip(t: string, n = 260) { const s = t.replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

function Section({ n, title, children, lead }: { n: number; title: string; children: React.ReactNode; lead?: string }) {
  return (
    <section className="r-section">
      <h2><span className="r-n">{n}</span>{title}</h2>
      {lead && <p className="r-lead">{lead}</p>}
      {children}
    </section>
  );
}

function Grade({ vm }: { vm: ViewModel }) {
  const a = vm.assessment;
  if (a.reviewed_points !== null) return <div className="r-grade ink"><span className="r-gnum">{fmtPoints(a.reviewed_points)}</span><span className="r-gof">/ {fmtPoints(a.max_points)}</span><div className="r-gcap">Final grade, approved by the teacher</div></div>;
  if (a.proposed_points !== null) return <div className="r-grade pencil"><span className="r-gnum">{fmtPoints(a.proposed_points)}</span><span className="r-gof">/ {fmtPoints(a.max_points)}</span><div className="r-gcap">Proposed grade, {a.reviewed_weight_percent}% approved so far</div></div>;
  return <div className="r-grade withheld"><span className="r-gw">Grade withheld</span><div className="r-gcap">{fmtPoints(a.assessed_subtotal_points)} of {fmtPoints(a.assessed_max_points)} marks on the {a.assessed_weight_percent}% assessed; {a.unresolved_criterion_ids.length} criteri{a.unresolved_criterion_ids.length === 1 ? 'on' : 'a'} still to assess</div></div>;
}

export default function ReportPage() {
  const { id } = useParams();
  const { rec, vm, error } = useEvaluation(id);
  const [audience, setAudience] = useState<'faculty' | 'student'>('faculty');
  const [excerpts, setExcerpts] = useState(true);
  useEffect(() => { if (rec) document.title = `ProjectIQ report: ${rec.result.submission.project_title}`; }, [rec]);
  if (error) return <div className="r-wrap"><p>{error}</p></div>;
  if (!rec || !vm) return <div className="r-wrap"><p>Preparing report…</p></div>;
  const r = rec.result; const sub = r.submission; const a = vm.assessment;
  const critById = new Map(vm.rubric.criteria.map((c) => [c.id, c]));
  const faculty = audience === 'faculty';
  const final = a.status === 'TEACHER_REVIEWED';
  const ex = vm.executioner; const demo = ex?.automatic; const rs = vm.research; const ig = vm.integrity;
  const findings = verifiedFindings(vm);
  const improvements = a.criteria.filter((c) => c.feedback && c.status !== 'EXCLUDED').map((c) => ({ title: c.title, text: c.feedback!.improvement }));
  let n = 0;
  return (
    <div className="r-wrap">
      <div className="r-toolbar no-print">
        <Link to={`/evaluations/${id}`} className="btn">Back to the dashboard</Link>
        <div className="row">
          <label className="row small">Version
            <select className="in" style={{ width: 190 }} value={audience} onChange={(e) => setAudience(e.target.value as any)}>
              <option value="faculty">Faculty (complete)</option>
              <option value="student">Student feedback</option>
            </select></label>
          <label className="check small"><input type="checkbox" checked={excerpts} onChange={(e) => setExcerpts(e.target.checked)} />Evidence excerpts</label>
          <button className="btn primary" onClick={() => window.print()}>Print or save as PDF</button>
        </div>
      </div>
      {!final && <div className="r-draftnote no-print">This report is a draft: {a.proposed_points === null ? 'some criteria are not assessed yet' : 'not every mark is approved'}. It will be marked as draft when printed.</div>}

      <article className={`r-doc ${final ? '' : 'draft'}`}>
        <header className="r-cover">
          <div className="r-brand">ProjectIQ evaluation report{!final && <span className="r-draft">Draft</span>}</div>
          <h1>{sub.project_title}</h1>
          <p className="r-who">{rec.meta.students?.join(', ') || 'Students not recorded'}{rec.meta.course ? `. ${rec.meta.course}` : ''}{rec.meta.cohort ? `, ${rec.meta.cohort}` : ''}</p>
          <div className="r-cover-grid">
            <Grade vm={vm} />
            <dl className="r-meta">
              <dt>Submission</dt><dd>{sub.id}</dd>
              <dt>Report</dt><dd>{sub.report_name}</dd>
              <dt>Repository</dt><dd>{sub.repository_url ? <>{sub.repository_url.replace('https://github.com/', '')}{ex?.commit && <> at {ex.commit.slice(0, 10)}</>}</> : 'None (report-only submission)'}</dd>
              <dt>Rubric</dt><dd>{vm.rubric.title}, {vm.rubric.total_points} marks</dd>
              <dt>Evaluated</dt><dd>{rec.meta.evaluated_utc ? new Date(rec.meta.evaluated_utc).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) : 'Not recorded'}</dd>
              {faculty && <><dt>Evidence</dt><dd>{r.collection_mode === 'LIVE' ? 'Collected live' : 'Bundled as imported evidence, acknowledged by an operator'}</dd></>}
            </dl>
          </div>
        </header>

        <Section n={++n} title="Mark sheet" lead="Each criterion with its share of the grade, the level awarded and who decided it. Criteria that are not assessed are not counted as zero.">
          <LedgerBar a={a} interactive={false} />
          <div className="r-rings"><SectionRings a={a} size={70} animate={false} /></div>
          <table className="r-table mt">
            <thead><tr><th>Criterion</th><th className="num">Weight</th><th>Level</th><th className="num">Marks</th><th>Status</th></tr></thead>
            <tbody>
              {a.criteria.map((c) => {
                const crit = critById.get(c.id);
                const lvl = crit?.levels.find((l) => l.id === c.level_id);
                return (
                  <tr key={c.id} className={c.status === 'EXCLUDED' ? 'r-excl' : ''}>
                    <td><strong>{c.title}</strong><div className="r-small">{humanize(c.group)}</div></td>
                    <td className="num">{c.status === 'EXCLUDED' ? 'n/a' : `${c.effective_weight_percent}%`}</td>
                    <td>{c.status === 'EXCLUDED' ? 'Not applicable' : lvl ? lvl.label : c.fraction !== null ? `${Math.round(c.fraction * 100)}%` : 'Not assessed'}</td>
                    <td className="num">{c.status === 'EXCLUDED' ? '' : c.earned_points === null ? `– / ${fmtPoints(c.max_points)}` : `${fmtPoints(c.earned_points)} / ${fmtPoints(c.max_points)}`}</td>
                    <td><Tag status={c.status} /></td>
                  </tr>
                );
              })}
              <tr className="r-total"><td>Total</td><td className="num">100%</td><td /><td className="num">{a.proposed_points !== null ? `${fmtPoints(a.proposed_points)} / ${fmtPoints(a.max_points)}` : `Withheld (${fmtPoints(a.assessed_subtotal_points)} / ${fmtPoints(a.assessed_max_points)} assessed)`}</td><td>{final ? <span className="r-tag ink">Approved</span> : <span className="r-tag warn">Draft</span>}</td></tr>
            </tbody>
          </table>
          <div className="r-legend">
            <span><i className="sw reviewed" />Approved by the teacher</span><span><i className="sw proposed" />Proposed by the AI rubric model, not yet approved</span><span><i className="sw unassessed" />Not assessed</span>
          </div>
        </Section>

        <Section n={++n} title="Assessment by criterion" lead="The reasoning behind each mark, the evidence it rests on and what would improve it.">
          {a.criteria.filter((c) => c.status !== 'EXCLUDED').map((c) => {
            const crit = critById.get(c.id)!;
            const t = rec.review.decisions[c.id];
            const lvl = crit.levels.find((l) => l.id === c.level_id);
            const ev = c.evidence_ids.map((eid) => vm.evidenceById.get(eid)).filter(Boolean);
            return (
              <div key={c.id} className="r-crit">
                <div className="r-crit-head">
                  <div><h3>{c.title}</h3><div className="r-small">{crit.description}</div></div>
                  <div className="r-crit-mark"><span className={`r-pts ${c.status === 'TEACHER_REVIEWED' ? 'ink' : c.status === 'PROPOSED' ? 'pencil' : ''}`}>{c.earned_points === null ? 'Not assessed' : `${fmtPoints(c.earned_points)} / ${fmtPoints(c.max_points)}`}</span><Tag status={c.status} /></div>
                </div>
                {lvl && <p className="r-level"><strong>{lvl.label}.</strong> {lvl.descriptor}</p>}
                {c.status === 'UNASSESSED' && <p className="r-note">{rec.review.pendingReassessment.includes(c.id) ? 'The criterion changed after evaluation and needs reassessment.' : reasonText(c.reason)} This criterion is not counted as zero.</p>}
                {c.feedback && (
                  <dl className="r-fb">
                    <dt>Strength</dt><dd>{c.feedback.strength}</dd>
                    <dt>Gap</dt><dd>{c.feedback.limitation}</dd>
                    <dt>Improve by</dt><dd>{c.feedback.improvement}</dd>
                  </dl>
                )}
                {t?.remarks && <div className="r-remark"><strong>Teacher’s remark{t.basis === 'OVERRIDE' ? ' (changed from the AI proposal)' : ''}:</strong> {t.remarks}</div>}
                {faculty && c.review_flags.map((f) => <p key={f} className="r-note">{FLAG_TEXT[f] ?? humanize(f)}</p>)}
                {faculty && excerpts && ev.length > 0 && (
                  <div className="r-ev">
                    <div className="r-ev-h">Evidence cited ({ev.length})</div>
                    {ev.slice(0, 6).map((e) => (
                      <div key={e!.id} className="r-ev-item">
                        <span className="r-ev-kind">{EVIDENCE_KIND[e!.kind].label}</span>
                        <span className="r-ev-src">{e!.kind === 'REPORT_EXCERPT' ? `${e!.data.section ?? 'Report'}, characters ${e!.data.start} to ${e!.data.end}` : `${evidenceAgent(e!)}: ${e!.label}`}{e!.data.outcome ? `, ${TEST_OUTCOME[e!.data.outcome]?.label.toLowerCase()}` : ''}{e!.data.source_locations?.[0] ? `, ${e!.data.source_locations[0].path}${e!.data.source_locations[0].line ? ':' + e!.data.source_locations[0].line : ''}` : ''}</span>
                        {e!.kind === 'REPORT_EXCERPT' && <span className="r-quote">“{clip(e!.text, 240)}”</span>}
                        {e!.kind === 'STATIC_SOURCE' && <span className="r-small">{clip(e!.text, 200)}</span>}
                      </div>
                    ))}
                    {ev.length > 6 && <div className="r-small">and {ev.length - 6} more in the dashboard.</div>}
                  </div>
                )}
              </div>
            );
          })}
          {a.criteria.some((c) => c.status === 'EXCLUDED') && <p className="r-small">Not applicable to this submission: {a.criteria.filter((c) => c.status === 'EXCLUDED').map((c) => c.title).join(', ')}. Their weight was shared among the other criteria.</p>}
        </Section>

        <Section n={++n} title="What the report claims and what was checked" lead="The left column repeats sentences from the report itself. The right column lists what the agents observed independently.">
          <div className="r-two">
            <div><h4>The report says</h4>{vm.summary ? <><p className="r-serif">{vm.summary.summary}</p><ul className="r-serif">{vm.summary.key_points.slice(0, 4).map((k, i) => <li key={i}>{k}</li>)}</ul></> : <p className="r-small">No summary available.</p>}</div>
            <div><h4>Checked independently</h4><ul>{findings.map((f, i) => <li key={i}>{f.text} <span className="r-small">({f.source})</span></li>)}{!findings.length && <li>Nothing could be verified independently.</li>}</ul></div>
          </div>
        </Section>

        {ex && ex.mode && (
          <Section n={++n} title="Repository and testing" lead={ex.intelligence.overview}>
            <div className="r-facts">
              <div><b>{demo ? 'TRUSTED_DEMO' : ex.mode}</b><span>Executioner mode{demo ? ' (separate automatic observations)' : ex.effectiveMode !== ex.mode ? ` (${ex.effectiveMode} evidence)` : ''}</span></div>
              <div><b>{ex.intelligence.server_endpoint_count ?? 0}</b><span>Endpoints found</span></div>
              {demo ? <><div><b>{demo.inventory ? `${demo.inventory.passed}/${demo.inventory.attempted}` : 'Unavailable'}</b><span>Independent checks passed / attempted</span></div><div><b>{demo.inventory ? `${demo.inventory.covered}/${demo.inventory.routes.length}` : 'Unavailable'}</b><span>Unique route cases exercised</span></div><div><b>{demo.inventory?.workflow ? `${demo.inventory.workflow.passed}/${demo.inventory.workflow.attempted}` : 'n/a'}</b><span>Workflow requests passed / attempted</span></div></> : <><div><b>{ex.counts.PASSED ?? 0} / {(ex.counts.PASSED ?? 0) + (ex.counts.FAILED ?? 0)}</b><span>Executed planned tests passed</span></div><div><b>{ex.counts.NOT_TESTED ?? 0}</b><span>Planned tests not executed</span></div><div><b>{ex.reviewedCounts.PASSED ?? 0} / {ex.reviewed.length}</b><span>Reviewed scenarios passed</span></div></>}
              <div><b>{ex.quality?.quality?.score ?? 'Withheld'}</b><span>Maintainability indicator (not a grade)</span></div>
            </div>
            <p className="r-small">Technologies: {(ex.intelligence.technologies ?? []).map((t: any) => t.name).join(', ')}. Feature candidates from source or documentation: {(ex.intelligence.features ?? []).map((f: any) => f.name).join('; ')}.</p>
            {demo ? <FunctionalReport f={demo} faculty={faculty} /> : ex.mode === 'STATIC' && <p className="r-note">STATIC mode: no student code was executed. Planned tests are specifications only.</p>}
            {[...ex.planned, ...ex.reviewed].some((t) => t.outcome === 'FAILED') && (
              <>
                <h4>Failed tests</h4>
                <table className="r-table">
                  <thead><tr><th>Test</th><th>Expected</th><th>Observed</th></tr></thead>
                  <tbody>{[...ex.planned, ...ex.reviewed].filter((t) => t.outcome === 'FAILED').map((t) => {
                    const obs = t.observations[0] ?? {}; const asserts: any[] = obs.assertions ?? [];
                    const failed = asserts.filter((x) => x.result === 'FAIL');
                    return <tr key={t.id}><td><strong>{t.title}</strong><div className="r-small">{t.target?.method} {t.target?.path}</div></td>
                      <td>{failed.map((x, i) => <div key={i}>{humanize(x.kind)} {String(x.expected)}</div>)}{!failed.length && t.expected.map((x: any, i: number) => <div key={i}>{humanize(x.kind)} {String(x.expected)}</div>)}</td>
                      <td>{failed.map((x, i) => <div key={i}>{String(x.actual)}</div>)}</td></tr>;
                  })}</tbody>
                </table>
                <p className="r-small">A failed check means the observed behaviour differed from a reviewed expectation. It does not on its own prove a student defect.</p>
              </>
            )}
            {faculty && ex.diagnoses.length > 0 && (
              <>
                <h4>Diagnosis by responsibility</h4>
                <table className="r-table"><tbody>{ex.diagnoses.map((d, i) => (
                  <tr key={i}><td style={{ width: '28%' }}><strong>{OWNER_INFO[d.owner]?.label}</strong><div className="r-small">{DIAG_CATEGORY[d.category]?.label}</div></td><td>{d.title}. {d.explanation}</td></tr>
                ))}</tbody></table>
              </>
            )}
            {(ex.quality?.findings ?? []).length > 0 && (
              <>
                <h4>Code-quality findings</h4>
                <ul className="r-list">{ex.quality.findings.slice(0, 8).map((f: any, i: number) => <li key={i}><strong>{humanize(f.severity)}</strong>: {f.message} <span className="r-small">{f.path}:{f.line}. {f.recommendation}</span></li>)}</ul>
              </>
            )}
            {faculty && ex.suites.some((s: any) => !s.vm_verified) && <p className="r-small">Not executed because of the evaluation environment: {ex.suites.filter((s: any) => !s.vm_verified).map((s: any) => `${humanize(s.kind)} (${humanize(s.reason ?? s.result_status)})`).join(', ')}. These are not student defects.</p>}
            {faculty && <p className="r-small">{demo ? 'Untested legacy reviewed specifications (not automatic coverage)' : 'Untested planned cases'}: {ex.planned.filter((t) => t.outcome === 'NOT_TESTED').map((t) => `${t.title} (${(TEST_REASON[t.reason] ?? humanize(t.reason)).toLowerCase()})`).join('; ') || 'none'}.</p>}
          </Section>
        )}

        {rs?.report && (
          <Section n={++n} title="Research and citations" lead={rs.report.overview}>
            <table className="r-table">
              <thead><tr><th className="num">#</th><th>Paper</th><th>Identity</th><th>Relevance</th><th>Year</th></tr></thead>
              <tbody>{rs.refs.map((x) => (
                <tr key={x.n}><td className="num">{x.n}</td><td>{x.title}{x.attention && <div className="r-small">{x.attention.reasons.join('. ')}.</div>}</td>
                  <td>{RESEARCH_VERIFICATION[x.verification]?.label ?? x.verification}</td><td>{RESEARCH_RELEVANCE[x.relevance]?.label ?? x.relevance}</td>
                  <td>{x.year ?? 'Unknown'}{x.yearSource !== 'verified_metadata' && x.year ? ' (unconfirmed)' : ''}<div className="r-small">{AGE_BAND[x.ageBand] ?? ''}</div></td></tr>
              ))}</tbody>
            </table>
            <h4>Recommended checks</h4>
            <ul className="r-list">{rs.report.recommended_checks.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            <p className="r-small">A missing database match does not prove a paper is fabricated. Relevance is a heuristic estimate, not proof that a source supports a specific claim.</p>
          </Section>
        )}
        {rs?.failed && <Section n={++n} title="Research and citations"><p className="r-note">The Research agent did not produce a result for this submission. The research criterion was left unassessed rather than scored as zero.</p></Section>}

        {faculty && ig && (
          <Section n={++n} title="Integrity (advisory)" lead="Similarity and AI-writing indicators help a teacher decide what to read closely. They are not misconduct findings and never change marks.">
            {ig.comparisonAvailable ? (
              <>
                <div className="r-facts">
                  <div><b>{pct(ig.overlap)}</b><span>Confirmed overlap with {ig.references.length} references</span></div>
                  {Object.entries(ig.byCategory).map(([k, v]) => <div key={k}><b>{pct(v)}</b><span>{INTEGRITY_CATEGORY[k]?.label ?? k}{INTEGRITY_CATEGORY[k] && !INTEGRITY_CATEGORY[k].counted ? ' (not counted)' : ''}</span></div>)}
                </div>
                {ig.matches.filter((m) => INTEGRITY_CATEGORY[m.category]?.counted).slice(0, 4).map((m, i) => (
                  <div key={i} className="r-pair"><div><span className="r-small">{INTEGRITY_CATEGORY[m.category]?.label}, report {m.location}{m.cited ? ', cited' : ', no citation marker'}</span><p className="r-serif">{m.text}</p></div><div><span className="r-small">{m.reference}, {m.reference_location}</span><p className="r-serif">{m.reference_text}</p></div></div>
                ))}
              </>
            ) : <p className="r-note">No reference documents were supplied, so similarity is unavailable (not zero).</p>}
            {ig.secondary.length > 0 && <p className="r-small">A second, experimental pass found {ig.secondary.length} additional loosely similar sentence{ig.secondary.length > 1 ? 's' : ''} ({ig.secondary.filter((m) => m.secondary_review?.status === 'MUTUAL_ENTAILMENT_CANDIDATE').length} possible paraphrase{ig.secondary.filter((m) => m.secondary_review?.status === 'MUTUAL_ENTAILMENT_CANDIDATE').length === 1 ? '' : 's'}, unconfirmed). They are not counted in the overlap.</p>}
            <h4>AI-generated writing estimate</h4>
            {ig.ai?.status === 'COMPLETED' ? <p>{ig.ai.interpretation} Confidence: {humanize(ig.ai.confidence)}.</p> : <p className="r-note">{ig.ai?.status === 'INCONCLUSIVE' ? 'Inconclusive: too little text could be scored.' : ig.ai?.status === 'FAILED' ? 'The estimate failed.' : 'Not available for this evaluation.'} This is not 0%.</p>}
          </Section>
        )}

        {r.ieee_format && r.ieee_format.status !== 'FAILED' && (
          <Section n={++n} title="Report formatting (IEEE conventions)" lead="Advisory check of common IEEE paper conventions. It does not affect marks.">
            <div className="r-format">
              <Ring value={r.ieee_format.score} size={78} animate={false} tone={toneFor(r.ieee_format.score, 85, 70)} />
              <div><strong>{VERDICT[r.ieee_format.format_verdict]?.label}</strong>. {r.ieee_format.rating}. <span className="r-small">{r.ieee_format.layout_verdict}.</span>
                <ul className="r-list">{(r.ieee_format.checks ?? []).filter((c) => c.status === 'fail' || c.status === 'warning').slice(0, 6).map((c, i) => <li key={i}><strong>{c.criterion}:</strong> {c.suggestion || c.detail}</li>)}</ul>
              </div>
            </div>
          </Section>
        )}

        <Section n={++n} title={faculty ? 'Recommendations' : 'How to improve'} lead={faculty ? 'Actions drawn from the criterion feedback and the agents’ findings.' : undefined}>
          <ol className="r-list">
            {improvements.map((x, i) => <li key={i}><strong>{x.title}:</strong> {x.text}</li>)}
            {ex?.recommendations.filter((x: any) => x.source === 'PHASE5' && (x.priority === 'HIGH' || x.priority === 'MEDIUM')).slice(0, 4).map((x: any, i: number) => <li key={'e' + i}><strong>Code ({x.path}):</strong> {x.recommendation}</li>)}
            {rs?.report?.recommended_checks.slice(0, 2).map((x: string, i: number) => <li key={'r' + i}><strong>Sources:</strong> {x}</li>)}
          </ol>
        </Section>

        {faculty && (
          <Section n={++n} title="Evidence sources and limitations">
            <table className="r-table">
              <thead><tr><th>Agent</th><th>Status</th><th>In this evaluation</th><th>Provenance</th></tr></thead>
              <tbody>{(['summarizer', 'integrity', 'research', 'executioner'] as AgentName[]).map((k) => {
                const ag = r.agents[k]; const p = ag.receipt_provenance;
                return <tr key={k}><td>{AGENT_INFO[k].name}</td><td>{humanize(ag.status)}</td><td>{humanize(vm.provenance.perAgent[k].action)}</td><td>{p ? PROVENANCE[p].label : humanize(ag.evidence_check)}</td></tr>;
              })}</tbody>
            </table>
            {vm.provenance.acknowledged && <p className="r-small">Imported results were acknowledged by an operator as belonging to this submission. The acknowledgment is recorded but is not independent authentication.</p>}
            <ul className="r-list r-small">{[...new Set([...r.limitations, ...a.limitations])].map((l, i) => <li key={i}>{l}</li>)}</ul>
          </Section>
        )}

        <footer className="r-foot">
          <span>ProjectIQ, {faculty ? 'faculty report' : 'student feedback'}. Generated {new Date().toLocaleString()}.</span>
          <span>Confidential: contains student work. {final ? 'Marks approved by the teacher.' : 'Draft: marks not yet approved.'}</span>
        </footer>
      </article>
    </div>
  );
}
