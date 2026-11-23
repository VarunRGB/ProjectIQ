"""Phase 6 core: deterministic diagnosis from existing reports; no code execution."""
from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any
from .catalog import SOURCE_ISSUES, diagnosis
from .contracts import (CLEAN, EXEC, NOT_ATTESTED, VERSION, MAX_RUNS, MAX_EVENTS,
                        MAX_DIAGNOSES, fail, source_key)
from .evidence import (FAIL_STATES, INCONCLUSIVE_STATES, NO_RUN_STATES, PASS_STATES,
                       normalize_plan, normalize_quality, normalize_run, normalize_vm)
from .dependencies import read_edges
from .reruns import analyze_reruns
from .hypotheses import derive


def build(*, plans: list[tuple[dict,str]] | None = None,
          run_inputs: list[tuple[dict,str,dict | None]] | None = None,
          vm_standalone: tuple[dict,str] | None = None,
          quality_input: tuple[dict,str] | None = None,
          dependency_input: tuple[dict,str] | None = None,
          receipts: list[dict] | None = None) -> dict:
    plans=plans or [];run_inputs=run_inputs or []
    if len(plans)>1 or len(run_inputs)>MAX_RUNS or not (plans or run_inputs or vm_standalone or quality_input):
        fail('INVALID_INPUT_COMBINATION')
    plan = normalize_plan(plans[0][0]) if plans else None
    vm = normalize_vm(vm_standalone[0]) if vm_standalone else None
    quality = normalize_quality(quality_input[0]) if quality_input else None
    runs=[]
    for i,(data,label,vm_data) in enumerate(run_inputs):
        run_vm = normalize_vm(vm_data) if vm_data is not None else None
        run=normalize_run(data,label=label,vm=run_vm)
        runs.append(run)
    sources=[s for s in ([plan['source']] if plan else [])+
             ([vm['source']] if vm else [])+
             ([quality['source']] if quality else [])+[r['source'] for r in runs]]
    if len(set(sources))!=1:
        fail('CROSS_ARTIFACT_SOURCE_MISMATCH')
    source=sources[0]
    # Explicit source identifiers; never copy arbitrary source metadata or URLs.
    public_source={'kind':source[0],'repository':source[1],'commit_sha':source[2]}
    known_ids=set(plan['cases'] if plan else ())
    for r in runs:
        known_ids.update(row['id'] for row in r['rows'])
        if plan:
            for row in r['rows']:
                if r['stage'] in ('reviewed_vm_functional_testing','reviewed_vm_api_testing') and row['id'] not in plan['cases']:
                    fail('RUN_TEST_ID_ABSENT_FROM_PLAN')
    deps=read_edges(dependency_input[0],expected_source=source,allowed_ids=known_ids) if dependency_input else {}
    diag=[];events=[];flags=[]
    def add(code:str, refs:list[str], ids:list[str]|None=None,confidence:str='MEDIUM',causal:str='UNPROVEN'):
        if len(diag)>=MAX_DIAGNOSES:fail('DIAGNOSIS_OUTPUT_LIMIT')
        d=diagnosis(code,evidence=refs,linked_tests=ids,confidence=confidence,causal_status=causal)
        if d not in diag:diag.append(d)

    security_hold = any(
        (r['cleanup'] is not None and r['cleanup'] not in (CLEAN,'NOT_STARTED'))
        or (r['vm'] is not None and r['vm']['cleanup'] not in (CLEAN,'NOT_STARTED'))
        for r in runs)
    security_hold |= bool(vm and vm['cleanup'] not in (CLEAN,'NOT_STARTED'))
    if security_hold:
        refs=[r['label'] for r in runs if r['cleanup'] not in (CLEAN,'NOT_STARTED')]
        if vm_standalone and vm['cleanup'] not in (CLEAN,'NOT_STARTED'):
            refs.append(vm_standalone[1])
        add('VM_CLEANUP_UNVERIFIED',refs,confidence='HIGH')
        flags.append('CLEANUP_NOT_VERIFIED')
    # Even completed runs are only operator-reviewed pilots, not an attestation
    # that arbitrary student code is safe to execute.
    if vm and not vm['ready']:
        code = (SOURCE_ISSUES.get(vm['reason']) or
                ('VM_LAUNCH_BLOCKED' if vm['execution']=='NOT_PERFORMED' else
                 'STARTUP_NOT_READY' if vm['http_status'] is not None and vm['http_status']!=200 else
                 'VM_EXECUTION_UNVERIFIED'))
        add(code,[vm_standalone[1]],confidence='MEDIUM')
    for r in runs:
        lab=r['label']; refs=[lab]
        if r['vm'] is not None:refs.append('VM:'+lab)
        status=r['status']
        if not r['supported']:
            add('UNSUPPORTED_STAGE',refs,confidence='HIGH')
            continue
        if r['vm'] is not None and not r['vm']['ready'] and not security_hold:
            code=SOURCE_ISSUES.get(r['vm']['reason']) or ('STARTUP_NOT_READY'
                if r['vm']['http_status'] is not None and r['vm']['http_status']!=200
                else 'VM_EXECUTION_UNVERIFIED')
            add(code,refs,confidence='HIGH')
        if status in ('BLOCKED','NO_RUNNABLE_TESTS'):
            add('VM_LAUNCH_BLOCKED',refs,confidence='MEDIUM')
        elif status in NO_RUN_STATES:
            if status=='CLEANUP_FAILED':
                if not security_hold:add('VM_CLEANUP_UNVERIFIED',refs,confidence='HIGH')
            else:
                code=SOURCE_ISSUES.get(r['issue'])
                add(code or 'VM_EXECUTION_UNVERIFIED',refs,confidence='MEDIUM')
        elif status in INCONCLUSIVE_STATES:
            add('TEST_INCONCLUSIVE',refs,confidence='MEDIUM')
        elif not security_hold and status in FAIL_STATES and not any(x['outcome']=='FAIL' for x in r['rows']):
            if r['auth_failed']:
                add('TEST_INCONCLUSIVE',refs,confidence='MEDIUM')
                continue
            code=('DATABASE_POSTCONDITION_FAILURE' if 'postcondition' in r['stage'] else
                  'BROWSER_CHECK_FAILURE' if 'browser' in r['stage'] else 'TEST_ASSERTION_MISMATCH')
            # Even a verified mismatch is not proof the student is responsible.
            add(code,refs,confidence='HIGH' if r['verified'] and not security_hold else 'LOW',
                causal='OBSERVED_MISMATCH_ONLY')
        if r['issue'] in SOURCE_ISSUES and status not in PASS_STATES and not security_hold:
            code=SOURCE_ISSUES[r['issue']]
            if code not in ('VM_CLEANUP_UNVERIFIED',):add(code,refs,confidence='MEDIUM')
        for row in r['rows']:
            if len(events)>=MAX_EVENTS:fail('EVENT_LIMIT')
            tid=row['id']; outcome=row['outcome']; linked=plan['cases'].get(tid) if plan else None
            event={'run':lab,'test_id':tid,'status':outcome,'http_status':row['http_status'],
                   'oracle':'GROUNDED' if linked and linked['grounded'] else
                            'NOT_ESTABLISHED' if linked else 'NOT_SUPPLIED',
                   'vm_verified':r['verified'],'student_defect':'NOT_ESTABLISHED'}
            events.append(event)
            if outcome=='FAIL':
                if r['stage']=='reviewed_vm_database_postconditions' and r['auth_failed']:
                    add('TEST_INCONCLUSIVE',refs,[tid],confidence='MEDIUM')
                    continue
                # Do not double count a generic aggregate failure when per-case evidence exists.
                if linked and not linked['grounded']:
                    add('INVALID_TEST_ORACLE',refs,[tid],confidence='HIGH')
                elif not security_hold:
                    code=('DATABASE_POSTCONDITION_FAILURE' if 'postcondition' in r['stage'] else
                          'BROWSER_CHECK_FAILURE' if 'browser' in r['stage'] else 'TEST_ASSERTION_MISMATCH')
                    add(code,refs,[tid],confidence='HIGH' if (r['verified'] and linked and linked['grounded']) else 'LOW',
                        causal='OBSERVED_MISMATCH_ONLY')
            elif outcome in ('BLOCKED','UNTESTED','INVALID_TEST'):
                add('TEST_BLOCKED',refs,[tid],confidence='HIGH')
            elif outcome=='INCONCLUSIVE':
                add('TEST_INCONCLUSIVE',refs,[tid],confidence='MEDIUM')
    if plan:
        appeared={e['test_id'] for e in events}
        for tid,case in sorted(plan['cases'].items()):
            if tid not in appeared and case['status']=='NEEDS_ORACLE':
                add('INVALID_TEST_ORACLE',[plans[0][1]],[tid],confidence='HIGH')
            if tid not in appeared and case['status']=='UNSUPPORTED_BY_RUNNER':
                add('TEST_BLOCKED',[plans[0][1]],[tid],confidence='HIGH')

    # Dependency edges are explicit, reviewed data. Never transform a real FAIL
    # into BLOCKED; only link rows already reported as untested or blocked.
    per_test=defaultdict(list)
    for event in events:per_test[event['test_id']].append(event)
    dependency_groups=[]
    for dependent, prereqs in sorted(deps.items()):
        drows=per_test.get(dependent,())
        if not drows or not all(x['status'] in ('BLOCKED','UNTESTED','INCONCLUSIVE') for x in drows):
            continue
        failed=[p for p in sorted(prereqs) if any(e['status']=='FAIL' for e in per_test.get(p,()))]
        if failed:
            dependency_groups.append({'blocked_test':dependent,'prerequisite_failures':failed,
                                      'attribution':'DEPENDENCY_LINK_ONLY'})
            add('DEPENDENT_TEST_BLOCKED', [drows[0]['run']],[dependent]+failed,confidence='HIGH')
    if quality:
        if quality['security_scan_partial']:
            add('PHASE5_SECURITY_SCAN_INCOMPLETE',[quality_input[1]],confidence='HIGH')
        for index,alert in enumerate(quality['security_alerts']):
            add('PHASE5_SECRET_INDICATOR',[quality_input[1]],confidence='MEDIUM')
        if quality['quality_finding_count']:
            add('PHASE5_QUALITY_ADVISORY',[quality_input[1]],confidence='LOW')
    if not runs and not vm:
        add('NO_EXECUTION_EVIDENCE', [plans[0][1]] if plans else [quality_input[1]], confidence='HIGH')
    hypotheses=derive(runs,security_hold=security_hold)
    rerun_diag, comparisons=analyze_reruns(runs)
    diag.extend(x for x in rerun_diag if x not in diag)
    if len(diag)>MAX_DIAGNOSES:fail('DIAGNOSIS_OUTPUT_LIMIT')
    # Stable sorting: priority then code then test identities and evidence.
    order={'P0':0,'P1':1,'P2':2,'P3':3}
    diag.sort(key=lambda d:(order[d['priority']],d['code'],d['test_ids'],d['evidence']))
    statuses=Counter(e['status'] for e in events)
    exec_proven=any(r['verified'] for r in runs)
    return {'schema_version':VERSION,'phase':6,'stage':'offline_failure_diagnosis',
            'source':public_source,'status':
            'SECURITY_HOLD' if security_hold else 'PARTIAL' if (any(not r['supported'] for r in runs)
                     or any(not r['verified'] for r in runs) or not runs) else 'DIAGNOSED',
            'execution':'NOT_PERFORMED','vm_launches':0,
            'security_attestation':NOT_ATTESTED,
            'summary':{'runs':len(runs),'vm_verified_runs':sum(r['verified'] for r in runs),
                       'tests_observed':len(events),'passes':statuses['PASS'],
                       'fails':statuses['FAIL'],'inconclusive':statuses['INCONCLUSIVE'],
                       'diagnoses':len(diag),'comparisons':len(comparisons)},
            'evidence_receipts':receipts or [],
            'events':events,'diagnoses':diag,'dependency_groups':dependency_groups,
            'rerun_comparisons':comparisons,'root_cause_hypotheses':hypotheses,
            'static_context':quality['static_locations'] if quality else [],
            'security_context':quality['security_alerts'][:20] if quality else [],
            'limitations':[
                'Evidence-only triage. No student code, tests, browser or VM executed.',
                'A reviewed assertion mismatch does not prove student-code responsibility.',
                'VM evidence and fixed expected values are not an independent security or oracle attestation.',
                'Flakiness candidates require independently validated comparable historical runs.',
                'Static quality and secret indicators are not runtime root-cause proofs.',
            ]}
