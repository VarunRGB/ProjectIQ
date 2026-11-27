import { useEffect, type ReactNode } from 'react';
import type { AgentStatus, Provenance } from '../api/types';
import { AGENT_STATUS, CRITERION_STATUS, PROVENANCE, TEST_OUTCOME, type Tone } from '../lib/labels';

export function Badge({ tone = 'muted', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Tip({ text, children, label = 'More information' }: { text: ReactNode; children?: ReactNode; label?: string }) {
  return (
    <span className="tip" tabIndex={0} aria-label={label}>
      {children ?? <span className="tip-i" aria-hidden>i</span>}
      <span className="tip-body" role="tooltip">{text}</span>
    </span>
  );
}

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  const s = AGENT_STATUS[status];
  return <Tip text={s.explain}><Badge tone={s.tone}><span className="dot" />{s.label}</Badge></Tip>;
}

export function CriterionStatusBadge({ status }: { status: keyof typeof CRITERION_STATUS }) {
  const s = CRITERION_STATUS[status];
  return <Tip text={s.explain}><Badge tone={s.tone}>{s.label}</Badge></Tip>;
}

export function ProvenanceBadge({ p }: { p: Provenance | null | undefined }) {
  if (!p) return null;
  const s = PROVENANCE[p];
  return <Tip text={s.explain}><Badge tone={s.tone}>{s.label}</Badge></Tip>;
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const s = TEST_OUTCOME[outcome] ?? { label: outcome, tone: 'muted' as Tone, explain: '' };
  return <Tip text={s.explain}><Badge tone={s.tone}>{s.label}</Badge></Tip>;
}

/** A measured value or an explicit "Unavailable" — never a fake zero. */
export function Fact({ value, label, scope, unit = '', tip }: { value: number | string | null | undefined; label: ReactNode; scope?: ReactNode; unit?: string; tip?: ReactNode }) {
  const missing = value === null || value === undefined;
  return (
    <div className="fact">
      {missing ? <span className="fact-v unavail">Unavailable</span> : <span className="fact-v">{value}{unit}</span>}
      <span className="fact-l">{label}{tip && <Tip text={tip} />}</span>
      {scope && <span className="fact-scope">{scope}</span>}
    </div>
  );
}

export function Callout({ tone = 'info', title, children, icon }: { tone?: 'info' | 'warn' | 'bad' | 'good' | 'muted'; title?: ReactNode; children?: ReactNode; icon?: string }) {
  const glyph = icon ?? { info: 'i', warn: '!', bad: '!', good: '✓', muted: '·' }[tone];
  return (
    <div className={`callout ${tone}`} role={tone === 'bad' ? 'alert' : undefined}>
      <span className="ci" aria-hidden>{glyph}</span>
      <div>{title && <strong>{title}</strong>}{title && children ? <div>{children}</div> : children}</div>
    </div>
  );
}

export function More({ summary, children, open }: { summary: ReactNode; children: ReactNode; open?: boolean }) {
  return (
    <details className="more" open={open}>
      <summary>{summary}</summary>
      <div className="more-body">{children}</div>
    </details>
  );
}

export function HBar({ label, value, max = 100, tone = 'info', display }: { label: ReactNode; value: number | null; max?: number; tone?: string; display?: string }) {
  return (
    <div className="hbar">
      <span>{label}</span>
      <span className="track">{value !== null && <span className={`tone-${tone}`} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />}</span>
      <span className="v">{value === null ? <span className="c-muted">n/a</span> : display ?? value}</span>
    </div>
  );
}

export function StackBar({ parts, label }: { parts: { value: number; tone: string; label: string }[]; label: string }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  return (
    <div className="stackbar" role="img" aria-label={label + ': ' + parts.map((p) => `${p.label} ${p.value}`).join(', ')}>
      {total > 0 && parts.filter((p) => p.value > 0).map((p) => <span key={p.label} className={`tone-${p.tone}`} style={{ width: `${(p.value / total) * 100}%` }} title={`${p.label}: ${p.value}`} />)}
    </div>
  );
}

export function Drawer({ onClose, head, children, foot, label }: { onClose: () => void; head: ReactNode; children: ReactNode; foot?: ReactNode; label: string }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', k); document.body.style.overflow = prev; };
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={label}>
        <div className="drawer-head">{head}</div>
        <div className="drawer-body">{children}</div>
        {foot && <div className="drawer-foot">{foot}</div>}
      </aside>
    </>
  );
}

export function SourceLoc({ path, line }: { path?: string | null; line?: number | null }) {
  if (!path) return null;
  return <span className="loc">{path}{line ? `:${line}` : ''}</span>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <div className="small">{children}</div>}</div>;
}

export function Unavailable({ children }: { children: ReactNode }) {
  return <span className="fact-v unavail" style={{ display: 'inline-block' }}>{children}</span>;
}

export function shortSha(s?: string | null) { return s ? s.slice(0, 10) : '—'; }

export function fmtDate(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function fmtDuration(sec?: number | null) {
  if (sec === null || sec === undefined) return '—';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m ? `${m} min ${s} s` : `${s} s`;
}
