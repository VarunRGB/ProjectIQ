import { describe, expect, it } from 'vitest';
import { functionalEvidence, functionalSummary, reviewedDiagnosis } from '../functionalEvidence';
import { reviewedCrud } from '../reviewedCrud';
import fixture from './phase9bd_express.fixture.json';
const fresh = (): any => structuredClone(fixture);
const read = (d: unknown = fresh(), check='MATCHED') => functionalEvidence(d,check);
describe('F2 saved automatic evidence',()=>{
  it('reuses the actual 9BD Express receipt without altering it',()=>{
    const d=fresh(), before=JSON.stringify(d);const f=read(d)!;expect(f.inventoryIssue).toBeNull();
    expect(f.inventory).toMatchObject({passed:3,failed:0,inconclusive:0,notRun:5,attempted:3,covered:7,workflowOnly:4,workflow:{status:'PASS',attempted:9,passed:9,unrecorded:0}});
    expect(f.inventory?.routes).toHaveLength(8);expect(f.vmStarts).toBe(1);expect(JSON.stringify(d)).toBe(before);
    expect(functionalSummary(f)).toContain('7/8');expect(f.inventory?.routes.find(r=>r.method==='GET'&&r.path_template==='/posts/:id/update')?.request_observed).toBe(false);
  });
  it('keeps legacy results out of automatic mode',()=>{expect(read(null)).toBeNull()});
  it('does not display unmatched evidence',()=>{expect(read(fresh(),'RECEIPT_OR_CONTRACT_MISMATCH')?.inventory).toBeNull()});
  it('does not display unknown schema',()=>{const d=fresh();d.schema_version='future';expect(read(d)?.inventory).toBeNull()});
  it('withholds inconsistent totals',()=>{const d=fresh();d.functional_inventory.summary.independent_pass=8;expect(read(d)?.inventory).toBeNull()});
  it('withholds an impossible independent pass',()=>{const d=fresh();d.functional_inventory.cases[0].independent_observation.assertions[0].matched=false;expect(read(d)?.inventory).toBeNull()});
  it('withholds duplicate route identity',()=>{const d=fresh();d.functional_inventory.cases[1].case_id=d.functional_inventory.cases[0].case_id;expect(read(d)?.inventory).toBeNull()});
  it('withholds unsupported approval and count claims',()=>{const d=fresh();d.vm_attempt_count_uncertain=true;expect(read(d)?.inventory).toBeNull();expect(read(d)?.vmStarts).toBeNull()});
  it('keeps workflow-only requests out of independent pass counts',()=>{const f=read()!;expect(f.inventory?.routes.filter(r=>r.request_observed&&r.independent_outcome==='NOT_RUN')).toHaveLength(4)});
  it('shows a stopped workflow with later steps unrecorded',()=>{
    const d=fresh(),i=d.functional_inventory,w=i.workflow;w.steps=w.steps.slice(0,2);Object.assign(w.steps[1],{status:'FAIL',matched:false,http_status:500});w.status='FAIL';w.requests_attempted=2;d.workflow_requests_attempted=2;
    for(const r of i.cases){const related=w.steps.filter((s:any)=>s.method===r.method&&s.path_template===r.path_template);r.workflow_related_steps=related.map((s:any)=>s.role);r.workflow_observations=related;r.request_observed=!!r.independent_observation?.request_attempted||related.some((s:any)=>s.status!=='NOT_RUN')}
    i.summary.unique_cases_with_attempted_request=4;i.summary.cases_without_attempted_request=4;i.summary.workflow_only_request_cases=1;
    expect(read(d)?.inventory?.workflow).toMatchObject({status:'FAIL',passed:1,failed:1,attempted:2,unrecorded:7});
  });
  it('handles smoke separately',()=>{const d=fresh();d.status='SMOKE_EVIDENCE_COMPLETE';d.functional_inventory=null;d.smoke={status:'STARTUP_VERIFIED',http_status:200,cleanup:'VM_STOPPED_DISPOSABLE_STORAGE_REMOVED'};const f=read(d)!;expect(f.inventory).toBeNull();expect(f.smoke?.http_status).toBe(200);expect(functionalSummary(f)).toContain('no functional assertions')});
  it('handles a blocked runtime without made-up totals',()=>{const d=fresh();d.status='STATIC_ONLY_LIMITED';d.vm_start_attempts=0;d.functional_inventory=null;expect(read(d)?.inventory).toBeNull();expect(read(d)?.vmStarts).toBe(0)});
  it('qualifies legacy diagnosis instead of mutating native evidence',()=>{const d={code:'NO_EXECUTION_EVIDENCE',title:'Old title',explanation:'Old explanation'};expect(reviewedDiagnosis(d,read()).title).toBe('No reviewed execution evidence');expect(d.title).toBe('Old title');expect(reviewedDiagnosis(d,null)).toBe(d)});
  it('groups recorded reviewed CRUD metrics without deriving test verdicts',()=>{const g=reviewedCrud({suite:'crud',suite_metrics:{create_http:200,created_db_fields_match:true,deleted_db_count:0}});expect(g.map(x=>x.label)).toEqual(['Create','Delete and verify removal']);expect(g[1].metrics[0].value).toBe(0);expect(reviewedCrud({suite:'api',create_http:200})).toEqual([])});
});

describe('independent-only inventory',()=>{
  it('does not invent a workflow or turn deferred routes into failures',()=>{
    const d=fresh(),i=d.functional_inventory;d.selected_path='INDEPENDENT_HTTP';i.workflow=null;d.workflow_requests_attempted=0;
    for(const r of i.cases){r.workflow_related_steps=[];r.workflow_observations=[];r.request_observed=!!r.independent_observation?.request_attempted}
    Object.assign(i.summary,{unique_cases_with_attempted_request:3,cases_without_attempted_request:5,workflow_only_request_cases:0});
    const f=read(d)!;expect(f.inventory).toMatchObject({passed:3,failed:0,notRun:5,attempted:3,covered:3,workflowOnly:0,workflow:null});
    expect(functionalSummary(f)).not.toContain('workflow requests passed');
  });
});
