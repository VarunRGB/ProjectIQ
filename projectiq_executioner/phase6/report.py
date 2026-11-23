"""Structured summary plus redacted, human-readable Markdown. No raw student values."""
from __future__ import annotations
from .contracts import fail
from .catalog import CATALOG

REQUIRED = {'schema_version','phase','stage','source','status','execution','vm_launches',
            'security_attestation','summary','evidence_receipts','events','diagnoses',
            'dependency_groups','rerun_comparisons','root_cause_hypotheses','static_context','security_context','limitations'}
STATES={'DIAGNOSED','PARTIAL','SECURITY_HOLD'}


def validate_report(r: object) -> None:
    if type(r) is not dict or set(r)!=REQUIRED or r['schema_version']!='0.1' or r['phase']!=6 or r['stage']!='offline_failure_diagnosis' or r['status'] not in STATES:
        fail('INVALID_PHASE6_REPORT_SCHEMA')
    if r['execution']!='NOT_PERFORMED' or r['vm_launches']!=0 or r['security_attestation']!='NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS':
        fail('INVALID_PHASE6_EXECUTION_CLAIM')
    if type(r['diagnoses']) is not list or type(r['events']) is not list or len(r['diagnoses'])>2000 or len(r['events'])>2000:
        fail('INVALID_PHASE6_COUNTS')
    for d in r['diagnoses']:
        if type(d) is not dict or d.get('code') not in CATALOG or d.get('student_defect')!='NOT_ESTABLISHED':
            fail('INVALID_DIAGNOSIS')
        category,priority,audience,title,explanation,action=CATALOG[d['code']]
        if any(d.get(k)!=v for k,v in {'category':category,'priority':priority,
                'audience':audience,'title':title,'explanation':explanation,
                'recommended_action':action}.items()):
            fail('TAMPERED_DIAGNOSIS_MESSAGE')
        if d.get('causal_status') not in ('UNPROVEN','OBSERVED_MISMATCH_ONLY') or d.get('confidence') not in ('LOW','MEDIUM','HIGH'):
            fail('INVALID_DIAGNOSIS_CONFIDENCE')
        if type(d.get('evidence')) is not list or len(d['evidence'])>100 or any(type(i) is not str or len(i)>100 for i in d['evidence']):
            fail('INVALID_EVIDENCE_REFERENCES')
    summary=r['summary']
    if type(summary) is not dict or summary.get('diagnoses')!=len(r['diagnoses']) or summary.get('tests_observed')!=len(r['events']) or summary.get('comparisons')!=len(r['rerun_comparisons']):
        fail('INVALID_SUMMARY_COUNTS')
    for field in ('passes','fails','inconclusive'):
        outcome={'passes':'PASS','fails':'FAIL','inconclusive':'INCONCLUSIVE'}[field]
        if summary.get(field)!=sum(x.get('status')==outcome for x in r['events']):
            fail('INCONSISTENT_SUMMARY_COUNTS')
    if type(r['security_context']) is not list or len(r['security_context'])>20:
        fail('INVALID_SECURITY_CONTEXT')
    if type(r['static_context']) is not list or len(r['static_context'])>20:
        fail('INVALID_STATIC_CONTEXT')
    if type(r['root_cause_hypotheses']) is not list or len(r['root_cause_hypotheses'])>400:
        fail('INVALID_HYPOTHESIS_LIST')
    for h in r['root_cause_hypotheses']:
        if type(h) is not dict or h.get('cause_established') is not False or h.get('status')!='UNVERIFIED_HYPOTHESES':
            fail('INVALID_HYPOTHESIS')
    if r['status']=='SECURITY_HOLD' and not any(d['code']=='VM_CLEANUP_UNVERIFIED' for d in r['diagnoses']):
        fail('SECURITY_HOLD_MISSING_DIAGNOSIS')


def markdown(r:dict) -> str:
    validate_report(r)
    s=r['summary']
    lines=['# ProjectIQ Executioner — Phase 6 Failure Diagnosis', '',
           '**Status:** '+r['status'],
           '**Evidence processing:** OFFLINE ONLY · No student code executed',
           '**Isolation security:** NOT ATTESTED FOR UNTRUSTED STUDENTS',
           '**Student defect attribution:** NOT ESTABLISHED', '',
           '## Coverage', '',
           f"- Reports examined: {s['runs']}",
           f"- VM-corroborated reports: {s['vm_verified_runs']}",
           f"- Test observations: {s['tests_observed']} (PASS {s['passes']}, FAIL {s['fails']}, INCONCLUSIVE {s['inconclusive']})",
           f"- Diagnostic findings: {s['diagnoses']}",
           f"- Comparable rerun groups: {s['comparisons']}", '',
           '## Prioritized diagnoses','']
    for d in r['diagnoses']:
        lines.extend([f"### {d['priority']} — {d['title']} (`{d['code']}`)", '',
              f"**Category:** {d['category']} · **Confidence:** {d['confidence']} · **Audience:** {d['audience']}", '',
              d['explanation'], '',
              '**Recommended action:** '+d['recommended_action'],'',
              '**Evidence IDs:** '+(', '.join('`'+e+'`' for e in d['evidence']) or 'none'),'',
              '**Test IDs:** '+(', '.join('`'+i+'`' for i in d['test_ids']) or 'not established'),'',
              '**Causality:** '+d['causal_status']+'; no proven student defect.', ''])
    if not r['diagnoses']:lines.extend(['No actionable diagnostic findings supported by the supplied evidence.',''])
    lines.extend(['## Declared test dependencies',''])
    for group in r['dependency_groups']:
        lines.append(f"- `{group['blocked_test']}` depends on failing prerequisites: "+', '.join('`'+v+'`' for v in group['prerequisite_failures']))
    if not r['dependency_groups']:lines.append('None verified from supplied reviewed dependency edges.')
    lines.extend(['','## Historical rerun comparison',''])
    for c in r['rerun_comparisons']:
        lines.append(f"- `{c['test_id']}`: {c['status']} across {c['sample_count']} report fingerprints; outcomes "+', '.join(c['outcomes']))
    if not r['rerun_comparisons']:lines.append('No independently comparable rerun group supplied.')
    lines.extend(['','## Potential secret exposure locations (values always redacted)',''])
    for item in r['security_context']:
        lines.append('- `'+item['path']+'`:'+str(item['line'] or '?')+' — pattern indicator; actual secret validity not established')
    if not r['security_context']:lines.append('No location indicators supplied.')
    lines.extend(['','## Root-cause hypotheses (not verified)',''])
    for h in r['root_cause_hypotheses']:
        lines.append('- `'+h['test_id']+'` ('+h['run']+'): '+', '.join('`'+v+'`' for v in h['candidate_codes'])+' — unverified possibilities')
    if not r['root_cause_hypotheses']:lines.append('No unsupported root-cause guesses generated.')
    lines.extend(['','## Independent static quality context (not causal evidence)',''])
    for item in r['static_context']:
        lines.append('- `'+item['path']+'`:'+str(item['line'] or '?')+' — `'+item['code']+'` ('+item['severity']+')')
    if not r['static_context']:lines.append('No Phase 5 source locations supplied.')
    lines.extend(['','## Input evidence fingerprints','',
                  '| Input ID | Role | SHA-256 |','|---|---|---|'])
    for e in r['evidence_receipts']:
        lines.append(f"| `{e['id']}` | {e['role']} | `{e['sha256']}` |")
    lines.extend(['','## Limitations',''])
    lines.extend('- '+x for x in r['limitations'])
    lines.append('')
    return '\n'.join(lines)
