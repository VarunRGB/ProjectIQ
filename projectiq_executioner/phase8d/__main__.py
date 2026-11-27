"""Phase 8D generalization: plan locally/remotely, execute only reviewed SHA."""
import argparse,json,time
from pathlib import Path
from ..phase8b.bridge import checked_file
from ..phase8b.planner import load_object
from ..phase8c_final.pypi import CompactMetadata,fetch_wheels,diagnostic_error
from ..phase8c_final.dispatch import write
from .resolver import resolve_requirements
from .dispatch import dispatch,plan_local


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('dispatch');src=a.add_mutually_exclusive_group(required=True);src.add_argument('--repo');src.add_argument('--source-dir',type=Path);a.add_argument('--ref');a.add_argument('--out',required=True,type=Path);a.add_argument('--node-version',default='v24.22.3');a.add_argument('--node-catalog',type=Path);a.add_argument('--python-version',default='3.12.3');a.add_argument('--allow-inferred',action='store_true');a.add_argument('--run-reviewed',action='store_true');a.add_argument('--legacy-reviewed',action='store_true');a.add_argument('--fetch',action='store_true');a.add_argument('--reuse-npm-bundle',type=Path);a.add_argument('--reuse-python-dir',type=Path);a.add_argument('--vm-config',type=Path);a.add_argument('--node-tarball',type=Path);a.add_argument('--node-sha256');a.add_argument('--pip-wheel',type=Path);a.add_argument('--pip-sha256');a.add_argument('--component');a.add_argument('--all-independent',action='store_true');a.add_argument('--max-vms',type=int,default=1);a.add_argument('--enable-optional-toolchains',action='store_true');a.add_argument('--require-bounded-volume',action='store_true');a.add_argument('--static-report',action='store_true')
    b=sub.add_parser('_resolve');b.add_argument('--input',required=True,type=Path);b.add_argument('--out',required=True,type=Path)
    args=p.parse_args(argv)
    stage='INPUT'
    try:
        if args.command=='_resolve':
            checked_file(args.input,32_000);obj=load_object(args.input.read_text('utf-8'));deadline=time.monotonic()+600
            requirements=obj['requirements'];version=obj['target_python']
            stage='RESOLUTION';lock=resolve_requirements(requirements,version,CompactMetadata(deadline,version))
            stage='FETCH';size=fetch_wheels(lock,args.out/'python-wheelhouse.zip',deadline)
            stage='LOCK_WRITE';write(args.out/'python-lock.json',lock)
            print(json.dumps({'wheel_count':len(lock['wheels']),'wheel_bytes':size}));return 0
        if args.source_dir:
            if any((args.ref,args.run_reviewed,args.legacy_reviewed,args.fetch,args.reuse_npm_bundle,args.reuse_python_dir,args.vm_config,args.node_tarball,args.node_sha256,args.node_catalog,args.pip_wheel,args.pip_sha256,args.component,args.all_independent,args.enable_optional_toolchains,args.require_bounded_volume,args.static_report)):p.error('local source is planning only')
            plan=plan_local(args.source_dir,args.out,node_version=args.node_version,python_version=args.python_version,allow_inferred=args.allow_inferred);print('Phase 8D:',plan['status']);result={'status':'PLANNED_NOT_EXECUTED','vm_start_attempts':0}
        else:
            options=vars(args).copy();options.pop('command');options.pop('source_dir');url=options.pop('repo');ref=options.pop('ref');out=options.pop('out');result=dispatch(url,ref,out,**options)
        print('Phase 8D:',result['status']);print('VM start attempts:',result['vm_start_attempts']);print('Automatic untrusted execution: BLOCKED')
        if result.get('error_code'):print('Error:',result['error_code'])
        return 2 if result['status']=='FAILED_OR_BLOCKED' else 0 if result['status'] in {'REVIEWED_COMPONENT_SMOKE_COMPLETE','LEGACY_REVIEWED_READINESS_VERIFIED'} else 3
    except (Exception,KeyboardInterrupt) as exc:
        if args.command=='_resolve':print(json.dumps({'error_code':diagnostic_error(exc,stage)}))
        else:print('Phase 8D refused:',type(exc).__name__)
        return 2

if __name__=='__main__':raise SystemExit(main())
