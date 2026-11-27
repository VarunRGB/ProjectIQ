import { Badge, Empty, fmtDate, shortSha } from '../../components/ui';
import { AGENT_INFO, humanize } from '../../lib/labels';
import type { ViewModel } from '../../lib/viewModel';
import type { AgentName } from '../../api/types';

export default function ActivityTab({ vm }: { vm: ViewModel }) {
  const r = vm.rec.result; const h = vm.rec.review.history; const ev = vm.rec.evidence;
  return (
    <div className="grid g2" style={{ alignItems: 'start' }}>
      <section className="panel" style={{ marginTop: 0 }}>
        <h2>Your review history</h2>
        {!h.length && <Empty title="No decisions yet">Approvals, overrides and rubric changes will appear here.</Empty>}
        <ul className="log" style={{ listStyle: 'none', padding: 0, marginTop: 12, maxHeight: 'none' }}>
          {h.map((x, i) => <li key={i}><span className="t">{new Date(x.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span><span>{x.summary}<div className="tiny muted">{fmtDate(x.at)}</div></span></li>)}
        </ul>
        <p className="tiny muted mt">Teacher-reviewed is a local status, not authenticated institutional approval.</p>
      </section>
      <section className="panel" style={{ marginTop: 0 }}>
        <h2>Run record</h2>
        <dl className="kv small mt">
          <dt>Result schema</dt><dd>{r.schema_version}, grading policy {r.evaluation_policy}</dd>
          <dt>Rubric mode</dt><dd>{humanize(r.rubric_mode)}{r.legacy_judge_executed === false && ', legacy report judge not used'}</dd>
          <dt>Collection</dt><dd>{humanize(r.collection_mode)}</dd>
          <dt>Report SHA-256</dt><dd><code>{shortSha(r.submission.report_sha256)}…</code></dd>
          {ev && <><dt>Evidence bundle</dt><dd><code>{shortSha(ev.evaluation_sha256)}…</code>, {ev.items.length} items</dd></>}
          {ev?.repository_commit && <><dt>Commit</dt><dd><code>{ev.repository_commit}</code></dd></>}
          <dt>Rubric exit code</dt><dd>{r.rubric_exit_code}</dd>
        </dl>
        <h4 className="mt-l">Agent receipts</h4>
        <table className="t mt"><tbody>{(Object.keys(r.agents) as AgentName[]).map((a) => (
          <tr key={a}><td>{AGENT_INFO[a].name}</td><td>{humanize(r.agent_actions[a])}</td><td><Badge tone={r.agents[a].evidence_check === 'MATCHED' ? 'good' : r.agents[a].evidence_check === 'NOT_RUN' ? 'muted' : 'bad'}>{humanize(r.agents[a].evidence_check)}</Badge></td></tr>
        ))}</tbody></table>
        {Object.keys(r.decision_reuse.dropped_decisions).length > 0 && (
          <><h4 className="mt-l">Decisions not carried over</h4><ul className="small">{Object.entries(r.decision_reuse.dropped_decisions).map(([k, v]) => <li key={k}>{k}: {humanize(v)}</li>)}</ul></>
        )}
        <h4 className="mt-l">Artifacts</h4>
        <ul className="small" style={{ paddingLeft: 18 }}>{Object.entries(r.artifacts).map(([k, v]) => <li key={k}>{humanize(k)}: <code>{v}</code></li>)}</ul>
        <h4 className="mt-l">Evaluation limitations</h4>
        <ul className="small muted" style={{ paddingLeft: 18, marginBottom: 0 }}>{r.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
      </section>
    </div>
  );
}
