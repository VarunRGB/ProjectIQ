/**
 * Rubric helpers that mirror backend behaviour:
 *  - validateRubric(): the pydantic validators in phase2/contracts.py (Criterion, Rubric)
 *  - isResearch(), configureRubric(): orchestrator.configure_rubric() and research_policy.is_research()
 * Templates are the backend's own *.rubric.json files, copied verbatim by
 * tools/generate_fixtures.py, plus teacher-saved templates.
 */
import type { Criterion, GradingKind, Level, Rubric } from '../api/types';
import unified from '../mock/rubrics/software_project_unified.rubric.json';
import softwareProject from '../mock/rubrics/software_project.rubric.json';
import intelligent from '../mock/rubrics/software_project_intelligent.rubric.json';
import minicollab from '../mock/rubrics/minicollab_hybrid.rubric.json';
import minicollabDetailed from '../mock/rubrics/minicollab_hybrid_detailed.rubric.json';

export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
export const GRADING_KINDS: GradingKind[] = ['REPORT_EXCERPT', 'STATIC_SOURCE', 'RESEARCH_RECORD', 'TEST_OBSERVATION'];

export function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

/** Fill defaults exactly as pydantic would for omitted fields. */
export function normalizeRubric(r: any): Rubric {
  const data = clone(r);
  data.schema_version = '2.0';
  data.revision = data.revision ?? '1';
  data.group_weights = data.group_weights ?? {};
  data.criteria = (data.criteria || []).map((c: any) => ({
    applicability: 'REQUIRED', method: 'JUDGMENT', allowed_evidence: ['REPORT_EXCERPT'], required_evidence: [],
    minimum_evidence: 1, levels: [], test_ids: [], requirement_map: {}, feedback_required: false, focus: 'GENERAL', ...c,
  }));
  return data as Rubric;
}

export function isResearch(c: Criterion): boolean {
  return c.method === 'JUDGMENT' && (c.group === 'research' || c.focus === 'RESEARCH' || c.id === 'research');
}

export function researchNeeded(r: Rubric): boolean {
  return r.criteria.some((c) => c.applicability !== 'EXCLUDED' && (isResearch(c) || c.required_evidence.includes('RESEARCH_RECORD')));
}

export function groupsOf(r: Rubric): string[] {
  return [...new Set(r.criteria.map((c) => c.group))];
}

/** Port of orchestrator.configure_rubric's research switch. Throws the backend error codes. */
export function setResearch(r: Rubric, required: boolean): Rubric {
  const data = clone(r);
  const matching = data.criteria.filter(isResearch);
  if (required && !matching.length) throw new Error('NO_RESEARCH_CRITERION_IN_RUBRIC');
  for (const c of matching) {
    c.applicability = required ? 'REQUIRED' : 'EXCLUDED';
    c.allowed_evidence = [...new Set([...c.allowed_evidence, 'RESEARCH_RECORD' as GradingKind])];
    c.required_evidence = [...new Set([...c.required_evidence, 'RESEARCH_RECORD' as GradingKind])];
  }
  if (!required && data.criteria.some((c) => c.applicability !== 'EXCLUDED' && c.required_evidence.includes('RESEARCH_RECORD')))
    throw new Error('ACTIVE_CRITERION_REQUIRES_RESEARCH');
  return data;
}

export interface RubricIssue { path: string; message: string }

export function validateRubric(r: Rubric): RubricIssue[] {
  const issues: RubricIssue[] = [];
  const push = (path: string, message: string) => issues.push({ path, message });
  if (!ID_PATTERN.test(r.id)) push('id', 'Template ID must start with a letter or digit and use only letters, digits, - or _.');
  if (!r.title.trim()) push('title', 'Give the rubric a title.');
  if (!(r.total_points > 0 && r.total_points <= 1_000_000)) push('total_points', 'Total marks must be greater than 0.');
  if (!r.criteria.length) push('criteria', 'Add at least one criterion.');
  const ids = new Set<string>();
  r.criteria.forEach((c, i) => {
    const p = `criteria.${i}`;
    if (!ID_PATTERN.test(c.id)) push(p + '.id', `"${c.title || c.id}": the ID must use only letters, digits, - or _.`);
    if (ids.has(c.id)) push(p + '.id', `Two criteria share the ID "${c.id}".`);
    ids.add(c.id);
    if (!c.title.trim()) push(p + '.title', 'Every criterion needs a title.');
    if (!c.description.trim()) push(p + '.description', `"${c.title}": add a short description of what is assessed.`);
    if (!(c.weight > 0)) push(p + '.weight', `"${c.title}": weight must be greater than 0. To leave a criterion out, mark it as not applicable.`);
    if (!c.allowed_evidence.length) push(p + '.allowed_evidence', `"${c.title}": allow at least one kind of evidence.`);
    if (!c.required_evidence.every((k) => c.allowed_evidence.includes(k))) push(p + '.required_evidence', `"${c.title}": required evidence must also be allowed.`);
    if (c.method === 'JUDGMENT') {
      if (c.levels.length < 2) push(p + '.levels', `"${c.title}": add at least two grading levels.`);
      const fr = c.levels.map((l) => l.fraction);
      const sorted = [...new Set(fr)].sort((a, b) => a - b);
      if (fr.length >= 2 && (JSON.stringify(sorted) !== JSON.stringify(fr) || fr[0] !== 0 || fr[fr.length - 1] !== 1))
        push(p + '.levels', `"${c.title}": levels must rise from 0% to 100% with no repeats.`);
      if (new Set(c.levels.map((l) => l.id)).size !== c.levels.length) push(p + '.levels', `"${c.title}": level IDs must be unique.`);
      c.levels.forEach((l, j) => { if (!l.descriptor.trim()) push(`${p}.levels.${j}`, `"${c.title}": level "${l.label}" needs a descriptor.`); });
    }
  });
  if (r.criteria.length && !r.criteria.some((c) => c.applicability !== 'EXCLUDED')) push('criteria', 'At least one criterion must apply.');
  const gw = r.group_weights || {};
  if (Object.keys(gw).length) {
    const groups = groupsOf(r);
    const missing = groups.filter((g) => !(g in gw));
    if (missing.length) push('group_weights', `Give a weight to every section: ${missing.join(', ')}.`);
    for (const [g, w] of Object.entries(gw)) if (!(w > 0)) push('group_weights.' + g, `Section "${g}" needs a weight greater than 0.`);
    const extra = Object.keys(gw).filter((g) => !groups.includes(g));
    if (extra.length) push('group_weights', `Section weights refer to sections with no criteria: ${extra.join(', ')}.`);
  }
  return issues;
}

export function defaultLevels(): Level[] {
  return clone((unified as any).criteria[0].levels);
}

export function blankCriterion(group: string, n: number): Criterion {
  return {
    id: `criterion_${n}`, title: 'New criterion', description: 'Describe what this criterion assesses.', group, weight: 100,
    applicability: 'REQUIRED', method: 'JUDGMENT', allowed_evidence: ['REPORT_EXCERPT'], required_evidence: ['REPORT_EXCERPT'],
    minimum_evidence: 1, levels: defaultLevels(), test_ids: [], requirement_map: {}, feedback_required: true, focus: 'GENERAL',
  };
}

/** Effective share of the total for each active criterion, using the backend's formula. */
export function effectiveShares(r: Rubric): Record<string, number> {
  const active = r.criteria.filter((c) => c.applicability !== 'EXCLUDED');
  const out: Record<string, number> = {};
  const grouped = Object.keys(r.group_weights || {}).length > 0;
  let ws: [string, number][] = active.map((c) => [c.id, c.weight]);
  if (grouped) {
    const sums: Record<string, number> = {};
    active.forEach((c) => (sums[c.group] = (sums[c.group] || 0) + c.weight));
    ws = active.map((c) => [c.id, ((r.group_weights[c.group] || 0) * c.weight) / sums[c.group]]);
  }
  const total = ws.reduce((a, [, w]) => a + w, 0);
  ws.forEach(([id, w]) => (out[id] = total ? w / total : 0));
  return out;
}

// ------------------------------------------------------------- Templates
export interface RubricTemplate {
  key: string;
  name: string;
  summary: string;
  origin: 'BACKEND' | 'BUILT_IN' | 'SAVED';
  rubric: Rubric;
}

function reportOnly(): Rubric {
  const r = normalizeRubric(unified);
  r.id = 'report-only-submission';
  r.title = 'Report-only submission';
  for (const c of r.criteria) {
    if (c.id === 'implementation_quality' || c.id === 'functional_validation') c.applicability = 'EXCLUDED';
    if (c.id === 'technical_design') { c.allowed_evidence = ['REPORT_EXCERPT']; c.required_evidence = ['REPORT_EXCERPT']; }
  }
  return r;
}

export const DEFAULT_TEMPLATE_KEY = 'software_project_unified';

export const BUILT_IN_TEMPLATES: RubricTemplate[] = [
  { key: DEFAULT_TEMPLATE_KEY, name: 'Software project (ProjectIQ default)', origin: 'BACKEND', rubric: normalizeRubric(unified),
    summary: 'Six sections covering problem, report, design, implementation, validation and optional research. The default used by the unified evaluator.' },
  { key: 'report_only', name: 'Report-only submission', origin: 'BUILT_IN', rubric: reportOnly(),
    summary: 'For submissions without a repository. Implementation and functional testing are not applicable; design is judged from the report.' },
  { key: 'software_project_intelligent', name: 'Software project (evidence-weighted)', origin: 'BACKEND', rubric: normalizeRubric(intelligent),
    summary: 'Backend rubric variant with explicit evidence focus per criterion.' },
  { key: 'software_project', name: 'Software project (flat weights)', origin: 'BACKEND', rubric: normalizeRubric(softwareProject),
    summary: 'Earlier backend rubric with criterion-level weights and no sections.' },
  { key: 'minicollab_hybrid', name: 'MiniCollab hybrid', origin: 'BACKEND', rubric: normalizeRubric(minicollab),
    summary: 'Assignment-specific rubric that mixes judgment with reviewed test ratios.' },
  { key: 'minicollab_hybrid_detailed', name: 'MiniCollab hybrid (detailed)', origin: 'BACKEND', rubric: normalizeRubric(minicollabDetailed),
    summary: 'Detailed assignment rubric with requirement coverage mapped to reviewed checks.' },
];

const SAVED_KEY = 'projectiq.savedTemplates.v1';

export function loadSavedTemplates(): RubricTemplate[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY);
    return raw ? (JSON.parse(raw) as RubricTemplate[]) : [];
  } catch { return []; }
}

export function saveTemplate(t: RubricTemplate): RubricTemplate[] {
  const all = loadSavedTemplates().filter((x) => x.key !== t.key);
  all.unshift(t);
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(all)); } catch { /* storage unavailable */ }
  return all;
}

export function deleteTemplate(key: string): RubricTemplate[] {
  const all = loadSavedTemplates().filter((x) => x.key !== key);
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(all)); } catch { /* storage unavailable */ }
  return all;
}

export function allTemplates(): RubricTemplate[] {
  return [...BUILT_IN_TEMPLATES, ...loadSavedTemplates()];
}

export function defaultRubric(): Rubric {
  return clone(BUILT_IN_TEMPLATES[0].rubric);
}
