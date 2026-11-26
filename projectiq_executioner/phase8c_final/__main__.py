"""Consolidated Phase 8C: restricted dependency acquisition and reviewed dispatch."""
import argparse, json, time
from pathlib import Path
from ..phase8b.planner import load_object
from ..phase8b.bridge import checked_file
from ..phase8a.planner import digest
from .dispatch import run,write
from .pypi import direct_pins,target,resolve,Metadata,fetch_wheels,SAFE_ERRORS


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('dispatch');a.add_argument('--repo',required=True);a.add_argument('--ref');a.add_argument('--out',required=True,type=Path);a.add_argument('--fetch',action='store_true');a.add_argument('--reuse-npm-bundle',type=Path);a.add_argument('--assemble-reviewed',action='store_true');a.add_argument('--run-reviewed',action='store_true');a.add_argument('--no-static',action='store_true');a.add_argument('--vm-config',type=Path);a.add_argument('--node-tarball',type=Path);a.add_argument('--node-sha256');a.add_argument('--pip-wheel',type=Path);a.add_argument('--pip-sha256');a.add_argument('--python-version',default='3.12.3');a.add_argument('--require-bounded-volume',action='store_true')
    b=sub.add_parser('inspect-storage');b.add_argument('--vm-config',type=Path,required=True);b.add_argument('--out',type=Path,required=True)
    c=sub.add_parser('_resolve');c.add_argument('--input',type=Path,required=True);c.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        if args.command=='_resolve':
            checked_file(args.input,16000);obj=load_object(args.input.read_text('utf-8'));pins=direct_pins('\n'.join(n+'=='+v for n,v in obj['pins'].items()));target(obj['target_python'])
            if pins!=obj['pins']:raise ValueError('PIN_INPUT_CHANGED')
            deadline=time.monotonic()+600;lock=resolve(pins,obj['target_python'],Metadata(deadline));size=fetch_wheels(lock,args.out/'python-wheelhouse.zip',deadline);write(args.out/'python-lock.json',lock);print(json.dumps({'wheel_count':len(lock['wheels']),'wheel_bytes':size}));return 0
        if args.command=='inspect-storage':
            from ..phase2.vm_worker.orchestrator import load_config
            from ..phase2.vm_worker.d5c_runtime_workspace import ManagedRuntimeRun
            from .storage import inspect_volume
            root=ManagedRuntimeRun(load_config(args.vm_config)).root;result=inspect_volume(root);result.update(status='OBSERVED_NOT_QUALIFIED',runtime_permission=False);result['result_sha256']=digest(result);args.out.mkdir(parents=True,exist_ok=False);write(args.out/'storage_report.json',result);print(json.dumps(result,indent=2));return 3
        result=run(args.repo,args.ref,args.out,fetch=args.fetch,assemble=args.assemble_reviewed,run_reviewed=args.run_reviewed,vm_config=args.vm_config,node_tarball=args.node_tarball,node_sha256=args.node_sha256,pip_wheel=args.pip_wheel,pip_sha256=args.pip_sha256,python_version=args.python_version,require_bounded_volume=args.require_bounded_volume,static=not args.no_static,reuse_npm_bundle=args.reuse_npm_bundle)
        print('Phase 8C consolidated:',result['status']);print('Preparation:',result['preparation']['status']);print('Runtime:',result['runtime']['status']);print('Automatic untrusted execution: BLOCKED')
        if result.get('error_code'):print('Error:',result['error_code'])
        return 3 if result['status']=='COMPLETED_WITH_LIMITATIONS' else 2
    except (Exception,KeyboardInterrupt) as exc:
        if args.command=='_resolve':
            code=str(exc) if type(exc) is ValueError and str(exc) in SAFE_ERRORS else 'PYPI_RESOLUTION_OR_FETCH_FAILED'
            print(json.dumps({'error_code':code}))
        else:print('Phase 8C refused:',type(exc).__name__)
        return 2

if __name__=='__main__':raise SystemExit(main())
