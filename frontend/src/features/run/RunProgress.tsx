import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { backend } from '../../api';
import type { RunStatus } from '../../api/types';
import { Callout } from '../../components/ui';
import { AGENT_INFO } from '../../lib/labels';

const LANES: { key: keyof RunStatus['agents']; name: string; role: string }[] = [
  { key: 'summarizer', name: AGENT_INFO.summarizer.name, role: 'Key sentences from the report' },
  { key: 'integrity', name: AGENT_INFO.integrity.name, role: 'Similarity and AI-like writing' },
  { key: 'research', name: AGENT_INFO.research.name, role: 'Bibliography, identity, relevance' },
  { key: 'executioner', name: AGENT_INFO.executioner.name, role: 'Repository analysis and tests' },
  { key: 'rubric', name: 'Rubric engine', role: 'Evidence bundle and proposed marks' },
];
const ICON: Record<string, string> = { done: '✓', partial: '!', failed: '×', skipped: '–', running: '', waiting: '' };

function clock(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export default function RunProgress() {
  const { runId } = useParams();
  const [run, setRun] = useState<RunStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () => backend.getRun(runId!).then((r) => { if (live) setRun(r); }).catch((e) => live && setErr(e.message));
    tick();
    const t = setInterval(tick, 500);
    return () => { live = false; clearInterval(t); };
  }, [runId]);
  if (err) return <div className="page"><Callout tone="bad" title="Run not found">{err}</Callout></div>;
  if (!run) return <div className="page"><p className="muted">Connecting to the run…</p></div>;
  const current = LANES.find((l) => run.agents[l.key].state === 'running');
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{run.title}</h1><p>{run.state === 'RUNNING' ? (current ? `${current.name}: ${run.agents[current.key].detail}` : 'Preparing…') : run.state === 'COMPLETED' ? 'Evaluation finished. Partial results and failures are kept and shown in the results.' : 'The run was cancelled. Completed artifacts were kept.'}</p></div>
        <div style={{ textAlign: 'right' }}>
          <div className="clock" aria-live="off">{clock(run.elapsed_ms)}</div>
          <div className="small muted">elapsed</div>
        </div>
      </div>
      <div className="grid g2" style={{ gridTemplateColumns: 'minmax(0, 1.3fr) minmax(0, 1fr)', alignItems: 'start' }}>
        <section className="panel" style={{ marginTop: 0 }} aria-live="polite">
          <h2>Agents</h2>
          <div className="mt">
            {LANES.map((l) => {
              const a = run.agents[l.key];
              return (
                <div key={l.key} className="agent-lane">
                  <span className={`lane-icon ${a.state}`} aria-hidden>{ICON[a.state]}</span>
                  <div><strong>{l.name}</strong><div className="tiny muted">{l.role}</div></div>
                  <div className={`small ${a.state === 'failed' ? 'c-bad' : a.state === 'partial' ? 'c-warn' : a.state === 'waiting' ? 'c-muted' : ''}`}>{a.detail ?? (a.state === 'waiting' ? 'Waiting' : '')}</div>
                  <div className="small muted" style={{ textAlign: 'right' }}>{a.elapsed_ms ? clock(a.elapsed_ms) : ''}</div>
                </div>
              );
            })}
          </div>
          <div className="row mt">
            {run.state === 'RUNNING' && <button className="btn" onClick={() => backend.cancelRun(run.run_id)}>Cancel</button>}
            {run.evaluation_id && <Link className="btn primary" to={`/evaluations/${run.evaluation_id}`}>Open results</Link>}
          </div>
        </section>
        <section className="panel" style={{ marginTop: 0 }}>
          <h2>Activity</h2>
          <ul className="log mt" style={{ listStyle: 'none', padding: 0 }}>
            {[...run.events].reverse().map((e, i) => <li key={i} className={e.level}><span className="t">{clock(e.at_ms)}</span><span>{e.message}</span></li>)}
          </ul>
          <p className="tiny muted mt">Agents run one after another on the evaluation machine so local models are not loaded at the same time.</p>
        </section>
      </div>
    </div>
  );
}
