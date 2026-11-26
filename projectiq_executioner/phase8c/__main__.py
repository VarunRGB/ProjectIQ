"""Assess admission without running a student's code or changing VM policy."""
from pathlib import Path
import argparse,json
from . import assess_directory,assess_repository,inspect_worker
from ..phase8b.bridge import checked_file


def _write(path,obj):
    with path.open('x',encoding='utf-8') as f:
        json.dump(obj,f,indent=2,ensure_ascii=True,allow_nan=False);f.write('\n')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    audit=sub.add_parser('inspect-worker',help='Read-only worker audit by default')
    audit.add_argument('--vm-config',type=Path,required=True)
    audit.add_argument('--out',type=Path,required=True)
    audit.add_argument('--run-clean-vms',action='store_true',help='Opt in to up to three existing source-free D5d probes, stopping on first failure')
    audit.add_argument('--qemu-sha256',help='Previously independently verified operator QEMU pin; not a self-attestation')
    a=sub.add_parser('assess',help='Fresh pinned source, dependencies, optional prepared artifact receipt')
    g=a.add_mutually_exclusive_group(required=True);g.add_argument('--repo');g.add_argument('--source-dir',type=Path)
    a.add_argument('--ref');a.add_argument('--vm-config',type=Path);a.add_argument('--preparation-dir',type=Path)
    a.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():p.error('output must not exist')
    if args.command=='assess':
        if args.source_dir and (args.ref or args.vm_config or args.preparation_dir):p.error('local source assessment cannot assert reviewed approval or artifact provenance')
        if args.source_dir and args.out.resolve().is_relative_to(args.source_dir.resolve()):p.error('output must be outside source')
        if args.preparation_dir and args.out.resolve().is_relative_to(args.preparation_dir.resolve()):p.error('output must be outside prepared evidence')
    elif args.run_clean_vms and not args.qemu_sha256:p.error('live probes require an independently verified operator QEMU pin')
    try:
        if args.command=='inspect-worker':
            result=inspect_worker(args.vm_config,run_clean_vms=args.run_clean_vms,qemu_sha256=args.qemu_sha256)
            args.out.mkdir(parents=True,exist_ok=False);_write(args.out/'worker_report.json',result)
            print('Phase 8C worker:',result['status']);print('Qualification:',result.get('qualification_status','UNVERIFIED'))
            for k,v in result['checks'].items():print(k+':',v)
            print('Automatic admission: BLOCKED; clean probe attempts:',result['clean_probe_attempts'])
            return 3  # current worker is never automatically admitted
        approved=();worker=None
        if args.vm_config:
            from ..phase2.vm_worker.orchestrator import load_config
            before=checked_file(args.vm_config,100_000)
            approved=tuple(load_config(args.vm_config)['approved_snapshots'])
            worker=inspect_worker(args.vm_config)
            if checked_file(args.vm_config,100_000)!=before:raise ValueError('CONFIG_CHANGED')
        if args.repo:
            plan,recipe,result=assess_repository(args.repo,args.ref,approved=approved,worker=worker,preparation_dir=args.preparation_dir)
        else:plan,recipe,result=assess_directory(args.source_dir)
        args.out.mkdir(parents=True,exist_ok=False)
        for name,obj in [('execution_plan.json',plan),('dependency_plan.json',recipe),('admission_assessment.json',result)]:_write(args.out/name,obj)
        print('Phase 8C:',result['status'])
        print('Adapter:',result['adapter_candidate'])
        print('Preparation:',result['automatic_preparation']['status'])
        print('Runtime:',result['automatic_runtime']['status'])
        print('Reviewed route:',result['reviewed_route']['status'])
        print('Artifacts:',result['artifact_evidence']['status'])
        print('Preparation VMs: 0; application VMs: 0; runtime permission: false')
        return 3
    except (Exception,KeyboardInterrupt) as exc:
        print('Phase 8C assessment refused:',type(exc).__name__);return 2

if __name__=='__main__':raise SystemExit(main())
