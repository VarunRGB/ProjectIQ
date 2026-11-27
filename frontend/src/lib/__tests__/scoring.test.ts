import { describe, expect, it } from 'vitest';
import { recompute } from '../scoring';
import campus from '../../mock/fixtures/campusconnect.json';
import medi from '../../mock/fixtures/meditrack.json';
import irr from '../../mock/fixtures/irrigation.json';

const keys = ['status', 'max_points', 'proposed_points', 'reviewed_points', 'assessed_subtotal_points', 'assessed_max_points',
  'assessed_weight_percent', 'reviewed_weight_percent', 'unresolved_criterion_ids'] as const;

describe.each([['campusconnect', campus], ['meditrack', medi], ['irrigation', irr]])('%s', (_n, fx: any) => {
  it('reproduces the backend assessment exactly with no teacher changes', () => {
    const a = recompute({ base: fx.result.assessment, rubric: fx.result.rubric, teacher: {} });
    for (const k of keys) expect(a[k]).toEqual(fx.result.assessment[k]);
    a.criteria.forEach((c, i) => {
      const b = fx.result.assessment.criteria[i];
      expect([c.status, c.earned_points, c.max_points, c.effective_weight_percent]).toEqual([b.status, b.earned_points, b.max_points, b.effective_weight_percent]);
    });
    expect(a.groups).toEqual(fx.result.assessment.groups);
  });
  it('matches the real engine reweight() for a weight and total change', () => {
    const exp = fx.reweight_check.assessment;
    const a = recompute({ base: fx.result.assessment, rubric: fx.reweight_check.rubric, teacher: {} });
    for (const k of keys) expect(a[k]).toEqual(exp[k]);
    a.criteria.forEach((c, i) => expect([c.earned_points, c.max_points, c.effective_weight_percent]).toEqual([exp.criteria[i].earned_points, exp.criteria[i].max_points, exp.criteria[i].effective_weight_percent]));
  });
});

describe('teacher decisions', () => {
  it('teacher scoring the last unresolved criterion releases the grade but keeps it proposed', () => {
    const fx: any = campus;
    const research = fx.result.rubric.criteria.find((c: any) => c.id === 'research');
    const a = recompute({ base: fx.result.assessment, rubric: fx.result.rubric, teacher: {
      research: { criterion_id: 'research', action: 'SCORE', source: 'TEACHER', level_id: 'adequate', evidence_ids: [], rationale: 'ok', remarks: 'ok', basis: 'TEACHER_SCORED', decided_utc: '' } as any } });
    expect(research).toBeTruthy();
    expect(a.unresolved_criterion_ids).toEqual([]);
    expect(a.status).toBe('READY_FOR_TEACHER_REVIEW');
    expect(a.proposed_points).not.toBeNull();
    expect(a.reviewed_points).toBeNull();
  });
});
