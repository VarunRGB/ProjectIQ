"""Dependency planning CLI, with a separately explicit reviewed preparation path."""
from pathlib import Path
import argparse,json
from . import compile_dependencies,prepare_reviewed
from .planner import load_object

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--repo');g.add_argument('--source-dir',type=Path)
    p.add_argument('--ref');p.add_argument('--expected-plan',type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--prepare-reviewed',action='store_true');p.add_argument('--allow-preparation-network',action='store_true');p.add_argument('--vm-config',type=Path)
    p.add_argument('--node-tarball',type=Path);p.add_argument('--node-sha256');p.add_argument('--node-version',default='v24.22.3');p.add_argument('--pip-wheel',type=Path);p.add_argument('--pip-sha256')
    a=p.parse_args(argv)
    if a.ref and not a.repo:p.error('--ref requires --repo')
    if a.out.exists() or a.out.is_symlink():p.error('output must not exist')
    if a.source_dir and a.out.resolve().is_relative_to(a.source_dir.resolve()):p.error('output must be outside source')
    if a.prepare_reviewed and not (a.repo and a.ref and a.vm_config and a.allow_preparation_network):p.error('reviewed preparation requires repo, exact ref, VM config and explicit network permission')
    if not a.prepare_reviewed and (a.allow_preparation_network or a.vm_config or a.node_tarball or a.node_sha256 or a.pip_wheel or a.pip_sha256):p.error('preparation options require --prepare-reviewed')
    try:
        expected=None
        if a.expected_plan:
            if a.expected_plan.stat().st_size>16*1024*1024:raise ValueError('EXPECTED_PLAN_TOO_LARGE')
            expected=load_object(a.expected_plan.read_text(encoding='utf-8-sig'))
        if a.prepare_reviewed:
            result=prepare_reviewed(a.repo,a.ref,out=a.out,vm_config=a.vm_config,allow_network=a.allow_preparation_network,node_tarball=a.node_tarball,node_sha256=a.node_sha256,node_version=a.node_version,pip_wheel=a.pip_wheel,pip_sha256=a.pip_sha256,expected_plan=expected)
            print('Phase 8B:',result['status'])
            if result['status']!='PREPARED_REVIEWED_ARTIFACTS':
                print('Failure stage:',result['failure_stage'])
                print('Error code:',result['error_code'])
                print('Cleanup:',result['cleanup'])
                return 2
            return 0
        if a.repo:
            from ..orchestration.adapters import pinned_checkout
            from ..orchestration.contracts import validate_request,source_matches_url
            req=validate_request({'submission_id':'PHASE8B-PLAN','repository_url':a.repo,'ref':a.ref,'mode':'STATIC','max_vm_launches':0})
            with pinned_checkout(req['repository_url'],req['ref']) as (source,checkout):
                if not source_matches_url(source,req['repository_url']) or a.ref and len(a.ref)==40 and source['commit_sha']!=a.ref.lower():raise ValueError('SOURCE_IDENTITY_MISMATCH')
                plan,result=compile_dependencies(checkout,source=source,expected_plan=expected)
        else:plan,result=compile_dependencies(a.source_dir,expected_plan=expected)
        a.out.mkdir(parents=True,exist_ok=False)
        for name,obj in [('execution_plan.json',plan),('dependency_plan.json',result)]:
            with (a.out/name).open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2,ensure_ascii=True,allow_nan=False);f.write('\n')
        print('Phase 8B:',result['status'])
        for c in result['components']:print(c['root'],c['manager'],c['status'],','.join(c['blockers']))
        print('Preparation VMs: 0; application VMs: 0; no repository commands executed')
        return 3 if result['status']=='PREPARATION_BLOCKED' else 0
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,ImportError) as exc:
        print('Phase 8B refused:',str(exc) if isinstance(exc,ValueError) and str(exc).isupper() and len(str(exc))<100 else type(exc).__name__);return 2
if __name__=='__main__':raise SystemExit(main())
