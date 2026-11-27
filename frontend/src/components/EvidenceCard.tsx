import { Fragment } from 'react';
import { reviewedCrud } from '../lib/reviewedCrud';
import type { Evidence, EvidenceDetail } from '../api/types';
import { EVIDENCE_KIND, humanize, RESEARCH_VERIFICATION, RESEARCH_RELEVANCE } from '../lib/labels';
import { Badge, OutcomeBadge, ProvenanceBadge, SourceLoc, Tip, More } from './ui';

type AnyEvidence = (Evidence | EvidenceDetail) & Record<string, any>;

function field(e: AnyEvidence, k: string) {
  return (e as any).data?.[k] ?? (e as any)[k];
}

export function evidenceAgent(e: AnyEvidence): string {
  if ('agent' in e && e.agent) return e.agent === 'report' ? 'Student report' : humanize(e.agent);
  return e.kind === 'REPORT_EXCERPT' ? 'Student report' : e.kind === 'RESEARCH_RECORD' ? 'Research' : 'Executioner';
}

function claimLine(e: AnyEvidence) {
  if (e.kind === 'REPORT_EXCERPT') return <Badge tone="muted">Student claim</Badge>;
  if (e.kind === 'TEST_OBSERVATION') return <Badge tone="good">Independently observed</Badge>;
  if (e.kind === 'STATIC_SOURCE') return <Badge tone="info">Found in source code</Badge>;
  if (e.kind === 'RESEARCH_RECORD') return <Badge tone="info">Agent analysis</Badge>;
  return null;
}

export function ObservationTable({ details }: { details: any[] }) {
  if (!details?.length) return null;
  return (
    <div className="stack" style={{ gap: 8, marginTop: 10 }}>
      {details.map((o, i) => {
        const asserts: any[] = o.assertions || o.expected_assertions || [];
        const nums = Object.entries(o).filter(([k, v]) => typeof v !== 'object' && !['pointer', 'suite', 'test_id', 'id', 'result', 'status', 'method', 'path'].includes(k));
        const metrics = Object.entries(o.suite_metrics || {});
        const crud = reviewedCrud(o);
        return (
          <div key={i} className="sub small">
            <div className="row between">
              <span><strong>{humanize(o.suite)}</strong> suite {o.method && <span className="method">{o.method}</span>} {o.path && <code>{o.path}</code>}</span>
              {o.result && <OutcomeBadge outcome={o.result === 'PASS' ? 'PASSED' : o.result === 'FAIL' ? 'FAILED' : o.result} />}
            </div>
            {asserts.length > 0 && (
              <table className="t" style={{ marginTop: 8 }}>
                <thead><tr><th>Check</th><th className="num">Expected</th><th className="num">Actual</th><th>Result</th></tr></thead>
                <tbody>{asserts.map((a, j) => (
                  <tr key={j}><td>{humanize(a.kind || a.type)}</td><td className="num">{String(a.expected ?? '—')}</td><td className="num">{String(a.actual ?? a.observed ?? '—')}</td>
                    <td>{a.result ? <OutcomeBadge outcome={a.result === 'PASS' ? 'PASSED' : a.result === 'FAIL' ? 'FAILED' : a.result} /> : '—'}</td></tr>
                ))}</tbody>
              </table>
            )}
            {crud.length>0 && <><p className="small muted">Recorded operations within this reviewed scenario; these are not separate test verdicts.</p><div className="table-wrap"><table className="t"><thead><tr><th>Operation</th><th>Recorded observations</th></tr></thead><tbody>{crud.map(g=><tr key={g.label}><td><strong>{g.label}</strong></td><td>{g.metrics.map(m=><div key={m.key}>{humanize(m.key)}: {String(m.value)}</div>)}</td></tr>)}</tbody></table></div></>}
            {(nums.length > 0 || metrics.length > 0) && (
              <dl className="kv" style={{ marginTop: 8 }}>
                {[...nums, ...metrics].map(([k, v]) => <Fragment key={k}><dt>{humanize(k)}</dt><dd>{String(v)}</dd></Fragment>)}
              </dl>
            )}
            <div className="loc" style={{ marginTop: 6 }}>{o.pointer}</div>
          </div>
        );
      })}
    </div>
  );
}

export function EvidenceCard({ e, compact = false }: { e: AnyEvidence; compact?: boolean }) {
  const kind = EVIDENCE_KIND[e.kind];
  const outcome = field(e, 'outcome');
  const locs: any[] = field(e, 'source_locations') || (e as any).locations || [];
  const obs = field(e, 'observation_details');
  const ver = field(e, 'verification_status');
  const rel = field(e, 'relevance_classification');
  const section = field(e, 'section');
  const isAssessment = field(e, 'record_type') === 'research_assessment';
  const id = (e as any).id ?? (e as any).evidence_id;
  return (
    <div className="ev">
      <div className="ev-head">
        <span className="ev-title">{e.kind === 'REPORT_EXCERPT' && section ? section : e.label}</span>
        <span className="row" style={{ gap: 6 }}>
          {claimLine(e)}
          <Tip text={kind?.explain}><Badge tone="muted">{kind?.label ?? e.kind}</Badge></Tip>
          {outcome && <OutcomeBadge outcome={outcome} />}
          {ver && <Badge tone={RESEARCH_VERIFICATION[ver]?.tone}>{RESEARCH_VERIFICATION[ver]?.label ?? ver}</Badge>}
          {rel && <Badge tone={RESEARCH_RELEVANCE[rel]?.tone}>{RESEARCH_RELEVANCE[rel]?.label ?? rel}</Badge>}
          <ProvenanceBadge p={e.provenance} />
        </span>
      </div>
      <div className="row tiny muted" style={{ marginTop: 4, gap: 12 }}>
        <span>{evidenceAgent(e)}</span>
        {e.kind === 'REPORT_EXCERPT' && field(e, 'start') !== undefined && <span>Report characters {field(e, 'start')} to {field(e, 'end')}</span>}
        {locs.slice(0, 4).map((l, i) => <SourceLoc key={i} path={l.path} line={l.line} />)}
        {field(e, 'target')?.path && <span><span className="method">{field(e, 'target').method}</span> <code>{field(e, 'target').path}</code></span>}
      </div>
      {!compact && e.kind === 'REPORT_EXCERPT' && <div className="ev-text">{e.text}</div>}
      {!compact && e.kind === 'STATIC_SOURCE' && <p className="small" style={{ marginTop: 8 }}>{e.text}{field(e, 'recommendation') && <><br /><span className="muted">Suggested fix: </span>{field(e, 'recommendation')}</>}</p>}
      {!compact && e.kind === 'RESEARCH_RECORD' && isAssessment && <p className="small" style={{ marginTop: 8 }}>{e.text}</p>}
      {!compact && e.kind === 'TEST_OBSERVATION' && <ObservationTable details={obs} />}
      {!compact && (e.limitations?.length ?? 0) > 0 && (
        <More summary="What this evidence does not show">
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{e.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
          <div className="loc" style={{ marginTop: 6 }}>{id} at {e.locator}, artifact {String(e.artifact_sha256).slice(0, 12)}…</div>
        </More>
      )}
    </div>
  );
}
