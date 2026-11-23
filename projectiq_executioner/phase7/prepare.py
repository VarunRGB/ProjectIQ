"""Generate an UNREVIEWED Phase 7 annotation template from saved Phase 6 reports.

Never auto-labels 'ground truth' from the model's prediction: human must review.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .contracts import DEFAULT_POLICY, require, BenchmarkError, load_json
from ..phase6.report import validate_report as validate_phase6


def main(argv=None):
    p=argparse.ArgumentParser(description='Prepare human-review template from saved Phase 6 JSON')
    p.add_argument('--root',required=True,type=Path,help='Folder containing Phase 6 reports')
    p.add_argument('--out',required=True,type=Path,help='New unreviewed dataset template')
    args=p.parse_args(argv)
    try:
        require(args.root.is_dir() and not args.root.is_symlink(), 'INVALID_SOURCE_ROOT')
        require(not args.out.exists() and not args.out.is_symlink() and
                not any(p.is_symlink() for p in args.out.parents), 'OUTPUT_ALREADY_EXISTS_OR_UNSAFE')
        require(args.out.parent.resolve()==args.root.resolve(), 'DRAFT_MUST_BE_IN_SOURCE_ROOT')
        files=sorted(args.root.rglob('phase6_diagnosis.json'))
        require(1 <= len(files) <= 128, 'INVALID_SOURCE_REPORT_COUNT')
        cases=[]
        for i,path in enumerate(files):
            require(path.is_file() and not path.is_symlink() and not any(x.is_symlink() for x in path.parents),
                    'UNSAFE_SOURCE_REPORT')
            report,digest,_=load_json(path)
            validate_phase6(report)
            cases.append({
                'id':f'REVIEW_{i+1:03d}', 'cohort':'INCONCLUSIVE',
                'source':report.get('source'),
                'phase6':{'path':path.relative_to(args.root).as_posix(),
                          'sha256':digest},
                'ground_truth':{
                    'reviewed':False,'reviewer_id':'REPLACE_WITH_REVIEWER_ID',
                    'reviewed_at':'YYYY-MM-DD','basis':'INDEPENDENT_REVIEW',
                    'expected_report_status':None,'expected_codes':[],
                    'expected_events':[], 'expected_comparisons':[],
                    'maintainability_rating':None,
                }
            })
        manifest={'schema_version':'0.1','stage':'phase7_benchmark_dataset',
                  'benchmark_id':'reviewed_historical_benchmark',
                  'cases':cases,'policy':dict(DEFAULT_POLICY)}
        with args.out.open('x',encoding='utf8') as f:
            json.dump(manifest,f,indent=2,sort_keys=True)
            f.write('\n')
        print('Unreviewed draft saved. DO NOT run benchmark until independent ground-truth annotation is complete.')
        print(f'Cases: {len(cases)}')
        return 0
    except (BenchmarkError,OSError,ValueError,KeyError,TypeError) as exc:
        code=exc.code if isinstance(exc,BenchmarkError) else 'INVALID_OR_UNAVAILABLE_SOURCE'
        print('Phase 7 preparation stopped safely: '+code)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
