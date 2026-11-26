import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { backend } from '../../api';
import type { RunStatus } from '../../api/types';
import { Callout } from '../../components/ui';
import { Ring, type Tone } from '../../components/Visuals';

type Lane = keyof RunStatus['agents'];

/** In the order the backend runs them: format check, then agents one by one, then the rubric. */
const LANES: { key: Lane; name: string; doing: string; weight: number; tau: number }[] = [
  { key: 'format', name: 'Format check', doing: 'Checking the report against IEEE formatting', weight: 5, tau: 3 },
  { key: 'summarizer', name: 'Summary', doing: 'Reading the report and picking its key sentences', weight: 5, tau: 3 },
  { key: 'integrity', name: 'Integrity', doing: 'Comparing with references and estimating AI-like writing', weight: 25, tau: 35 },
  { key: 'research', name: 'Research', doing: 'Checking each reference: identity, relevance and age', weight: 20, tau: 30 },
  { key: 'executioner', name: 'Executioner', doing: 'Inspecting the repository and its tests', weight: 25, tau: 50 },
  { key: 'rubric', name: 'Rubric engine', doing: 'Matching evidence to the rubric and proposing marks', weight: 20, tau: 45 },
];

const DONE_TEXT: Record<string, string> = { done: 'Finished', partial: 'Finished with limits', failed: 'Could not finish; other agents continue', skipped: 'Not needed', waiting: 'Waiting' };

function clock(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Agents do not report fine-grained progress, so a running lane shows a smooth
 * time-based estimate that never reaches 100 until the backend says it finished.
 * The rubric lane uses real batch progress when the backend reports it.
 */
function lanePct(lane: (typeof LANES)[number], a: RunStatus['agents'][Lane]): number | null {
  if (a.state === 'skipped') return null;
  if (a.state === 'done' || a.state === 'partial' || a.state === 'failed') return 100;
  if (a.state === 'waiting') return 0;
  const t = (a.elapsed_ms ?? 0) / 1000;
  const est = 95 * (1 - Math.exp(-t / lane.tau));
  return Math.max(2, Math.min(95, Math.max(est, a.progress ?? 0)));
}

function laneTone(state: string): Tone {
  return state === 'done' ? 'ink' : state === 'partial' ? 'warn' : state === 'failed' ? 'bad' : 'info';
}

function FlowArrow({ state }: { state: 'idle' | 'active' | 'done' }) {
  return (
    <div className={`flow ${state === 'active' ? '' : state}`} aria-hidden>
      <svg width="20" height="34" viewBox="0 0 20 34">
        <line className="track" x1="10" y1="0" x2="10" y2="26" strokeWidth="3" strokeLinecap="round" />
        <line className="stream" x1="10" y1="0" x2="10" y2="26" strokeWidth="3" strokeLinecap="round" />
        <path d="M4 24 L10 32 L16 24" fill="none" stroke={state === 'idle' ? '#d6dbe5' : state === 'done' ? '#0b7466' : '#24609f'} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

export default function RunProgress() {
  const { runId } = useParams();
  const [run, setRun] = useState<RunStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const best = useRef<Record<string, number>>({});
  useEffect(() => {
    let live = true;
    const tick = () => backend.getRun(runId!).then((r) => { if (live) setRun(r); }).catch((e) => live && setErr(e.message));
    tick();
    const t = setInterval(tick, 400);
    return () => { live = false; clearInterval(t); };
  }, [runId]);
  if (err) return <div className="page"><Callout tone="bad" title="Run not found">{err}</Callout></div>;
  if (!run) return <div className="page"><p className="muted">Connecting to the run…</p></div>;

  const finished = run.state !== 'RUNNING';
  const pcts = LANES.map((l) => {
    let p = lanePct(l, run.agents[l.key] ?? { state: 'waiting' });
    if (p !== null) { p = Math.round(Math.max(p, best.current[l.key] ?? 0)); best.current[l.key] = p; } // never move backwards
    return p;
  });
  const active = LANES.filter((_l, i) => pcts[i] !== null);
  const wsum = active.reduce((a, l) => a + l.weight, 0) || 1;
  let overall = Math.round(LANES.reduce((a, l, i) => a + (pcts[i] === null ? 0 : (pcts[i]! * l.weight) / wsum), 0));
  if (run.state === 'COMPLETED') overall = 100;
  const current = LANES.find((l) => run.agents[l.key]?.state === 'running');
  const failedLanes = LANES.filter((l) => run.agents[l.key]?.state === 'failed');

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head">
        <div>
          <h1>{run.title}</h1>
          <p>{run.state === 'RUNNING' ? (current ? `${current.name}: ${current.doing}…` : 'Getting ready…')
            : run.state === 'COMPLETED' ? 'Evaluation finished. Open the results to review the marks.'
            : run.state === 'CANCELLED' ? 'The run was cancelled. Anything already finished was kept.' : 'The run stopped before finishing.'}</p>
        </div>
      </div>

      <section className="panel" aria-live="polite">
        <div className="overall">
          <div>
            <div className="row between small" style={{ marginBottom: 8 }}>
              <strong style={{ color: 'var(--ink)' }}>{finished ? (run.state === 'COMPLETED' ? 'Complete' : 'Stopped') : 'Evaluating'}</strong>
              <span className="muted">{overall}%, {clock(run.elapsed_ms)} elapsed</span>
            </div>
            <div className={`loadbar ${run.state === 'COMPLETED' ? 'done' : finished ? 'stopped' : ''}`} role="progressbar" aria-valuenow={overall} aria-valuemin={0} aria-valuemax={100}>
              <span style={{ width: `${overall}%` }} />
            </div>
          </div>
          <div className="row">
            {run.state === 'RUNNING' && <button className="btn" onClick={() => backend.cancelRun(run.run_id)}>Cancel</button>}
            {run.evaluation_id && <Link className="btn primary" to={`/evaluations/${run.evaluation_id}`}>Open results</Link>}
          </div>
        </div>
        {failedLanes.length > 0 && <div className="mt"><Callout tone="warn">{failedLanes.map((l) => l.name).join(', ')} could not finish. The other agents keep going, and anything missing is shown as unavailable, not as zero.</Callout></div>}
      </section>

      <section className="panel">
        <div className="pipeline">
          {LANES.map((l, i) => {
            const a = run.agents[l.key] ?? { state: 'waiting' };
            const p = pcts[i];
            const running = a.state === 'running';
            const next = LANES.slice(i + 1).find((x) => run.agents[x.key]?.state !== 'skipped');
            const nextState = next ? run.agents[next.key]?.state : undefined;
            const flow: 'idle' | 'active' | 'done' = running ? 'active' : ['done', 'partial', 'failed', 'skipped'].includes(a.state) && nextState && nextState !== 'waiting' ? 'done' : ['done', 'partial', 'failed', 'skipped'].includes(a.state) ? 'active' : 'idle';
            return (
              <div key={l.key}>
                <div className={`pnode ${a.state}`}>
                  <div className={running ? 'pulse' : undefined}>
                    <Ring value={p} size={76} stroke={8} tone={laneTone(a.state)} display={a.state === 'skipped' ? '–' : a.state === 'failed' ? '!' : undefined} animate />
                  </div>
                  <div>
                    <h3>{l.name}</h3>
                    <div className={`pstate ${a.state === 'failed' ? 'c-bad' : a.state === 'partial' ? 'c-warn' : 'muted'}`}>{running ? `${l.doing}…` : DONE_TEXT[a.state] ?? a.state}</div>
                  </div>
                  <div className="small muted" style={{ textAlign: 'right', minWidth: 48 }}>{a.elapsed_ms && a.state !== 'skipped' ? clock(a.elapsed_ms) : ''}</div>
                </div>
                {i < LANES.length - 1 && <FlowArrow state={finished && run.state === 'COMPLETED' ? 'done' : flow} />}
              </div>
            );
          })}
        </div>
        <p className="tiny muted mt">Agents run one after another so the local models are never loaded at the same time. Percentages for running agents are estimates; a step reaches 100% only when it has actually finished.</p>
      </section>
    </div>
  );
}
