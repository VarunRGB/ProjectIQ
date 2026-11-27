"""Local trusted demo: run independent tests and one source-bound CRUD lifecycle."""
import argparse
from pathlib import Path
from .execution import run_functional


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True);p.add_argument('--ref',required=True)
    p.add_argument('--from-compiled',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--vm-config',type=Path,required=True);p.add_argument('--profile',type=Path);p.add_argument('--component')
    p.add_argument('--trust-demo-source',action='store_true',required=True)
    p.add_argument('--confirm-disposable-state',action='store_true',required=True,help='Operator confirms no external side effects and all state is disposable.')
    p.add_argument('--require-bounded-volume',action='store_true');a=p.parse_args(argv)
    try:r=run_functional(a.repo,a.ref,a.from_compiled,a.out,trust_demo_source=a.trust_demo_source,vm_config=a.vm_config,profile=a.profile,component=a.component,require_bounded_volume=a.require_bounded_volume,adaptive=True,confirm_disposable_state=a.confirm_disposable_state)
    except (Exception,KeyboardInterrupt) as exc:print('Phase 9B-C refused:',type(exc).__name__);return 2
    print('Phase 9B-C:',r['status']);print('VM attempts:',r['vm_start_attempts'],'uncertain:',r['vm_attempt_count_uncertain']);print('Independent tests:',r['summary']);print('Workflow:',r['workflow_status'],'requests attempted:',r['workflow_requests_attempted']);print('Report:',a.out/'functional_result.json')
    if r.get('error_code'):print('Error:',r['error_code'],'Dispatch:',r.get('dispatch_error_code'))
    return 0 if r['status']=='FUNCTIONAL_EVIDENCE_COMPLETE' and r['workflow_status']=='PASS' and r['summary']['PASS']==r['functional_tests_executed'] and not any(r['summary'][k] for k in ('FAIL','INCONCLUSIVE','NOT_RUN')) else 2

if __name__=='__main__':raise SystemExit(main())
