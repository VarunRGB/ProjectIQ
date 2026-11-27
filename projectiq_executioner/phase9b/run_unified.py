"""Operator-only unified evaluation: legacy static + supported local demo."""
import argparse
from pathlib import Path
from .unified import add_demo_arguments,demo_options,evaluate_project

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--ref');p.add_argument('--submission-id',default='PHASE9BD-DEMO');p.add_argument('--out',required=True,type=Path);add_demo_arguments(p);a=p.parse_args(argv)
    try:
        r=evaluate_project(a.repo,a.ref,a.out,submission_id=a.submission_id,options=demo_options(a));d=r['trusted_demo']
    except (ValueError,OSError,RuntimeError) as exc:
        print('Phase 9B-D refused:',type(exc).__name__);return 2
    print('Phase 9B-D:',d['status']);print('VM attempts:',d['vm_start_attempts'],'uncertain:',d['vm_attempt_count_uncertain']);print('Report:',a.out/'executioner_result.json')
    return 0 if d['status'] in ('FUNCTIONAL_EVIDENCE_COMPLETE','SMOKE_EVIDENCE_COMPLETE') else 3
if __name__=='__main__':raise SystemExit(main())
