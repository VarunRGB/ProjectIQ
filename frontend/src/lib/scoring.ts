/**
 * Faithful TypeScript port of the arithmetic in projectiq_core/phase2/scoring.py
 * `assess()`, used to recompute marks instantly after the teacher changes weights,
 * total marks, applicability or individual decisions. No agent or model reruns.
 *
 * Rules carried over unchanged:
 *  - Every applicable weight stays in the denominator until explicitly excluded.
 *  - Exclusion redistributes weight proportionally; unresolved weight is NOT redistributed.
 *  - Any unresolved criterion withholds the complete mark (proposed_points = null).
 *  - A MODEL score is "proposed"; only TEACHER decisions make a criterion "reviewed".
 *  - Group weights split equally by criterion weight inside the group.
 *  - Display values round half-up to 2 decimals; totals use unrounded arithmetic.
 *
 * Qualitative evidence validation (hardening, evidence coverage) stays authoritative
 * in the backend. The mock backend relies on this port; the HTTP backend re-validates
 * every saved decision with the real engine.
 */
import type { Assessment, CriterionResult, CriterionStatus, Rubric, TeacherDecisionEntry } from '../api/types';

export function round2(v: number): number {
  const sign = v < 0 ? -1 : 1;
  return (sign * Math.round(Math.abs(v) * 100 + 1e-9)) / 100;
}

export interface RecomputeInput {
  base: Assessment;
  /** Rubric as configured now. Must contain the same criterion ids as base. */
  rubric: Rubric;
  teacher: Record<string, TeacherDecisionEntry>;
  /** Criteria whose policy changed after evaluation: their earlier decision no longer applies. */
  invalidated?: string[];
}

export interface RecomputedCriterion extends CriterionResult {
  /** Where the current mark comes from. */
  origin: 'MODEL' | 'TEACHER' | 'OBJECTIVE' | 'NONE' | 'EXCLUDED';
  /** The model's proposal as returned by the backend, kept for comparison after an override. */
  proposal: { level_id: string | null; fraction: number | null; status: CriterionStatus } | null;
}

export interface Recomputed extends Omit<Assessment, 'criteria'> {
  criteria: RecomputedCriterion[];
  changedFromBackend: boolean;
}

export function recompute({ base, rubric, teacher, invalidated = [] }: RecomputeInput): Recomputed {
  const baseById = new Map(base.criteria.map((c) => [c.id, c]));
  const excluded = new Set<string>();
  for (const c of rubric.criteria) {
    const t = teacher[c.id];
    if (c.applicability === 'EXCLUDED') excluded.add(c.id);
    else if (t && t.action === 'EXCLUDE' && c.applicability === 'OPTIONAL') excluded.add(c.id);
  }
  if (excluded.size === rubric.criteria.length) throw new Error('NO_APPLICABLE_CRITERIA');

  // Effective weights, mirroring the group normalization in scoring.py.
  let weights = new Map<string, number>();
  for (const c of rubric.criteria) if (!excluded.has(c.id)) weights.set(c.id, c.weight);
  const grouped = Object.keys(rubric.group_weights || {}).length > 0;
  if (grouped) {
    const sums = new Map<string, number>();
    for (const c of rubric.criteria) if (weights.has(c.id)) sums.set(c.group, (sums.get(c.group) || 0) + weights.get(c.id)!);
    const next = new Map<string, number>();
    for (const c of rubric.criteria) {
      if (!weights.has(c.id)) continue;
      next.set(c.id, ((rubric.group_weights[c.group] ?? 0) * weights.get(c.id)!) / sums.get(c.group)!);
    }
    weights = next;
  }
  const totalWeight = [...weights.values()].reduce((a, b) => a + b, 0);
  const total = rubric.total_points;

  let earned = 0, assessedW = 0, reviewedW = 0;
  const missing: string[] = [];
  const groups = new Map<string, { weight: number; assessed: number; reviewed: number; earned: number; missing: string[] }>();
  const out: RecomputedCriterion[] = [];

  for (const c of rubric.criteria) {
    const b = baseById.get(c.id);
    const t = teacher[c.id];
    const w = weights.get(c.id) ?? 0;
    const possible = totalWeight ? (total * w) / totalWeight : 0;
    const proposal = b && b.status !== 'EXCLUDED' && b.status !== 'TEACHER_REVIEWED'
      ? { level_id: b.level_id, fraction: b.fraction, status: b.status } : null;
    let status: CriterionStatus = 'UNASSESSED';
    let fraction: number | null = null;
    let level: string | null = null;
    let origin: RecomputedCriterion['origin'] = 'NONE';
    let reason = b?.reason ?? 'No valid score decision yet.';
    let evidenceIds = b?.evidence_ids ?? [];
    let feedback = b?.feedback ?? null;

    if (excluded.has(c.id)) {
      status = 'EXCLUDED'; origin = 'EXCLUDED';
      reason = t?.action === 'EXCLUDE' ? t.rationale : 'Excluded by the teacher-defined rubric.';
      evidenceIds = []; feedback = null;
    } else if (t && t.action === 'DEFER') {
      reason = t.rationale;
    } else if (t && (t.action === 'SCORE' || t.action === 'CONFIRM_TEST_RATIO')) {
      if (t.action === 'SCORE') {
        const lv = c.levels.find((l) => l.id === t.level_id);
        if (!lv) throw new Error('UNKNOWN_RUBRIC_LEVEL');
        fraction = lv.fraction; level = lv.id; evidenceIds = t.evidence_ids;
      } else {
        fraction = b?.fraction ?? null; level = b?.level_id ?? null;
      }
      if (fraction !== null) { status = 'TEACHER_REVIEWED'; origin = 'TEACHER'; reason = t.rationale; feedback = t.feedback ?? feedback; }
    } else if (b && !invalidated.includes(c.id) && b.status !== 'EXCLUDED' && b.fraction !== null) {
      fraction = b.fraction; level = b.level_id; status = b.status;
      origin = b.status === 'OBJECTIVE_PROVISIONAL' ? 'OBJECTIVE' : b.status === 'TEACHER_REVIEWED' ? 'TEACHER' : 'MODEL';
    } else if (b && (invalidated.includes(c.id) || b.status === 'EXCLUDED')) {
      reason = invalidated.includes(c.id)
        ? 'Criterion policy changed after evaluation; reassessment required.'
        : 'Criterion was excluded during evaluation; reassessment required.';
      evidenceIds = []; feedback = null;
    }

    const g = groups.get(c.group) ?? { weight: 0, assessed: 0, reviewed: 0, earned: 0, missing: [] };
    groups.set(c.group, g);
    g.weight += w;
    let points: number | null = null;
    if (fraction !== null) {
      points = possible * fraction; earned += points; assessedW += w; g.assessed += w; g.earned += points;
      if (status === 'TEACHER_REVIEWED') { reviewedW += w; g.reviewed += w; }
    } else if (status !== 'EXCLUDED') { missing.push(c.id); g.missing.push(c.id); }

    out.push({
      ...(b ?? ({} as CriterionResult)),
      id: c.id, group: c.group, title: c.title, applicability: c.applicability, status,
      configured_weight: c.weight,
      effective_weight_percent: totalWeight ? round2((w * 100) / totalWeight) : 0,
      max_points: round2(possible), earned_points: points === null ? null : round2(points), fraction,
      level_id: level, evidence_ids: evidenceIds, reason, feedback,
      review_flags: b?.review_flags ?? [], objective_details: b?.objective_details ?? {},
      evidence_details: b?.evidence_details ?? [], origin, proposal,
    });
  }
  const complete = missing.length === 0;
  const reviewed = complete && Math.abs(reviewedW - totalWeight) < 1e-9;
  const result: Recomputed = {
    ...base,
    status: reviewed ? 'TEACHER_REVIEWED' : complete ? 'READY_FOR_TEACHER_REVIEW' : 'INSUFFICIENT_EVIDENCE',
    max_points: total,
    proposed_points: complete ? round2(earned) : null,
    reviewed_points: reviewed ? round2(earned) : null,
    assessed_subtotal_points: round2(earned),
    assessed_max_points: totalWeight ? round2((total * assessedW) / totalWeight) : 0,
    assessed_weight_percent: totalWeight ? round2((assessedW * 100) / totalWeight) : 0,
    reviewed_weight_percent: totalWeight ? round2((reviewedW * 100) / totalWeight) : 0,
    unresolved_criterion_ids: missing,
    criteria: out,
    groups: [...groups.entries()].map(([id, v]) => ({
      id, effective_weight_percent: totalWeight ? round2((v.weight * 100) / totalWeight) : 0,
      max_points: totalWeight ? round2((total * v.weight) / totalWeight) : 0,
      assessed_max_points: totalWeight ? round2((total * v.assessed) / totalWeight) : 0,
      assessed_subtotal_points: round2(v.earned),
      assessed_weight_percent: v.weight ? round2((v.assessed * 100) / v.weight) : null,
      unresolved_criterion_ids: v.missing,
    })),
    changedFromBackend: false,
  };
  result.changedFromBackend = Object.keys(teacher).length > 0
    || result.max_points !== base.max_points
    || result.criteria.some((c, i) => c.effective_weight_percent !== base.criteria[i]?.effective_weight_percent);
  return result;
}

/**
 * Mirrors the policy comparison in scoring.reweight() / orchestrator.reuse_decisions():
 * weight, group weight, total and title changes keep every decision; any other change
 * to a criterion invalidates that criterion's decision only.
 */
export function policyChanges(before: Rubric, after: Rubric): { invalidated: string[]; excludedNow: string[]; includedNow: string[] } {
  const strip = (c: Rubric['criteria'][number]) => {
    const { weight: _w, applicability: _a, ...rest } = c;
    return JSON.stringify(rest);
  };
  const old = new Map(before.criteria.map((c) => [c.id, c]));
  const invalidated: string[] = [], excludedNow: string[] = [], includedNow: string[] = [];
  for (const c of after.criteria) {
    const o = old.get(c.id);
    if (!o) { invalidated.push(c.id); continue; }
    if (strip(o) !== strip(c)) invalidated.push(c.id);
    if (o.applicability !== 'EXCLUDED' && c.applicability === 'EXCLUDED') excludedNow.push(c.id);
    if (o.applicability === 'EXCLUDED' && c.applicability !== 'EXCLUDED') includedNow.push(c.id);
  }
  return { invalidated, excludedNow, includedNow };
}
