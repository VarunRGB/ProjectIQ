"""8F-A CLI: zero VM launches; no changes to config or approvals."""
import argparse
from pathlib import Path
from ..phase8d.dispatch import fresh
from ..phase8c_final.dispatch import write
from .qualification import qualify
from .admission import plan_remote


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('qualify');a.add_argument('--vm-config',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    b=sub.add_parser('assess');b.add_argument('--repo',required=True);b.add_argument('--ref',required=True);b.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        out=fresh(args.out)
        if args.cmd=='qualify':
            report=qualify(args.vm_config); name='worker_qualification.json'; print('Phase 8F-A:',report['status'])
        else:
            report=plan_remote(args.repo,args.ref); name='admission_result.json'; print('Phase 8F-A:',report['compatibility'])
        write(out/name,report)
        print('Automatic execution: BLOCKED');print('VM attempts: 0');print('Report:',out/name)
        # 0 means diagnostic completed, never permission; 2 means tool error.
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        print('Phase 8F-A diagnostic failed:',type(exc).__name__);return 2

if __name__=='__main__':raise SystemExit(main())
