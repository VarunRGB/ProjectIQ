import { Ring } from './Visuals';
import { Link } from 'react-router-dom';
import type { Recomputed, RecomputedCriterion } from '../lib/scoring';
import { fmtPoints } from '../lib/viewModel';
import { Tip } from './ui';

export function segClass(c: RecomputedCriterion) {
  return c.status === 'TEACHER_REVIEWED' ? 'reviewed' : c.status === 'PROPOSED' ? 'proposed' : c.status === 'OBJECTIVE_PROVISIONAL' ? 'objective' : 'unassessed';
}

export function GradeHeadline({ a }: { a: Recomputed }) {
  if (a.reviewed_points !== null) {
    return (
      <>
        <span className="grade-caption">Approved grade</span>
        <span className="grade-num ink">{fmtPoints(a.reviewed_points)}<span className="of">/ {fmtPoints(a.max_points)}</span></span>
        <span className="grade-sub">Every criterion has a teacher decision.</span>
      </>
    );
  }
  if (a.proposed_points !== null) {
    return (
      <>
        <span className="grade-caption">Proposed grade, not yet approved</span>
        <span className="grade-num pencil">{fmtPoints(a.proposed_points)}<span className="of">/ {fmtPoints(a.max_points)}</span></span>
        <span className="grade-sub">You have approved {a.reviewed_weight_percent}% of the weight. Approve or adjust the rest to finalise it.</span>
      </>
    );
  }
  return (
    <>
      <span className="grade-caption">Complete grade</span>
      <span className="grade-withheld">Withheld until {a.unresolved_criterion_ids.length === 1 ? 'one criterion is' : `${a.unresolved_criterion_ids.length} criteria are`} assessed</span>
      <span className="grade-sub">
        So far {fmtPoints(a.assessed_subtotal_points)} of {fmtPoints(a.assessed_max_points)} marks on the {a.assessed_weight_percent}% of the weight that has been assessed.
        Unassessed criteria are not counted as zero.
      </span>
    </>
  );
}

/** The mark sheet bar: width = weight, fill = earned share, style = who decided. */
export function LedgerBar({ a, linkBase, interactive = true }: { a: Recomputed; linkBase?: string; interactive?: boolean }) {
  const active = a.criteria.filter((c) => c.status !== 'EXCLUDED');
  const excluded = a.criteria.filter((c) => c.status === 'EXCLUDED');
  return (
    <div>
      <div className="ledger-bar" role="img" aria-label="Marks by criterion">
        {active.map((c) => {
          const cls = segClass(c);
          const body = (
            <>
              <span className="fill" style={{ width: c.fraction === null ? 0 : `${c.fraction * 100}%` }} />
            </>
          );
          const title = `${c.title}: ${c.earned_points === null ? 'not assessed' : `${fmtPoints(c.earned_points)} of ${fmtPoints(c.max_points)}`} (${c.effective_weight_percent}% of the grade)`;
          return interactive && linkBase ? (
            <Link key={c.id} to={`${linkBase}?criterion=${c.id}`} className={`lseg ${cls}`} style={{ flex: c.effective_weight_percent }} title={title} aria-label={title}>{body}</Link>
          ) : (
            <span key={c.id} className={`lseg ${cls}`} style={{ flex: c.effective_weight_percent }} title={title}>{body}</span>
          );
        })}
      </div>
      <div className="ledger-labels" aria-hidden>
        {active.map((c) => (
          <div key={c.id} style={{ flex: c.effective_weight_percent, visibility: c.effective_weight_percent < 6 ? 'hidden' : undefined }}>
            <strong>{c.title}</strong>
            {c.earned_points === null ? <span className="c-warn">Not assessed</span> : <span>{fmtPoints(c.earned_points)} / {fmtPoints(c.max_points)}</span>}
          </div>
        ))}
      </div>
      <div className="legend">
        <span><i className="sw reviewed" />Approved by you</span>
        <span><i className="sw proposed" />AI proposed</span>
        <span><i className="sw objective" />Objective, provisional</span>
        <span><i className="sw unassessed" />Not assessed (not zero)</span>
        {excluded.length > 0 && (
          <Tip text={`Not applicable: ${excluded.map((c) => c.title).join(', ')}. Their weight is shared among the criteria shown.`}>
            <span><i className="sw excluded" />{excluded.length} not applicable</span>
          </Tip>
        )}
      </div>
    </div>
  );
}

export function CoverageMeter({ a }: { a: Recomputed }) {
  const prop = Math.max(0, a.assessed_weight_percent - a.reviewed_weight_percent);
  return (
    <div>
      <div className="meter" role="img" aria-label={`Approved ${a.reviewed_weight_percent}%, proposed ${prop}%, not assessed ${100 - a.assessed_weight_percent}%`}>
        <span className="m-rev" style={{ width: `${a.reviewed_weight_percent}%` }} />
        <span className="m-prop" style={{ width: `${prop}%` }} />
        <span className="m-gap" style={{ width: `${100 - a.assessed_weight_percent}%` }} />
      </div>
      <div className="row between tiny muted" style={{ marginTop: 6 }}>
        <span>Approved {a.reviewed_weight_percent}%</span>
        <span>Proposed or objective {Math.round(prop * 100) / 100}%</span>
        <span>Not assessed {Math.round((100 - a.assessed_weight_percent) * 100) / 100}%</span>
      </div>
    </div>
  );
}

/** One ring per rubric section: earned marks out of the section's marks. Unassessed parts are not counted as zero. */
export function SectionRings({ a, size = 92, animate = true }: { a: Recomputed; size?: number; animate?: boolean }) {
  const groups = a.groups.filter((g) => g.max_points > 0);
  return (
    <div className="section-rings">
      {groups.map((g) => {
        const crits = a.criteria.filter((c) => c.group === g.id && c.status !== 'EXCLUDED');
        const reviewed = crits.length > 0 && crits.every((c) => c.status === 'TEACHER_REVIEWED');
        const partial = g.unresolved_criterion_ids.length > 0;
        const name = g.id.charAt(0).toUpperCase() + g.id.slice(1).replace(/_/g, ' ');
        if (partial && g.assessed_max_points === 0) {
          return <Ring key={g.id} value={null} size={size} label={name} sub={`Not assessed (of ${fmtPoints(g.max_points)})`} animate={animate} />;
        }
        return (
          <Ring key={g.id} value={g.assessed_subtotal_points} max={partial ? g.assessed_max_points : g.max_points} size={size} animate={animate}
            tone={reviewed ? 'ink' : 'pencil'} display={fmtPoints(g.assessed_subtotal_points)} label={name}
            sub={partial ? `of ${fmtPoints(g.assessed_max_points)} assessed so far` : `of ${fmtPoints(g.max_points)}${reviewed ? ', approved' : ', proposed'}`} />
        );
      })}
    </div>
  );
}
