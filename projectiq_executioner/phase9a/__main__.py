"""Plan functional tests only. No VM, dependencies or runtime authorization."""
import argparse
from pathlib import Path
from .planner import plan_repository


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--ref');p.add_argument('--out',required=True,type=Path);a=p.parse_args(argv)
    try:r=plan_repository(a.repo,a.ref,a.out)
    except (Exception,KeyboardInterrupt) as exc:
        print('Phase 9A refused:',type(exc).__name__);return 2
    print('Phase 9A:',r['status']);print('Source:',r['source']['repository'],r['source']['commit_sha']);print('Summary:',r['summary']);print('VM attempts: 0; functional tests executed: 0');print('Report:',a.out/'functional_plan.json');return 0

if __name__=='__main__':raise SystemExit(main())
