/**
 * Visual indicators. Every component takes `value: number | null`; null is drawn as
 * an empty hatched/grey state labelled "n/a", never as 0.
 * Animations count up once on mount and are skipped when the user prefers reduced motion.
 */
import { useEffect, useRef, useState } from 'react';

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Animate from 0 to target with an ease-out curve. */
export function useCountUp(target: number | null, ms = 1100): number {
  const [v, setV] = useState(() => (target === null || reduced() ? target ?? 0 : 0));
  const from = useRef(0);
  useEffect(() => {
    if (target === null) return;
    if (reduced()) { setV(target); return; }
    const start = performance.now(); const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      setV(a + (target - a) * e);
      if (k < 1) raf = requestAnimationFrame(tick); else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

export type Tone = 'good' | 'warn' | 'bad' | 'info' | 'pencil' | 'ink' | 'muted';
export const TONE_COLOR: Record<Tone, string> = {
  good: '#1d7a4a', warn: '#c27c12', bad: '#b02a22', info: '#24609f', pencil: '#6750b0', ink: '#0b7466', muted: '#9aa3b5',
};

/** Higher is better: green / amber / red. */
export function toneFor(pct: number | null, good = 75, ok = 50): Tone {
  if (pct === null) return 'muted';
  return pct >= good ? 'good' : pct >= ok ? 'warn' : 'bad';
}

/** Lower is better (overlap, AI-like writing). */
export function toneForRisk(pct: number | null, low = 10, high = 25): Tone {
  if (pct === null) return 'muted';
  return pct < low ? 'good' : pct < high ? 'warn' : 'bad';
}

/**
 * Circular progress ring with the number inside.
 * `value` and `max` are in the same unit; `display` overrides the centre text.
 */
export function Ring({ value, max = 100, size = 96, stroke = 9, tone, label, sub, display, suffix = '%', title, animate = true }: {
  value: number | null; max?: number; size?: number; stroke?: number; tone?: Tone; label?: string; sub?: string;
  display?: string; suffix?: string; title?: string; animate?: boolean;
}) {
  const pct = value === null || !max ? null : Math.max(0, Math.min(1, value / max));
  const anim = useCountUp(pct === null ? null : pct * 100, animate ? 1100 : 0);
  const r = (size - stroke) / 2; const c = 2 * Math.PI * r;
  const color = TONE_COLOR[tone ?? toneFor(pct === null ? null : pct * 100)];
  const pctText = (x: number) => (max === 100 && value !== null && value < 10 && value % 1 ? `${x.toFixed(1)}%` : `${Math.round(x)}%`);
  const shown = display ?? (pct === null ? 'n/a' : suffix === '%' ? pctText(anim) : `${Math.round((anim / 100) * (max ?? 100))}${suffix}`);
  return (
    <figure className="ring" style={{ width: size }} title={title} aria-label={`${label ?? ''} ${pct === null ? 'not available' : shown}`.trim()}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={pct === null ? '#e7eaf0' : '#eceff4'} strokeWidth={stroke}
          strokeDasharray={pct === null ? '3 5' : undefined} />
        {pct !== null && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
            strokeDasharray={`${c} ${c}`} strokeDashoffset={c * (1 - anim / 100)}
            transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        )}
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" className="ring-num"
          style={{ fontSize: size * (shown.length > 5 ? 0.19 : shown.length >= 4 ? 0.22 : 0.25), fill: pct === null ? '#8a92a6' : '#1d2b53' }}>{shown}</text>
      </svg>
      {(label || sub) && <figcaption>{label && <strong>{label}</strong>}{sub && <span>{sub}</span>}</figcaption>}
    </figure>
  );
}

/**
 * Speed-test style gauge: a 240° dial with coloured bands, tick marks and a needle
 * that sweeps up to the value. `bands` are upper limits (in %) with their tone.
 */
export function Gauge({ value, size = 230, label, caption, bands = [[10, 'good'], [25, 'warn'], [100, 'bad']] as [number, Tone][], unit = '%' }: {
  value: number | null; size?: number; label?: string; caption?: string; bands?: [number, Tone][]; unit?: string;
}) {
  const anim = useCountUp(value, 1600);
  const cx = size / 2, cy = size / 2, r = size * 0.4, sw = size * 0.075;
  const start = 150, sweep = 240; // degrees, clockwise from +x axis
  const pt = (deg: number, rad: number) => [cx + rad * Math.cos((deg * Math.PI) / 180), cy + rad * Math.sin((deg * Math.PI) / 180)];
  const arc = (a0: number, a1: number, rad: number) => {
    const [x0, y0] = pt(a0, rad), [x1, y1] = pt(a1, rad);
    return `M ${x0} ${y0} A ${rad} ${rad} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const at = (p: number) => start + (sweep * Math.max(0, Math.min(100, p))) / 100;
  let prev = 0;
  const needle = value === null ? null : at(anim);
  const tone = value === null ? 'muted' : (bands.find(([lim]) => value <= lim)?.[1] ?? 'bad');
  return (
    <figure className="gauge" style={{ width: size }} aria-label={`${label ?? 'Gauge'}: ${value === null ? 'not available' : value + unit}`}>
      <svg width={size} height={cy + r * 0.85 + size * 0.06} viewBox={`0 0 ${size} ${cy + r * 0.85 + size * 0.06}`} aria-hidden>
        <path d={arc(start, start + sweep, r)} fill="none" stroke="#eceff4" strokeWidth={sw} strokeLinecap="round" />
        {value !== null && bands.map(([lim, t], i) => {
          const d = <path key={i} d={arc(at(prev) + (i ? 1 : 0), at(lim), r)} fill="none" stroke={TONE_COLOR[t]} strokeOpacity={0.22} strokeWidth={sw} />;
          prev = lim; return d;
        })}
        {value !== null && anim > 0.3 && <path d={arc(start, at(anim), r)} fill="none" stroke={TONE_COLOR[tone]} strokeWidth={sw} strokeLinecap="round" />}
        {[0, 20, 40, 60, 80, 100].map((p) => {
          const [x0, y0] = pt(at(p), r - sw * 0.9), [x1, y1] = pt(at(p), r - sw * 1.6), [tx, ty] = pt(at(p), r - sw * 2.5);
          return <g key={p}><line x1={x0} y1={y0} x2={x1} y2={y1} stroke="#b7bfcd" strokeWidth={1.5} /><text x={tx} y={ty} textAnchor="middle" dominantBaseline="central" style={{ font: `500 ${size * 0.045}px var(--sans)`, fill: '#8a92a6' }}>{p}</text></g>;
        })}
        {needle !== null && (() => {
          const [nx, ny] = pt(needle, r - sw * 1.1);
          return <g><line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#1d2b53" strokeWidth={3} strokeLinecap="round" /><circle cx={cx} cy={cy} r={size * 0.035} fill="#1d2b53" /></g>;
        })()}
        <text x={cx} y={cy + r * 0.85} textAnchor="middle" style={{ font: `600 ${size * 0.13}px var(--serif)`, fill: value === null ? '#8a92a6' : '#1d2b53' }}>
          {value === null ? 'n/a' : `${anim.toFixed(value % 1 ? 1 : 0)}${unit}`}
        </text>
      </svg>
      {(label || caption) && <figcaption>{label && <strong>{label}</strong>}{caption && <span>{caption}</span>}</figcaption>}
    </figure>
  );
}

/** Donut of parts (for example test outcomes), with the total or a headline in the middle. */
export function Donut({ parts, size = 120, stroke = 16, center, sub }: {
  parts: { value: number; tone: Tone; label: string }[]; size?: number; stroke?: number; center?: string; sub?: string;
}) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const k = useCountUp(total ? 100 : null, 1000) / 100;
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <figure className="ring" style={{ width: size }} aria-label={parts.map((p) => `${p.label} ${p.value}`).join(', ')}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#eceff4" strokeWidth={stroke} strokeDasharray={total ? undefined : '3 5'} />
        {total > 0 && parts.filter((p) => p.value > 0).map((p) => {
          const len = (p.value / total) * c * k;
          const el = <circle key={p.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={TONE_COLOR[p.tone]} strokeWidth={stroke}
            strokeDasharray={`${Math.max(0, len - 2)} ${c}`} strokeDashoffset={-acc * k} transform={`rotate(-90 ${size / 2} ${size / 2})`} />;
          acc += (p.value / total) * c; return el;
        })}
        <text x="50%" y={sub ? '45%' : '50%'} textAnchor="middle" dominantBaseline="central" className="ring-num" style={{ fontSize: size * 0.22, fill: '#1d2b53' }}>{center ?? total}</text>
        {sub && <text x="50%" y="63%" textAnchor="middle" dominantBaseline="central" style={{ font: `500 ${size * 0.09}px var(--sans)`, fill: '#5f6880' }}>{sub}</text>}
      </svg>
    </figure>
  );
}

export function Legend({ parts }: { parts: { value: number; tone: Tone; label: string }[] }) {
  return (
    <div className="vlegend">
      {parts.map((p) => <span key={p.label}><i style={{ background: TONE_COLOR[p.tone] }} />{p.label}<b>{p.value}</b></span>)}
    </div>
  );
}
