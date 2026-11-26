"""Phase 8C.2A: manifest-only npm download + reviewed offline assembly pilot."""
import argparse,json,shutil,subprocess,sys
from pathlib import Path
from .broker import compile_inputs,build_bundle,require
from ..phase8a.planner import digest
from ..phase8a.scanner import inspect,_linked
from ..phase8b.planner import compile_dependencies,load_object
from ..phase8b.bridge import checked_file


def write(path,obj):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2,allow_nan=False);f.write('\n')


def source_inputs(root,source):
    _,recipe=compile_dependencies(root,source=source);scan=inspect(root)
    require(digest(scan.digests)==recipe['snapshot_inspection']['inspected_content_sha256'] and digest(scan.paths)==recipe['snapshot_inspection']['inventory_sha256'] and sorted(scan.issues)==recipe['snapshot_inspection']['issues'],'SOURCE_CHANGED')
    # One component only for this pilot; next/other adapter may be inventoried
    # but this path does not certify its runtime compatibility.
    rows=recipe['components'];require(len(rows)==1 and rows[0]['manager']=='npm' and not rows[0]['blockers'],'NPM_SINGLE_COMPONENT_REQUIRED')
    c=rows[0];prefix='' if c['root']=='.' else c['root']+'/'
    inputs=compile_inputs(load_object(scan.files[prefix+'package.json']),load_object(scan.files[prefix+'package-lock.json']))
    return recipe,inputs


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('prepare');a.add_argument('--repo',required=True);a.add_argument('--ref',required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--fetch',action='store_true');a.add_argument('--assemble-reviewed',action='store_true');a.add_argument('--vm-config',type=Path);a.add_argument('--node-tarball',type=Path);a.add_argument('--node-sha256')
    b=sub.add_parser('_download');b.add_argument('--input',type=Path,required=True);b.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    if args.cmd=='_download':
        try:
            raw=load_object(args.input.read_text(encoding='utf-8'));inputs=compile_inputs(raw['manifest'],raw['lock']);require(inputs==raw,'INPUT_PLAN_CHANGED');stats=build_bundle(inputs,args.out);print(json.dumps(stats));return 0
        except (Exception,KeyboardInterrupt):return 2
    if args.assemble_reviewed and (not args.fetch or not all((args.vm_config,args.node_tarball,args.node_sha256))):p.error('reviewed assembly requires --fetch and trusted VM/toolchain inputs')
    import re
    if not re.fullmatch('[0-9a-f]{40}',args.ref):p.error('exact lowercase commit SHA required')
    out=args.out.absolute()
    if out.exists() or out.is_symlink() or any(_linked(x) for x in out.parents if x.exists()):p.error('fresh unlinked output required')
    result={'schema_version':'8C2.npm.1','status':'BLOCKED','runtime_permission':False,'automatic_admission_supported':False,'application_vms_started':0,'vm_start_attempts':0,'cleanup':'NOT_STARTED','security_attestation':'NOT_ATTESTED_FOR_UNTRUSTED_STUDENTS','stage':'SOURCE_PREFLIGHT'}
    out.mkdir(parents=True,exist_ok=False)
    try:
        from ..orchestration.adapters import pinned_checkout
        from ..orchestration.contracts import validate_request,source_matches_url
        req=validate_request({'submission_id':'PHASE8C2','repository_url':args.repo,'ref':args.ref,'mode':'STATIC','max_vm_launches':0})
        with pinned_checkout(req['repository_url'],args.ref) as (source,root):
            require(source_matches_url(source,req['repository_url']) and source['commit_sha']==args.ref,'SOURCE_IDENTITY_MISMATCH');recipe,inputs=source_inputs(root,source)
        result.update(source=source,recipe_sha256=recipe['recipe_sha256'],input_plan_sha256=digest(inputs),download_count=len(inputs['downloads']),platform_skipped=inputs['platform_skipped'],status='DOWNLOAD_PLAN_READY')
        write(out/'dependency_plan.json',recipe);write(out/'download_plan.json',inputs)
        if args.fetch:
            result['stage']='REGISTRY_ACQUISITION';require(shutil.disk_usage(out).free>=500_000_000,'HOST_DISK_HEADROOM_INSUFFICIENT')
            bundle=out/'manifest-inputs.zip'
            # This subprocess invokes only our fixed downloader, never package
            # code. A parent wall deadline also bounds DNS and blocking reads.
            try:
                cp=subprocess.run([sys.executable,'-m','projectiq_executioner.phase8c2','_download','--input',str(out/'download_plan.json'),'--out',str(bundle)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=630,check=False)
                require(cp.returncode==0,'REGISTRY_ACQUISITION_FAILED');stats=load_object(cp.stdout.decode('utf-8'));result.update(stats);result['bundle']=checked_file(bundle,145_000_000);result['status']='LOCKED_ARCHIVES_ACQUIRED_NOT_INSTALLED'
            except BaseException:
                bundle.unlink(missing_ok=True)
                staging=out/'.registry-staging'
                if staging.exists() and not _linked(staging):shutil.rmtree(staging)
                raise
            if args.assemble_reviewed:
                result['stage']='REVIEWED_OFFLINE_ASSEMBLY'
                from .worker import assemble_reviewed
                result['assembly']=assemble_reviewed(source=source,bundle=bundle,out=out,vm_config=args.vm_config,node_tarball=args.node_tarball,node_sha256=args.node_sha256)
                result.update(status=result['assembly']['status'],cleanup=result['assembly']['cleanup'],vm_start_attempts=result['assembly']['vm_start_attempts'])
    except (Exception,KeyboardInterrupt) as exc:
        known={'SOURCE_IDENTITY_MISMATCH','SOURCE_CHANGED','NPM_SINGLE_COMPONENT_REQUIRED','NPM_PREFLIGHT_BLOCKED','ONLY_LOCK_V3_SUPPORTED','LOCK_RECORD_LIMIT','REGISTRY_URL_NOT_EXACT_PACKAGE','MANDATORY_PACKAGE_PLATFORM_MISMATCH','INVALID_PACKAGE_NAME','INVALID_PACKAGE_VERSION','INVALID_LOCK_PATH','REGISTRY_ACQUISITION_FAILED','NONREGISTRY_TRANSITIVE_SPEC','HOST_DISK_HEADROOM_INSUFFICIENT','EXISTING_OPERATOR_APPROVAL_REQUIRED','NODE_ASSET_MISMATCH','MANAGED_ROOT_ACL_UNVERIFIED','HOST_TOOLCHAIN_UNVERIFIED','WINDOWS_WHPX_REQUIRED'}
        result.update(status='BLOCKED_OR_FAILED',error_code=str(exc) if type(exc) is ValueError and str(exc) in known else 'STAGE_FAILED_OR_INTERRUPTED')
    result['result_sha256']=digest(result);write(out/'hardening_result.json',result)
    print('Phase 8C.2A:',result['status']);print('Stage:',result['stage'],'VM start attempts:',result['vm_start_attempts'],'Cleanup:',result['cleanup']);print('Automatic admission: BLOCKED')
    if result.get('error_code'):print('Error:',result['error_code'])
    return 0 if result['status'] in {'DOWNLOAD_PLAN_READY','LOCKED_ARCHIVES_ACQUIRED_NOT_INSTALLED','OFFLINE_CACHE_ASSEMBLED_REVIEWED_NOT_AUTHORIZED'} else 2

if __name__=='__main__':raise SystemExit(main())
