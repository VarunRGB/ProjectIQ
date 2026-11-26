"""One sequential acceptance campaign. New source review remains operator-owned."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, tempfile
from pathlib import Path
from ..phase8a.planner import digest
from ..phase8b.bridge import checked_file, CLEAN
from ..phase8c_final.dispatch import write, source_approved
from ..phase8d import dispatch as runtime
from ..phase8d.planner import compile_runtime, SUPPORTED
from .corpus import PUBLIC, SMOKE, controls, write_tree, export_files
from .audit import require, plan_read, dispatch_read, audit_runtime, classify_error

SHA=re.compile(r'[0-9a-f]{40}\Z'); REPO=re.compile(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\Z')


def classify_plan(plan,adapter,root='.',expected_blockers=()):
    rows=[r for r in plan['components'] if r['root']==root]
    eligible=[r for r in rows if r['status']=='REVIEWED_CANDIDATE' and not r['blockers']]
    blockers=sorted({b for r in rows for b in r['blockers']})
    match=[r for r in eligible if r['adapter']==adapter]
    if expected_blockers:
        outcome='UNEXPECTED_ADMISSION' if eligible else 'EXPECTED_BLOCK' if set(expected_blockers)&set(blockers) else 'BLOCK_REASON_DIFFERENT'
    else:outcome='CANDIDATE' if len(rows)==len(match)==1 else 'GENERALIZATION_GAP'
    return {'outcome':outcome,'expected_adapter':adapter,'expected_blockers':list(expected_blockers),'root':root,'observed_adapters':sorted({r['adapter'] for r in rows if r['adapter']}),'blockers':blockers,'dependency_kind':match[0]['dependency']['kind'] if len(match)==1 else None,'optional_toolchain':match[0]['optional_toolchain'] if len(match)==1 else False}


def save_plan(root,out,source=None,**opts):
    base,plan,_=compile_runtime(root,source=source,**opts);out.mkdir(parents=True,exist_ok=False);write(out/'execution_plan.json',base);write(out/'runtime_plan.json',plan);return plan_read(out,source)


def exact_source(repo,sha):
    m=REPO.fullmatch(repo);require(m and '..' not in repo and isinstance(sha,str) and SHA.fullmatch(sha),'EXACT_GITHUB_IDENTITY_REQUIRED')
    return {'kind':'github','repository':m[1],'commit_sha':sha}


def verify_fixture_tree(root):
    expected=export_files();actual={}
    for p in Path(root).rglob('*'):
        if p.is_dir() and not p.is_symlink():continue
        rel=p.relative_to(root).as_posix();require(rel in expected,'FIXTURE_UNEXPECTED_FILE')
        actual[rel]=checked_file(p,100_000)['sha256']
    require(actual=={k:hashlib.sha256(v.encode('utf-8')).hexdigest() for k,v in expected.items()},'FIXTURE_CONTENT_MISMATCH')


def acquire_plan(repo,sha,dest,*,fixture=False,**opts):
    from ..orchestration.adapters import pinned_checkout
    expected=exact_source(repo,sha)
    with pinned_checkout(repo,sha) as (source,root):
        require(source==expected,'PINNED_SOURCE_MISMATCH')
        if fixture:verify_fixture_tree(root)
        return save_plan(root,dest,source,**opts)


def assert_no_qemu():
    require(os.name=='nt','WINDOWS_REVIEWED_WORKER_REQUIRED')
    # Fixed local query; fail closed if the process inventory cannot be checked.
    p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',"$ErrorActionPreference='Stop'; if (Get-Process -Name 'qemu*' -ErrorAction SilentlyContinue) { 'PRESENT' } else { 'CLEAR' }"],capture_output=True,text=True,timeout=15,check=False)
    require(p.returncode==0 and p.stdout.strip()=='CLEAR','QEMU_PROCESS_PRESENT_OR_UNVERIFIED')


def summarize(record):
    local=record['local_controls'];public=record['public_cases'];fixture=record['fixture_cases'];live=record['live_cases']
    adapters=sorted(SUPPORTED)
    coverage=[]
    for a in adapters:
        coverage.append({'adapter':a,'synthetic_plan_pass':any(x['expected_adapter']==a and x['outcome']=='CANDIDATE' for x in local),'public_candidate_count':sum(x['expected_adapter']==a and x['outcome']=='CANDIDATE' for x in public),'operator_fixture_candidate_count':sum(x['expected_adapter']==a and x['outcome']=='CANDIDATE' for x in fixture),'fresh_reviewed_smoke_count':sum(x.get('adapter')==a and x['status']=='REVIEWED_SMOKE_VERIFIED' for x in live)})
    return {'adapter_types':len(adapters),'synthetic_adapter_types_planned':sum(x['synthetic_plan_pass'] for x in coverage),'synthetic_controls_passed':sum(x['outcome'] in {'CANDIDATE','EXPECTED_BLOCK'} for x in local),'synthetic_controls_total':len(local),'public_candidates':sum(x['outcome']=='CANDIDATE' for x in public),'public_expected_blocks':sum(x['outcome']=='EXPECTED_BLOCK' for x in public),'public_gaps_or_unavailable':sum(x['outcome'] not in {'CANDIDATE','EXPECTED_BLOCK'} for x in public),'new_live_adapter_types':sum(x['fresh_reviewed_smoke_count']>0 for x in coverage),'new_live_smoke_passes':sum(x['status']=='REVIEWED_SMOKE_VERIFIED' for x in live),'live_attempts_observed':sum(x.get('vm_attempts') or 0 for x in live),'live_attempt_count_uncertain':any(x.get('vm_attempts') is None for x in live),'functional_tests_executed':0,'automatic_untrusted_execution':False,'coverage':coverage}


def markdown(record):
    s=record['summary'];lines=['# ProjectIQ Phase 8E — Generalization acceptance','',f"Status: **{record['status']}**",'',f"Synthetic planning: {s['synthetic_controls_passed']}/{s['synthetic_controls_total']} controls; {s['synthetic_adapter_types_planned']}/{s['adapter_types']} adapter types.",f"New reviewed smoke: {s['new_live_smoke_passes']} cases across {s['new_live_adapter_types']} adapter types.",'','| Adapter | Synthetic plan | Public candidates | Operator fixture candidates | Fresh live passes |','|---|---:|---:|---:|---:|']
    for r in s['coverage']:lines.append(f"| {r['adapter']} | {'yes' if r['synthetic_plan_pass'] else 'no'} | {r['public_candidate_count']} | {r['operator_fixture_candidate_count']} | {r['fresh_reviewed_smoke_count']} |")
    lines+=['','## Public repository outcomes','','| Case | Expected adapter | Outcome | Blockers |','|---|---|---|---|']
    for r in record['public_cases']:lines.append(f"| {r['id']} | {r['expected_adapter']} | {r['outcome']} | {', '.join(r.get('blockers',[])) or '—'} |")
    lines+=['','## Live outcomes','']
    for r in record['live_cases']:lines.append(f"- {r['id']}: {r['status']} — {r.get('failure_category','scoped readiness/CLI smoke')}")
    lines+=['','## Limits','',*['- '+v for v in record['limitations']]]
    if record.get('error_code'):lines+=['',f"Campaign stop: `{record['error_code']}`"]
    return '\n'.join(lines)+'\n'


def campaign(out,*,local_only=False,skip_public=False,fixture_repo=None,fixture_ref=None,live_cases=(),vm_config=None,max_vms=4,fetch=False,node_tarball=None,node_sha256=None,pip_wheel=None,pip_sha256=None,node_version='v24.22.3',python_version='3.12.3',enable_optional_toolchains=False,require_bounded_volume=False):
    live_cases=list(live_cases);require(type(max_vms) is int and 1<=max_vms<=8 and len(live_cases)==len(set(live_cases))<=max_vms,'CAMPAIGN_VM_BUDGET_INVALID')
    require(not live_cases or vm_config is not None,'LIVE_REQUIRES_OPERATOR_CONFIG');require(not local_only or not any((live_cases,fixture_repo,fixture_ref,vm_config)),'LOCAL_ONLY_HAS_NO_LIVE_OR_REMOTE_OPTIONS')
    require(bool(fixture_repo)==bool(fixture_ref),'FIXTURE_REPO_AND_REF_REQUIRED')
    if fixture_repo:exact_source(fixture_repo,fixture_ref)
    require(not fetch or bool(live_cases),'FETCH_REQUIRES_SELECTED_LIVE_CASES')
    out=runtime.fresh(out);rec={'schema_version':'8E.1','status':'CAMPAIGN_INCOMPLETE','local_controls':[],'public_cases':[],'fixture_cases':[],'live_cases':[],'negative_control':None,'requested_live_cases':live_cases,'vm_budget':max_vms,'runtime_permission':False,'automatic_untrusted_execution':False,'limitations':['Synthetic planning fixtures are not real package installations; their fake npm digests are never used for runtime.','Public repository outcomes are observed only after fresh exact-SHA acquisition.','Reviewed smoke is HTTP 2xx or CLI exit zero, not functional correctness or rubric credit.','No automatic approvals, dedicated untrusted worker qualification or global disk hard quota.','A blocked or unavailable repository is not a failed student project.','No retries; first unexpected live failure stops further launches.']}
    candidates={};stage='LOCAL_CONTROL';opts={'node_version':node_version,'python_version':python_version}
    try:
        with tempfile.TemporaryDirectory(prefix='piq-8e-controls-') as tmp:
            for name,adapter,files,blockers,inf in controls():
                source=Path(tmp)/name;write_tree(source,files);dest=out/'local'/name
                plan=save_plan(source,dest,allow_inferred=inf);row=classify_plan(plan,adapter,expected_blockers=blockers);row.update(id=name,scope='SYNTHETIC_PLAN_ONLY',plan_sha256=plan['plan_sha256']);rec['local_controls'].append(row)
            # Test simultaneous discovery, not service communication.
            p=Path(tmp)/'multi';write_tree(p,export_files());plan=save_plan(p,out/'local'/'multi',allow_inferred=True)
            rec['multi_component_control']={'expected_components':len(SMOKE),'observed_components':len(plan['components']),'composition':plan['composition'],'all_expected_roots':all(classify_plan(plan,a,k)['outcome']=='CANDIDATE' for k,(a,_) in SMOKE.items()),'plan_sha256':plan['plan_sha256']}
        require(all(x['outcome'] in {'CANDIDATE','EXPECTED_BLOCK'} for x in rec['local_controls']) and rec['multi_component_control']['all_expected_roots'] and rec['multi_component_control']['observed_components']==len(SMOKE) and summarize(rec)['synthetic_adapter_types_planned']==len(SUPPORTED),'LOCAL_CONTROL_REGRESSION')
        if not (local_only or skip_public):
            stage='PUBLIC_PLANNING'
            for key,repo,sha,adapter,blockers,description in PUBLIC:
                url='https://github.com/'+repo;source=exact_source(url,sha);dest=out/'public'/key
                try:
                    plan=acquire_plan(url,sha,dest,**opts);row=classify_plan(plan,adapter,expected_blockers=blockers);row['plan_sha256']=plan['plan_sha256']
                    if row['outcome']=='CANDIDATE':candidates[key]=(source,row,False)
                except (Exception,KeyboardInterrupt) as e:
                    if isinstance(e,KeyboardInterrupt):raise
                    row={'outcome':'SOURCE_OR_PLAN_UNAVAILABLE','expected_adapter':adapter,'root':'.','expected_blockers':list(blockers),'blockers':[],'error_code':'PINNED_ACQUISITION_OR_PLANNING_FAILED'}
                row.update(id=key,scope='PINNED_PUBLIC_SOURCE',source=source,description=description);rec['public_cases'].append(row)
        if fixture_repo:
            stage='OPERATOR_FIXTURE_PLANNING';source=exact_source(fixture_repo,fixture_ref);plan=acquire_plan(fixture_repo,fixture_ref,out/'fixture',fixture=True,allow_inferred=True,**opts)
            require(len(plan['components'])==len(SMOKE),'FIXTURE_COMPONENT_INVENTORY_MISMATCH')
            for key,(adapter,_) in SMOKE.items():
                row=classify_plan(plan,adapter,key);row.update(id='fixture-'+key,scope='OPERATOR_CREATED_FIXTURE',source=source,plan_sha256=plan['plan_sha256']);rec['fixture_cases'].append(row)
                if row['outcome']=='CANDIDATE':candidates[row['id']]=(source,row,True)
            require(all(x['outcome']=='CANDIDATE' for x in rec['fixture_cases']),'FIXTURE_PLAN_REGRESSION')
        require(not any(x['outcome']=='UNEXPECTED_ADMISSION' for x in rec['public_cases']),'NEGATIVE_CONTROL_UNEXPECTED_ADMISSION')
        if vm_config:
            stage='APPROVAL_PREFLIGHT';cfg=checked_file(vm_config,100_000)
            # Only check exact source membership; never modify approvals.
            rec['eligible_for_operator_review']=[{'id':k,'source':s,'root':r['root'],'already_approved':source_approved(s,vm_config)} for k,(s,r,_) in sorted(candidates.items())]
            # New public negative control, not a redundant previous VM run.
            neg=next((x for x in rec['public_cases'] if x['outcome']=='CANDIDATE' and not source_approved(x['source'],vm_config)),None)
            if neg:
                s=neg['source'];dest=out/'refusal';require(checked_file(vm_config,100_000)==cfg,'CONFIG_CHANGED')
                runtime.dispatch('https://github.com/'+s['repository'],s['commit_sha'],dest,run_reviewed=True,vm_config=vm_config,component='__8e_negative_never_exists__',**opts)
                _,d=dispatch_read(dest,s);require(d['status']=='FAILED_OR_BLOCKED' and d.get('error_code')=='EXISTING_OPERATOR_APPROVAL_REQUIRED' and d.get('exact_sha_approved') is False and d['vm_start_attempts']==0 and d.get('components')==[],'UNAPPROVED_REFUSAL_UNVERIFIED')
                rec['negative_control']={'source':s,'status':'UNAPPROVED_REFUSAL_VERIFIED','vm_attempts':0,'dispatch_sha256':d['result_sha256']}
            else:rec['negative_control']={'status':'NOT_RUN_NO_UNAPPROVED_PUBLIC_CANDIDATE'}
        if live_cases:
            stage='LIVE_PREFLIGHT';require(all(k in candidates for k in live_cases),'SELECTED_CASE_NOT_ELIGIBLE')
            require(all(source_approved(candidates[k][0],vm_config) for k in live_cases),'EXISTING_OPERATOR_APPROVAL_REQUIRED')
            rows=[candidates[k][1] for k in live_cases]
            require(enable_optional_toolchains or not any(r['optional_toolchain'] for r in rows),'OPTIONAL_TOOLCHAIN_OPT_IN_REQUIRED')
            require(fetch or not any(r['dependency_kind']!='NONE' for r in rows),'EXPLICIT_FETCH_REQUIRED')
            if any(r['expected_adapter'].startswith('NODE_') for r in rows):require(node_tarball and Path(node_tarball).name==f'node-{node_version}-linux-x64.tar.xz' and checked_file(node_tarball,65_000_000)['sha256']==node_sha256,'NODE_ASSET_MISMATCH')
            if any(r['dependency_kind']=='PYTHON' for r in rows):require(pip_wheel and Path(pip_wheel).name=='pip-25.2-py3-none-any.whl' and checked_file(pip_wheel,5_000_000)['sha256']==pip_sha256,'PIP_ASSET_MISMATCH')
            require(checked_file(vm_config,100_000)==cfg,'CONFIG_CHANGED');assert_no_qemu()
            for k in live_cases:
                stage='LIVE_'+k;source,row,inf=candidates[k];dest=out/'live'/k;attempt={'id':k,'source':source,'root':row['root'],'adapter':row['expected_adapter'],'status':'RUNTIME_UNVERIFIED','vm_attempts':None};rec['live_cases'].append(attempt)
                require(checked_file(vm_config,100_000)==cfg and source_approved(source,vm_config),'CONFIG_CHANGED');assert_no_qemu()
                try:
                    runtime.dispatch('https://github.com/'+source['repository'],source['commit_sha'],dest,run_reviewed=True,vm_config=vm_config,component=row['root'],max_vms=1,fetch=row['dependency_kind']!='NONE',allow_inferred=inf,enable_optional_toolchains=enable_optional_toolchains,require_bounded_volume=require_bounded_volume,node_tarball=node_tarball,node_sha256=node_sha256,pip_wheel=pip_wheel,pip_sha256=pip_sha256,**opts)
                    plan,d=dispatch_read(dest,source);attempt['vm_attempts']=d['vm_start_attempts'];require(plan['plan_sha256']==row['plan_sha256'],'FRESH_PLAN_CHANGED')
                    if d['status']!='REVIEWED_COMPONENT_SMOKE_COMPLETE':
                        code=d.get('error_code');refs=d.get('components',[])
                        if len(refs)==1 and isinstance(refs[0].get('id'),str) and re.fullmatch(r'component-[0-9a-f]{16}',refs[0]['id']):
                            from .audit import read
                            w=read(dest/refs[0]['id']/'runtime_result.json','result_sha256')
                            if w.get('source')==source and w.get('result_sha256')==refs[0].get('result_sha256'):code=w.get('error_code') or code;attempt['cleanup']=w.get('cleanup')
                        attempt['failure_category']=classify_error(code);attempt['failure_code']=code if isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}',code) else 'UNCLASSIFIED';attempt['status']='REVIEWED_RUN_BLOCKED_OR_FAILED';require(False,'LIVE_CASE_NOT_VERIFIED')
                    attempt.update(audit_runtime(dest,source,row['root'],row['expected_adapter'],require_artifacts=True))
                    assert_no_qemu()
                except BaseException:
                    # Never resume automatically after uncertain cleanup or report failure.
                    if attempt['status']=='REVIEWED_SMOKE_VERIFIED':attempt['status']='RUNTIME_UNVERIFIED'
                    raise
        rec['status']='REVIEWED_SELECTED_CAMPAIGN_COMPLETE' if live_cases else 'PLANNING_CAMPAIGN_COMPLETE'
        if any(x['outcome'] not in {'CANDIDATE','EXPECTED_BLOCK'} for x in rec['public_cases']):rec['status']+='_WITH_GAPS'
    except (Exception,KeyboardInterrupt) as exc:
        rec['status']='STOPPED_ON_GATE';rec['failed_stage']=stage
        code=str(exc);rec['error_code']=code if type(exc) is ValueError and re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}',code) else 'STAGE_FAILED_OR_INTERRUPTED'
    rec['summary']=summarize(rec);rec['campaign_sha256']=digest(rec);write(out/'campaign_result.json',rec);(out/'coverage.md').write_text(markdown(rec),encoding='utf-8');return rec
