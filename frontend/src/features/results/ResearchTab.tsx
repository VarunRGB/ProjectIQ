import { Fragment, useState } from 'react';
import { AgentStatusBadge, Badge, Callout, Empty, Fact, HBar, More, StackBar, Tip } from '../../components/ui';
import { AGE_BAND, RESEARCH_RELEVANCE, RESEARCH_VERIFICATION, humanize } from '../../lib/labels';
import { pct, type ViewModel } from '../../lib/viewModel';

const VTONE: Record<string, string> = { verified: 'good', needs_review: 'warn', not_found: 'warn', metadata_mismatch: 'bad', lookup_error: 'warn', not_checked: 'muted' };
const RTONE: Record<string, string> = { directly_relevant: 'good', methodologically_relevant: 'info', partially_relevant: 'warn', insufficient_evidence: 'muted', possibly_unrelated: 'bad' };

export default function ResearchTab({ vm }: { vm: ViewModel }) {
  const r = vm.research;
  const card = vm.rec.result.agents.research;
  const [open, setOpen] = useState<number | null>(null);
  const researchCrit = vm.assessment.criteria.find((c) => c.group === 'research' || c.id === 'research');
  if (!r) return <div className="panel"><Empty title="Research was not assessed">Research is not part of this rubric, so the Research agent was skipped. You can turn research on under Grading, Adjust rubric and weights; only the Research agent would then need to run.</Empty></div>;
  if (r.failed || !r.report) {
    return (
      <div className="panel">
        <div className="panel-head"><h2>Research</h2><AgentStatusBadge status={card.status} /></div>
        <Callout tone="bad" title="The Research agent did not produce a result">
          {card.limitations.join(' ')} {card.error_code && <code>{card.error_code}</code>}
          <p className="mt">The research criterion is left unassessed rather than scored as zero{researchCrit ? `, and the complete grade is withheld until you assess it or the agent is rerun` : ''}.</p>
        </Callout>
      </div>
    );
  }
  const rep = r.report; const p1 = rep.phase_1; const p4 = rep.phase_4;
  const vc = rep.phase_2.status_counts ?? {}; const rc = rep.phase_3.classification_counts ?? {};
  const ages = Object.entries(p4.age_distribution ?? {}) as [string, number][];
  const online = !(card.details.notes ?? []).some((n: string) => /not enabled/i.test(n));
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <div><h2>{rep.headline}</h2><p>{rep.overview}</p></div>
          <AgentStatusBadge status={card.status} />
        </div>
        <div className="grid g4">
          <Fact value={p1.reference_entries} label="Bibliography entries" scope={`${p4.distinct_references} distinct works`} />
          <Fact value={`${p1.matched_reference_entries} of ${p1.reference_entries}`} label="Linked to in-text citations" tip="Bibliography entries that the report actually cites in the text." />
          <Fact value={p1.literature_review_found ? 'Found' : 'Not found'} label="Literature review section" scope={`${p1.literature_review_citation_mentions} citations inside it`} />
          <Fact value={p4.reliable_publication_year_count ? pct(p4.recent_reliable_percent, 0) : null} label="Published in the last 5 years" scope={`Of ${p4.reliable_publication_year_count} with a confirmed year`} tip="Unconfirmed years are shown but excluded. Older foundational sources are not automatically a problem." />
        </div>
        <div className="mt">
          <Callout tone={online ? 'info' : 'muted'}>{online ? 'Online metadata verification was enabled for this evaluation (opt-in). Identities were looked up in academic databases.' : 'Online metadata verification was not enabled, so paper identities are unchecked.'} A missing database match does not prove a paper is fabricated.</Callout>
        </div>
      </section>

      <div className="grid g3 mt-l">
        <section className="panel" style={{ marginTop: 0 }}>
          <h3>Identity verification</h3>
          <div className="mt"><StackBar label="Verification" parts={Object.entries(vc).map(([k, v]) => ({ value: v as number, tone: VTONE[k] ?? 'muted', label: RESEARCH_VERIFICATION[k]?.label ?? k }))} /></div>
          <div className="mt">{Object.entries(vc).map(([k, v]) => <div key={k} className="row between small" style={{ padding: '3px 0' }}><span><span className={`dot tone-${VTONE[k] ?? 'muted'}`} style={{ marginRight: 6 }} /><Tip text={RESEARCH_VERIFICATION[k]?.explain ?? ''}><span>{RESEARCH_VERIFICATION[k]?.label ?? humanize(k)}</span></Tip></span><strong>{v as number}</strong></div>)}</div>
        </section>
        <section className="panel" style={{ marginTop: 0 }}>
          <h3>Relevance to the project</h3>
          <div className="mt"><StackBar label="Relevance" parts={Object.entries(rc).map(([k, v]) => ({ value: v as number, tone: RTONE[k] ?? 'muted', label: RESEARCH_RELEVANCE[k]?.label ?? k }))} /></div>
          <div className="mt">{Object.entries(rc).sort((a, b) => (RESEARCH_RELEVANCE[a[0]]?.rank ?? 9) - (RESEARCH_RELEVANCE[b[0]]?.rank ?? 9)).map(([k, v]) => <div key={k} className="row between small" style={{ padding: '3px 0' }}><span><span className={`dot tone-${RTONE[k] ?? 'muted'}`} style={{ marginRight: 6 }} />{RESEARCH_RELEVANCE[k]?.label ?? humanize(k)}</span><strong>{v as number}</strong></div>)}</div>
          <p className="tiny muted mt">Heuristic estimates. Relevance does not prove a source supports the specific claim it is cited for.</p>
        </section>
        <section className="panel" style={{ marginTop: 0 }}>
          <h3>Publication age</h3>
          <div className="mt">{ages.map(([k, v]) => <HBar key={k} label={AGE_BAND[k] ?? humanize(k)} value={v} max={p1.reference_entries || 1} tone={k === 'within_5_years' ? 'good' : k === 'unknown' ? 'muted' : 'info'} display={String(v)} />)}</div>
          <p className="tiny muted mt">Median confirmed year: {p4.median_reliable_publication_year ?? 'not available'}. Directly relevant and recent: {p4.verified_direct_recent_count} of {p4.verified_direct_count}.</p>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head"><div><h2>Papers</h2><p>Select a paper to compare what the student submitted with what was found.</p></div></div>
        <div className="table-wrap"><table className="t">
          <thead><tr><th className="num">#</th><th>Paper</th><th>Identity</th><th>Relevance</th><th>Year</th><th>Cited in text</th></tr></thead>
          <tbody>
            {r.refs.map((ref) => (
              <Fragment key={ref.n}>
                <tr className="clickable" onClick={() => setOpen(open === ref.n ? null : ref.n)} aria-expanded={open === ref.n}>
                  <td className="num">[{ref.n}]</td>
                  <td><strong className="small">{ref.title}</strong>{ref.attention && <div className={`tiny ${ref.attention.priority === 'high' ? 'c-bad' : 'c-warn'}`}>{ref.attention.reasons[0]}</div>}</td>
                  <td><Tip text={RESEARCH_VERIFICATION[ref.verification]?.explain ?? ''}><Badge tone={RESEARCH_VERIFICATION[ref.verification]?.tone}>{RESEARCH_VERIFICATION[ref.verification]?.label ?? ref.verification}</Badge></Tip></td>
                  <td><Badge tone={RESEARCH_RELEVANCE[ref.relevance]?.tone}>{RESEARCH_RELEVANCE[ref.relevance]?.label ?? ref.relevance}</Badge><div className="tiny muted">{humanize(ref.confidence)} confidence</div></td>
                  <td className="small">{ref.year ?? 'Unknown'}<div className="tiny muted">{ref.yearSource === 'verified_metadata' ? 'Confirmed' : ref.yearSource === 'disputed' ? 'Disputed' : 'Unconfirmed'}</div></td>
                  <td className="small">{ref.linked ? 'Yes' : <span className="c-warn">No</span>}{ref.inLiteratureReview && <div className="tiny muted">In literature review</div>}</td>
                </tr>
                {open === ref.n && (
                  <tr><td colSpan={6} style={{ background: 'var(--surface-2)' }}>
                    <div className="grid g2">
                      <div className="pair" style={{ gridTemplateColumns: '1fr' }}>
                        <div className="side"><div className="h">Submitted in the bibliography (student claim)</div>{ref.title}{ref.submittedAuthors && <div className="small muted">{ref.submittedAuthors}</div>}</div>
                        <div className="side"><div className="h">Found in academic metadata</div>
                          {ref.verifiedTitle ? <>{ref.verifiedTitle}<div className="small muted">{[ref.verifiedAuthors?.join(', '), ref.verifiedYear].filter(Boolean).join(', ')} {ref.verifiedDoi && <code>{ref.verifiedDoi}</code>}</div>{ref.titleSimilarity != null && <div className="tiny muted">Title similarity {Math.round(ref.titleSimilarity * 100)}%</div>}</> : <span className="muted small">No matching record.</span>}
                        </div>
                      </div>
                      <div className="stack small">
                        {ref.explanation && <p style={{ margin: 0 }}><span className="muted">Relevance reasoning: </span>{ref.explanation}</p>}
                        <div>{ref.scores?.topical != null && <HBar label="Topic similarity" value={Math.round(ref.scores.topical * 100)} tone="info" display={`${Math.round(ref.scores.topical * 100)}%`} />}
                          {ref.scores?.methodological != null && <HBar label="Method similarity" value={Math.round(ref.scores.methodological * 100)} tone="info" display={`${Math.round(ref.scores.methodological * 100)}%`} />}
                          {ref.scores?.citation != null && <HBar label="Citation context" value={Math.round(ref.scores.citation * 100)} tone="info" display={`${Math.round(ref.scores.citation * 100)}%`} />}</div>
                        {ref.engineering && <Badge tone="good">Supports the engineering approach</Badge>}
                        {ref.attention && <Callout tone={ref.attention.priority === 'high' ? 'bad' : 'warn'} title={`${humanize(ref.attention.priority)} priority`}>{ref.attention.reasons.join('. ')}.</Callout>}
                        {ref.notes.map((n, i) => <p key={i} className="muted" style={{ margin: 0 }}>{n}</p>)}
                      </div>
                    </div>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table></div>
      </section>

      <div className="grid g2 mt-l" style={{ alignItems: 'start' }}>
        <section className="panel" style={{ marginTop: 0 }}>
          <h3>Recommended checks</h3>
          <ol className="small" style={{ paddingLeft: 18, marginBottom: 0 }}>{rep.recommended_checks.map((x: string, i: number) => <li key={i} style={{ marginBottom: 6 }}>{x}</li>)}</ol>
          <h4 className="mt-l">Key findings</h4>
          <ul className="small" style={{ paddingLeft: 18, marginBottom: 0 }}>{rep.key_findings.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
        </section>
        <section className="panel" style={{ marginTop: 0 }}>
          <h3>How to read these results</h3>
          <ul className="small muted" style={{ paddingLeft: 18, marginBottom: 0 }}>{rep.methodological_notes.map((x: string, i: number) => <li key={i}>{x}</li>)}{card.limitations.map((x, i) => <li key={'l' + i}>{x}</li>)}</ul>
          <More summary="Agent notes"><ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>{(card.details.notes ?? []).map((n: string, i: number) => <li key={i}>{n}</li>)}</ul></More>
        </section>
      </div>
    </>
  );
}
