/**
 * TypeScript mirror of the ProjectIQ backend contracts. Nothing here is invented:
 *
 *  - Rubric, Criterion, Level, Decision, DecisionSet, Assessment, CriterionResult,
 *    Evidence, EvidenceBundle  -> projectiq_core/phase2/contracts.py (schema 2.0)
 *  - AgentResult, AIAnalysis, Metric, Submission -> projectiq_core/contracts.py (1.0)
 *  - UnifiedResult -> result.json written by projectiq_core/orchestrator.py evaluate()
 *    (schema_version "2E.1")
 *  - Native `details` shapes -> the adapters in projectiq_core/adapters.py, which keep
 *    native field names from each agent (Executioner 0.1, Research report 1.1,
 *    Integrity DocumentReport, Summarizer SUMMARY_ONLY).
 *
 * Native details are typed as optional because agents can be partial, failed or
 * withheld (the orchestrator empties details when a receipt does not match).
 */

export type AgentName = 'summarizer' | 'integrity' | 'research' | 'executioner';
export const AGENTS: AgentName[] = ['summarizer', 'integrity', 'research', 'executioner'];
export type AgentStatus = 'COMPLETED' | 'PARTIAL' | 'FAILED' | 'NOT_RUN' | 'REVIEW_REQUIRED';

export type EvidenceKind =
  | 'REPORT_EXCERPT' | 'STATIC_SOURCE' | 'RESEARCH_RECORD' | 'TEST_OBSERVATION' | 'TEST_PLAN'
  | 'REPORT_SUMMARY' | 'LEGACY_JUDGEMENT' | 'INTEGRITY_ADVISORY' | 'METRIC';
export type GradingKind = 'REPORT_EXCERPT' | 'STATIC_SOURCE' | 'RESEARCH_RECORD' | 'TEST_OBSERVATION';
export type Provenance = 'REPORT_SNAPSHOT' | 'NATIVE_RECEIPT_MATCHED' | 'IMPORTED_ACKNOWLEDGED' | 'IMPORTED_UNCONFIRMED';

// ---------------------------------------------------------------- Rubric (2.0)
export interface Level {
  id: string;
  label: string;
  fraction: number; // 0..1, strictly increasing, first 0 and last 1
  descriptor: string;
  requires_failed_observation?: boolean;
}

export type Applicability = 'REQUIRED' | 'OPTIONAL' | 'EXCLUDED';
export type CriterionMethod = 'JUDGMENT' | 'TEST_RATIO' | 'REVIEWED_RATIO' | 'REQUIREMENT_COVERAGE';
export type Focus = 'GENERAL' | 'REPORT' | 'DESIGN' | 'IMPLEMENTATION' | 'FUNCTIONAL' | 'RESEARCH'
  | 'TEST_STRATEGY' | 'STYLE' | 'MAINTAINABILITY' | 'SECURITY';

export interface Criterion {
  id: string;
  title: string;
  description: string;
  group: string;
  weight: number;
  applicability: Applicability;
  method: CriterionMethod;
  allowed_evidence: GradingKind[];
  required_evidence: GradingKind[];
  minimum_evidence: number;
  levels: Level[];
  test_ids: string[];
  requirement_map: Record<string, string[]>;
  feedback_required: boolean;
  focus: Focus;
}

export interface Rubric {
  schema_version: '2.0';
  id: string;
  title: string;
  revision: string;
  total_points: number;
  group_weights: Record<string, number>;
  criteria: Criterion[];
}

// ------------------------------------------------------------ Decisions (2.0)
export interface Feedback {
  strength: string;
  limitation: string;
  improvement: string;
  observed_failure: boolean;
}

export type DecisionAction = 'SCORE' | 'EXCLUDE' | 'DEFER' | 'CONFIRM_TEST_RATIO';
export interface Decision {
  criterion_id: string;
  action: DecisionAction;
  source: 'TEACHER' | 'MODEL';
  level_id?: string | null;
  evidence_ids: string[];
  rationale: string;
  feedback?: Feedback | null;
}

export interface DecisionSet {
  schema_version: '2.0';
  evidence_sha256: string;
  rubric_sha256: string;
  decisions: Decision[];
}

// ----------------------------------------------------------- Assessment (2.0)
export type CriterionStatus = 'UNASSESSED' | 'EXCLUDED' | 'PROPOSED' | 'TEACHER_REVIEWED' | 'OBJECTIVE_PROVISIONAL';
export type AssessmentStatus = 'INSUFFICIENT_EVIDENCE' | 'READY_FOR_TEACHER_REVIEW' | 'TEACHER_REVIEWED';

export interface EvidenceDetail {
  evidence_id: string;
  kind: EvidenceKind;
  label: string;
  locator: string;
  artifact_sha256: string;
  provenance: Provenance;
  text: string;
  limitations: string[];
  [extra: string]: unknown;
}

export interface CriterionResult {
  id: string;
  group: string;
  title: string;
  applicability: string;
  status: CriterionStatus;
  configured_weight: number;
  effective_weight_percent: number;
  max_points: number;
  earned_points: number | null;
  fraction: number | null;
  level_id: string | null;
  evidence_ids: string[];
  reason: string;
  feedback: Feedback | null;
  review_flags: string[];
  objective_details: Record<string, unknown>;
  evidence_details: EvidenceDetail[];
}

export interface GroupResult {
  id: string;
  effective_weight_percent: number;
  max_points: number;
  assessed_max_points: number;
  assessed_subtotal_points: number;
  assessed_weight_percent: number | null;
  unresolved_criterion_ids: string[];
}

export interface Assessment {
  schema_version: '2.0';
  evidence_sha256: string;
  rubric_sha256: string;
  decisions_sha256: string;
  status: AssessmentStatus;
  max_points: number;
  proposed_points: number | null;
  reviewed_points: number | null;
  assessed_subtotal_points: number;
  assessed_max_points: number;
  assessed_weight_percent: number;
  reviewed_weight_percent: number;
  unresolved_criterion_ids: string[];
  criteria: CriterionResult[];
  groups: GroupResult[];
  limitations: string[];
}

// ------------------------------------------------------------- Evidence (2.0)
export interface Evidence {
  id: string;
  agent: 'report' | AgentName;
  kind: EvidenceKind;
  label: string;
  text: string;
  artifact_sha256: string;
  locator: string;
  provenance: Provenance;
  eligible_for_review: boolean;
  limitations: string[];
  data: Record<string, any>;
}

export interface EvidenceBundle {
  schema_version: '2.0';
  evaluation_sha256: string;
  submission_id: string;
  report_sha256: string;
  extracted_text_sha256: string | null;
  report_extraction: 'COMPLETE' | 'TRUNCATED' | 'UNAVAILABLE';
  repository_commit: string | null;
  agent_checks: Record<string, string>;
  items: Evidence[];
  observations: Record<string, any>;
  limitations: string[];
}

// ------------------------------------------------- Phase 1 envelope (1.0)
export interface Metric {
  id: string;
  label: string;
  value: number | null;
  unit: string;
  scope: string;
}

/** Integrity AI estimate. COMPLETED requires a value; every other state carries null, never 0. */
export interface AIAnalysis {
  status: 'NOT_AVAILABLE' | 'COMPLETED' | 'INCONCLUSIVE' | 'FAILED';
  estimated_percent: number | null;
  interpretation: string | null;
  confidence: string | null;
  flagged_passages: AIFlaggedPassage[] | unknown[];
  limitations: string[];
}

/** Current native passage shape from projectiq_integrity/ai_detection.py. */
export interface AIFlaggedPassage {
  location?: string;
  words?: number;
  score?: number;
  threshold?: number;
  detector?: string;
  score_name?: string;
  rule?: string;
  stock_phrases?: string[];
  text?: string;
}

export interface Submission {
  id: string;
  project_title: string;
  report_name: string;
  report_sha256: string;
  repository_url: string | null;
  requested_ref: string | null;
}

/** Frontend-facing agent card from orchestrator._frontend_agents(). */
export interface AgentCard {
  agent: AgentName;
  status: AgentStatus;
  native_status: string | null;
  metrics: Metric[];
  details: Record<string, any>;
  limitations: string[];
  error_code: string | null;
  ai_detection: AIAnalysis | null;
  evidence_check: 'MATCHED' | 'RECEIPT_OR_CONTRACT_MISMATCH' | 'NOT_RUN' | 'FAILED' | string;
  receipt_provenance: Provenance | null;
  required_by_rubric?: boolean;
}

export type AgentAction = 'COLLECTED' | 'SKIPPED' | 'REUSED' | 'NOT_AVAILABLE' | 'IMPORTED';
export type WorkflowStatus = 'COMPLETE' | 'INCOMPLETE_ASSESSMENT' | 'PARTIAL_ERROR' | 'CANCELLED';
export type ReviewState = CriterionStatus | 'REVIEW_REQUIRED' | 'PREVIEW_PENDING';

/** result.json, schema 2E.1 */
export interface UnifiedResult {
  schema_version: '2E.1';
  submission: Submission;
  collection_mode: 'LIVE' | 'IMPORTED';
  agent_actions: Record<AgentName, AgentAction>;
  agents: Record<AgentName, AgentCard>;
  rubric: Rubric;
  assessment: Assessment;
  evidence_intelligence: Record<string, any>;
  decision_reuse: { preserved_criterion_ids: string[]; dropped_decisions: Record<string, string> };
  research_required: boolean;
  evaluation_policy: string | null;
  criterion_review_states: Record<string, ReviewState>;
  legacy_judge_executed: false;
  rubric_mode: 'PREVIEW' | 'OBJECTIVE_ONLY' | 'AUTOMATIC';
  workflow_status: WorkflowStatus;
  agent_failures: AgentName[];
  rubric_exit_code: number;
  elapsed_seconds: number;
  artifacts: Record<string, string>;
  limitations: string[];
  /** IEEE format checker (projectiq_core/ieee_integration.py). Advisory; never affects marks. Absent in older runs. */
  ieee_format?: IEEEFormat;
}

export type IEEEVerdict = 'LIKELY_IEEE_STYLE' | 'NEEDS_REVIEW' | 'NEEDS_PDF_VERIFICATION' | 'NOT_IEEE_LIKE' | 'CANNOT_VERIFY_LAYOUT' | 'NOT_EVALUATED';

export interface IEEECategory {
  category: string;
  rubric_weight: number;
  earned: number | null;
  possible: number;
  status: 'pass' | 'needs_attention' | 'not_applicable';
}

export interface IEEECheck {
  category: string;
  criterion: string;
  earned: number;
  possible: number;
  detail: string;
  suggestion: string;
  page: number | null;
  applicable: boolean;
  status: 'pass' | 'warning' | 'fail' | 'not_applicable';
}

export interface IEEEFormat {
  status: 'COMPLETED' | 'REVIEW_REQUIRED' | 'FAILED';
  score: number | null;
  max_score: number;
  rating?: string;
  format_verdict: IEEEVerdict;
  assessment_scope?: 'layout_and_structure' | 'style_and_structure' | 'structure_only' | string;
  layout_verdict?: string;
  categories?: IEEECategory[];
  checks?: IEEECheck[];
  notes?: string[];
  disclaimer?: string;
  error_code?: string;
}

// ------------------------------------------------- Frontend-only envelope
/**
 * What the frontend holds for one evaluation. `result`, `evidence` and `decisions`
 * are exactly the backend artifacts (result.json, rubric/evidence.json,
 * rubric/decisions.json). `meta` holds course/roster context that the backend
 * does not store yet; it is optional and displayed only when present.
 */
export interface EvaluationRecord {
  id: string;
  result: UnifiedResult;
  evidence: EvidenceBundle | null;
  decisions: DecisionSet | null;
  reportText?: string | null;
  meta: EvaluationMeta;
  /** Teacher review state layered on top of the backend decisions. */
  review: TeacherReview;
  source: 'MOCK_FIXTURE' | 'BACKEND';
}

export interface EvaluationMeta {
  students?: string[];
  course?: string;
  cohort?: string;
  submitted_utc?: string;
  evaluated_utc?: string;
  executioner_mode_requested?: ExecutionerMode | null;
  references_uploaded?: number;
  verify_research?: boolean;
  elapsed_seconds?: number;
}

export interface TeacherDecisionEntry extends Decision {
  source: 'TEACHER';
  decided_utc: string;
  /** Free-text remarks shown in the report. Mapped to Decision.rationale on save. */
  remarks: string;
  basis: 'APPROVED_PROPOSAL' | 'OVERRIDE' | 'TEACHER_SCORED';
}

export interface TeacherReview {
  decisions: Record<string, TeacherDecisionEntry>;
  /** Rubric edits applied after evaluation (weights/total). Policy edits are tracked as pending. */
  rubricOverride: Rubric | null;
  pendingReassessment: string[];
  /** Criteria whose MODEL deferral the teacher asked to retry. */
  retryRequested: string[];
  history: ReviewEvent[];
}

export interface ReviewEvent {
  at: string;
  kind: 'APPROVED' | 'OVERRIDDEN' | 'SCORED' | 'CLEARED' | 'REWEIGHTED' | 'REASSESS_REQUESTED' | 'RETRY_REQUESTED' | 'REASSESSED';
  criterion_id?: string;
  summary: string;
}

// ------------------------------------------------- Evaluation requests
export type ExecutionerMode = 'STATIC' | 'REVIEWED' | 'RESUME';

export interface EvaluationRequest {
  title: string;
  submission_id: string;
  students: string[];
  course?: string;
  report_file_name: string;
  report_file?: File | null;
  repository_url: string | null;
  ref: string | null;
  reference_files: string[];
  /** File objects for upload (HTTP backend); names above are kept for display. */
  reference_file_objects?: File[];
  /** Existing executioner_result.json to import (REVIEWED/RESUME evidence produced elsewhere). */
  executioner_import_file?: File | null;
  /** Earlier executioner_result.json for RESUME when run through the bridge. */
  resume_from_file?: File | null;
  /** Operator acknowledgment that imported results belong to this submission. */
  acknowledge_imported?: boolean;
  rubric: Rubric;
  research_required: boolean;
  verify_research: boolean;
  executioner: ExecutionerRequest | null;
  rubric_mode: 'AUTOMATIC' | 'PREVIEW' | 'OBJECTIVE_ONLY';
}

export interface ExecutionerRequest {
  mode: ExecutionerMode;
  run_readiness: 'AUTO' | 'YES' | 'NO';
  max_vm_launches: number;
  suites: { kind: string; catalogs: Record<string, string> }[];
  resume_target?: 'STATIC' | 'REVIEWED';
  resume_from?: string;
}

export type RunStage = 'queued' | 'format' | 'summarizer' | 'integrity' | 'research' | 'executioner' | 'evidence' | 'rubric' | 'done' | 'failed';

export interface RunEvent {
  at_ms: number;
  stage: RunStage;
  agent?: AgentName | 'rubric' | 'format';
  message: string;
  level: 'info' | 'warn' | 'error' | 'success';
}

export interface RunStatus {
  run_id: string;
  evaluation_id: string | null;
  title: string;
  started_at: string;
  elapsed_ms: number;
  stage: RunStage;
  state: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
  agents: Record<AgentName | 'rubric' | 'format', { state: 'waiting' | 'running' | 'done' | 'skipped' | 'failed' | 'partial'; detail?: string; elapsed_ms?: number; /** Real progress when the backend reports it (rubric batches). */ progress?: number }>;
  events: RunEvent[];
}
