/** Read-only projection of the Phase 9BD extension. No grade or oracle generation. */
export type Outcome = 'PASS' | 'FAIL' | 'INCONCLUSIVE' | 'NOT_RUN';
export interface Assertion { kind: string; matched: boolean | null; error_code: string | null }
export interface Observation { case_id: string; status: Outcome; request_attempted: boolean; http_status: number | null; assertions: Assertion[]; error_code: string | null }
export interface Step { role: string; method: string; path_template: string; status: Outcome; http_status: number | null; matched: boolean | null; error_code: string | null }
export interface Route { case_id: string; component_id: string; method: string; path_template: string; oracle_basis: string | null; assertion_kinds: string[]; source_evidence: {path: string; line?: number; reason?: string}[]; independent_outcome: Outcome; independent_reason: string | null; independent_observation: Observation | null; workflow_related_steps: string[]; request_observed: boolean; deferred_reasons: string[] }
export interface Diagnostic { code?: string; title: string; explanation: string; recommended_action?: string }
export interface FunctionalEvidence { status: string; path: string | null; component: string | null; adapter: string | null; vmStarts: number | null; approvalCleanup: string | null; configUnchanged: boolean | null; error: string | null; limitations: string[]; diagnostics: Diagnostic[]; receipt: string | null; inventory: Inventory | null; inventoryIssue: string | null; smoke: {status: string; http_status?: number | null; cli_exit_code?: number | null; cleanup?: string} | null }
export interface Inventory { routes: Route[]; passed: number; failed: number; inconclusive: number; notRun: number; attempted: number; covered: number; workflowOnly: number; workflow: {status: Outcome; steps: Step[]; attempted: number; passed: number; failed: number; inconclusive: number; unrecorded: number} | null }
const obj = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === 'string';
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const nullableString = (v: unknown): v is string | null => v === null || str(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(str);
const outcome = (v: unknown): v is Outcome => typeof v === 'string' && ['PASS','FAIL','INCONCLUSIVE','NOT_RUN'].includes(v);
const http = (v: unknown) => v === null || (count(v) && v >= 100 && v <= 599);
const match = (v: unknown) => v === null || typeof v === 'boolean';
const ROLES = ['BASELINE','CREATE','LIST_CREATED','READ_CREATED','UPDATE','READ_UPDATED','VERIFY_UPDATED_LIST','DELETE','VERIFY_REMOVED'];
const diagnostic = (v: unknown): v is Diagnostic => obj(v) && str(v.title) && str(v.explanation) && (v.code === undefined || str(v.code)) && (v.recommended_action === undefined || str(v.recommended_action));
const observation = (o: unknown): o is Observation => obj(o) && str(o.case_id) && outcome(o.status) && typeof o.request_attempted === 'boolean' && http(o.http_status) && nullableString(o.error_code) && Array.isArray(o.assertions) && o.assertions.every((a: unknown) => obj(a) && str(a.kind) && match(a.matched) && nullableString(a.error_code)) && o.request_attempted === (o.status !== 'NOT_RUN') && (o.status !== 'PASS' || (o.assertions.length > 0 && o.assertions.every((a: Assertion) => a.matched === true)));
const route = (v: unknown): v is Route => obj(v) && str(v.case_id) && str(v.component_id) && str(v.method) && str(v.path_template) && nullableString(v.oracle_basis) && strings(v.assertion_kinds) && Array.isArray(v.source_evidence) && v.source_evidence.every((l: unknown) => obj(l) && str(l.path) && (l.line === undefined || count(l.line))) && outcome(v.independent_outcome) && nullableString(v.independent_reason) && (v.independent_observation === null ? v.independent_outcome === 'NOT_RUN' : observation(v.independent_observation) && v.independent_observation.status === v.independent_outcome && v.independent_observation.case_id === v.case_id) && strings(v.workflow_related_steps) && typeof v.request_observed === 'boolean' && strings(v.deferred_reasons) && v.grade_credit === null;
const step = (v: unknown): v is Step => obj(v) && str(v.role) && str(v.method) && str(v.path_template) && outcome(v.status) && http(v.http_status) && match(v.matched) && nullableString(v.error_code) && (v.status === 'PASS' ? v.matched === true : v.status === 'FAIL' ? v.matched === false : v.matched === null);
function inventory(v: unknown, d: Record<string, any>): Inventory | null {
  if (!obj(v) || v.scope !== 'PHASE9_CHARACTERIZATION_NOT_PHASE3_REVIEWED_CREDIT' || !Array.isArray(v.cases) || !v.cases.every(route) || !obj(v.summary)) return null;
  const routes: Route[] = v.cases;
  if (new Set(routes.map(r=>r.case_id)).size !== routes.length || new Set(routes.map(r=>JSON.stringify([r.component_id,r.method,r.path_template]))).size !== routes.length) return null;
  const passed = routes.filter(r=>r.independent_outcome==='PASS').length, failed = routes.filter(r=>r.independent_outcome==='FAIL').length, inconclusive = routes.filter(r=>r.independent_outcome==='INCONCLUSIVE').length;
  const notRun = routes.length-passed-failed-inconclusive;
  const attempted = routes.filter(r=>r.independent_observation?.request_attempted).length;
  let workflow: Inventory['workflow'] = null;
  if (v.workflow !== null) {
    const w = v.workflow;
    if (!obj(w) || !outcome(w.status) || !Array.isArray(w.steps) || !w.steps.every(step) || !w.steps.length || w.steps.length>ROLES.length || !w.steps.every((s: Step,i: number)=>s.role===ROLES[i] && (i===w.steps.length-1 || s.status==='PASS')) || w.status!==w.steps[w.steps.length-1].status || (w.status==='PASS' && w.steps.length!==ROLES.length)) return null;
    const steps: Step[]=w.steps;
    const n=steps.filter(s=>s.status!=='NOT_RUN').length;
    if (n!==w.requests_attempted) return null;
    workflow={status:w.status,steps,attempted:n,passed:steps.filter(s=>s.status==='PASS').length,failed:steps.filter(s=>s.status==='FAIL').length,inconclusive:steps.filter(s=>s.status==='INCONCLUSIVE').length,unrecorded:ROLES.length-steps.length};
  }
  for (const r of routes) {
    const related = (workflow?.steps ?? []).filter(s=>r.component_id===d.selected_component?.id && s.method===r.method && s.path_template===r.path_template);
    if (JSON.stringify(r.workflow_related_steps)!==JSON.stringify(related.map(s=>s.role))) return null;
    if (r.request_observed!== (!!r.independent_observation?.request_attempted || related.some(s=>s.status!=='NOT_RUN'))) return null;
  }
  const covered=routes.filter(r=>r.request_observed).length, workflowOnly=routes.filter(r=>r.request_observed&&!r.independent_observation?.request_attempted).length;
  const summary={discovered_cases:routes.length,independent_pass:passed,independent_fail:failed,independent_inconclusive:inconclusive,independent_not_run:notRun,unique_cases_with_attempted_request:covered,cases_without_attempted_request:routes.length-covered,workflow_only_request_cases:workflowOnly};
  if (Object.entries(summary).some(([k,n])=>v.summary[k]!==n) || d.functional_tests_executed!==attempted || d.workflow_requests_attempted!==(workflow?.attempted??0)) return null;
  return {routes,passed,failed,inconclusive,notRun,attempted,covered,workflowOnly,workflow};
}
export function functionalEvidence(raw: unknown, evidenceCheck: string): FunctionalEvidence | null {
  if (raw == null) return null;
  const d=obj(raw)?raw:{};
  const valid=d.schema_version==='9BD.1' && d.mode==='OPERATOR_TRUSTED_LOCAL_DEMO' && d.grade_credit===null && d.automatic_untrusted_execution===false && d.runtime_permission===false && evidenceCheck==='MATCHED';
  const cleanup=d.vm_start_attempts===1 && d.vm_attempt_count_uncertain===false && d.temporary_approval_cleanup==='REMOVED' && d.original_config_unchanged===true;
  const complete=valid && cleanup && d.status==='FUNCTIONAL_EVIDENCE_COMPLETE';
  const inv=complete?inventory(d.functional_inventory,d):null;
  const s=d.smoke;
  const smoke=valid && cleanup && d.status==='SMOKE_EVIDENCE_COMPLETE' && obj(s) && str(s.status) && s.cleanup==='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' && (s.http_status===undefined||http(s.http_status)) && (s.cli_exit_code===undefined||s.cli_exit_code===null||Number.isInteger(s.cli_exit_code)) ? s as FunctionalEvidence['smoke'] : null;
  return {status:valid&&str(d.status)?d.status:'EVIDENCE_UNAVAILABLE',path:valid&&str(d.selected_path)?d.selected_path:null,component:valid&&str(d.selected_component?.root)?d.selected_component.root:null,adapter:valid&&str(d.selected_component?.adapter)?d.selected_component.adapter:null,vmStarts:valid&&d.vm_attempt_count_uncertain===false&&count(d.vm_start_attempts)&&d.vm_start_attempts<=1?d.vm_start_attempts:null,approvalCleanup:valid&&str(d.temporary_approval_cleanup)?d.temporary_approval_cleanup:null,configUnchanged:valid&&typeof d.original_config_unchanged==='boolean'?d.original_config_unchanged:null,error:valid&&str(d.error_code)?d.error_code:null,limitations:valid&&strings(d.limitations)?d.limitations:[],diagnostics:valid&&Array.isArray(d.diagnostics)?d.diagnostics.filter(diagnostic):[],receipt:valid&&str(d.runtime_report?.sha256)?d.runtime_report.sha256:null,inventory:inv,inventoryIssue:!valid?'Automatic evidence is not receipt-matched or uses an unsupported schema.':d.status==='FUNCTIONAL_EVIDENCE_COMPLETE'&&!inv?'Functional inventory is missing or inconsistent; counts withheld.':null,smoke};
}
export function functionalSummary(f: FunctionalEvidence): string {
  const i=f.inventory;
  if (!i) return f.inventoryIssue ?? (f.smoke?'Startup/smoke evidence recorded; no functional assertions established.':'No complete automatic functional inventory is available.');
  return `${i.attempted?`${i.passed}/${i.attempted} independent checks passed (${i.failed} failed, ${i.inconclusive} inconclusive)`:'No independent checks attempted'}; ${i.routes.length?`${i.covered}/${i.routes.length} discovered route cases exercised`:'no discovered route cases'}${i.workflow?i.workflow.attempted?`; ${i.workflow.passed}/${i.workflow.attempted} workflow requests passed`:'; no workflow requests attempted':''}. Source-derived observations only; no automatic reviewed grading credit.`;
}
export function reviewedDiagnosis<T extends Diagnostic>(d:T, f:FunctionalEvidence|null):T {
  if (!f) return d;
  if (d.code==='NO_EXECUTION_EVIDENCE') return {...d,title:'No reviewed execution evidence',explanation:'The legacy static/reviewed matrix has no verified execution. Automatic observations are reported separately and are not reviewed grading evidence.',recommended_action:'Review the automatic evidence or supply eligible reviewed execution evidence.'};
  if (d.code==='ORACLE_NOT_ESTABLISHED') return {...d,title:'Reviewed expectations not established',explanation:'Some legacy planned tests still lack reviewed expectations. This does not describe the separate automatic inventory.'};
  return d;
}
export const STEP_LABEL: Record<string,string>={BASELINE:'Establish baseline',CREATE:'Create record',LIST_CREATED:'Locate created record',READ_CREATED:'Read created record',UPDATE:'Update record',READ_UPDATED:'Read updated record',VERIFY_UPDATED_LIST:'Verify updated list',DELETE:'Delete record',VERIFY_REMOVED:'Verify removal'};
export const STEP_EXPECTATION: Record<string,string>={BASELINE:'HTTP 200; synthetic marker absent',CREATE:'Redirect (302/303) to list',LIST_CREATED:'HTTP 200; exactly one new matching record',READ_CREATED:'HTTP 200; bound identity and created value match',UPDATE:'Redirect (302/303) to list',READ_UPDATED:'HTTP 200; bound identity and updated value match',VERIFY_UPDATED_LIST:'HTTP 200; list equals baseline plus updated record',DELETE:'Redirect (302/303) to list',VERIFY_REMOVED:'HTTP 200; original list restored'};
