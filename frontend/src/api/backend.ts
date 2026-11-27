import type {
  EvaluationRecord, EvaluationRequest, Rubric, RunStatus, TeacherDecisionEntry, TeacherReview,
} from './types';

export interface EvaluationSummary {
  id: string;
  title: string;
  submission_id: string;
  students: string[];
  course?: string;
  evaluated_utc?: string;
  workflow_status: string;
  assessment_status: string;
  max_points: number;
  proposed_points: number | null;
  reviewed_points: number | null;
  assessed_weight_percent: number;
  reviewed_weight_percent: number;
  has_repository: boolean;
  agent_statuses: Record<string, string>;
  source: EvaluationRecord['source'];
}

/**
 * Honest description of what the connected backend can actually do today.
 * The UI disables or labels anything that is not operational.
 */
export interface BackendCapabilities {
  name: string;
  kind: 'mock' | 'http';
  /** Modes the unified evaluate() can run live. Today the live collector is STATIC-only. */
  liveExecutionerModes: ('STATIC' | 'REVIEWED' | 'RESUME' | 'TRUSTED_DEMO')[];
  /** Saved Executioner results (including REVIEWED/RESUME runs) can be imported with acknowledgment. */
  importExecutionerResults: boolean;
  onlineResearchVerification: boolean;
  integrityReferenceUpload: boolean;
  aiDetection: 'AVAILABLE' | 'NOT_INSTALLED';
  rubricReassessment: boolean;
  persistsTeacherDecisions: 'BACKEND' | 'BROWSER_ONLY';
  notes: string[];
}

export interface ReassessResult {
  updated: TeacherReview;
  messages: string[];
  /** The backend changed the model base (new proposals); reload the evaluation. */
  reload?: boolean;
}

export interface Backend {
  capabilities(): Promise<BackendCapabilities>;
  listEvaluations(): Promise<EvaluationSummary[]>;
  getEvaluation(id: string): Promise<EvaluationRecord>;
  startEvaluation(request: EvaluationRequest): Promise<{ run_id: string }>;
  getRun(runId: string): Promise<RunStatus>;
  cancelRun(runId: string): Promise<void>;
  saveTeacherDecision(id: string, entry: TeacherDecisionEntry): Promise<TeacherReview>;
  clearTeacherDecision(id: string, criterionId: string): Promise<TeacherReview>;
  /** Weights, total marks or applicability changes after evaluation. Never reruns agents. */
  updateRubric(id: string, rubric: Rubric, invalidated: string[]): Promise<TeacherReview>;
  /** Rubric-model reassessment of selected criteria only; optionally retry MODEL deferrals. */
  reassess(id: string, criterionIds: string[], opts: { retryDeferred: boolean }): Promise<ReassessResult>;
  resetReview(id: string): Promise<TeacherReview>;
}

export function emptyReview(): TeacherReview {
  return { decisions: {}, rubricOverride: null, pendingReassessment: [], retryRequested: [], history: [] };
}
