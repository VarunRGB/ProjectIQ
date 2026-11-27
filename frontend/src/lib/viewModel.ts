/**
 * Shared view model for the results dashboard and the printable report, so both
 * show the same numbers and wording. Reads only backend artifacts.
 */
import type { AgentCard, AgentName, EvaluationRecord, Evidence, Rubric } from '../api/types';
import { recompute, type Recomputed } from './scoring';
import { DIAG_CATEGORY } from './labels';
import { functionalEvidence, reviewedDiagnosis, type FunctionalEvidence } from './functionalEvidence';

export interface ViewModel {
  rec: EvaluationRecord;
  rubric: Rubric;
  assessment: Recomputed;
  evidenceById: Map<string, Evidence>;
  provenance: ProvenanceSummary;
  executioner: ExecutionerVM | null;
  research: ResearchVM | null;
  integrity: IntegrityVM | null;
  summary: { summary: string | null; key_points: string[]; method: string | null; word_count: number | null } | null;
  agentOrder: AgentName[];
}

export interface ProvenanceSummary {
  mode: 'LIVE' | 'IMPORTED';
  acknowledged: boolean;
  perAgent: Record<AgentName, { action: string; provenance: string | null; check: string }>;
  reused: AgentName[];
  live: AgentName[];
  imported: AgentName[];
  acknowledgmentRequired: boolean;
}

export function buildViewModel(rec: EvaluationRecord): ViewModel {
  const rubric = rec.review.rubricOverride ?? rec.result.rubric;
  // The TS scoring port is retained for mock fixtures only. The live backend is
  // authoritative, including its evidence eligibility and unresolved criteria.
  const authoritative = rec.source === 'BACKEND' ? (rec.engineAssessment ?? rec.result.assessment) : null;
  const assessment: Recomputed = authoritative ? { ...authoritative,
    changedFromBackend: !!rec.review.rubricOverride || Object.keys(rec.review.decisions).length > 0,
    criteria: authoritative.criteria.map((c) => {
      const b = rec.result.assessment.criteria.find((x) => x.id === c.id);
      const origin = c.status === 'TEACHER_REVIEWED' ? 'TEACHER' : c.status === 'PROPOSED' ? 'MODEL' : c.status === 'OBJECTIVE_PROVISIONAL' ? 'OBJECTIVE' : c.status === 'EXCLUDED' ? 'EXCLUDED' : 'NONE';
      const proposal = b && b.status !== 'EXCLUDED' && b.status !== 'TEACHER_REVIEWED' ? { level_id: b.level_id, fraction: b.fraction, status: b.status } : null;
      return { ...c, origin, proposal };
    }) } : recompute({ base: rec.result.assessment, rubric, teacher: rec.review.decisions, invalidated: rec.review.pendingReassessment });
  const evidenceById = new Map((rec.evidence?.items ?? []).map((e) => [e.id, e]));
  const agents = rec.result.agents;
  const perAgent = Object.fromEntries(Object.entries(agents).map(([k, a]) => [k, { action: rec.meta.bridge_job_actions?.[k as AgentName] ?? rec.result.agent_actions[k as AgentName], provenance: a.receipt_provenance, check: a.evidence_check }])) as ProvenanceSummary['perAgent'];
  const names = Object.keys(agents) as AgentName[];
  const provenance: ProvenanceSummary = {
    mode: rec.result.collection_mode,
    acknowledged: names.some((n) => agents[n].receipt_provenance === 'IMPORTED_ACKNOWLEDGED'),
    perAgent,
    reused: names.filter((n) => perAgent[n].action === 'REUSED'),
    live: names.filter((n) => agents[n].receipt_provenance === 'NATIVE_RECEIPT_MATCHED'),
    imported: names.filter((n) => (agents[n].receipt_provenance || '').startsWith('IMPORTED')),
    acknowledgmentRequired: names.some((n) => agents[n].receipt_provenance === 'IMPORTED_UNCONFIRMED'),
  };
  const s = agents.summarizer;
  return {
    rec, rubric, assessment, evidenceById, provenance,
    executioner: executionerVM(agents.executioner),
    research: researchVM(agents.research),
    integrity: integrityVM(agents.integrity),
    summary: s.status === 'COMPLETED' || s.status === 'PARTIAL'
      ? { summary: s.details.summary?.summary ?? null, key_points: s.details.summary?.key_points ?? [], method: s.details.summary_display_method ?? null, word_count: s.details.word_count ?? null }
      : null,
    agentOrder: ['executioner', 'research', 'integrity', 'summarizer'],
  };
}

// ------------------------------------------------------------- Executioner
export interface TestRow {
  id: string;
  title: string;
  category: string;
  scope: 'PLANNED' | 'REVIEWED_SCENARIO';
  target: { method?: string; path?: string } | null;
  outcome: string;
  reason: string;
  planning_status?: string;
  suite?: string;
  expected: any[];
  observations: any[];
  reused: boolean | null;
  related: any[];
}

export interface ExecutionerVM {
  card: AgentCard;
  mode: string;
  effectiveMode: string;
  status: string;
  commit: string | null;
  repository: string | null;
  intelligence: any;
  plan: any;
  planned: TestRow[];
  reviewed: TestRow[];
  counts: Record<string, number>;
  reviewedCounts: Record<string, number>;
  suites: any[];
  quality: any;
  diagnoses: (any & { owner: string })[];
  hypotheses: any[];
  recommendations: any[];
  reviewRequests: any[];
  limitations: string[];
  workflow: any[];
  automatic: FunctionalEvidence | null;
  freshSuites: number;
  reusedSuites: number;
}

function executionerVM(card: AgentCard): ExecutionerVM | null {
  if (!card || card.status === 'NOT_RUN' || !card.details || !Object.keys(card.details).length) return card && card.status !== 'NOT_RUN' ? emptyExec(card) : null;
  const d = card.details;
  const m = d.test_case_matrix ?? {};
  const suites: any[] = d.suites ?? [];
  const reusedOf = (suite?: string) => { const s = suites.find((x) => x.kind === suite); return s ? !!s.reused_from_prior : null; };
  const planned: TestRow[] = (m.discovered_cases ?? []).map((c: any) => ({
    id: c.test_id, title: c.title, category: c.category, scope: 'PLANNED', target: c.target, outcome: c.outcome, reason: c.reason,
    planning_status: c.planning_status, expected: c.expected_assertions ?? [], observations: c.evidence ?? [],
    suite: c.evidence?.[0]?.suite, reused: c.evidence?.length ? !!c.evidence[0].reused : null, related: c.related_reviewed_checks ?? [],
  }));
  const reviewed: TestRow[] = (m.reviewed_only_checks ?? []).map((c: any) => ({
    id: `${c.suite}:${c.check_id}`, title: String(c.check_id).replace(/_/g, ' ').toLowerCase().replace(/^./, (x: string) => x.toUpperCase()),
    category: c.suite, scope: 'REVIEWED_SCENARIO', target: null, outcome: c.outcome, reason: c.reason, suite: c.suite,
    expected: [], observations: c.evidence ?? [], reused: reusedOf(c.suite), related: [],
  }));
  const automatic = functionalEvidence(d.trusted_demo, card.evidence_check);
  const diagnoses = (d.diagnostics?.diagnoses ?? []).map((x: any) => ({ ...reviewedDiagnosis(x, automatic), owner: DIAG_CATEGORY[x.category]?.owner ?? 'evidence' }));
  return {
    automatic, card, mode: d.mode, effectiveMode: d.effective_mode, status: card.native_status ?? '', commit: d.source?.commit_sha ?? null,
    repository: d.source?.repository ?? null, intelligence: d.project_intelligence ?? {}, plan: d.test_plan ?? {}, planned, reviewed,
    counts: m.counts ?? {}, reviewedCounts: m.reviewed_check_counts ?? {}, suites, quality: d.code_quality ?? null, diagnoses,
    hypotheses: d.diagnostics?.root_cause_hypotheses ?? [], recommendations: d.recommendations ?? [], reviewRequests: d.review_requests ?? [],
    limitations: d.limitations ?? [], workflow: d.workflow ?? [],
    freshSuites: suites.filter((s) => s.vm_verified && !s.reused_from_prior).length,
    reusedSuites: suites.filter((s) => s.vm_verified && s.reused_from_prior).length,
  };
}

function emptyExec(card: AgentCard): ExecutionerVM {
  return { automatic: null, card, mode: '', effectiveMode: '', status: card.native_status ?? card.status, commit: null, repository: null, intelligence: {}, plan: {}, planned: [], reviewed: [],
    counts: {}, reviewedCounts: {}, suites: [], quality: null, diagnoses: [], hypotheses: [], recommendations: [], reviewRequests: [], limitations: card.limitations, workflow: [], freshSuites: 0, reusedSuites: 0 };
}

// ------------------------------------------------------------- Research
export interface ReferenceRow {
  n: number;
  title: string;
  verification: string;
  relevance: string;
  confidence: string;
  year: number | null;
  yearSource: string;
  ageBand: string;
  inLiteratureReview: boolean;
  linked: boolean;
  engineering: boolean;
  verifiedTitle?: string | null;
  verifiedYear?: number | null;
  verifiedDoi?: string | null;
  verifiedAuthors?: string[];
  submittedAuthors?: string | null;
  titleSimilarity?: number | null;
  explanation?: string;
  notes: string[];
  attention?: { priority: string; reasons: string[] };
  scores?: { topical?: number | null; methodological?: number | null; citation?: number | null };
}

export interface ResearchVM {
  card: AgentCard;
  report: any;
  refs: ReferenceRow[];
  assessmentState: string | null;
  attentionCodes: string[];
  failed: boolean;
}

function researchVM(card: AgentCard): ResearchVM | null {
  if (!card || card.status === 'NOT_RUN') return null;
  if (card.status === 'FAILED' || !card.details?.research_report) return { card, report: null, refs: [], assessmentState: null, attentionCodes: [], failed: card.status === 'FAILED' };
  const d = card.details; const r = d.research_report;
  const att = new Map<number, any>((r.attention_items ?? []).map((a: any) => [a.reference_number, a]));
  const refs: ReferenceRow[] = (r.reference_overview ?? []).map((o: any, i: number) => {
    const v = d.verification_results?.[i] ?? {}; const rel = d.relevance_results?.[i] ?? {};
    return {
      n: o.reference_number, title: o.title, verification: o.verification_status, relevance: o.relevance_classification, confidence: o.confidence,
      year: o.publication_year ?? null, yearSource: o.year_source, ageBand: o.age_band, inLiteratureReview: !!o.cited_in_literature_review,
      linked: !!o.citation_context_linked, engineering: !!o.supporting_engineering_connection,
      verifiedTitle: v.verified_title, verifiedYear: v.verified_year, verifiedDoi: v.verified_doi, verifiedAuthors: v.verified_authors, submittedAuthors: v.submitted_authors,
      titleSimilarity: v.title_similarity, explanation: rel.explanation, notes: [...(v.notes ?? []), ...(rel.notes ?? [])],
      attention: att.get(o.reference_number), scores: { topical: rel.topical_similarity, methodological: rel.methodological_similarity, citation: rel.citation_context_similarity },
    };
  });
  return { card, report: r, refs, assessmentState: null, attentionCodes: [], failed: false };
}

// ------------------------------------------------------------- Integrity
export interface IntegrityVM {
  card: AgentCard;
  comparisonAvailable: boolean;
  overlap: number | null;
  inChunks: number | null;
  wordsChecked: number | null;
  references: string[];
  byCategory: Record<string, number | null>;
  byReference: Record<string, number>;
  matches: any[];
  /** Secondary low-threshold retrieval (advisory, never counted). */
  secondary: any[];
  chunks: any[];
  backends: Record<string, string>;
  notes: string[];
  ai: AgentCard['ai_detection'];
}

function integrityVM(card: AgentCard): IntegrityVM | null {
  if (!card || card.status === 'NOT_RUN') return null;
  const d = card.details ?? {};
  const metric = (id: string) => card.metrics.find((m) => m.id === id)?.value ?? null;
  const overlap = metric('reference_overlap');
  return {
    card, comparisonAvailable: overlap !== null, overlap, inChunks: metric('copied_chunks'), wordsChecked: d.words_checked ?? null,
    references: d.references ?? [], byCategory: d.percent_by_category ?? {}, byReference: d.percent_by_reference ?? {},
    matches: (d.matches ?? []).filter((m: any) => m.category), secondary: (d.matches ?? []).filter((m: any) => !m.category && m.secondary_review), chunks: d.chunks ?? [], backends: d.backends ?? {}, notes: d.notes ?? [],
    ai: card.ai_detection,
  };
}

export function fmtPoints(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '—';
  const r = Math.round(v * 100) / 100;
  return Number.isInteger(r) ? String(r) : r.toFixed(digits).replace(/0$/, '');
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return 'Unavailable';
  return `${Number(v.toFixed(digits))}%`;
}
