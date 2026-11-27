"""9B-D: local trusted orchestration, preserving the original STATIC contract.

Demo observations are a separate extension, never Phase 3 reviewed test IDs or
rubric credit. One selected component, at most one application VM, no retries.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from ..orchestration import evaluate
from ..orchestration.contracts import validate_request, validate_source, source_matches_url
from ..orchestration.report import esc, table
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file, CLEAN
from ..phase8b.planner import load_object
from ..phase8d.dispatch import fresh
from ..phase8f.demo import run_demo
from ..phase8c_final.dispatch import write
from ..phase6.catalog import diagnosis
from ..phase8e.audit import classify_error
from ..phase9a import planner
from .compiler import _compile_rederived, read_object, verify_checksum, require, SHA
from .execution import run_functional
from .adaptive_compiler import compile_workflow


@dataclass(frozen=True)
class DemoOptions:
    vm_config: Path
    trust_demo_source: bool = False
    confirm_disposable_state: bool = False
    component: str | None = None
    require_bounded_volume: bool = False

    def __post_init__(self):
        require(self.trust_demo_source is True,'EXPLICIT_DEMO_TRUST_REQUIRED')
        require(type(self.confirm_disposable_state) is bool and type(self.require_bounded_volume) is bool,'DEMO_OPTION_INVALID')
        require(isinstance(self.vm_config,Path),'OPERATOR_VM_CONFIG_REQUIRED')
        require(self.component is None or isinstance(self.component,str) and 0<len(self.component)<=200,'DEMO_COMPONENT_INVALID')


def add_demo_arguments(parser):
    parser.add_argument('--trust-demo-source',action='store_true',help='Local operator trust only; never enable for student input')
    parser.add_argument('--demo-vm-config',type=Path,help='Operator-owned local VM config; requires explicit trust')
    parser.add_argument('--confirm-disposable-state',action='store_true',help='Allow supported adaptive mutation workflow')
    parser.add_argument('--demo-component',help='Select component ID or root if multiple components')
    parser.add_argument('--demo-require-bounded-volume',action='store_true')


def demo_options(args):
    values=(getattr(args,'demo_vm_config',None),getattr(args,'trust_demo_source',False),getattr(args,'confirm_disposable_state',False),getattr(args,'demo_component',None),getattr(args,'demo_require_bounded_volume',False))
    if not any(values):return None
    return DemoOptions(*values)


def _receipt(path, data, *, limit=8_000_000, field=None):
    info=checked_file(path,limit)
    saved=load_object(Path(path).read_text('utf-8-sig'))
    require(saved==data and checked_file(path,limit)==info,'UNIFIED_RECEIPT_MISMATCH')
    if field:verify_checksum(saved,field)
    return info


def _error(exc):
    code=str(exc)
    return code if isinstance(exc,ValueError) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',code) else 'STAGE_'+type(exc).__name__.upper()[:40]


def select_runtime(runtime, options):
    rows=runtime['components']
    if options.component is not None:
        rows=[r for r in rows if options.component in (r['id'],r['root'])]
        require(len(rows)==1,'COMPONENT_SELECTION_NOT_UNIQUE')
    else:
        require(len(rows)==1,'EXPLICIT_MULTI_COMPONENT_SELECTION_REQUIRED' if rows else 'NO_COMPONENT_SELECTED')
    row=rows[0]
    require(row['status']=='REVIEWED_CANDIDATE' and not row['blockers'] and not runtime['snapshot_inspection']['issues'],'SELECTED_COMPONENT_BLOCKED')
    return row


def _prepare(url,source,out,options):
    # Reuse 9A and 9BA; this compiler receives a freshly generated plan, not a
    # caller-supplied saved plan. The dispatcher rederives it again before VM launch.
    dest=out/'planning'
    proposal=planner.plan_repository(url,source['commit_sha'],dest)
    runtime=read_object(dest/'runtime_plan.json')
    require(proposal['source']==runtime['source']==source,'UNIFIED_SOURCE_MISMATCH')
    compiled=_compile_rederived(proposal,runtime);write(dest/'compiled_plan.json',compiled)
    return proposal,runtime,compiled


def _adaptive(url,source,proposal,row):
    with planner.adapters.pinned_checkout(url,source['commit_sha']) as (actual,root):
        require(actual==source,'UNIFIED_SOURCE_MISMATCH')
        return compile_workflow(root,proposal,row)


def _diag(code,refs,ids=()):
    # Reuse Phase 6's fixed classification/action vocabulary; clarify the
    # non-reviewed nature of automatic observations, without inventing causes.
    d=diagnosis(code,evidence=refs,linked_tests=list(ids),confidence='MEDIUM',causal_status='OBSERVED_MISMATCH_ONLY' if code=='TEST_ASSERTION_MISMATCH' else 'UNPROVEN')
    if code=='TEST_ASSERTION_MISMATCH':
        d['explanation']='An automatic characterization observation did not match its proposed assertion or state expectation.'
        d['recommended_action']='Review the source-derived expectation and observed behavior before attributing a defect.'
    return d


def inventory(proposal,compiled,evidence=None,contract=None):
    """Separate Phase 9 case inventory; no fake Phase 3 ID mapping or grade.

    Request coverage counts unique method/path/component identities, not workflow
    steps. A workflow request is not an independently verified route oracle.
    """
    evidence=evidence or {};cid=evidence.get('component_id')
    by_id={r['case_id']:r for r in evidence.get('cases',[])}
    require(len(by_id)==len(evidence.get('cases',[])),'UNIFIED_CASE_DUPLICATE')
    group={t['case_id']:t for g in compiled['components'] for t in g['tests'] if g['component_id']==cid}
    require(set(by_id)<=set(group),'UNIFIED_CASE_MISMATCH')
    if evidence:
        from .http_runner import validate_results
        suite={'compiled_sha256':compiled['compiled_sha256'],'tests':[{k:t[k] for k in ('case_id','method','path','assertions')} for t in group.values()]}
        observations={'compiled_sha256':compiled['compiled_sha256'],'cases':evidence['cases']}
        if contract is not None:suite['workflow']=contract;observations['workflow']=evidence['workflow']
        validate_results(observations,suite)
    deferred={r['case_id']:r['reasons'] for r in compiled['deferred_cases']}
    wf=evidence.get('workflow');links={};steps=[]
    if wf is not None:
        from .adaptive_runner import validate_report
        require(contract is not None,'UNIFIED_WORKFLOW_CONTRACT_MISSING');validate_report(wf,contract)
        paths={'BASELINE':('GET',contract['list_path']),'CREATE':('POST',contract['create_path']),
               'LIST_CREATED':('GET',contract['list_path']),'READ_CREATED':('GET',contract['read_template']),
               'UPDATE':(contract['update_method'],contract['update_template']),'READ_UPDATED':('GET',contract['read_template']),
               'VERIFY_UPDATED_LIST':('GET',contract['list_path']),'DELETE':('DELETE',contract['delete_template']),
               'VERIFY_REMOVED':('GET',contract['list_path'])}
        for s in wf['steps']:
            method,path=paths[s['role']];step={**s,'method':method,'path_template':path}
            steps.append(step);links.setdefault((cid,method,path),[]).append(step)
    rows=[];diags=[]
    for c in proposal['cases']:
        tid=c['id'];obs=by_id.get(tid);related=links.get((c['component_id'],c['method'],c['path_template']),[])
        attempted=bool(obs and obs['request_attempted']) or any(s['status']!='NOT_RUN' for s in related)
        outcome=obs['status'] if obs else 'NOT_RUN'
        reason=None if obs else 'NOT_SELECTED_OR_NOT_EXECUTED' if tid not in deferred else 'DEFERRED_INDEPENDENT_CASE'
        row={'case_id':tid,'component_id':c['component_id'],'method':c['method'],'path_template':c['path_template'],
             'oracle_basis':c['oracle_basis'],'assertion_kinds':[a['kind'] for a in c['assertions']],
             'source_evidence':c['evidence'],'independent_outcome':outcome,'independent_reason':reason,
             'independent_observation':obs,'workflow_related_steps':[s['role'] for s in related],
             'workflow_observations':related,'request_observed':attempted,'deferred_reasons':deferred.get(tid,[]),
             'grade_credit':None}
        rows.append(row)
        if obs and outcome in ('FAIL','INCONCLUSIVE'):
            diags.append(_diag('TEST_ASSERTION_MISMATCH' if outcome=='FAIL' else 'TEST_INCONCLUSIVE',['runtime/functional_result.json'],[tid]))
    if wf and wf['status'] in ('FAIL','INCONCLUSIVE'):
        diags.append(_diag('TEST_ASSERTION_MISMATCH' if wf['status']=='FAIL' else 'TEST_INCONCLUSIVE',['runtime/functional_result.json'],[wf['workflow_id']]))
    counts=Counter(r['independent_outcome'] for r in rows)
    return {'cases':rows,'summary':{'discovered_cases':len(rows),'independent_pass':counts['PASS'],'independent_fail':counts['FAIL'],
            'independent_inconclusive':counts['INCONCLUSIVE'],'independent_not_run':counts['NOT_RUN'],
            'unique_cases_with_attempted_request':sum(r['request_observed'] for r in rows),
            'cases_without_attempted_request':sum(not r['request_observed'] for r in rows),
            'workflow_only_request_cases':sum(r['request_observed'] and not (r['independent_observation'] or {}).get('request_attempted',False) for r in rows)},
            'workflow_proposals':[{'workflow_id':w['id'],'component_id':w['component_id'],'status':'OBSERVED_WORKFLOW' if wf and w['id']==wf['workflow_id'] else 'DEFERRED_NOT_EXECUTED','binding_requirements':w['binding_requirements']} for w in proposal['workflows']],
            'workflow':None if wf is None else {'workflow_id':wf['workflow_id'],'status':wf['status'],'requests_attempted':wf['requests_attempted'],'steps':steps},
            'diagnostics':diags,'scope':'PHASE9_CHARACTERIZATION_NOT_PHASE3_REVIEWED_CREDIT'}


def _report_section(d):
    lines=['','## Trusted local demonstration — Phase 8/9','',
           'This section is separate from the STATIC / reviewed Phase 3 matrix above. It does not award marks.',
           '',f"- Mode: {esc(d['mode'])}",f"- Runtime status: {esc(d['status'])}",f"- Selected path: {esc(d.get('selected_path'))}",
           f"- Application VM attempts: {esc(d['vm_start_attempts'])} (uncertain: {esc(d['vm_attempt_count_uncertain'])})",f"- Runtime error: {esc(d.get('error_code'))}",
           f"- Temporary approval cleanup: {esc(d.get('temporary_approval_cleanup'))}",f"- Original config unchanged: {esc(d.get('original_config_unchanged'))}",'']
    inv=d.get('functional_inventory') or {};s=inv.get('summary',{})
    for key,value in s.items():lines.append('- '+esc(key.replace('_',' '))+': '+esc(value))
    lines+=['','### Functional case observations','']
    table(lines,('Method','Path','Independent outcome','Workflow-related observations','Request attempted'),[(r['method'],r['path_template'],r['independent_outcome'],', '.join(r['workflow_related_steps']),r['request_observed']) for r in inv.get('cases',[])])
    wf=inv.get('workflow')
    if wf:table(lines,('Workflow step','Outcome','HTTP'),[(r['role'],r['status'],r['http_status']) for r in wf['steps']])
    lines+=['### Demo diagnosis','']
    for row in d.get('diagnostics',[]):lines.append('- '+esc(row['code'])+': '+esc(row['recommended_action']))
    lines+=['','### Limitations','']+['- '+esc(x) for x in d['limitations']]
    return '\n'.join(lines)+'\n'


def evaluate_project(repository_url, ref, output_dir, *, submission_id, options:DemoOptions):
    """Explicit operator-only entry point. Never pass student-controlled options.

    Existing STATIC report is preserved verbatim inside static/. Additive
    trusted_demo field is for display/review only; no new reviewed suites.
    """
    require(type(options) is DemoOptions and options.trust_demo_source is True,'EXPLICIT_DEMO_TRUST_REQUIRED')
    req={'submission_id':submission_id,'repository_url':repository_url,'ref':ref,'mode':'STATIC','max_vm_launches':0,'run_readiness':'NO'}
    validate_request(req);out=fresh(output_dir)
    static=evaluate(req,output_dir=out/'static')
    sr=_receipt(out/'static'/'executioner_result.json',static,limit=64_000_000)
    require(static.get('stage')=='executioner_evaluation' and static.get('submission_id')==submission_id and static.get('effective_mode')=='STATIC' and static.get('vm_attempts_scheduled')==0,'UNIFIED_STATIC_MISMATCH')
    result=dict(static)
    d={'schema_version':'9BD.1','mode':'OPERATOR_TRUSTED_LOCAL_DEMO','status':'NOT_RUN','source':static.get('source'),'selected_path':None,'selected_component':None,'vm_attempt_budget':1,'vm_start_attempts':0,'vm_attempt_count_uncertain':False,'functional_tests_executed':0,'workflow_requests_attempted':0,'temporary_approval_cleanup':'NOT_CREATED','original_config_unchanged':None,'automatic_untrusted_execution':False,'runtime_permission':False,'security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','grade_credit':None,'static_report':{'path':'static/executioner_result.json',**sr},'runtime_report':None,'planning_reports':{},'functional_inventory':None,'diagnostics':[],'limitations':['Demo evidence is source-derived characterization, not independently reviewed grading evidence. The original STATIC matrix and rubric policy remain unchanged.','At most one selected component and one VM. No automatic retry or smoke rerun after a functional attempt.','Operator trust is not security attestation. Only local trusted code; mutation requires separate disposable-state confirmation.']}
    proposal=compiled=contract=None;runtime_started=False
    try:
        require(static.get('status') in ('COMPLETED','COMPLETED_WITH_LIMITATIONS','NEEDS_REVIEW'),'STATIC_EVALUATION_UNAVAILABLE')
        source=validate_source(static['source']);require(source_matches_url(source,repository_url) and (not ref or not SHA.fullmatch(ref) or source['commit_sha']==ref.lower()),'UNIFIED_SOURCE_MISMATCH')
        proposal,runtime,compiled=_prepare(repository_url,source,out,options)
        for name in ('functional_plan.json','runtime_plan.json','compiled_plan.json','phase3_test_plan.json'):
            path='planning/'+name;d['planning_reports'][path]=checked_file(out/path,8_000_000)
        d['functional_inventory']=inventory(proposal,compiled)
        row=select_runtime(runtime,options);d['selected_component']={'id':row['id'],'root':row['root'],'adapter':row['adapter']}
        groups=[g for g in compiled['components'] if g['component_id']==row['id']]
        adaptive=False
        if groups and options.confirm_disposable_state and any(w['component_id']==row['id'] for w in proposal['workflows']):
            try:contract=_adaptive(repository_url,source,proposal,row);adaptive=True
            except ValueError as exc:
                if str(exc)!='ADAPTIVE_SOURCE_PROFILE_UNSUPPORTED':raise
                d['limitations'].append('Adaptive source profile unsupported; only independent GETs selected.')
        elif groups and any(w['component_id']==row['id'] for w in proposal['workflows']):d['limitations'].append('Adaptive workflow deferred: disposable state not explicitly confirmed.')
        d['selected_path']='ADAPTIVE' if adaptive else 'INDEPENDENT_HTTP' if groups else 'SMOKE_ONLY'
        runtime_started=True;d['vm_attempt_count_uncertain']=True
        if groups:
            native=run_functional(repository_url,source['commit_sha'],out/'planning'/'compiled_plan.json',out/'runtime',trust_demo_source=True,vm_config=options.vm_config,component=row['id'],require_bounded_volume=options.require_bounded_volume,adaptive=adaptive,confirm_disposable_state=options.confirm_disposable_state)
            name='functional_result.json';success='FUNCTIONAL_EVIDENCE_COMPLETE'
        else:
            native=run_demo(repository_url,source['commit_sha'],out/'runtime',trust_demo_source=True,vm_config=options.vm_config,component=row['id'],max_vms=1,require_bounded_volume=options.require_bounded_volume)
            name='demo_result.json';success='DEMO_REVIEWED_SMOKE_COMPLETE'
        info=_receipt(out/'runtime'/name,native,field='result_sha256');d['runtime_report']={'path':'runtime/'+name,**info,'status':native['status']}
        require(native.get('source')==source and native.get('automatic_untrusted_execution') is False and native.get('runtime_permission') is False,'UNIFIED_RUNTIME_SOURCE_OR_SCOPE_MISMATCH')
        attempts=native['vm_start_attempts'];require(type(attempts) is int and 0<=attempts<=1 and type(native['vm_attempt_count_uncertain']) is bool,'UNIFIED_VM_COUNT_INVALID')
        d.update(vm_start_attempts=attempts,vm_attempt_count_uncertain=native['vm_attempt_count_uncertain'],temporary_approval_cleanup=native['temporary_approval_cleanup'],original_config_unchanged=native['original_config_unchanged'])
        if native['status']!=success:
            d['error_code']=native.get('dispatch_error_code') or native.get('error_code') or 'RUNTIME_EVIDENCE_UNAVAILABLE'
            d['status']='RUNTIME_INCOMPLETE';raise ValueError('UNIFIED_RUNTIME_INCOMPLETE')
        require(attempts==1 and not d['vm_attempt_count_uncertain'] and d['temporary_approval_cleanup']=='REMOVED' and d['original_config_unchanged'] is True,'UNIFIED_RUNTIME_CLEANUP_UNVERIFIED')
        if groups:
            require(native['compiled_sha256']==compiled['compiled_sha256'] and native['evidence']['component_id']==row['id'] and native['evidence']['cleanup']==CLEAN,'UNIFIED_FUNCTIONAL_LINK_MISMATCH')
            ev=native['evidence'];require(set(t['case_id'] for t in groups[0]['tests'])==set(r['case_id'] for r in ev['cases']),'UNIFIED_CASE_MISMATCH')
            require(native.get('functional_count_uncertain') is False and (not adaptive or native.get('workflow_count_uncertain') is False),'UNIFIED_FUNCTIONAL_COUNT_UNCERTAIN')
            inv=inventory(proposal,compiled,ev,contract)
            if adaptive:require(native['workflow_requests_attempted']==ev['workflow']['requests_attempted'] and native['workflow_status']==ev['workflow']['status'],'UNIFIED_WORKFLOW_COUNT_MISMATCH')
            else:require('workflow' not in ev,'UNEXPECTED_ADAPTIVE_CONTRACT')
            require(native['functional_tests_executed']==sum(r['request_attempted'] for r in ev['cases']),'UNIFIED_FUNCTIONAL_COUNT_MISMATCH')
            d.update(status='FUNCTIONAL_EVIDENCE_COMPLETE',functional_tests_executed=native['functional_tests_executed'],workflow_requests_attempted=native.get('workflow_requests_attempted',0),functional_inventory=inv,diagnostics=inv['diagnostics'])
        else:
            records=native['components'];require(len(records)==1 and records[0]['id']==row['id'] and records[0]['evidence']['cleanup']==CLEAN,'UNIFIED_SMOKE_LINK_MISMATCH')
            d.update(status='SMOKE_EVIDENCE_COMPLETE',smoke={'status':records[0]['evidence']['status'],'http_status':records[0]['evidence'].get('http_status'),'cli_exit_code':records[0]['evidence'].get('cli_exit_code'),'cleanup':CLEAN})
    except (Exception,KeyboardInterrupt) as exc:
        d.setdefault('error_code',_error(exc))
        if runtime_started:
            # If a VM may have launched and no verified successful cleanup exists,
            # halt future work; never silently fall back to another VM.
            hold=d['vm_attempt_count_uncertain'] or d['vm_start_attempts']>0 or d['temporary_approval_cleanup']=='UNVERIFIED' or d['original_config_unchanged'] is False
            d['status']='SECURITY_HOLD' if hold else 'RUNTIME_INCOMPLETE'
            code='VM_CLEANUP_UNVERIFIED' if hold else 'VM_LAUNCH_BLOCKED'
            if not hold and classify_error(d['error_code'])=='STARTUP_OR_SMOKE':code='STARTUP_NOT_READY'
            d['diagnostics'].append(_diag(code,['runtime/'+('functional_result.json' if d['selected_path']!='SMOKE_ONLY' else 'demo_result.json')]))
        else:
            d['status']='STATIC_ONLY_LIMITED';d['diagnostics'].append(_diag('TEST_BLOCKED',['planning/functional_plan.json'] if proposal else ['static/executioner_result.json']))
        if isinstance(exc,KeyboardInterrupt):d['limitations'].append('Operator interrupted demo; no automatic retries.')
    d['demo_sha256']=digest(d)
    result['trusted_demo']=d
    # Keep STATIC as effective mode of the original report, so the rubric does
    # not misinterpret these observations as reviewed test results.
    result['limitations']=[*static.get('limitations',[]),'Trusted demo observations are separately scoped and not automatic rubric credit.']
    if d['status']=='SECURITY_HOLD':result['status']='SECURITY_HOLD'
    elif static['status'] not in ('FAILED','SECURITY_HOLD'):result['status']='COMPLETED_WITH_LIMITATIONS'
    result['artifacts']={'static/'+k:v for k,v in static.get('artifacts',{}).items()}
    result['artifacts']['static/executioner_result.json']=sr
    result['artifacts'].update(d['planning_reports'])
    if d['runtime_report']:
        r=d['runtime_report'];result['artifacts'][r['path']]={k:r[k] for k in ('sha256','bytes')}
    # Original report's relative evidence paths stay relative to static/.
    original=(out/'static'/'executioner_report.md').read_text('utf-8')
    result['report_markdown']='executioner_report.md'
    (out/'executioner_report.md').write_text(original+'\n*The preceding static report evidence paths are relative to `static/`.*\n'+_report_section(d),encoding='utf-8')
    write(out/'executioner_result.json',result)
    return result


def validate_extension(raw):
    """Check additive display data, not runtime authorization or independent provenance."""
    d=raw.get('trusted_demo')
    if d is None:return None
    verify_checksum(d,'demo_sha256')
    require(raw.get('effective_mode')=='STATIC' and raw.get('mode')=='STATIC' and raw.get('vm_attempts_scheduled')==0 and not raw.get('suites') and d.get('schema_version')=='9BD.1' and d.get('mode')=='OPERATOR_TRUSTED_LOCAL_DEMO' and d.get('source')==raw.get('source') and d.get('automatic_untrusted_execution') is False and d.get('runtime_permission') is False and d.get('grade_credit') is None and d.get('security_attestation')=='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','UNIFIED_EXTENSION_SCOPE_MISMATCH')
    require(d.get('status') in ('STATIC_ONLY_LIMITED','SECURITY_HOLD','RUNTIME_INCOMPLETE','FUNCTIONAL_EVIDENCE_COMPLETE','SMOKE_EVIDENCE_COMPLETE') and type(d.get('vm_start_attempts')) is int and 0<=d['vm_start_attempts']<=1 and type(d.get('vm_attempt_count_uncertain')) is bool,'UNIFIED_EXTENSION_INVALID')
    if d['status'] in ('FUNCTIONAL_EVIDENCE_COMPLETE','SMOKE_EVIDENCE_COMPLETE'):
        require(d['vm_start_attempts']==1 and d['vm_attempt_count_uncertain'] is False and d['temporary_approval_cleanup']=='REMOVED' and d['original_config_unchanged'] is True,'UNIFIED_EXTENSION_CLEANUP_INVALID')
    return d
