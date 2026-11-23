"""Independent, offline Phase 7 evaluation of saved Phase 6/5 artifacts.

Never runs or imports student source. No network, VM, browser, subprocess or LLM.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math

from ..phase6.report import validate_report as validate_phase6
from ..phase5.contracts import validate_report as validate_phase5
from .contracts import (BenchmarkError, DEFAULT_POLICY, MAX_TOTAL_BYTES,
                        load_json, load_bytes, record_source, require, safe_path,
                        validate_dataset, VERSION)


def ratio(a,b):
    return round(a/b,6) if b else None


def ranks(values):
    order=sorted(range(len(values)),key=lambda i:values[i])
    result=[0.0]*len(values)
    i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:
            j+=1
        mean=((i+1)+j)/2
        for k in order[i:j]:result[k]=mean
        i=j
    return result


def spearman(xs,ys):
    if len(xs)<2:return None
    a,b=ranks(xs),ranks(ys)
    ma=sum(a)/len(a);mb=sum(b)/len(b)
    va=sum((x-ma)**2 for x in a);vb=sum((y-mb)**2 for y in b)
    if va<=0 or vb<=0:return None
    return round(sum((x-ma)*(y-mb) for x,y in zip(a,b))/math.sqrt(va*vb),6)


def _safe_code(exc):
    if isinstance(exc,BenchmarkError):return exc.code
    return 'INVALID_OR_UNAVAILABLE_EVIDENCE'


def read_bound(root:Path, desc:dict):
    path=safe_path(root,desc['path'])
    data,sha,size=load_json(path)
    require(sha==desc['sha256'],'ARTIFACT_SHA_MISMATCH')
    return data,size


def evaluate_case(root:Path, spec:dict):
    case_id=spec['id'];g=spec['ground_truth'];truth_src=record_source(spec['source'])
    phase6,size=read_bound(root,spec['phase6'])
    try:validate_phase6(phase6)
    except (ValueError,KeyError,TypeError):raise BenchmarkError('INVALID_PHASE6_REPORT') from None
    require(record_source(phase6['source'])==truth_src,'CROSS_ARTIFACT_SOURCE_MISMATCH')
    phase5=None; quality_score=None; quality_status=None; bytes_total=size
    if 'phase5' in spec:
        phase5,n=read_bound(root,spec['phase5'])
        bytes_total+=n
        try:validate_phase5(phase5)
        except (ValueError,KeyError,TypeError):raise BenchmarkError('INVALID_PHASE5_REPORT') from None
        require(record_source(phase5['source'])==truth_src,'CROSS_ARTIFACT_SOURCE_MISMATCH')
        quality_status=phase5['quality']['status']
        quality_score=phase5['quality']['score']
        if quality_score is not None:
            require(type(quality_score) is int and 0<=quality_score<=100,'INVALID_QUALITY_INDICATOR')
    receipts={x['id']:x['sha256'] for x in phase6['evidence_receipts']}
    checked=0
    for receipt in spec.get('receipt_files',[]):
        require(receipt['id'] in receipts,'EVIDENCE_RECEIPT_MISSING')
        p=safe_path(root,receipt['path'])
        raw,sha=load_bytes(p)
        bytes_total+=len(raw)
        require(sha==receipts[receipt['id']],'EVIDENCE_RECEIPT_MISMATCH')
        checked+=1
    require(bytes_total<=MAX_TOTAL_BYTES,'TOTAL_EVIDENCE_BYTE_LIMIT')
    actual_codes={d['code'] for d in phase6['diagnoses']}
    expected_codes=set(g['expected_codes'])
    predicted_events={(ev['run'],ev['test_id']):ev for ev in phase6['events']}
    # A malformed duplicate should never get silently collapsed into one observation.
    require(len(predicted_events)==len(phase6['events']),'DUPLICATE_PHASE6_EVENT')
    confusion=Counter();missing_events=[];event_results=[]
    caused=0;cause_hits=0;cause_missing=0
    hypotheses={(h['run'],h['test_id']):h for h in phase6['root_cause_hypotheses']}
    require(len(hypotheses)==len(phase6['root_cause_hypotheses']),'DUPLICATE_HYPOTHESIS')
    for ev in g['expected_events']:
        key=(ev['run'],ev['test_id']);prediction=predicted_events.get(key)
        actual=prediction['status'] if prediction else 'MISSING'
        expected=ev['outcome']
        confusion[(expected,actual)]+=1
        if prediction is None:missing_events.append(ev['test_id'])
        if prediction is not None:
            require(prediction['student_defect']=='NOT_ESTABLISHED','STUDENT_FAULT_OVERCLAIM')
        known=ev['known_cause']
        if known is not None:
            caused+=1
            candidate=hypotheses.get(key)
            if candidate is None:
                cause_missing+=1
            elif known in candidate['candidate_codes'][:3]:
                cause_hits+=1
        event_results.append({'run':key[0],'test_id':key[1],
                              'expected':expected,'observed':actual,
                              'correct':actual==expected,'cause_reviewed':known is not None,
                              'cause_top3_hit':(known in hypotheses[key]['candidate_codes'][:3]
                                                if known is not None and key in hypotheses else None)})
    observed_comps={x['test_id']:x for x in phase6['rerun_comparisons']}
    comparison_results=[]
    for expected in g['expected_comparisons']:
        actual=observed_comps.get(expected['test_id'])
        outcome=actual['status'] if actual else 'MISSING'
        # Phase 6 explicitly refuses to attest equivalent environments. Preserve that boundary.
        require(actual is None or actual.get('environment_equivalence')=='NOT_ESTABLISHED',
                'RERUN_EQUIVALENCE_OVERCLAIM')
        comparison_results.append({'test_id':expected['test_id'],'expected':expected['status'],
                                   'observed':outcome,'matches':outcome==expected['status'],
                                   'external_equivalence_declared':expected['environment_equivalent'],
                                   'equivalence_attested_by_executioner':False})
    overclaims=sum(x.get('student_defect')!='NOT_ESTABLISHED' for x in phase6['events'])
    overclaims+=sum(x.get('student_defect')!='NOT_ESTABLISHED' for x in phase6['diagnoses'])
    overclaims+=sum(x.get('cause_established') is not False for x in phase6['root_cause_hypotheses'])
    if phase6['status']=='SECURITY_HOLD' and phase6['root_cause_hypotheses']:
        overclaims+=1
    code_fn=sorted(expected_codes-actual_codes)
    code_fp=sorted(actual_codes-expected_codes)
    status_correct=phase6['status']==g['expected_report_status']
    return {
        'id':case_id,'cohort':spec['cohort'],'result':'EVALUATED',
        'source':{'kind':truth_src[0],'repository':truth_src[1],'commit_sha':truth_src[2]},
        'phase6_status':phase6['status'],'expected_status':g['expected_report_status'],
        'status_correct':status_correct,
        'expected_codes':sorted(expected_codes),'observed_codes':sorted(actual_codes),
        'missing_codes':code_fn,'unexpected_codes':code_fp,
        'events':event_results,'comparisons':comparison_results,
        'known_causes':caused,'top3_hits':cause_hits,'missing_hypothesis':cause_missing,
        'false_attributions':overclaims,'verified_input_receipts':checked,
        'total_report_receipts':len(receipts),
        'quality':{'status':quality_status,'score':quality_score,'reviewer_maintainability':g['maintainability_rating']},
        'phase5_sha256':spec['phase5']['sha256'] if 'phase5' in spec else None,
        'phase6_sha256':spec['phase6']['sha256'],
        'review_basis':g['basis'],
    },bytes_total


def _aggregate(items, policy, case_count):
    evaluated=[c for c in items if c['result']=='EVALUATED']
    counters=Counter(c['result'] for c in items)
    cohorts=Counter(c['cohort'] for c in evaluated)
    sources={c['source']['repository'] for c in evaluated}
    confusion=Counter()
    missed=extra=tp=0
    for c in evaluated:
        missed+=len(c['missing_codes']); extra+=len(c['unexpected_codes']);tp+=len(set(c['expected_codes']) & set(c['observed_codes']))
        for ev in c['events']:confusion[(ev['expected'],ev['observed'])]+=1
    false_pass=sum(v for (expected,actual),v in confusion.items() if expected=='FAIL' and actual=='PASS')
    false_fail=sum(v for (expected,actual),v in confusion.items() if expected=='PASS' and actual=='FAIL')
    missing=sum(v for (expected,actual),v in confusion.items() if actual=='MISSING')
    correct=sum(v for (expected,actual),v in confusion.items() if expected==actual)
    cause_total=sum(c['known_causes'] for c in evaluated)
    cause_hits=sum(c['top3_hits'] for c in evaluated)
    comparisons=[comp for c in evaluated for comp in c['comparisons']]
    # One pinned Phase 5 report is only one calibration sample even if reused in many cases.
    ratings={};duplicates=0
    for c in evaluated:
        q=c['quality']
        if q['reviewer_maintainability'] is None:continue
        key=(c['source']['repository'],c['source']['commit_sha'],c['phase5_sha256'])
        pair=(q['score'],q['reviewer_maintainability'],q['status'])
        if key in ratings:
            if ratings[key]!=pair:duplicates+=1
        else:ratings[key]=pair
    valid_ratings=[(s,r) for s,r,status in ratings.values()
                   if s is not None and status in ('PROVISIONAL','PROVISIONAL_LIMITED')]
    xs=[v[0] for v in valid_ratings];ys=[v[1] for v in valid_ratings]
    mae=round(sum(abs(a-b) for a,b in zip(xs,ys))/len(xs),4) if xs else None
    max_abs=max((abs(a-b) for a,b in zip(xs,ys)),default=None)
    required={
       'enough_cases':len(evaluated)>=policy['min_cases'],
       'repository_diversity':len(sources)>=policy['min_repositories'],
       'cohort_diversity':len(cohorts)>=policy['min_cohorts'],
       'application_defect_coverage':cohorts['APPLICATION_DEFECT']>=policy['min_application_defects'],
       'enough_test_labels':sum(confusion.values())>=policy['min_event_labels'],
       'known_cause_coverage':cause_total>=policy['min_known_causes'],
       'calibration_sample_coverage':len(xs)>=policy['min_calibration_pairs'],
       'no_false_passes':false_pass<=policy['max_false_passes'],
       'no_student_attribution_overclaim':sum(c['false_attributions'] for c in evaluated)<=policy['max_false_attributions'],
       'no_rejected_evidence':counters['REJECTED']==0,
       'consistent_quality_labels':duplicates==0,
       'expected_diagnostics_covered':missed==0,
       'unexpected_diagnostics_reviewed':extra==0,
       'expected_outcomes_matched':correct==sum(confusion.values()),
       'expected_statuses_matched':all(c['status_correct'] for c in evaluated),
       'historical_comparisons_matched':all(x['matches'] for x in comparisons),
    }
    hard=['no_false_passes','no_student_attribution_overclaim','no_rejected_evidence','consistent_quality_labels']
    status=('FAIL' if any(not required[k] for k in hard) else
            'PASS' if all(required.values()) else 'LIMITED')
    return status,{
        'cases':case_count,'evaluated':len(evaluated),'rejected':counters['REJECTED'],
        'repositories':len(sources),'cohorts':dict(sorted(cohorts.items())),
        'event_labels':sum(confusion.values()),'correct_event_outcomes':correct,
        'event_accuracy':ratio(correct,sum(confusion.values())),
        'missing_expected_events':missing,'false_passes':false_pass,'false_failures':false_fail,
        'confusion_matrix':[{'expected':k[0],'observed':k[1],'count':v} for k,v in sorted(confusion.items())],
        'diagnostic_code_precision':ratio(tp,tp+extra),
        'diagnostic_code_recall':ratio(tp,tp+missed),
        'diagnostic_code_true_positive':tp,'missing_diagnostic_codes':missed,
        'unexpected_diagnostic_codes':extra,
        'root_cause_top3_hit_rate':ratio(cause_hits,cause_total),
        'root_cause_labeled_failures':cause_total,'root_cause_top3_hits':cause_hits,
        'historical_comparison_checks':len(comparisons),
        'historical_comparison_matches':sum(c['matches'] for c in comparisons),
        'declared_equivalent_environment_checks':sum(c['external_equivalence_declared'] for c in comparisons),
        'executioner_equivalence_attestations':0,
        'false_student_attributions':sum(c['false_attributions'] for c in evaluated),
        'input_receipts_crosschecked':sum(c['verified_input_receipts'] for c in evaluated),
        'phase6_report_receipts_declared':sum(c['total_report_receipts'] for c in evaluated),
        'score_calibration':{'status':'DESCRIPTIVE_ONLY' if len(xs)>=5 else 'INSUFFICIENT_SAMPLES',
                             'pairs':len(xs),'unscorable_or_insufficient':len(ratings)-len(xs),
                             'mae':mae,'max_absolute_error':max_abs,
                             'spearman_rank_correlation':spearman(xs,ys),
                             'reference':'REVIEWER_MAINTAINABILITY_INDEX_NOT_STUDENT_GRADE',
                             'duplicate_label_conflicts':duplicates},
        'release_checks':required,
    }


def run(manifest:dict, root:Path) -> dict:
    validate_dataset(manifest)
    require(root.is_dir() and not root.is_symlink(), 'INVALID_DATASET_ROOT')
    results=[];total=0;seen_artifacts=set()
    for spec in sorted(manifest['cases'],key=lambda x:x['id']):
        try:
            require(spec['phase6']['sha256'] not in seen_artifacts, 'DUPLICATE_BENCHMARK_ARTIFACT')
            seen_artifacts.add(spec['phase6']['sha256'])
            result,n=evaluate_case(root,spec)
            total+=n
        except (BenchmarkError,OSError,ValueError,TypeError,KeyError,RecursionError) as exc:
            result={'id':spec['id'],'cohort':spec['cohort'],'result':'REJECTED',
                    'error':_safe_code(exc)}
        results.append(result)
    require(total<=MAX_TOTAL_BYTES,'TOTAL_EVIDENCE_BYTE_LIMIT')
    status,metrics=_aggregate(results,manifest['policy'],len(manifest['cases']))
    return {
        'schema_version':VERSION,'phase':7,'stage':'offline_ground_truth_benchmark',
        'benchmark_id':manifest['benchmark_id'], 'status':status,
        'execution':'NOT_PERFORMED','vm_launches':0,
        'ground_truth_basis':'INDEPENDENT_REVIEWER_DECLARATIONS_NOT_CRYPTOGRAPHICALLY_ATTESTED',
        'results':results,'metrics':metrics,'policy':dict(manifest['policy']),
        'limitations':[
            'Phase 7 did not execute student code or launch any VM; it audits existing saved reports.',
            'Ground-truth labels require independent, documented review; this pipeline cannot prove reviewer independence.',
            'A Phase 6 JSON report and its SHA-256 do not independently authenticate the original VM execution.',
            'Unmatched labels and missing evidence must not be counted as correct predictions.',
            'Root-cause Top-3 hit rate measures candidate inclusion, not established causality.',
            'Phase 5 quality is a heuristic maintainability indicator, not a rubric grade.',
            'Phase 6 cannot attest runtime/fixture equivalence across historical reruns.',
            'Sample metrics are descriptive and are not generalization guarantees for arbitrary projects.',
            'Arbitrary untrusted repository execution remains restricted to existing operator security gates.',
        ]
    }
