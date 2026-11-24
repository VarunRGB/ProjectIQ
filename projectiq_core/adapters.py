"""Translate the native contracts at 0b4bbde; do not reinterpret scores as grades."""
from __future__ import annotations
import math
from typing import Any
from .contracts import AgentResult, AIAnalysis, Metric, Receipt

class AdapterError(ValueError): pass

def obj(v):
    if not isinstance(v, dict): raise AdapterError('EXPECTED_OBJECT')
    return v

def number(v, *, low=0, high=None, integer=False):
    if v is None: return None
    if type(v) not in (int, float) or not math.isfinite(v) or v < low or (high is not None and v > high) or (integer and type(v) is not int):
        raise AdapterError('INVALID_METRIC')
    return v

def metric(id, label, value, unit, scope, **limits):
    return Metric(id=id, label=label, value=number(value, **limits), unit=unit, scope=scope)

def subset(d, keys): return {k:d[k] for k in keys if k in d}

def skipped(agent, status='NOT_RUN', code=None, note=None, native=None):
    return AgentResult(agent=agent,status=status,error_code=code,native=native,
        limitations=[note] if note else [],ai_detection=AIAnalysis() if agent=='integrity' else None)

def summarizer(d, receipt):
    if d.get('result_kind')=='SUMMARY_ONLY':
        summary=obj(d.get('summary'))
        if (not isinstance(summary.get('summary'),str) or not summary['summary'].strip() or
            not isinstance(summary.get('key_points'),list) or any(not isinstance(p,str) for p in summary['key_points'])):
            raise AdapterError('INVALID_SUMMARY_ONLY_RESULT')
        if any(key in d for key in ('weighted_score','criteria','recommendation','confidence')):
            raise AdapterError('SUMMARY_ONLY_CONTAINS_LEGACY_JUDGEMENT')
        notes=['Generated report summary only; model claims are not independently verified.',
               'All project grading is performed separately by the unified rubric engine.']
        if isinstance(d.get('collector_resource_lifecycle'),dict) and d['collector_resource_lifecycle'].get('ollama_release')=='RELEASE_NOT_VERIFIED':
            notes.append('Ollama model release not verified; inspect resource usage before VM work.')
        return AgentResult(agent='summarizer',status='COMPLETED',native_status='SUMMARY_GENERATED',native=receipt,
            metrics=[metric('word_count','Report words',d.get('word_count'),'count','Whitespace-delimited extracted report text',integer=True)],
            details=subset(d,('result_kind','summary','model','word_count','chunks','elapsed_s','collector_resource_lifecycle')),
            limitations=notes)
    obj(d.get('summary')); conf=obj(d.get('confidence'))
    criteria=d.get('criteria')
    if not isinstance(criteria,list) or not criteria or any(not isinstance(c,dict) for c in criteria):
        raise AdapterError('MISSING_RUBRIC_CRITERIA')
    for c in criteria: number(c.get('score'),high=10)
    # Existing output does not carry the full expected rubric; do not certify completeness.
    notes=['Legacy report-writing score only; expected criterion coverage is not certified by this adapter.',
           'Confidence measures internal consistency and grounding, not calibrated grading accuracy.']
    lifecycle=d.get('collector_resource_lifecycle')
    if isinstance(lifecycle,dict) and lifecycle.get('ollama_release')=='RELEASE_NOT_VERIFIED':
        notes.append('Ollama model release not verified; check available memory before any VM launch.')
    return AgentResult(agent='summarizer',status='PARTIAL',native_status='REPORT_GENERATED',native=receipt,
        metrics=[metric('report_writing_score','Report-writing score',d.get('weighted_score'),'out_of_10','Legacy report-only rubric',high=10),
                 metric('judge_confidence','Judge confidence',conf.get('score'),'out_of_100','Internal consistency and grounding',high=100)],
        details=subset(d,('summary','criteria','recommendation','recommendation_reason','concrete_details','generic_claims','writing_quality','technical_depth','confidence','model','word_count','elapsed_s','rationale','content_type','ai_style_indicators','ai_style_likelihood','collector_resource_lifecycle')),limitations=notes)

def integrity(d, receipt):
    refs=d.get('references'); words=number(d.get('words_checked'),integer=True)
    if not isinstance(refs,list) or words is None: raise AdapterError('REQUIRE_DETAILED_INTEGRITY_REPORT')
    comparable=bool(refs) and words>0
    overlap=number(d.get('overall_percent'),high=100)
    chunks=number(d.get('percent_in_chunks'),high=100)
    # Future teammate output must be explicitly mapped to this named contract.
    try:
        ai=AIAnalysis.model_validate(d['ai_detection']) if 'ai_detection' in d else AIAnalysis(
            limitations=['No structured percentage is available from the current Integrity implementation.'])
    except ValueError:
        ai=AIAnalysis(status='FAILED', limitations=['AI output did not match the unified schema; similarity is unaffected.'])
    notes=['Similarity is overlap with supplied references, not a plagiarism verdict.',
           'AI indicators are advisory, not proof of authorship or grounds for automatic deductions.']
    if not comparable: notes.append('No usable reference comparison denominator; overlap withheld.')
    return AgentResult(agent='integrity',status='PARTIAL' if not comparable or overlap is None or ai.status!='COMPLETED' else 'COMPLETED',native_status='SIMILARITY_REPORT_GENERATED',native=receipt,ai_detection=ai,
        metrics=[metric('reference_overlap','Reference overlap',overlap if comparable else None,'percent','Checked words; supplied references only',high=100),
                 metric('copied_chunks','Words in copied chunks',chunks if comparable else None,'percent','Checked words',high=100),
                 metric('references_compared','References compared',len(refs),'count','Supplied readable references',integer=True)],
        details=subset(d,('references','sentences_checked','words_checked','percent_by_category','percent_by_reference','chunks','matches','backends','notes','ai_findings','ai_notes')),limitations=notes)

def research(d, receipt):
    r=obj(d.get('research_report')); p1=obj(r.get('phase_1')); p2=obj(r.get('phase_2')); p3=obj(r.get('phase_3')); p4=obj(r.get('phase_4'))
    return AgentResult(agent='research',status='PARTIAL',native_status='RESEARCH_REPORT_GENERATED',native=receipt,
        metrics=[metric('references','Reference entries',p1.get('reference_entries'),'count','Extracted bibliography entries',integer=True),
                 metric('references_checked','References checked',p2.get('checked_references'),'count','Metadata checks attempted; see status breakdown',integer=True),
                 metric('references_assessed','Relevance assessments',p3.get('assessed_references'),'count','Heuristic relevance assessment',integer=True),
                 metric('recent_reliable','Recent confirmed-year papers',p4.get('recent_reliable_percent'),'percent','Distinct references with confirmed publication years',high=100)],
        details={'research_report':r,**subset(d,('verification_results','relevance_results','recency_results','recency_summary','citation_reference_matches','notes'))},
        limitations=['Metadata verification, relevance and factual claim support are distinct; inspect coverage and per-reference statuses.'])

def executioner(d, receipt):
    native=d.get('status')
    if native not in ('COMPLETED','COMPLETED_WITH_LIMITATIONS','NEEDS_REVIEW','SECURITY_HOLD','FAILED'):
        raise AdapterError('UNKNOWN_EXECUTIONER_STATUS')
    if native=='FAILED':
        return AgentResult(agent='executioner',status='FAILED',native_status=native,native=receipt,error_code='EXECUTIONER_FAILED',limitations=['See native artifact for bounded diagnostic information.'])
    p=obj(d.get('project_intelligence',{})); matrix=obj(d.get('test_case_matrix',{})); suites=d.get('suites',[])
    if not isinstance(suites,list) or any(not isinstance(s,dict) for s in suites): raise AdapterError('INVALID_SUITES')
    verified=sum(s.get('vm_verified') is True and s.get('cleanup')=='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' for s in suites)
    passed=sum(s.get('state')=='PASS' and s.get('vm_verified') is True and s.get('cleanup')=='VM_STOPPED_DISPOSABLE_STORAGE_REMOVED' for s in suites)
    counts=obj(matrix.get('reviewed_check_counts',{})); planned=obj(matrix.get('counts',{}))
    quality=obj(d.get('code_quality',{})); q=obj(quality.get('quality',{}))
    status={'COMPLETED':'COMPLETED','COMPLETED_WITH_LIMITATIONS':'PARTIAL','NEEDS_REVIEW':'REVIEW_REQUIRED','SECURITY_HOLD':'REVIEW_REQUIRED'}[native]
    return AgentResult(agent='executioner',status=status,native_status=native,native=receipt,
        metrics=[metric('server_endpoints','Server endpoint records',p.get('server_endpoint_count'),'count','Static server path/method records',integer=True),
                 metric('vm_suites_verified','VM suites verified',verified,'count','Native result reports verified execution and cleanup',integer=True),
                 metric('vm_suites_passed','Verified suites passed',passed,'count','Selected reviewed suites only',integer=True),
                 metric('reviewed_passes','Reviewed scenarios passed',counts.get('PASSED'),'count','Separate from whole-route coverage',integer=True),
                 metric('planned_passes','Exact planned passes',planned.get('PASSED'),'count','Original planned test specifications',integer=True),
                 metric('maintainability','Maintainability indicator',q.get('score') if q.get('status')=='PROVISIONAL' else None,'out_of_100','Provisional indicator; not a grade',high=100)],
        details=subset(d,('source','mode','effective_mode','security_attestation','workflow','project_intelligence','runtime_plan','test_plan','test_case_matrix','suites','code_quality','diagnostics','recommendations','review_requests','limitations','error_codes','vm_attempts_scheduled','vm_attempt_budget')),
        limitations=['VM evidence is scoped to reviewed tests; a pass is not whole-project correctness.', 'No sandbox attestation is inferred by this adapter.'])

ADAPTERS={'summarizer':summarizer,'integrity':integrity,'research':research,'executioner':executioner}

def normalize(agent, raw, receipt):
    return ADAPTERS[agent](obj(raw),receipt)
