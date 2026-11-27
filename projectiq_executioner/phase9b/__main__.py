"""Compile Phase 9A proposals only; no runtime execution or approval."""
import argparse
import re
from pathlib import Path
from .compiler import compile_repository


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True)
    p.add_argument('--ref',required=True)
    p.add_argument('--from-plan',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a = p.parse_args(argv)
    try:
        r = compile_repository(a.repo,a.ref,a.from_plan,a.out)
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}',code): code = type(exc).__name__
        print('Phase 9B-A refused:',code)
        return 2
    print('Phase 9B-A:',r['status'])
    print('Source:',r['source']['repository'],r['source']['commit_sha'])
    print('Summary:',r['summary'])
    print('VM attempts: 0; functional tests executed: 0')
    print('Report:',a.out/'compiled_plan.json')
    return 0

if __name__ == '__main__': raise SystemExit(main())
