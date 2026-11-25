import { Link } from 'react-router-dom';
import type { AgentName } from '../../api/types';
import { CoverageMeter, GradeHeadline, LedgerBar } from '../../components/Ledger';
import { AgentStatusBadge, Badge, Callout, Fact, ProvenanceBadge, Tip } from '../../components/ui';
import { AGENT_INFO, humanize } from '../../lib/labels';
import { pct, type ViewModel } from '../../lib/viewModel';

export interface AttentionItem { tone: 'warn' | 'bad' | 'info' | 'muted'; text: string; to: string; action: string }

export function attentionItems(vm: ViewModel, base: string): AttentionItem[] {
  const a = vm.assessment; const out: AttentionItem[] = [];
  const un = a.criteria.filter((c) => c.status === 'UNASSESSED');
  if (un.length) out.push({ tone: 'warn', text: `${un.map((c) => c.title).join(', ')} could not be assessed automatically. Score ${un.length === 1 ? 'it' : 'them'} from the evidence to release the complete grade.`, to: `${base}/grading?criterion=${un[0].id}`, action: 'Assess now' });
  const prop = a.criteria.filter((c) => c.status === 'PROPOSED' || c.status === 'OBJECTIVE_PROVISIONAL');
  if (prop.length) out.push({ tone: 'info', text: `${prop.length} AI-proposed or objective mark${prop.length > 1 ? 's are' : ' is'} waiting for your approval.`, to: `${base}/grading`, action: 'Review proposals' });
  for (const f of vm.rec.result.agent_failures) out.push({ tone: 'bad', text: `The ${AGENT_INFO[f].name} agent failed. Criteria that need its evidence stay unassessed; nothing is scored as zero.`, to: `${base}/${f === 'summarizer' ? 'summary' : f}`, action: 'See details' });
  if (vm.provenance.acknowledgmentRequired) out.push({ tone: 'bad', text: 'Some imported results are not acknowledged. Their evidence is shown for information only and cannot support marks.', to: `${base}/activity`, action: 'See provenance' });
  const ex = vm.executioner;
  if (ex && ex.counts.FAILED) out.push({ tone: 'bad', text: `${ex.counts.FAILED} planned test${ex.counts.FAILED > 1 ? 's' : ''} failed in a verified run. Check the expectation before attributing the failure to the student.`, to: `${base}/executioner`, action: 'See failed tests' });
  const blocked = ex?.suites.filter((s: any) => !s.vm_verified).length ?? 0;
  if (blocked) out.push({ tone: 'muted', text: `${blocked} test suite${blocked > 1 ? 's' : ''} could not run because of the execution environment. This is not a student defect.`, to: `${base}/executioner`, action: 'See why' });
  const high = vm.research?.report?.attention_items?.filter((x: any) => x.priority === 'high').length ?? 0;
  if (high) out.push({ tone: 'warn', text: `${high} reference${high > 1 ? 's' : ''} could not be verified or point to a different paper.`, to: `${base}/research`, action: 'Check references' });
  const verb = vm.integrity?.byCategory?.verbatim;
  if (verb) out.push({ tone: 'warn', text: `${pct(verb)} of checked words match a reference word for word. Read both passages before drawing any conclusion.`, to: `${base}/integrity`, action: 'Compare passages' });
  return out;
}

function agentFacts(vm: ViewModel, n: AgentName) {
  const card = vm.rec.result.agents[n];
  if (card.status === 'NOT_RUN') return <p className="small muted">{n === 'executioner' ? 'No repository was submitted, so the code was not inspected.' : n === 'research' ? 'Research is not part of this rubric.' : 'Not requested.'}</p>;
  if (card.status === 'FAILED') return <p className="small c-bad">{card.limitations[0] ?? 'The agent failed.'} {card.error_code && <code>{card.error_code}</code>}</p>;
  if (n === 'executioner' && vm.executioner) {
    const e = vm.executioner;
    const run = (e.counts.PASSED ?? 0) + (e.counts.FAILED ?? 0);
    return (
      <div className="g3 grid" style={{ gap: 12 }}>
        <Fact value={e.intelligence.server_endpoint_count ?? null} label="Endpoints found" />
        <Fact value={run ? `${e.counts.PASSED ?? 0}/${run}` : null} label="Planned tests passed" tip="Only exact planned tests with verified observations. Untested cases are not failures." />
        <Fact value={e.effectiveMode || null} label="Mode" />
      </div>
    );
  }
  if (n === 'research' && vm.research?.report) {
    const p = vm.research.report;
    return (
      <div className="g3 grid" style={{ gap: 12 }}>
        <Fact value={p.phase_1.reference_entries} label="References" />
        <Fact value={p.phase_2.status_counts?.verified ?? 0} label="Verified" tip="Identity matched an academic metadata record." />
        <Fact value={p.phase_4.recent_reliable_percent === null ? null : pct(p.phase_4.recent_reliable_percent, 0)} label="Recent (5 yrs)" tip="Share of references with a confirmed year that are under five years old." />
      </div>
    );
  }
  if (n === 'integrity' && vm.integrity) {
    const i = vm.integrity; const ai = i.ai;
    return (
      <div className="g3 grid" style={{ gap: 12 }}>
        <Fact value={i.overlap === null ? null : pct(i.overlap)} label="Reference overlap" tip="Confirmed verbatim, near-verbatim and paraphrased words as a share of checked words. Not a plagiarism verdict." />
        <Fact value={i.references.length} label="References compared" />
        <Fact value={ai?.status === 'COMPLETED' ? pct(ai.estimated_percent) : null} label="AI-like writing" tip="Advisory estimate. Never proof and never a deduction." />
      </div>
    );
  }
  if (n === 'summarizer' && vm.summary) return <p className="small" style={{ margin: 0 }}>{vm.summary.summary?.slice(0, 220)}{(vm.summary.summary?.length ?? 0) > 220 ? '…' : ''}</p>;
  return null;
}

export default function Overview({ vm, base }: { vm: ViewModel; base: string }) {
  const a = vm.assessment;
  const items = attentionItems(vm, base);
  const tabOf: Record<AgentName, string> = { executioner: 'executioner', research: 'research', integrity: 'integrity', summarizer: 'summary' };
  return (
    <>
      <section className="panel" aria-label="Grade">
        <div className="ledger">
          <div className="grade-block">
            <GradeHeadline a={a} />
            <div style={{ marginTop: 'auto', paddingTop: 14 }}><CoverageMeter a={a} /></div>
          </div>
          <div>
            <div className="panel-head" style={{ marginBottom: 12 }}>
              <div>
                <h2>Mark sheet</h2>
                <p>Each block is a criterion. Its width is the share of the grade; the filled part is the mark. Select a block to review it.</p>
              </div>
              <span className="small muted">{vm.rubric.title}, {a.max_points} marks</span>
            </div>
            <LedgerBar a={a} linkBase={`${base}/grading`} />
          </div>
        </div>
      </section>

      <div className="grid g2 mt-l" style={{ alignItems: 'start' }}>
        <section className="panel" aria-label="Needs attention">
          <div className="panel-head"><h2>What needs your attention</h2></div>
          {!items.length && <Callout tone="good">Nothing outstanding. All marks are approved.</Callout>}
          <div className="stack">
            {items.map((it, i) => (
              <div key={i} className={`callout ${it.tone}`}>
                <div style={{ flex: 1 }}>{it.text}</div>
                <Link className="btn sm" to={it.to}>{it.action}</Link>
              </div>
            ))}
          </div>
        </section>

        <section className="panel" aria-label="Evidence sources">
          <div className="panel-head">
            <div><h2>Where the evidence comes from</h2><p>Marks may cite only evidence that is eligible and verified against its saved receipt.</p></div>
          </div>
          <table className="t">
            <thead><tr><th>Agent</th><th>This run</th><th>Provenance</th></tr></thead>
            <tbody>
              {(vm.agentOrder).map((n) => {
                const p = vm.provenance.perAgent[n];
                return (
                  <tr key={n}>
                    <td><strong>{AGENT_INFO[n].name}</strong></td>
                    <td><Tip text={p.action === 'REUSED' ? 'Reused from the saved collection. The agent was not rerun.' : p.action === 'COLLECTED' ? 'Collected live in this evaluation.' : p.action === 'SKIPPED' ? 'Not needed for this rubric or submission.' : 'Not available.'}><Badge tone={p.action === 'COLLECTED' ? 'good' : p.action === 'REUSED' ? 'info' : 'muted'}>{humanize(p.action)}</Badge></Tip></td>
                    <td>{p.provenance ? <ProvenanceBadge p={p.provenance as any} /> : <span className="small muted">{p.check === 'MATCHED' ? 'Matched' : humanize(p.check)}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {vm.provenance.acknowledged && (
            <p className="small muted mt">An operator acknowledged that the imported results belong to this submission. Acknowledgment is recorded, but it is not independent authentication; re-verify before relying on it for a final decision.</p>
          )}
        </section>
      </div>

      <h2 className="mt-l" style={{ marginBottom: 12 }}>Agent results</h2>
      <div className="grid g2">
        {vm.agentOrder.map((n) => {
          const card = vm.rec.result.agents[n];
          return (
            <section key={n} className="panel" style={{ marginTop: 0 }}>
              <div className="panel-head" style={{ marginBottom: 10 }}>
                <div><h3>{AGENT_INFO[n].name}</h3><p className="small">{AGENT_INFO[n].role}</p></div>
                <AgentStatusBadge status={card.status} />
              </div>
              {agentFacts(vm, n)}
              <div className="mt"><Link to={`${base}/${tabOf[n]}`} className="btn sm">Open {AGENT_INFO[n].name.toLowerCase()} results</Link></div>
            </section>
          );
        })}
      </div>
    </>
  );
}
