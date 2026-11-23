"""Compare historical report outcomes only; does not schedule or execute VM runs."""
from __future__ import annotations
from collections import defaultdict
from .catalog import diagnosis
from .contracts import stable_hash


def analyze_reruns(runs: list[dict]) -> tuple[list[dict],list[dict]]:
    """Requires independently matched VM results and matching reviewed oracle fingerprints.

    A different response to the same test is a *flakiness candidate*, never proof
    of the student's defect or of test nondeterminism.
    """
    comparable: dict[tuple, list[tuple[str,str,str]]] = defaultdict(list)
    not_comparable = []
    for run in runs:
        if run['status'] not in ('TESTS_PASSED','TESTS_FAILED','BROWSER_PASSED',
                                 'BROWSER_FAILED','LOGIN_PASSED','LOGIN_FAILED',
                                 'POSTCONDITIONS_PASSED','POSTCONDITIONS_FAILED'):
            continue
        if not (run['verified'] and run['fingerprint'] and run['source'][0]=='github'):
            not_comparable.append(run['label'])
            continue
        # Fingerprint + exact pinned Git commit + adapter + stage. No comparison on
        # bare status when the test suite or reviewed assertions may have changed.
        for item in run['rows']:
            if item['outcome'] in ('PASS','FAIL'):
                key = (run['source'],run['stage'],run['adapter'],run['fingerprint'],item['id'])
                comparable[key].append((item['outcome'],run['label'],run['digest']))
    findings=[];comparisons=[]
    for key, values in sorted(comparable.items(),key=lambda p:str(p[0])):
        distinct = {(out,digest) for out,lab,digest in values}
        # Same report included twice is not an independent rerun.
        outcomes = {out for out,digest in distinct}
        labels = sorted({lab for out,lab,digest in values})
        if len({d for _,d in distinct}) < 2:
            continue
        comp = {'test_id':key[4], 'stage':key[1],
                'environment_equivalence':'NOT_ESTABLISHED', 'sample_count':len({d for _,d in distinct}),
                'outcomes':sorted(outcomes), 'evidence':labels, 'status':
                'OUTCOME_INCONSISTENT_REVIEW_REQUIRED' if outcomes == {'PASS','FAIL'} else 'CONSISTENT'}
        comparisons.append(comp)
        if comp['status']=='OUTCOME_INCONSISTENT_REVIEW_REQUIRED':
            findings.append(diagnosis('RERUN_OUTCOME_INCONSISTENT', evidence=labels,
                                      linked_tests=[key[4]],confidence='MEDIUM'))
    if len(runs)>1 and not_comparable and not comparisons:
        findings.append(diagnosis('RERUN_NOT_COMPARABLE',evidence=not_comparable,confidence='HIGH'))
    return findings, comparisons
