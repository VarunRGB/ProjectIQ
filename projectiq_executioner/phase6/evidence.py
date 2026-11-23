"""Normalize existing Phase 2–5 JSON into bounded typed observations.

A valid JSON file is NOT proof that a self-reported operation really occurred.
Independent VM evidence improves provenance but doesn't attest arbitrary student isolation.
"""
from __future__ import annotations

from collections import Counter
from typing import Any
from .contracts import (EvidenceError, CLEAN, EXEC, NOT_ATTESTED, MAX_TESTS,
                        fail, safe_id, safe_path, source_key, stable_hash)

# Stage-specific, closed status vocabulary. Do not convert unknown statuses to FAIL.
STAGES = {
 'reviewed_vm_functional_testing': ('TESTS_PASSED','TESTS_FAILED','TESTS_INCONCLUSIVE','GUEST_RESULTS_NOT_VERIFIED','VM_EXECUTION_NOT_VERIFIED','CLEANUP_FAILED','BLOCKED','NO_RUNNABLE_TESTS'),
 'reviewed_vm_api_testing': ('TESTS_PASSED','TESTS_FAILED','TESTS_INCONCLUSIVE','GUEST_RESULTS_NOT_VERIFIED','VM_EXECUTION_NOT_VERIFIED','CLEANUP_FAILED','BLOCKED'),
 'reviewed_browser_ui': ('BROWSER_PASSED','BROWSER_FAILED','BROWSER_INCONCLUSIVE','BROWSER_NOT_VERIFIED','BROWSER_VM_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_browser_login': ('LOGIN_PASSED','LOGIN_FAILED','LOGIN_INCONCLUSIVE','LOGIN_VM_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_vm_database_postconditions': ('POSTCONDITIONS_PASSED','POSTCONDITIONS_FAILED','POSTCONDITIONS_INCONCLUSIVE','VM_EXECUTION_NOT_VERIFIED','GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_authenticated_crud': ('CRUD_PASSED','CRUD_FAILED','CRUD_INCONCLUSIVE','CRUD_VM_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_vm_dependent_api_workflow': ('WORKFLOW_PASSED','WORKFLOW_FAILED','WORKFLOW_INCONCLUSIVE','WORKFLOW_VM_NOT_VERIFIED','WORKFLOW_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_database_fixture_seeding': ('FIXTURES_SEEDED','FIXTURES_FAILED','VM_EXECUTION_NOT_VERIFIED','GUEST_RESULTS_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_vm_scenario_isolation': ('SCENARIO_ISOLATION_VERIFIED','ISOLATION_FAILED','ISOLATION_NOT_VERIFIED','CLEANUP_FAILED'),
 'reviewed_cross_repository_functional_pilot': ('CROSS_REPOSITORY_VERIFIED','FUNCTIONAL_FAILURE_DETECTED','CROSS_REPOSITORY_INCONCLUSIVE','CLEANUP_FAILED'),
}
VM_STAGE_PROOFS = {
 'reviewed_vm_functional_testing':'phase4_status',
 'reviewed_vm_api_testing':'phase4c_status',
 'reviewed_browser_ui':'phase4dc_status',
 'reviewed_browser_login':'phase4dd_status',
 'reviewed_vm_database_postconditions':'phase4d3_status',
 'reviewed_authenticated_crud':'phase4db_status',
 'reviewed_vm_dependent_api_workflow':'phase4da_status',
 'reviewed_database_fixture_seeding':'phase4d1_status',
}
PASS_STATES = {'TESTS_PASSED','BROWSER_PASSED','LOGIN_PASSED','POSTCONDITIONS_PASSED',
               'CRUD_PASSED','WORKFLOW_PASSED','FIXTURES_SEEDED','SCENARIO_ISOLATION_VERIFIED',
               'CROSS_REPOSITORY_VERIFIED'}
FAIL_STATES = {'TESTS_FAILED','BROWSER_FAILED','LOGIN_FAILED','POSTCONDITIONS_FAILED',
               'CRUD_FAILED','WORKFLOW_FAILED','FIXTURES_FAILED','ISOLATION_FAILED','FUNCTIONAL_FAILURE_DETECTED'}
INCONCLUSIVE_STATES = {'TESTS_INCONCLUSIVE','BROWSER_INCONCLUSIVE','LOGIN_INCONCLUSIVE',
                       'POSTCONDITIONS_INCONCLUSIVE','CRUD_INCONCLUSIVE','WORKFLOW_INCONCLUSIVE',
                       'CROSS_REPOSITORY_INCONCLUSIVE'}
NO_RUN_STATES = {'BLOCKED','NO_RUNNABLE_TESTS','VM_EXECUTION_NOT_VERIFIED',
                 'GUEST_RESULTS_NOT_VERIFIED','BROWSER_NOT_VERIFIED','BROWSER_VM_NOT_VERIFIED','LOGIN_VM_NOT_VERIFIED',
                 'CRUD_VM_NOT_VERIFIED','WORKFLOW_VM_NOT_VERIFIED','WORKFLOW_NOT_VERIFIED','ISOLATION_NOT_VERIFIED','CLEANUP_FAILED'}
VALID_RESULTS = {'PASS','FAIL','INCONCLUSIVE','BLOCKED','UNTESTED','INVALID_TEST','FLAKY'}


def normalize_plan(data: dict) -> dict:
    # Reuse the authoritative Phase 3 schema validator. This must never
    # downgrade an invalid/unreviewed oracle to a grounded assertion.
    try:
        from ..phase3.contracts import validate_test_plan, TestPlanContractError
        validate_test_plan(data)
    except ImportError:
        fail('PHASE3_CONTRACT_UNAVAILABLE')
    except (ValueError, TypeError, KeyError):
        fail('INVALID_PHASE3_PLAN_CONTRACT')
    if data.get('schema_version') != '1.1' or data.get('stage') != 'deterministic_test_generation':
        fail('UNSUPPORTED_TEST_PLAN_SCHEMA')
    src = source_key(data.get('source'))
    cases = data.get('test_cases')
    if type(cases) is not list or len(cases) > MAX_TESTS:
        fail('INVALID_TEST_PLAN_CASES')
    result = {}
    for c in cases:
        if type(c) is not dict or (key := safe_id(c.get('id'))) is None or key in result:
            fail('INVALID_TEST_PLAN_IDS')
        if c.get('status') not in ('READY_FOR_PHASE4','NEEDS_ORACLE','UNSUPPORTED_BY_RUNNER'):
            fail('INVALID_TEST_PLAN_STATUS')
        oracle = c.get('oracle')
        grounded = (type(oracle) is dict and oracle.get('status') == 'GROUNDED'
                    and type(c.get('assertions')) is list and len(c['assertions']) > 0
                    and c['status'] == 'READY_FOR_PHASE4')
        if c['status'] == 'READY_FOR_PHASE4' and not grounded:
            fail('PLAN_READY_WITHOUT_ORACLE')
        if c['status'] != 'READY_FOR_PHASE4' and grounded:
            fail('PLAN_UNRUNNABLE_WITH_GROUNDED_ASSERTIONS')
        assertions = c.get('assertions', [])
        if type(assertions) is not list or len(assertions) > 32:
            fail('INVALID_TEST_PLAN_ASSERTIONS')
        for a in assertions:
            if type(a) is not dict or a.get('kind') not in ('HTTP_STATUS_EQUALS','HTTP_BODY_SHA256_EQUALS'):
                fail('UNSUPPORTED_TEST_ASSERTION')
        result[key] = {'status':c['status'], 'grounded':bool(grounded),
                       'oracle_basis':oracle.get('basis') if grounded else None}
    return {'source':src,'cases':result,'digest':stable_hash(data)}


def normalize_vm(data: dict) -> dict:
    src = source_key(data.get('source'))
    status = data.get('status')
    exe, cleanup = data.get('execution'), data.get('cleanup')
    if type(status) is not str or len(status) > 80 or type(exe) is not str or len(exe) > 80 or type(cleanup) is not str or len(cleanup) > 90:
        fail('INVALID_VM_STATE')
    backend = data.get('backend')
    att = data.get('security_attestation')
    guest = data.get('guest_result')
    http=(guest.get('http_status') if type(guest) is dict and type(guest.get('http_status')) is int
          and 100 <= guest['http_status'] <= 599 else None)
    reason=data.get('reason')
    reason=reason if type(reason) is str and len(reason)<100 else None
    ready = (status == 'STARTUP_VERIFIED' and exe == EXEC and cleanup == CLEAN
             and backend == 'QEMU_DISPOSABLE_QCOW2_VM' and att == NOT_ATTESTED)
    return {'source':src,'status':status,'execution':exe,'cleanup':cleanup,
            'backend':backend,'security_attestation':att,'ready':ready,
            'adapter':data.get('adapter') if type(data.get('adapter')) is str else None,
            'validated_stages':{v for v in VM_STAGE_PROOFS.values() if data.get(v)=='VALIDATED'},
            'reason':reason,'http_status':http}


def _check_rows(rows: object, summary: object, *, stage: str) -> list[dict]:
    if type(rows) is not list or len(rows) > MAX_TESTS:
        fail('INVALID_TEST_RESULT_LIST')
    normalized = []
    seen = set()
    for i, r in enumerate(rows):
        if type(r) is not dict:
            fail('INVALID_TEST_RESULT_ROW')
        rid = safe_id(r.get('test_id')) or safe_id(r.get('id')) or safe_id(r.get('step'))
        if not rid or rid in seen:
            fail('INVALID_TEST_RESULT_ID')
        seen.add(rid)
        outcome = r.get('result')
        if outcome not in VALID_RESULTS:
            fail('INVALID_TEST_RESULT_OUTCOME')
        http = r.get('http_status')
        if http is not None and (type(http) is not int or not 100 <= http <= 599):
            fail('INVALID_HTTP_STATUS')
        normalized.append({'id':rid,'outcome':outcome,'http_status':http})
    if summary is not None:
        if type(summary) is not dict:
            fail('INVALID_RESULT_SUMMARY')
        c = Counter(x['outcome'] for x in normalized)
        fields = {'total':len(rows),'passed':c['PASS'],'failed':c['FAIL'],'inconclusive':c['INCONCLUSIVE']}
        for key, val in fields.items():
            if summary.get(key) != val or type(summary.get(key)) is not int:
                fail('INCONSISTENT_RESULT_COUNTS')
    return normalized


def normalize_run(data: dict, *, label: str, vm: dict | None = None) -> dict:
    stage = data.get('stage')
    if type(stage) is not str or len(stage) > 90:
        fail('INVALID_RUN_STAGE')
    source = source_key(data.get('source'))
    adapter = data.get('adapter')
    if adapter is not None and (type(adapter) is not str or len(adapter) > 90):
        fail('INVALID_RUN_ADAPTER')
    status = data.get('status')
    if type(status) is not str or len(status) > 100:
        fail('INVALID_RUN_STATUS')
    supported = stage in STAGES and stage not in ('reviewed_vm_scenario_isolation',
                                                   'reviewed_cross_repository_functional_pilot')
    if supported and status not in STAGES[stage]:
        fail('INVALID_RUN_STATUS_FOR_STAGE')
    exe, clean = data.get('execution'), data.get('cleanup')
    if not type(exe) is str or len(exe) > 90 or clean is not None and (type(clean) is not str or len(clean) > 90):
        fail('INVALID_RUN_EXECUTION')
    if supported and status in PASS_STATES | FAIL_STATES | INCONCLUSIVE_STATES and (exe != EXEC or clean != CLEAN):
        fail('DECISIVE_RESULT_WITHOUT_EXECUTION')
    if supported and status in PASS_STATES | FAIL_STATES | INCONCLUSIVE_STATES and data.get('security_attestation') != NOT_ATTESTED:
        fail('RUN_SECURITY_ATTESTATION_MISMATCH')
    if vm is not None and vm['source'] != source:
        fail('VM_RUN_SOURCE_MISMATCH')
    if vm is not None and data.get('security_attestation') not in (None,vm['security_attestation']):
        fail('VM_RUN_SECURITY_ATTESTATION_MISMATCH')
    if vm is not None and vm['adapter'] and adapter and vm['adapter'] != adapter:
        fail('VM_RUN_ADAPTER_MISMATCH')
    if vm is not None and vm['cleanup'] != clean:
        fail('VM_RUN_CLEANUP_MISMATCH')
    if vm is not None and vm['execution'] != exe:
        fail('VM_RUN_EXECUTION_MISMATCH')
    rows = []
    if stage in ('reviewed_vm_functional_testing','reviewed_vm_api_testing','reviewed_vm_database_postconditions'):
        rows = _check_rows(data.get('results', []), data.get('summary'), stage=stage)
        c = Counter(x['outcome'] for x in rows)
        if status == 'TESTS_PASSED' and (not rows or c['FAIL'] or c['INCONCLUSIVE']):
            fail('PASS_STATUS_CONTRADICTS_RESULTS')
        if status == 'TESTS_FAILED' and not c['FAIL']:
            fail('FAIL_STATUS_CONTRADICTS_RESULTS')
        if status == 'TESTS_INCONCLUSIVE' and not c['INCONCLUSIVE']:
            fail('INCONCLUSIVE_STATUS_CONTRADICTS_RESULTS')
    elif stage in ('reviewed_browser_login','reviewed_browser_ui'):
        checks = data.get('checks')
        if checks is not None:
            if type(checks) is not list or len(checks) > 128:
                fail('INVALID_BROWSER_CHECKS')
            seen = set()
            for i, check in enumerate(checks):
                if type(check) is not dict:
                    fail('INVALID_BROWSER_CHECK')
                rid = safe_id(check.get('id')) or safe_id(check.get('tag'))
                if not rid or rid in seen:
                    fail('INVALID_BROWSER_CHECK_ID')
                seen.add(rid)
                value = check.get('matched',check.get('passed',None))
                if value is not None and type(value) is not bool:
                    fail('INVALID_BROWSER_CHECK_VALUE')
                rows.append({'id':rid,'outcome':'INCONCLUSIVE' if value is None else 'PASS' if value else 'FAIL','http_status':None})
            if status in PASS_STATES and any(r['outcome']!='PASS' for r in rows):
                fail('PASS_STATUS_CONTRADICTS_CHECKS')
            if status in FAIL_STATES and rows and not any(r['outcome']=='FAIL' for r in rows):
                fail('FAIL_STATUS_CONTRADICTS_CHECKS')
    # Some older reviewed reports legitimately expose aggregate outcomes only.
    digest = None
    for key in ('catalog_sha256','oracle_catalog_sha256','phase3_plan_sha256'):
        val = data.get(key)
        if type(val) is str and len(val) == 64 and all(c in '0123456789abcdef' for c in val):
            digest = val
            break
    issue = data.get('issue_code')
    # Unknown source-supplied issue strings are never passed through to report prose.
    issue = issue if type(issue) is str and len(issue) <= 100 else None
    verified = bool(vm and vm['ready'] and source == vm['source'] and supported
                    and vm['validated_stages'].__contains__(VM_STAGE_PROOFS.get(stage))
                    and source[0]=='github'
                    and status in PASS_STATES | FAIL_STATES | INCONCLUSIVE_STATES)
    return {'label':label,'source':source,'stage':stage,'status':status,'adapter':adapter,
            'execution':exe,'cleanup':clean,'rows':rows,'issue':issue,
            'supported':supported,'vm':vm,'verified':verified,
            'fingerprint':digest,'digest':stable_hash(data),
            'attestation':data.get('security_attestation') == NOT_ATTESTED,
            'auth_failed':(type(data.get('auth_summary')) is dict
                            and type(data['auth_summary'].get('failed')) is int
                            and data['auth_summary']['failed']>0)}


def normalize_quality(data: dict) -> dict:
    if data.get('schema_version') != '0.3' or data.get('stage') != 'phase5_code_quality':
        # Earlier MVP may use another stage; do not guess.
        fail('UNSUPPORTED_QUALITY_REPORT_SCHEMA')
    raw = data.get('source')
    if type(raw) is not dict:
        fail('INVALID_QUALITY_SOURCE')
    kind = {'github_pinned':'github','local_unverified':'local'}.get(raw.get('kind'))
    if kind is None:
        fail('INVALID_QUALITY_SOURCE')
    source = source_key({'kind':kind,'repository':raw.get('repository'),
                         'commit_sha':raw.get('commit_sha')})
    quality = data.get('quality')
    security = data.get('security')
    if type(quality) is not dict or type(security) is not dict:
        fail('INVALID_QUALITY_REPORT')
    findings = security.get('findings')
    if type(findings) is not list or len(findings) > 750:
        fail('INVALID_SECURITY_FINDINGS')
    alerts = []
    for f in findings:
        if type(f) is not dict:
            fail('INVALID_SECURITY_FINDING')
        path = safe_path(f.get('path'))
        if path is None:
            # Avoid printing repository-controlled unsafe file names.
            continue
        alerts.append({'path':path,'line':f.get('line') if type(f.get('line')) is int else None})
    code_locations=[]
    severity_rank={'HIGH':0,'MEDIUM':1,'LOW':2,'ADVISORY':3}
    for f in (data.get('findings') or [])[:750]:
        if type(f) is not dict:continue
        path=safe_path(f.get('path'))
        code=safe_id(f.get('code'))
        severity=f.get('severity')
        if path and code and severity in severity_rank:
            ln=f.get('line') if type(f.get('line')) is int and f.get('line')>0 else None
            code_locations.append({'code':code,'path':path,'line':ln,'severity':severity})
    code_locations.sort(key=lambda x:(severity_rank[x['severity']],x['path'],x['line'] or 0,x['code']))
    return {'source':source,'security_alerts':alerts,'static_locations':code_locations[:20],
            'security_scan_partial':security.get('status')=='PARTIAL' or security.get('truncated') is True,
            'score_status':quality.get('status') if quality.get('status') in ('PROVISIONAL','PROVISIONAL_LIMITED','INSUFFICIENT_COVERAGE') else 'UNKNOWN',
            'quality_finding_count':len(data.get('findings',[])) if type(data.get('findings')) is list else 0}
