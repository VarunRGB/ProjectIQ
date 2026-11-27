import type { IEEEFormat, IEEEVerdict } from '../../api/types';
import { Badge, Callout, Empty, More, Tip } from '../../components/ui';
import { Gauge, Ring, toneFor } from '../../components/Visuals';
import type { Tone } from '../../lib/labels';

export const VERDICT: Record<IEEEVerdict, { label: string; tone: Tone; explain: string }> = {
  LIKELY_IEEE_STYLE: { label: 'Looks IEEE-style', tone: 'good', explain: 'Strong score and a two-column layout were detected in the PDF.' },
  NEEDS_REVIEW: { label: 'Needs review', tone: 'warn', explain: 'Some formatting conventions are missing or uncertain. Check the items below.' },
  NEEDS_PDF_VERIFICATION: { label: 'Check the PDF', tone: 'info', explain: 'A Word file was checked for styles and structure. Page layout can only be confirmed from the exported PDF.' },
  NOT_IEEE_LIKE: { label: 'Not IEEE layout', tone: 'bad', explain: 'The PDF body is not laid out in two columns, which IEEE papers use.' },
  CANNOT_VERIFY_LAYOUT: { label: 'Layout not checkable', tone: 'muted', explain: 'This file type has no page layout (for example plain text), so only the text structure was checked.' },
  NOT_EVALUATED: { label: 'Not evaluated', tone: 'muted', explain: 'The format check did not run.' },
};

const SCOPE: Record<string, string> = {
  layout_and_structure: 'PDF: page layout, typography and structure checked',
  style_and_structure: 'Word file: styles and structure checked; layout needs the PDF',
  structure_only: 'Text only: structure checked; layout and fonts cannot be seen',
};

const STATUS_TONE: Record<string, Tone> = { pass: 'good', warning: 'warn', fail: 'bad', not_applicable: 'muted' };
const STATUS_LABEL: Record<string, string> = { pass: 'Pass', warning: 'Partly', fail: 'Missing', not_applicable: 'Not checkable' };

export function FormatSummary({ f, size = 96 }: { f: IEEEFormat | undefined; size?: number }) {
  if (!f) return <p className="small muted">This evaluation predates the format checker.</p>;
  const v = VERDICT[f.format_verdict] ?? VERDICT.NOT_EVALUATED;
  return (
    <div className="row" style={{ gap: 18, flexWrap: 'nowrap' }}>
      <Ring value={f.score} size={size} tone={toneFor(f.score, 85, 70)} />
      <div>
        <Tip text={v.explain}><Badge tone={v.tone}>{v.label}</Badge></Tip>
        <p className="small" style={{ margin: '6px 0 0' }}>{f.rating ?? (f.status === 'FAILED' ? 'The check failed.' : '')}</p>
      </div>
    </div>
  );
}

export default function FormatTab({ f }: { f: IEEEFormat | undefined }) {
  if (!f) return <div className="panel"><Empty title="No format check for this evaluation">It was evaluated before the IEEE format checker was added. Run a new evaluation to include it.</Empty></div>;
  const v = VERDICT[f.format_verdict] ?? VERDICT.NOT_EVALUATED;
  if (f.status === 'FAILED') {
    return <div className="panel"><div className="panel-head"><h2>IEEE format check</h2><Badge tone="bad">Failed</Badge></div>
      <Callout tone="bad" title="The format check did not run">{f.error_code && <code>{f.error_code}</code>} The score is unavailable, not zero. Formatting never affects marks.</Callout></div>;
  }
  const cats = f.categories ?? [];
  const checks = f.checks ?? [];
  const groups = [...new Set(checks.map((c) => c.category))];
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <div><h2>IEEE format check</h2><p>How closely the report follows common IEEE conference-paper conventions. Advisory: it never changes marks.</p></div>
          <Tip text={v.explain}><Badge tone={v.tone}>{v.label}</Badge></Tip>
        </div>
        <div className="grid g2" style={{ alignItems: 'center' }}>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <Gauge value={f.score} label={f.rating ?? 'Format score'} caption={SCOPE[f.assessment_scope ?? ''] ?? ''} bands={[[50, 'bad'], [70, 'warn'], [85, 'info'], [100, 'good']]} />
          </div>
          <div className="stack">
            <dl className="kv">
              <dt>Verdict</dt><dd>{v.label}. <span className="muted small">{v.explain}</span></dd>
              <dt>Layout</dt><dd>{f.layout_verdict ?? 'Not available'}</dd>
              <dt>Scope</dt><dd>{SCOPE[f.assessment_scope ?? ''] ?? f.assessment_scope}</dd>
            </dl>
            {f.disclaimer && <Callout tone="muted">{f.disclaimer}</Callout>}
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><div><h2>Score by area</h2><p>Areas that cannot be checked for this file type are shown as n/a and left out of the score.</p></div></div>
        <div className="section-rings">
          {cats.map((c) => (
            <Ring key={c.category} value={c.possible ? c.earned : null} max={c.possible || 1} size={92} label={c.category}
              sub={c.possible ? `${c.earned} of ${c.possible} points` : 'Not checkable'} tone={c.possible ? toneFor(((c.earned ?? 0) / c.possible) * 100, 99, 50) : 'muted'} />
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head"><div><h2>What was checked</h2><p>Each convention with what the checker saw and how to fix it.</p></div></div>
        {groups.map((g) => (
          <div key={g} className="mt">
            <h4>{g}</h4>
            <div className="table-wrap mt"><table className="t">
              <tbody>{checks.filter((c) => c.category === g).map((c, i) => (
                <tr key={i}>
                  <td style={{ width: 120 }}><Badge tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status] ?? c.status}</Badge></td>
                  <td><strong className="small">{c.criterion}</strong><div className="small muted">{c.detail}</div>{c.suggestion && <div className="small">Fix: {c.suggestion}</div>}</td>
                  <td className="num small">{c.applicable ? `${c.earned}/${c.possible}` : 'n/a'}{c.page ? <div className="tiny muted">page {c.page}</div> : null}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </div>
        ))}
        {(f.notes?.length ?? 0) > 0 && <More summary="Checker notes"><ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{f.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul></More>}
      </section>
    </>
  );
}
