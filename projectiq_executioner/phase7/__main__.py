"""Phase 7 CLI — reads saved trusted JSON; no repositories/VMs are executed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .contracts import BenchmarkError, load_json, require
from .engine import run
from .report import markdown


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline Phase 7 independent ground-truth benchmark')
    parser.add_argument('--manifest',type=Path,required=True,help='Reviewed Phase 7 benchmark manifest')
    parser.add_argument('--out',type=Path,required=True,help='New directory; existing paths refused')
    parser.add_argument('--check-determinism',action='store_true',help='Re-evaluate and compare canonical outputs')
    args=parser.parse_args(argv)
    try:
        require(not args.manifest.is_symlink() and not any(p.is_symlink() for p in args.manifest.parents),
                'UNSAFE_MANIFEST_PATH')
        require(not args.out.exists() and not args.out.is_symlink() and
                not any(p.is_symlink() for p in args.out.parents),'OUTPUT_ALREADY_EXISTS_OR_UNSAFE')
        dataset,manifest_sha,_=load_json(args.manifest)
        result=run(dataset,args.manifest.parent)
        result['manifest_sha256']=manifest_sha
        result['deterministic_replay']='NOT_REQUESTED'
        if args.check_determinism:
            second=run(dataset,args.manifest.parent)
            require(second=={k:v for k,v in result.items() if k not in ('manifest_sha256','deterministic_replay')},
                    'DETERMINISM_MISMATCH')
            result['deterministic_replay']='MATCHED'
        md=markdown(result)
        require(not args.out.exists() and not args.out.is_symlink(), 'OUTPUT_ALREADY_EXISTS_OR_UNSAFE')
        args.out.mkdir(parents=True,exist_ok=False)
        with (args.out/'phase7_benchmark.json').open('x',encoding='utf-8') as f:
            json.dump(result,f,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)
            f.write('\n')
        with (args.out/'phase7_benchmark.md').open('x',encoding='utf-8') as f:
            f.write(md)
    except BenchmarkError as exc:
        print('Phase 7 stopped safely: '+exc.code,file=sys.stderr)
        return 2
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Phase 7 stopped safely: INTERNAL_OR_IO_ERROR',file=sys.stderr)
        return 2
    print('ProjectIQ Phase 7 — Offline Reliability Benchmark')
    print('  Status:       '+result['status'])
    print('  Evaluated:    '+str(result['metrics']['evaluated']))
    print('  Rejected:     '+str(result['metrics']['rejected']))
    print('  False passes: '+str(result['metrics']['false_passes']))
    print('  VM launches:  0 (NO EXECUTION)')
    print('  JSON:         '+str(args.out/'phase7_benchmark.json'))
    print('  Markdown:     '+str(args.out/'phase7_benchmark.md'))
    return 0 if result['status']=='PASS' else 3 if result['status']=='LIMITED' else 4

if __name__=='__main__':
    raise SystemExit(main())
