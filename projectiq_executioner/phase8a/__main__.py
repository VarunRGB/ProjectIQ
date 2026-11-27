"""Standalone planning CLI; no dependency installation or runtime execution."""
from pathlib import Path
import argparse
import json
from . import plan_directory, plan_repository


def main(argv=None):
    p=argparse.ArgumentParser(description='ProjectIQ Phase 8A — generalized advisory planning')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--repo',help='Public GitHub repository root URL')
    g.add_argument('--source-dir',type=Path,help='Trusted local snapshot directory; identity unverified')
    p.add_argument('--ref',help='GitHub ref; resolved to immutable commit by existing acquisition')
    p.add_argument('--out',type=Path,required=True,help='Fresh output directory, outside source tree')
    a=p.parse_args(argv)
    if a.ref and not a.repo:p.error('--ref requires --repo')
    if a.out.exists() or a.out.is_symlink():p.error('output already exists')
    if a.source_dir and a.out.resolve().is_relative_to(a.source_dir.resolve()):p.error('output must be outside source tree')
    try:
        result=plan_repository(a.repo,a.ref) if a.repo else plan_directory(a.source_dir)
        a.out.mkdir(parents=True,exist_ok=False)
        with (a.out/'execution_plan.json').open('x',encoding='utf-8') as f:
            json.dump(result,f,indent=2,ensure_ascii=True,allow_nan=False);f.write('\n')
        print('Phase 8A:',result['status'])
        for c in result['components']:
            print(c['root'],c['ecosystem'],','.join(c['framework_hints']),c['status'])
        print('VM launches: 0; execution authorized: false')
        print('Plan:',a.out/'execution_plan.json')
        return 0 if result['status']=='PLANNED' else 3
    except (ValueError,OSError,RuntimeError,TypeError,KeyError) as e:
        print('Planning failed:',type(e).__name__)
        return 2

if __name__=='__main__':raise SystemExit(main())
