/**
 * Adapter for the local ProjectIQ bridge (frontend/bridge/server.py).
 *
 *   GET  /api/capabilities                 -> BackendCapabilities
 *   GET  /api/evaluations                  -> EvaluationSummary[]
 *   GET  /api/evaluations/:id              -> { result, evidence, decisions, meta, review, engine_assessment }
 *   POST /api/evaluations                  -> { run_id }  body: { request, report, references[], executioner_import?, resume_from?, acknowledge_imported }
 *                                             (files as { name, b64 })
 *   GET  /api/runs/:run_id                 -> RunStatus
 *   POST /api/runs/:run_id/cancel
 *   PUT  /api/evaluations/:id/review       -> { review, assessment }   validated by the real Phase 2 engine; 422 { error } if rejected
 *   POST /api/evaluations/:id/reassess     -> { review, assessment, messages }   phase2 propose for the selected criteria
 */
import type { Backend, BackendCapabilities, EvaluationSummary, ReassessResult } from './backend';
import { emptyReview } from './backend';
import type { EvaluationRecord, EvaluationRequest, Rubric, RunStatus, TeacherDecisionEntry, TeacherReview } from './types';

export class EngineError extends Error {
  constructor(public code: string) { super(code); }
}

async function b64(f: File): Promise<{ name: string; b64: string }> {
  const buf = new Uint8Array(await f.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { name: f.name, b64: btoa(s) };
}

export class HttpBackend implements Backend {
  private reviews = new Map<string, TeacherReview>();
  constructor(private base: string) {}

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(this.base + path, {
      method, headers: body ? { 'Content-Type': 'application/json' } : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 422) throw new EngineError(data.error ?? 'REJECTED');
    if (!res.ok) throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
    return data as T;
  }

  async capabilities(): Promise<BackendCapabilities> {
    try { return await this.call<BackendCapabilities>('GET', '/api/capabilities'); } catch {
      return { name: 'Local bridge (not reachable)', kind: 'http', liveExecutionerModes: [], importExecutionerResults: false, onlineResearchVerification: false,
        integrityReferenceUpload: false, aiDetection: 'NOT_INSTALLED', rubricReassessment: false, persistsTeacherDecisions: 'BROWSER_ONLY',
        notes: ['The local bridge did not respond. Start it with: python frontend/bridge/server.py'] };
    }
  }

  listEvaluations() { return this.call<EvaluationSummary[]>('GET', '/api/evaluations'); }

  async getEvaluation(id: string): Promise<EvaluationRecord> {
    const d = await this.call<any>('GET', `/api/evaluations/${encodeURIComponent(id)}`);
    const review: TeacherReview = { ...emptyReview(), ...(d.review ?? {}) };
    this.reviews.set(id, review);
    return { id, result: d.result, evidence: d.evidence ?? null, decisions: d.decisions ?? null, reportText: null, meta: d.meta ?? {}, review, source: 'BACKEND' };
  }

  async startEvaluation(req: EvaluationRequest) {
    const { report_file, reference_file_objects, executioner_import_file, resume_from_file, acknowledge_imported, ...rest } = req;
    if (!report_file) throw new Error('Attach the report file.');
    const body = {
      request: rest,
      report: await b64(report_file),
      references: await Promise.all((reference_file_objects ?? []).map(b64)),
      executioner_import: executioner_import_file ? await b64(executioner_import_file) : null,
      resume_from: resume_from_file ? await b64(resume_from_file) : null,
      acknowledge_imported: !!acknowledge_imported,
    };
    return this.call<{ run_id: string }>('POST', '/api/evaluations', body);
  }

  getRun(runId: string) { return this.call<RunStatus>('GET', `/api/runs/${encodeURIComponent(runId)}`); }
  async cancelRun(runId: string) { await this.call('POST', `/api/runs/${encodeURIComponent(runId)}/cancel`, {}); }

  private current(id: string) { return structuredClone(this.reviews.get(id) ?? emptyReview()); }

  /** Every review change is validated and saved by the real engine before it is accepted. */
  private async put(id: string, review: TeacherReview): Promise<TeacherReview> {
    const r = await this.call<{ review: TeacherReview }>('PUT', `/api/evaluations/${encodeURIComponent(id)}/review`, { review });
    const saved = { ...emptyReview(), ...r.review };
    this.reviews.set(id, saved);
    return saved;
  }

  async saveTeacherDecision(id: string, entry: TeacherDecisionEntry) {
    const r = this.current(id);
    r.decisions[entry.criterion_id] = entry;
    r.pendingReassessment = r.pendingReassessment.filter((c) => c !== entry.criterion_id);
    r.history.unshift({ at: entry.decided_utc, kind: entry.basis === 'APPROVED_PROPOSAL' ? 'APPROVED' : entry.basis === 'OVERRIDE' ? 'OVERRIDDEN' : 'SCORED', criterion_id: entry.criterion_id,
      summary: `${entry.basis === 'APPROVED_PROPOSAL' ? 'Approved' : entry.basis === 'OVERRIDE' ? 'Overrode' : 'Scored'} ${entry.criterion_id}${entry.level_id ? ' at level ' + entry.level_id : ''}` });
    return this.put(id, r);
  }
  async clearTeacherDecision(id: string, criterionId: string) {
    const r = this.current(id); delete r.decisions[criterionId];
    r.history.unshift({ at: new Date().toISOString(), kind: 'CLEARED', criterion_id: criterionId, summary: `Returned ${criterionId} to the AI proposal` });
    return this.put(id, r);
  }
  async updateRubric(id: string, rubric: Rubric, invalidated: string[]) {
    const r = this.current(id); r.rubricOverride = rubric; r.pendingReassessment = [...new Set([...r.pendingReassessment, ...invalidated])];
    invalidated.forEach((c) => delete r.decisions[c]);
    r.history.unshift({ at: new Date().toISOString(), kind: 'REWEIGHTED', summary: invalidated.length ? `Rubric updated; ${invalidated.length} criterion(s) need reassessment` : `Weights or marks updated (total ${rubric.total_points}); no agents rerun` });
    return this.put(id, r);
  }
  async reassess(id: string, criterionIds: string[], opts: { retryDeferred: boolean }): Promise<ReassessResult> {
    const review = this.current(id);
    review.history.unshift({ at: new Date().toISOString(), kind: opts.retryDeferred ? 'RETRY_REQUESTED' : 'REASSESSED', summary: `${opts.retryDeferred ? 'Retried' : 'Reassessed'} ${criterionIds.join(', ')} with the rubric model` });
    const r = await this.call<{ review: TeacherReview; messages: string[] }>('POST', `/api/evaluations/${encodeURIComponent(id)}/reassess`,
      { criteria: criterionIds, retry_deferred: opts.retryDeferred, review });
    const saved = { ...emptyReview(), ...r.review };
    this.reviews.set(id, saved);
    return { updated: saved, messages: r.messages, reload: true };
  }
  async resetReview(id: string) { return this.put(id, emptyReview()); }
}
