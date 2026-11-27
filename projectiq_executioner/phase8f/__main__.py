"""8F: non-authorizing assessment and explicit local trusted demo."""
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
    c=sub.add_parser('configure-demo-assets',help='Record previously verified local Node and pip asset hashes')
    c.add_argument('--vm-config',type=Path,required=True);c.add_argument('--node-tarball',type=Path,required=True);c.add_argument('--node-sha256',required=True);c.add_argument('--pip-wheel',type=Path,required=True);c.add_argument('--pip-sha256',required=True)
    d=sub.add_parser('demo',help='Local operator-trusted repositories only; never untrusted submissions')
    d.add_argument('--repo',dest='url',required=True);d.add_argument('--ref');d.add_argument('--out',type=Path,required=True)
    d.add_argument('--trust-demo-source',action='store_true',required=True);d.add_argument('--vm-config',type=Path,required=True)
    selection=d.add_mutually_exclusive_group();selection.add_argument('--component');selection.add_argument('--all-independent',action='store_true')
    d.add_argument('--max-vms',type=int,default=1);d.add_argument('--node-version',default='v24.22.3');d.add_argument('--node-catalog',type=Path)
    d.add_argument('--node-tarball',type=Path);d.add_argument('--node-sha256');d.add_argument('--pip-wheel',type=Path);d.add_argument('--pip-sha256')
    d.add_argument('--python-version',default='3.12.3');d.add_argument('--allow-inferred',action='store_true');d.add_argument('--enable-optional-toolchains',action='store_true');d.add_argument('--require-bounded-volume',action='store_true')
    d.add_argument('--profile',type=Path);d.add_argument('--require-unapproved',action='store_true');d.add_argument('--expected-dependency',choices=['NONE','NPM','PYTHON'])
    d.add_argument('--with-static',action='store_true');d.add_argument('--submission-id',default='PHASE8F-DEMO')
    args=p.parse_args(argv)
    if args.cmd=='configure-demo-assets':
        from .profile import NAME,configure
        from ..phase2.vm_worker.orchestrator import load_config
        from ..phase8b.bridge import checked_file
        try:
            cfg=args.vm_config.absolute();checked_file(cfg,100_000);load_config(cfg)
            path=cfg.parent/NAME
            configure(path,node_tarball=args.node_tarball,node_sha256=args.node_sha256,pip_wheel=args.pip_wheel,pip_sha256=args.pip_sha256)
            print('Demo asset profile verified:',path);return 0
        except (Exception,KeyboardInterrupt) as exc:
            print('Asset configuration refused:',type(exc).__name__);return 2
    if args.cmd=='demo':
        from .demo import run_demo
        options=vars(args).copy();options.pop('cmd')
        combined=options.pop('with_static');submission_id=options.pop('submission_id')
        try:
            if combined:
                from .integration import evaluate_trusted_demo
                report=evaluate_trusted_demo(**options,submission_id=submission_id)
                print('Phase 8F-D:',report['status']);print('VM attempts:',report['vm_start_attempts'],'uncertain:',report['vm_attempt_count_uncertain']);print('Report:',args.out/'integrated_result.json')
                return 0 if report['status']=='DEMO_AND_STATIC_COMPLETE' else 2
            report=run_demo(**options)
            print('Phase 8F-B:',report['status']);print('Mode: OPERATOR_TRUSTED_LOCAL_DEMO')
            print('VM attempts:',report['vm_start_attempts'],'uncertain:',report['vm_attempt_count_uncertain'])
            print('Automatic untrusted execution: BLOCKED')
            print('Temporary approval cleanup:',report['temporary_approval_cleanup'])
            if report.get('error_code'):print('Error:',report['error_code'])
            print('Report:',args.out/'demo_result.json')
            return 0 if report['status']=='DEMO_REVIEWED_SMOKE_COMPLETE' else 2
        except (Exception,KeyboardInterrupt) as exc:
            print('Phase 8F-B refused:',type(exc).__name__);return 2
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
