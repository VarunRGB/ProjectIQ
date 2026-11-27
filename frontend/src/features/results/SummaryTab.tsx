import { Badge, Callout, Empty, Tip } from '../../components/ui';
import { RESEARCH_VERIFICATION } from '../../lib/labels';
import { functionalSummary } from '../../lib/functionalEvidence';
import type { ViewModel } from '../../lib/viewModel';

export interface VerifiedFinding { tone: 'good' | 'bad' | 'warn' | 'info' | 'muted'; text: string; source: string }

/** Independently observed facts, as opposed to what the report says. Used by the report too. */
export function verifiedFindings(vm: ViewModel): VerifiedFinding[] {
  const out: VerifiedFinding[] = [];
  const e = vm.executioner;
  if (e && e.mode) {
    const pc = e.counts;
    if ((pc.PASSED ?? 0) + (pc.FAILED ?? 0) > 0) out.push({ tone: pc.FAILED ? 'warn' : 'good', text: `${pc.PASSED ?? 0} planned tests passed and ${pc.FAILED ?? 0} failed in verified VM runs.`, source: 'Executioner, verified execution' });
    for (const t of e.planned.filter((x) => x.outcome === 'FAILED')) out.push({ tone: 'bad', text: `Failed: ${t.title}.`, source: `Executioner test ${t.id}` });
    const rp = e.reviewedCounts.PASSED ?? 0;
    if (rp) out.push({ tone: 'good', text: `${rp} reviewed scenarios passed within their approved scope.`, source: 'Executioner, reviewed scenarios only' });
    if (e.automatic) out.push({tone:'info',text:functionalSummary(e.automatic),source:'Executioner, automatic source-derived characterization'});
    else if (e.mode === 'STATIC') out.push({ tone: 'muted', text: 'No behaviour was executed; functional claims in the report are unverified.', source: 'Executioner, STATIC mode' });
    out.push({ tone: 'info', text: `${e.intelligence.features_discovered ?? 0} feature candidates and ${e.intelligence.server_endpoint_count ?? 0} endpoints were identified from source or documentation (recognised, not verified by this inventory).`, source: 'Executioner, static analysis' });
    const sec = e.quality?.security?.findings?.length ?? 0;
    if (sec) out.push({ tone: 'warn', text: `${sec} possible security issue${sec > 1 ? 's' : ''} flagged in the source (indicators, not confirmed vulnerabilities).`, source: 'Executioner, code quality' });
  }
  const r = vm.research?.report;
  if (r) {
    const v = r.phase_2.status_counts ?? {};
    out.push({ tone: v.metadata_mismatch || v.not_found ? 'warn' : 'good', text: `${v.verified ?? 0} of ${r.phase_1.reference_entries} references verified against academic metadata${v.metadata_mismatch ? `; ${v.metadata_mismatch} point to a different paper` : ''}.`, source: 'Research agent' });
  }
  const i = vm.integrity;
  if (i?.comparisonAvailable) out.push({ tone: 'info', text: `${i.overlap}% confirmed overlap with ${i.references.length} supplied references.`, source: 'Integrity, advisory' });
  return out;
}

export default function SummaryTab({ vm }: { vm: ViewModel }) {
  const s = vm.summary;
  const findings = verifiedFindings(vm);
  if (!s) return <div className="panel"><Empty title="No summary available" /></div>;
  return (
    <>
      <div className="grid g2" style={{ alignItems: 'start' }}>
        <section className="panel" style={{ marginTop: 0 }}>
          <div className="panel-head">
            <div><h2>What the report says</h2><p>Exact sentences selected from the report. These are the students’ claims, not verified facts.</p></div>
            <Tip text="Selected deterministically from the extracted report text. No model rewrote them, and they never contribute to marks."><Badge tone="muted">{s.method === 'EXTRACTIVE_REPORT_CLAIMS' ? 'Extracted sentences' : 'Summary'}</Badge></Tip>
          </div>
          <p className="prose" style={{ fontFamily: 'var(--serif)', fontSize: 16.5, lineHeight: 1.65 }}>{s.summary}</p>
          {s.key_points.length > 0 && <ul className="prose" style={{ fontFamily: 'var(--serif)', fontSize: 15, lineHeight: 1.6, paddingLeft: 18 }}>{s.key_points.map((k, i) => <li key={i} style={{ marginBottom: 6 }}>{k}</li>)}</ul>}
          <p className="small muted">{s.word_count ? `${s.word_count.toLocaleString()} words in the extracted report.` : ''} Figures, tables and scanned pages may be missed by text extraction.</p>
        </section>
        <section className="panel" style={{ marginTop: 0 }}>
          <div className="panel-head"><div><h2>What ProjectIQ checked independently</h2><p>Observations made by the agents, with their scope.</p></div></div>
          {!findings.length && <Callout tone="muted">Nothing could be verified independently for this submission.</Callout>}
          <div className="stack" style={{ gap: 8 }}>
            {findings.map((f, i) => (
              <div key={i} className={`callout ${f.tone === 'good' ? 'good' : f.tone === 'bad' ? 'bad' : f.tone === 'warn' ? 'warn' : f.tone === 'muted' ? 'muted' : 'info'}`}>
                <div><div>{f.text}</div><div className="tiny" style={{ opacity: 0.8 }}>{f.source}</div></div>
              </div>
            ))}
          </div>
          {vm.research?.refs.some((r) => r.verification !== 'verified') && <p className="tiny muted mt">{RESEARCH_VERIFICATION.not_found.explain}</p>}
        </section>
      </div>
    </>
  );
}
