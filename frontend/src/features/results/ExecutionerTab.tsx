import { useMemo, useState } from 'react';
import { ObservationTable } from '../../components/EvidenceCard';
import { AgentStatusBadge, Badge, Callout, Empty, Fact, HBar, More, OutcomeBadge, SourceLoc, Tip, shortSha } from '../../components/ui';
import { DIAG_CATEGORY, OWNER_INFO, TEST_OUTCOME, TEST_REASON, humanize, type Tone } from '../../lib/labels';
import type { TestRow, ViewModel } from '../../lib/viewModel';
import { Donut, Legend, Ring, toneFor } from '../../components/Visuals';

const OUTCOMES = ['PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE', 'NOT_TESTED'];
const OUT_TONE: Record<string, string> = { PASSED: 'good', FAILED: 'bad', BLOCKED: 'warn', INCONCLUSIVE: 'pencil', NOT_TESTED: 'muted' };
const SEV_TONE: Record<string, Tone> = { HIGH: 'bad', MEDIUM: 'warn', LOW: 'muted', ADVISORY: 'muted', INFO: 'info' };

const LIMIT_TEXT: Record<string, string> = {
  FUNCTIONAL_BEHAVIOR_NOT_EXECUTED: 'Functional behaviour was not executed (STATIC mode).',
  FUNCTIONAL_ORACLES_STILL_REQUIRED: 'Some planned tests still need reviewed expected results before they can run.',
  TEST_TARGETS_UNSUPPORTED_BY_RUNNER: 'Some features use technology the runner cannot test.',
  STATIC_QUALITY_COVERAGE_OR_CONFIDENCE_LIMITED: 'Code-quality analysis has limited coverage or confidence for this language.',
  DIAGNOSIS_EVIDENCE_PARTIAL: 'Failure diagnosis had only partial evidence.',
  PHASE6_DIAGNOSIS_UNAVAILABLE: 'Failure diagnosis was unavailable.',
};

const MODE_INFO = {
  STATIC: { title: 'STATIC', text: 'Reads the pinned repository: inventory, architecture, routes, features, planned tests and code quality. Never runs student code.' },
  REVIEWED: { title: 'REVIEWED', text: 'Adds operator-approved test suites run in disposable Linux VMs, for an exact commit and reviewed catalogs only.' },
  RESUME: { title: 'RESUME', text: 'Re-verifies a previous result’s receipts and reuses eligible saved evidence; can run only genuinely new approved work.' },
};

function TestItem({ t, vm }: { t: TestRow; vm: ViewModel }) {
  const ev = useMemo(() => (vm.rec.evidence?.items ?? []).find((e) => e.kind === 'TEST_OBSERVATION' && (e.data.test_id === t.id || `${e.data.suite}:${e.data.check_id}` === t.id)), [vm, t.id]);
  const details = ev?.data.observation_details as any[] | undefined;
  return (
    <details className="test">
      <summary>
        <OutcomeBadge outcome={t.outcome} />
        <span style={{ minWidth: 0 }}>
          <strong>{t.title}</strong>
          <span className="tiny muted" style={{ display: 'block' }}>
            {t.target?.method && <span className="method">{t.target.method}</span>} {t.target?.path && <code>{t.target.path}</code>} {humanize(t.category)}{t.suite ? `, ${t.suite} suite` : ''}
          </span>
        </span>
        <span className="row" style={{ gap: 6 }}>
          {t.reused === true && <Tip text="Observed in an earlier verified run and reused after its receipts were re-checked."><Badge tone="info">Reused evidence</Badge></Tip>}
          {t.reused === false && <Tip text="Executed in a fresh, verified VM run during this Executioner call."><Badge tone="good">Fresh run</Badge></Tip>}
        </span>
      </summary>
      <div className="test-body">
        <p className="small" style={{ margin: 0 }}><span className="muted">Why this outcome: </span>{TEST_REASON[t.reason] ?? humanize(t.reason)}. {TEST_OUTCOME[t.outcome]?.explain}</p>
        {t.expected.length > 0 && !details?.length && (
          <div className="small mt"><span className="muted">Expected: </span>{t.expected.map((a: any) => `${humanize(a.kind)} ${a.operator ?? ''} ${a.expected ?? ''}`).join('; ')}</div>
        )}
        {details?.length ? <ObservationTable details={details} /> : t.observations.length > 0 && <ObservationTable details={t.observations} />}
        {t.related.length > 0 && <p className="small muted mt">Related reviewed scenarios: {t.related.map((r: any) => `${r.check_id} (${humanize(r.outcome)})`).join(', ')}. These are related, not this exact test.</p>}
        {t.planning_status && <p className="tiny muted" style={{ marginTop: 8 }}>Test {t.id}, planning status {humanize(t.planning_status)}.</p>}
      </div>
    </details>
  );
}

export default function ExecutionerTab({ vm }: { vm: ViewModel }) {
  const e = vm.executioner;
  const [filter, setFilter] = useState<string>('ALL');
  const card = vm.rec.result.agents.executioner;
  if (!e) {
    return <div className="panel"><Empty title="Executioner did not run">{vm.rec.result.submission.repository_url ? 'The agent was not requested for this evaluation.' : 'This is a report-only submission, so there was no repository to inspect or test. Criteria that need source code or test results should be marked not applicable in the rubric.'}</Empty></div>;
  }
  if (card.status === 'FAILED' || !e.mode) {
    return <div className="panel"><div className="panel-head"><h2>Executioner</h2><AgentStatusBadge status={card.status} /></div><Callout tone="bad" title="No usable Executioner result">{card.limitations.join(' ')} {card.error_code && <code>{card.error_code}</code>}</Callout></div>;
  }
  const demo = e.card.details.trusted_demo;
  const pi = e.intelligence;
  const planned = e.planned; const reviewed = e.reviewed;
  const pc = e.counts; const rc = e.reviewedCounts;
  const executedPlanned = (pc.PASSED ?? 0) + (pc.FAILED ?? 0);
  const all = [...planned, ...reviewed];
  const list = filter === 'ALL' ? all : filter === 'REVIEWED' ? reviewed : all.filter((t) => t.outcome === filter);
  const langs = Object.entries(pi.languages ?? {}) as [string, number][];
  const langTotal = langs.reduce((a, [, v]) => a + v, 0);
  const q = e.quality; const qi = q?.quality ?? {};
  const owners = ['student', 'security', 'environment', 'unsupported', 'evidence'];
  const imported = vm.rec.result.collection_mode === 'IMPORTED';
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <div><h2>Repository evaluation</h2><p>{pi.overview}</p></div>
          <AgentStatusBadge status={card.status} />
        </div>
        <div className="grid g4">
          <Fact value={demo ? 'TRUSTED_DEMO' : e.mode} label="Mode requested" scope={demo ? 'Separate automatic evidence + static matrix' : e.effectiveMode !== e.mode ? `Effective evidence: ${e.effectiveMode}` : undefined} />
          <Fact value={shortSha(e.commit)} label="Pinned commit" scope={e.repository ?? undefined} />
          <Fact value={`${e.freshSuites} fresh, ${e.reusedSuites} reused`} label="Verified test suites" tip="Fresh: executed in a new disposable VM during this call. Reused: saved evidence whose receipts were re-verified." />
          <Fact value={demo ? (demo.vm_attempt_count_uncertain ? 'Uncertain' : `${demo.vm_start_attempts ?? 0} of 1`) : `${e.card.details.vm_attempts_scheduled ?? 0} of ${e.card.details.vm_attempt_budget ?? 0}`} label="VM launches used" />
        </div>
        <div className="grid g3 mt-l">
          {(Object.keys(MODE_INFO) as (keyof typeof MODE_INFO)[]).map((m) => (
            <div key={m} className="sub" style={!demo && m === e.mode ? { borderColor: 'var(--ink)', background: '#f4f6fb' } : undefined}>
              <div className="row between"><strong>{MODE_INFO[m].title}</strong>{!demo && m === e.mode && <Badge tone="info">This result</Badge>}</div>
              <p className="small muted" style={{ margin: '4px 0 0' }}>{MODE_INFO[m].text}</p>
            </div>
          ))}
        </div>
        <div className="mt">
          {demo ? <Callout tone="info" title="Automatic trusted demo">{humanize(demo.status)}. {humanize(demo.selected_path)}. {demo.functional_tests_executed ?? 0} independent tests and {demo.workflow_requests_attempted ?? 0} workflow requests reported. The original STATIC matrix below is retained and is not automatic functional coverage. No reviewed grading credit; see the separate demo evidence.</Callout> : e.mode === 'STATIC'
            ? <Callout tone="info">Nothing was executed. Planned tests are specifications only, so functional criteria need verified runs or your own judgment.</Callout>
            : <Callout tone="warn" title={imported ? 'Imported reviewed evidence' : 'Reviewed execution'}>{imported ? `This ${e.mode} result was produced by a separate Executioner run and imported with operator acknowledgment. Reviewed and resume retain their specialized composition path.` : 'Only operator-approved suites ran.'} VM runs are not security-attested for arbitrary student code.</Callout>}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><div><h2>What the code contains</h2><p>Found by reading the source. Recognised does not mean runnable, and runnable does not mean tested.</p></div></div>
        <div className="grid g2">
          <div>
            <h4>Technologies</h4>
            <div className="row mt" style={{ gap: 6 }}>
              {(pi.technologies ?? []).map((t: any) => <Tip key={t.name} text={`${humanize(t.category)}, ${String(t.confidence).toLowerCase()} confidence`}><Badge tone={t.confidence === 'HIGH' ? 'info' : 'muted'}>{t.name}</Badge></Tip>)}
            </div>
            <h4 className="mt-l">Languages by lines of code</h4>
            <div className="mt">{langs.map(([l, n]) => <HBar key={l} label={l} value={n} max={langTotal} tone="info" display={String(n)} />)}</div>
            <dl className="kv mt-l">
              <dt>Architecture</dt><dd>{humanize(pi.architecture)} <span className="muted small">({String(pi.architecture_confidence).toLowerCase()} confidence)</span></dd>
              <dt>Runtime adapter</dt><dd>{e.card.details.runtime_plan?.adapter ? <code>{e.card.details.runtime_plan.adapter}</code> : <span className="muted">None supported</span>} <span className="small muted">{humanize(e.card.details.runtime_plan?.capability_status)}</span></dd>
              <dt>Existing tests</dt><dd>{pi.existing_tests ?? 0} test file{pi.existing_tests === 1 ? '' : 's'}</dd>
              <dt>Database schema</dt><dd>{(pi.database_schema_files ?? []).map((f: string) => <code key={f} style={{ marginRight: 6 }}>{f}</code>)}{!pi.database_schema_files?.length && <span className="muted">None found</span>}</dd>
              <dt>Environment names</dt><dd>{(pi.environment_variable_names ?? []).join(', ') || <span className="muted">None</span>} <span className="tiny muted">(values never read)</span></dd>
            </dl>
          </div>
          <div>
            <h4>Features ({pi.features_discovered ?? 0})</h4>
            <table className="t mt">
              <thead><tr><th>Feature</th><th>Found in</th><th>Confidence</th></tr></thead>
              <tbody>{(pi.features ?? []).map((f: any) => (
                <tr key={f.name}><td>{f.name}{f.evidence?.[0] && <div><SourceLoc path={f.evidence[0].path} line={f.evidence[0].line} /></div>}</td>
                  <td><Badge tone={f.origin === 'source' ? 'info' : 'muted'}>{f.origin === 'source' ? 'Source code' : f.origin === 'readme' ? 'README only' : humanize(f.origin)}</Badge></td>
                  <td className="small">{humanize(f.confidence)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
        <More summary={`Server endpoints (${pi.server_endpoint_count ?? 0})`}>
          <p className="small muted">Unique path and method records seen in the source. Not verified URLs.</p>
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Method</th><th>Path</th><th>Declared as</th><th className="num">Client calls</th><th>Source</th></tr></thead>
            <tbody>{(pi.server_endpoints ?? []).map((s: any, i: number) => (
              <tr key={i}><td><span className="method">{s.intended_method}</span>{s.method_basis === 'CLIENT_REFERENCE_HINT_ONLY' && <Tip text="Method inferred from client code only." />}</td><td><code>{s.path}</code></td><td className="small">{humanize(s.kind)}</td><td className="num">{s.client_references}</td><td>{s.evidence?.[0] && <SourceLoc path={s.evidence[0].path} line={s.evidence[0].line} />}</td></tr>
            ))}</tbody>
          </table></div>
        </More>
        {(pi.findings ?? []).length > 0 && (
          <More summary="Repository observations">
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{pi.findings.map((f: any, i: number) => <li key={i}><Badge tone={SEV_TONE[f.severity] ?? 'muted'}>{humanize(f.severity)}</Badge> {f.message} {f.evidence?.[0] && <SourceLoc path={f.evidence[0].path} line={f.evidence[0].line} />}</li>)}</ul>
          </More>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <div><h2>Tests</h2><p>Planned tests come from the project; reviewed scenarios come from operator-approved suites. They are counted separately and never added together.</p></div>
        </div>
        <div className="grid g2">
          <div className="sub">
            <div className="row between"><h4>Planned tests ({planned.length})</h4><span className="small muted">{executedPlanned} executed</span></div>
            <div className="row mt" style={{ gap: 22, flexWrap: 'nowrap' }}>
              <Donut parts={OUTCOMES.map((o) => ({ value: pc[o] ?? 0, tone: OUT_TONE[o] as any, label: TEST_OUTCOME[o].label }))} center={String(planned.length)} sub="planned" />
              <Legend parts={OUTCOMES.map((o) => ({ value: pc[o] ?? 0, tone: OUT_TONE[o] as any, label: TEST_OUTCOME[o].label }))} />
              <Ring value={executedPlanned ? pc.PASSED ?? 0 : null} max={executedPlanned || 1} size={86} label="Pass rate" sub={executedPlanned ? 'Of executed tests' : 'Nothing executed'} tone={toneFor(executedPlanned ? ((pc.PASSED ?? 0) / executedPlanned) * 100 : null)} />
            </div>
          </div>
          <div className="sub">
            <div className="row between"><h4>Reviewed scenarios ({reviewed.length})</h4><Tip text="A reviewed scenario checks one approved behaviour. It does not cover a whole route or every requirement."><span className="small muted">Not full coverage</span></Tip></div>
            <div className="row mt" style={{ gap: 22, flexWrap: 'nowrap' }}>
              <Donut parts={OUTCOMES.map((o) => ({ value: rc[o] ?? 0, tone: OUT_TONE[o] as any, label: TEST_OUTCOME[o].label }))} center={String(reviewed.length)} sub="scenarios" />
              {reviewed.length ? <Legend parts={OUTCOMES.filter((o) => rc[o]).map((o) => ({ value: rc[o] ?? 0, tone: OUT_TONE[o] as any, label: TEST_OUTCOME[o].label }))} /> : <span className="small muted">None in this run</span>}
            </div>
          </div>
        </div>

        {e.suites.length > 0 && (
          <div className="table-wrap mt-l"><table className="t">
            <thead><tr><th>Suite</th><th>Result</th><th>Execution</th><th>Evidence</th><th>Note</th></tr></thead>
            <tbody>{e.suites.map((s: any) => (
              <tr key={s.kind}>
                <td><strong>{humanize(s.kind)}</strong></td>
                <td>{s.vm_verified ? <OutcomeBadge outcome={s.state === 'PASS' ? 'PASSED' : s.state === 'FAIL' ? 'FAILED' : s.state} /> : <OutcomeBadge outcome="BLOCKED" />}</td>
                <td className="small">{s.vm_verified ? 'Verified VM run' : 'Not verified'}{s.cleanup === 'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' && <div className="tiny muted">VM stopped, storage removed</div>}</td>
                <td>{s.vm_verified ? (s.reused_from_prior ? <Badge tone="info">Reused</Badge> : <Badge tone="good">Fresh</Badge>) : <span className="muted small">None</span>}</td>
                <td className="small">{s.reason ? <><span className="c-muted">Execution infrastructure: </span>{humanize(s.reason)}</> : humanize(s.result_status)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}

        <div className="row mt-l" role="group" aria-label="Filter tests">
          {['ALL', 'FAILED', 'PASSED', 'BLOCKED', 'INCONCLUSIVE', 'NOT_TESTED', 'REVIEWED'].map((f) => {
            const n = f === 'ALL' ? all.length : f === 'REVIEWED' ? reviewed.length : all.filter((t) => t.outcome === f).length;
            return <button key={f} className={`btn sm ${filter === f ? 'primary' : ''}`} onClick={() => setFilter(f)} disabled={!n}>{f === 'ALL' ? 'All' : f === 'REVIEWED' ? 'Reviewed scenarios' : TEST_OUTCOME[f].label} ({n})</button>;
          })}
        </div>
        <div className="mt">{list.map((t) => <TestItem key={t.scope + t.id} t={t} vm={vm} />)}{!list.length && <Empty title="No tests in this group" />}</div>
      </section>

      <section className="panel">
        <div className="panel-head"><div><h2>Diagnosis</h2><p>Who each issue belongs to. Hypotheses are unproven; a failed check alone does not establish student fault.</p></div></div>
        {!e.diagnoses.length && <Empty title="No diagnoses" />}
        <div className="stack">
          {owners.filter((o) => e.diagnoses.some((d) => d.owner === o)).map((o) => (
            <div key={o} className="sub">
              <div className="row between"><h4>{OWNER_INFO[o].label}</h4><Badge tone={OWNER_INFO[o].tone}>{e.diagnoses.filter((d) => d.owner === o).length}</Badge></div>
              <p className="tiny muted" style={{ margin: '2px 0 8px' }}>{OWNER_INFO[o].explain}</p>
              {e.diagnoses.filter((d) => d.owner === o).map((d, i) => {
                const hyps = e.hypotheses.filter((h: any) => d.test_ids?.includes(h.test_id));
                return (
                  <div key={i} style={{ borderTop: '1px solid var(--line)', padding: '10px 0' }}>
                    <div className="row between"><strong className="small">{d.title}</strong><span className="row" style={{ gap: 6 }}><Badge tone="muted">{DIAG_CATEGORY[d.category]?.label ?? humanize(d.category)}</Badge><Badge tone="muted">{d.priority}</Badge></span></div>
                    <p className="small" style={{ margin: '4px 0' }}>{d.explanation} <span className="muted">Suggested action: {d.recommended_action}</span></p>
                    {d.test_ids?.length > 0 && <p className="tiny muted" style={{ margin: 0 }}>Tests: {d.test_ids.map((t: string) => planned.find((p) => p.id === t)?.title ?? t).join('; ')}</p>}
                    {hyps.map((h: any, j: number) => (
                      <div key={j} className="small" style={{ marginTop: 6, paddingLeft: 10, borderLeft: '3px solid var(--line-strong)' }}>
                        <span className="muted">Possible cause ({String(h.confidence).toLowerCase()} confidence, unproven): </span>{h.hypothesis} {h.source_locations?.map((l: any, k: number) => <SourceLoc key={k} path={l.path} line={l.line} />)}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </section>

      {q && (
        <section className="panel">
          <div className="panel-head"><div><h2>Code quality</h2><p>Static, heuristic indicator. It is not a grade and never sets marks on its own.</p></div></div>
          <div className="grid g4">
            <Fact value={qi.score ?? null} label="Maintainability indicator" unit={qi.score != null ? ' / 100' : ''} tip={qi.overall_score_status === 'WITHHELD_LIMITED_COVERAGE' ? `Withheld: coverage too limited for an overall figure. Measured dimensions alone give ${qi.measured_dimensions_indicator}.` : `Confidence ${String(qi.confidence).toLowerCase()}.`} scope={qi.overall_score_status === 'WITHHELD_LIMITED_COVERAGE' ? 'Withheld: limited coverage' : `${humanize(qi.confidence)} confidence`} />
            <Fact value={q.summary?.analyzed_files} label="Files analysed" scope={`${q.summary?.production_code_lines ?? 0} production lines`} />
            <Fact value={q.summary?.functions_found} label="Functions found" />
            <Fact value={q.findings?.length ?? 0} label="Findings" scope={`${q.security?.findings?.length ?? 0} security indicators`} />
          </div>
          <div className="grid g2 mt-l">
            <div>
              <h4>Dimensions</h4>
              <div className="vis-row mt">{Object.entries(qi.dimensions ?? {}).map(([k, v]) => <Ring key={k} value={v as number | null} size={78} label={humanize(k)} tone={toneFor(v as number | null, 80, 60)} />)}</div>
              {Object.values(qi.dimensions ?? {}).some((v) => v === null) && <p className="tiny muted">n/a: not measurable for this language. Excluded from the indicator, not counted as zero.</p>}
            </div>
            <div>
              <h4>Analysis coverage</h4>
              <dl className="kv mt small">
                <dt>Source read</dt><dd>{Math.round((q.coverage?.source_ratio ?? 0) * 100)}%</dd>
                <dt>Function bodies parsed</dt><dd>{Math.round((q.coverage?.function_body_ratio ?? 0) * 100)}%</dd>
                <dt>Complexity measured</dt><dd>{Math.round((q.coverage?.ast_complexity_share ?? 0) * 100)}% of function lines</dd>
                <dt>Duplicated lines</dt><dd>{q.coverage?.duplicate_production_lines ?? 0} ({((q.coverage?.duplicate_production_ratio ?? 0) * 100).toFixed(1)}%)</dd>
              </dl>
            </div>
          </div>
          <div className="table-wrap mt-l"><table className="t">
            <thead><tr><th>Finding</th><th>Severity</th><th>Where</th><th>Suggested fix</th></tr></thead>
            <tbody>{(q.findings ?? []).map((f: any, i: number) => (
              <tr key={i}><td><strong className="small">{humanize(f.code)}</strong><div className="small">{f.message}</div></td>
                <td><Badge tone={SEV_TONE[f.severity] ?? 'muted'}>{humanize(f.severity)}</Badge>{f.code.startsWith('SEC_') && <div className="tiny muted">Security indicator</div>}</td>
                <td><SourceLoc path={f.path} line={f.line} /></td><td className="small">{f.recommendation}</td></tr>
            ))}</tbody>
          </table></div>
        </section>
      )}

      <section className="panel">
        <div className="panel-head"><h2>Limitations of this result</h2></div>
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {e.limitations.map((l) => <li key={l}>{LIMIT_TEXT[l] ?? humanize(l)}</li>)}
          {e.reviewRequests.map((r: any, i: number) => <li key={'r' + i}>Operator review requested: {humanize(r.type)} ({humanize(r.stage)})</li>)}
          {card.limitations.map((l, i) => <li key={'c' + i}>{l}</li>)}
        </ul>
      </section>
    </>
  );
}
