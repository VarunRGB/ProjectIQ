"""Deterministic, capability-aware one-call ProjectIQ Executioner.

The three modes are STATIC, REVIEWED, and RESUME. A RESUME revalidates pinned
static evidence and may reuse a previously verified, cleaned, exact-catalog
functional result. Phase 7 is a release benchmark, never a submission stage.

There is no LLM, shell, dependency installer, implicit catalog approval, or
arbitrary VM command selection in this module.
"""
from __future__ import annotations

import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import adapters, runners
from .contracts import (ATTESTATION,CLEAN,EXECUTED,VM_COST,OrchestrationError,
                        canonical_hash,no_symlinks,read_json,reject,source_matches_url,
                        validate_request,validate_source,verified_run_shape,write_json)
from .report import render_markdown
from .matrix import build_test_case_matrix
from .review_errors import review_error_codes
from .coverage import route_inventory, plan_review_hints, bounded_observations, workflow_metrics

# Independent VM workflows; this is a preference ranking, not a dependency
# chain. Each reviewed workflow starts and cleans its own VM context.
VM_PROOF_FIELDS = {'readiness':'phase4_status','api':'phase4c_status',
    'auth':'phase4d2_status','db_fixture':'phase4d1_status',
    'db_postconditions':'phase4d3_status', 'dependent_workflow':'phase4da_status',
    'crud':'phase4db_status','browser_ui':'phase4dc_status',
    'browser_login':'phase4dd_status'}
ORDER = ('api','browser_ui','auth','db_postconditions','dependent_workflow',
         'crud','browser_login','db_fixture','scenario_isolation','readiness')
_MAX_ARTIFACT_BYTES=64_000_000

@dataclass(frozen=True)
class OperatorContext:
    """Operator-owned capabilities never sourced from the student's GitHub repo."""
    vm_config: Path|None = None
    review_root: Path|None = None
    review_registry: Path|None = None
    work_root: Path|None = None


def _stage(result: dict, name: str, status: str, reason: str|None=None) -> None:
    result['workflow'].append({'stage':name,'status':status,'reason':reason})


def _reason(exc: BaseException) -> str:
    # No exception message or hostname/student file content in final output.
    if isinstance(exc,OrchestrationError):
        return exc.code
    if type(exc).__module__=='projectiq_executioner.phase6.contracts' and type(exc).__name__=='EvidenceError':
        code=getattr(exc,'code','')
        if isinstance(code,str) and code and len(code)<=90 and code.replace('_','').isalnum() and code.isupper():return code
    return 'STAGE_'+type(exc).__name__.upper()[:38]


def _check_report_path(path: Path, base: Path) -> None:
    if not no_symlinks(path) or not path.is_relative_to(base):
        reject('UNSAFE_ARTIFACT_PATH')
    if not path.is_file() or not 0 < path.stat().st_size <= _MAX_ARTIFACT_BYTES:
        reject('ARTIFACT_UNAVAILABLE_OR_TOO_LARGE')


def _digest_file(path: Path, root: Path) -> dict:
    _check_report_path(path,root)
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(65536):
            h.update(chunk)
    return {'sha256':h.hexdigest(),'bytes':path.stat().st_size}


def _collect_artifacts(out: Path) -> dict:
    """Hash outputs, but never include operator inputs or raw student checkout."""
    artifacts={}
    for folder in ('phase1','phase2','phase3','phase4','phase5','phase6'):
        p=out/folder
        if not p.exists():continue
        if not no_symlinks(p):reject('UNSAFE_ARTIFACT_PATH')
        for file in sorted(p.rglob('*')):
            if file.is_symlink():reject('UNSAFE_ARTIFACT_PATH')
            if not file.is_file():continue
            rel=file.relative_to(out).as_posix()
            if len(artifacts)>=250:
                reject('ARTIFACT_RECORD_LIMIT')
            artifacts[rel]=_digest_file(file,out)
    return artifacts


def _fresh_dir(out: Path) -> None:
    if not no_symlinks(out):reject('UNSAFE_OUTPUT_PATH')
    # Must be a new directory for each call. Outputs are immutable evidence.
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        reject('OUTPUT_ALREADY_EXISTS')


def _result(request: dict) -> dict:
    return {'schema_version':'0.1','stage':'executioner_evaluation','submission_id':request['submission_id'],
            'mode':request['mode'],'effective_mode':request.get('resume_target') or request['mode'],
            'status':'NOT_STARTED','source':None,'security_attestation':ATTESTATION,
            'phase7':'NOT_RUN_PER_SUBMISSION',
            'workflow':[],'summary':{},'project_intelligence':{},
            'test_case_matrix':{},
            'runtime_plan':{},'test_plan':{},'suites':[],
            'code_quality':{},'diagnostics':{},'recommendations':[],
            'limitations':[],
            'review_requests':[],'artifacts':{},'error_codes':[],
            'vm_attempt_budget':request['max_vm_launches'], 'vm_attempts_scheduled':0}


def _limit(result: dict, code: str) -> None:
    if code not in result['limitations']:
        result['limitations'].append(code)


def _review(result: dict, code: str, stage: str, *, source: dict|None = None) -> None:
    item={'type':code,'stage':stage}
    if source:
        item['review_identity']=source['repository']+'@'+source['commit_sha']
    if item not in result['review_requests']:
        result['review_requests'].append(item)


def _receipt(path: Path, out: Path, label: str) -> dict:
    return {'id':label,'role':label.split('_')[0],'sha256':_digest_file(path,out)['sha256'],
            'bytes':path.stat().st_size}


def _static_snapshot(result: dict, manifest: dict, candidate: dict|None,
                     capabilities: dict|None, plan:dict|None, quality:dict|None) -> None:
    """Bound summaries; detailed validated reports are kept separately."""
    result['project_intelligence']={
        'project_name':manifest.get('project_name'),
        'architecture':manifest.get('architecture'),
        'architecture_confidence':manifest.get('architecture_confidence'),
        'languages':manifest.get('languages',{}),
        'technologies':[{k:t.get(k) for k in ('name','category','confidence')}
                        for t in manifest.get('technologies',[])[:150] if type(t) is dict],
        'services':[{k:x.get(k) for k in ('name','kind','technology','root','confidence')}
                    for x in manifest.get('services',[])[:100] if type(x) is dict],
        'routes_discovered':len(manifest.get('routes',[])),
        'routes':[{k:x.get(k) for k in ('path','method','kind','confidence')}
                  for x in manifest.get('routes',[])[:400] if type(x) is dict],
        'features_discovered':len(manifest.get('features',[])),
        'features':[{k:x.get(k) for k in ('name','origin','confidence')}
                    for x in manifest.get('features',[])[:250] if type(x) is dict],
        'existing_tests':len(manifest.get('tests',[])),
        'test_artifacts':[{k:x.get(k) for k in ('path','kind','framework')}
                          for x in manifest.get('tests',[])[:200] if type(x) is dict],
        'setup_files':manifest.get('setup_files',[])[:200],
        'database_schema_files':manifest.get('database_schema_files',[])[:200],
        'environment_variable_names':manifest.get('environment_variables',[])[:150],
        'inventory':{k:manifest.get('inventory',{}).get(k) for k in ('indexed_files','file_role_counts')},
        'findings':[{k:f.get(k) for k in ('code','severity','message')}
                    for f in manifest.get('findings',[])[:100] if type(f) is dict],
        'unknowns':manifest.get('unknowns',[])[:100],
    }
    inventory=route_inventory(manifest)
    result['project_intelligence'].update(inventory)
    result['project_intelligence']['findings']=[{k:f.get(k) for k in ('code','severity','message','evidence')}
        for f in manifest.get('findings',[])[:100] if type(f) is dict]
    techs=[t.get('name') for t in result['project_intelligence']['technologies']
           if type(t.get('name')) is str][:8]
    features=[f.get('name') for f in result['project_intelligence']['features']
              if type(f.get('name')) is str][:6]
    result['project_intelligence']['overview']=(
        'Static inspection suggests a '+str(manifest.get('architecture','unclassified'))+
        ' project using '+(', '.join(techs) if techs else 'unclassified technologies')+
        '. Identified feature candidates: '+(', '.join(features) if features else 'none confidently detected')+
        '. These are source-derived observations, not verified user-visible behavior.')
    result['runtime_plan']={
        'adapter':candidate.get('adapter') if candidate else None,
        'status':candidate.get('status') if candidate else 'NOT_AVAILABLE',
        'capability_status':capabilities.get('status') if capabilities else None,
        'execution_authorized':'NOT_BY_PLANNING',
    }
    if plan:
        summary=plan['summary']
        result['test_plan']={'status':plan['status'],'summary':summary,
                             'issues':[x.get('code') for x in plan.get('issues',[])],
                             'cases':[{'id':c['id'],'category':c['category'],
                                       'title':c['title'],'status':c['status'],
                                       'target':c.get('target'),
                                       'oracle':c.get('oracle'),
                                       'assertions':c.get('assertions',[])}
                                      for c in plan.get('test_cases',[])],
                             'trust':'TEST_SPECIFICATIONS_NOT_EXECUTION'}
    if plan:
        result['test_plan']['review_candidates']=plan_review_hints(plan,inventory,manifest)
    if quality:
        result['code_quality']={'status':quality['status'],'summary':quality['summary'],
                                'coverage':quality['coverage'], 'quality':quality['quality'],
                                'security':{'status':quality['security']['status'],
                                            'findings':quality['security']['findings']},
                                'findings':quality['findings'],
                                'functions':quality['functions'],
                                'duplications':quality['duplications'],
                                'limitations':quality['limitations'],
                                'score_is_grade':False}
        # Raw Phase 5 v0.3 evidence remains intact for existing consumers. The
        # public aggregate must not call an incomplete score overall quality.
        q=dict(result['code_quality']['quality'])
        if q.get('status')!='PROVISIONAL':
            q['measured_dimensions_indicator']=q.get('score')
            q['score']=None
            q['overall_score_status']='WITHHELD_LIMITED_COVERAGE'
        result['code_quality']['quality']=q


def _restore_static(prior_path: Path, out: Path, source: dict) -> tuple[dict,dict,dict,dict,dict,dict]:
    """Revalidate immutable evidence fingerprints and contracts; no network."""
    prior_path=Path(prior_path).absolute()
    if not no_symlinks(prior_path): reject('UNSAFE_RESUME_PATH')
    old=read_json(prior_path,limit=8_000_000)
    if (old.get('schema_version')!='0.1' or old.get('stage')!='executioner_evaluation'
            or old.get('source')!=source or old.get('status')=='SECURITY_HOLD'):
        reject('UNTRUSTED_RESUME_RESULT')
    meta=old.get('artifacts')
    if type(meta) is not dict:
        reject('INVALID_RESUME_ARTIFACT_INDEX')
    base=prior_path.parent
    files=['phase1/manifest.json','phase1/manifest.md','phase2/phase2c_candidate.json',
           'phase3/test_plan.json','phase5/code_quality.json','phase5/code_quality.md']
    required=['phase1/manifest.json','phase2/phase2c_candidate.json',
              'phase3/test_plan.json','phase5/code_quality.json']
    for rel in required:
        entry=meta.get(rel)
        if type(entry) is not dict or type(entry.get('sha256')) is not str:
            reject('MISSING_RESUME_STATIC_ARTIFACT')
        origin=base.joinpath(*rel.split('/'))
        if _digest_file(origin,base)!=entry:
            reject('RESUME_ARTIFACT_CHANGED')
    manifest=read_json(base/'phase1'/'manifest.json')
    candidate=read_json(base/'phase2'/'phase2c_candidate.json')
    plan=read_json(base/'phase3'/'test_plan.json')
    quality=read_json(base/'phase5'/'code_quality.json')
    if (validate_source(manifest.get('source'))!=source or
            validate_source(candidate.get('source'))!=source or
            validate_source(plan.get('source'))!=source or
            validate_source(quality.get('source'))!=source):
        reject('RESUME_SOURCE_MISMATCH')
    from projectiq_executioner.phase3.contracts import validate_test_plan
    from projectiq_executioner.phase5.contracts import validate_report
    validate_test_plan(plan)
    validate_report(quality)
    for rel in files:
        if rel in meta:
            origin=base.joinpath(*rel.split('/'))
            if _digest_file(origin,base)!=meta[rel]:reject('RESUME_ARTIFACT_CHANGED')
            target=out.joinpath(*rel.split('/'))
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(origin,target)
    # Recompute Phase 2 plans and check the adapter/candidate against prior.
    recalculated, caps=adapters.compile_phase2(manifest,out/'phase2_verified')
    if recalculated!=candidate:reject('RESUME_CANDIDATE_CHANGED')
    # Move recomputed plan reports to canonical location without overwriting.
    for p in sorted((out/'phase2_verified').iterdir()):
        dest=out/'phase2'/p.name
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.move(str(p),str(dest))
        else:p.unlink()
    (out/'phase2_verified').rmdir()
    return old,manifest,candidate,caps,plan,quality


def _resume_source(request: dict) -> dict:
    old=read_json(Path(request['resume_from']),limit=8_000_000)
    if old.get('submission_id') != request['submission_id']:
        reject('RESUME_SUBMISSION_ID_MISMATCH')
    source=validate_source(old.get('source'))
    if not source_matches_url(source,request['repository_url']):
        reject('RESUME_REPOSITORY_MISMATCH')
    if request['ref'] is not None and request['ref']!=source['commit_sha']:
        reject('RESUME_COMMIT_MISMATCH')
    return source


def _candidate_valid(candidate: dict|None, source:dict) -> bool:
    return (type(candidate) is dict and candidate.get('source')==source
            and candidate.get('status')=='CANDIDATE_NEEDS_VM_VERIFICATION'
            and type(candidate.get('adapter')) is str)


def _priority_suite_specs(request: dict, plan: dict|None, *, approved:bool) -> list[dict]:
    specs=list(request['suites'])
    ready=bool(plan and plan.get('summary',{}).get('ready_for_phase4',0)>0)
    want=request['run_readiness']
    if want=='YES' or (want=='AUTO' and not specs and ready):
        specs.append({'kind':'readiness','catalogs':{}})
    return sorted(specs,key=lambda item:ORDER.index(item['kind']))


def _verify_vm_provenance(vm: dict, source: dict, adapter: str) -> bool:
    return (vm.get('source')==source and vm.get('adapter')==adapter and
            vm.get('security_attestation')==ATTESTATION and
            vm.get('backend')=='QEMU_DISPOSABLE_QCOW2_VM')


def _vm_evidence(kind: str, dir: Path, report: dict, source: dict, adapter: str) -> tuple[dict|None,bool]:
    """Return VM report and hold flag; never infer cleanup from a PASS label."""
    _,vmfile=runners.output_files(kind)
    if kind=='scenario_isolation':
        scenarios=report.get('scenarios')
        if type(scenarios) is not list or not scenarios:
            return None,True
        if report.get('status')!='SCENARIO_ISOLATION_VERIFIED' or len(scenarios)!=2:
            return None,True
        leases=set()
        for entry in scenarios:
            if (type(entry) is not dict or entry.get('status')!='RESET_VERIFIED'
                    or entry.get('cleanup')!=CLEAN):return None,True
            path=(entry.get('artifacts') or {}).get('vm')
            if (type(path) is not str or not path.startswith('scenarios/')
                    or '..' in path.split('/')):return None,True
            try: vm=read_json(dir.joinpath(*path.split('/')),limit=200_000)
            except (OSError,ValueError):return None,True
            if (not _verify_vm_provenance(vm,source,adapter) or vm.get('cleanup')!=CLEAN
                    or vm.get('execution')!=EXECUTED
                    or vm.get('managed_runtime')!='SIGNED_LEASED_WORKSPACE_REMOVED'
                    or vm.get('phase4d3_status')!='VALIDATED'):
                return None,True
            lease=vm.get('managed_run_lease_id') or vm.get('runtime_lease_id')
            # The underlying D4 validator already checks signed unique leases;
            # do not invent a second lease-name contract here.
            if lease is not None:
                if lease in leases:return None,True
                leases.add(lease)
        return {'source':source,'adapter':adapter,'backend':'QEMU_DISPOSABLE_QCOW2_VM',
                'security_attestation':ATTESTATION,'cleanup':CLEAN,'execution':EXECUTED,
                'scenario_vms':len(scenarios)},False
    if vmfile is None:
        return None,True
    p=dir/vmfile
    if not p.is_file():
        if report.get('execution')=='NOT_PERFORMED' and report.get('cleanup')=='NOT_STARTED':
            return None,False
        return None,True
    try:
        vm=read_json(p,limit=8_000_000)
    except (OrchestrationError,OSError,ValueError):
        return None,True
    if not _verify_vm_provenance(vm,source,adapter):
        # A non-executed denied VM may not have a complete provenance witness.
        if vm.get('execution')=='NOT_PERFORMED' and vm.get('cleanup')=='NOT_STARTED':
            return None,False
        return None,True
    if vm.get('execution')==EXECUTED and (vm.get('cleanup')!=CLEAN
                                         or vm.get('status')!='STARTUP_VERIFIED'
                                         or vm.get(VM_PROOF_FIELDS[kind])!='VALIDATED'):
        return vm,True
    if vm.get('execution') not in ('NOT_PERFORMED',EXECUTED) and vm.get('cleanup')!=CLEAN:
        return vm,True
    if report.get('execution')!=vm.get('execution') or report.get('cleanup')!=vm.get('cleanup'):
        return vm,True
    return vm,False


def _summary_suites(entries: list[dict]) -> dict:
    counts={}
    for x in entries:counts[x['state']]=counts.get(x['state'],0)+1
    return {'suite_count':len(entries),'states':dict(sorted(counts.items())),
            'verified_passes':sum(s['state']=='PASS' and s.get('vm_verified',False) for s in entries),
            'observed_failures':sum(s['state']=='FAIL' for s in entries),
            'unverified':sum(not s.get('vm_verified',False) for s in entries)}


def _normalized_state(kind: str, r: dict, vm: dict|None) -> str:
    status=r['status']
    if status.endswith('INCONCLUSIVE'):return 'INCONCLUSIVE'
    if status in ('NO_RUNNABLE_TESTS','BLOCKED'):return 'BLOCKED'
    if status.endswith('NOT_VERIFIED') or status in ('GUEST_RESULTS_NOT_VERIFIED','VM_EXECUTION_NOT_VERIFIED'):
        return 'NOT_VERIFIED'
    if status.endswith('FAILED'):return 'FAIL'
    if status.endswith('PASSED') or status in ('FIXTURES_SEEDED','SCENARIO_ISOLATION_VERIFIED'):
        return 'PASS' if kind=='scenario_isolation' or vm is not None else 'NOT_VERIFIED'
    return 'NOT_VERIFIED'


def _suite_fingerprint(kind: str, catalogs: dict) -> str:
    return canonical_hash({'kind':kind,'catalogs':{k:canonical_hash(v) for k,v in sorted(catalogs.items())}})


def _reuse_suite(prior:dict|None, kind:str, fingerprint:str, out:Path, source:dict,
                 adapter:str) -> tuple[dict|None,dict|None]:
    if not prior or prior.get('status')=='SECURITY_HOLD':return None,None
    base=Path(prior['_result_path']).parent
    meta=prior.get('artifacts',{})
    for ent in prior.get('suites',[]):
        if (ent.get('kind')!=kind or ent.get('fingerprint')!=fingerprint
                or ent.get('state') not in ('PASS','FAIL','INCONCLUSIVE')
                or not ent.get('vm_verified') or ent.get('cleanup')!=CLEAN):
            continue
        prefix='phase4/'+kind+'/'
        files={p:v for p,v in meta.items() if p.startswith(prefix)}
        if not files:return None,None
        for rel,receipt in files.items():
            if not rel.startswith(prefix) or type(receipt) is not dict:return None,None
            src=base.joinpath(*rel.split('/'))
            try:
                if _digest_file(src,base)!=receipt:return None,None
            except (OrchestrationError,OSError):return None,None
        directory=out/'phase4'/kind
        directory.mkdir(parents=True,exist_ok=True)
        for rel in sorted(files):
            target=out.joinpath(*rel.split('/'))
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(base.joinpath(*rel.split('/')),target)
        runfile,vmfile=runners.output_files(kind)
        report=read_json(directory/runfile)
        vm=read_json(directory/vmfile) if vmfile else None
        if (not verified_run_shape(kind,report,source,adapter) or
            not vm or not _verify_vm_provenance(vm,source,adapter)
                or vm.get('status')!='STARTUP_VERIFIED'
                or vm.get(VM_PROOF_FIELDS[kind])!='VALIDATED'
                or vm.get('execution')!=EXECUTED or vm.get('cleanup')!=CLEAN):
            reject('REUSED_RUN_EVIDENCE_INVALID')
        return report,vm
    return None,None


def evaluate(request: Mapping[str,Any], *, output_dir:Path,
             operator:OperatorContext|None=None) -> dict:
    """Synchronous main-orchestrator API; returns full structured result.

    Never silently promotes a test into an approved oracle or a VM into an
    attested sandbox. Errors become bounded evidence statuses rather than
    unhandled student-controlled exception text.
    """
    req=validate_request(request)
    out=Path(output_dir).absolute()
    op=operator or OperatorContext()
    _fresh_dir(out)
    result=_result(req)
    plan=quality=manifest=candidate=capabilities=None
    phase6_runs=[]; receipts=[];prior=None
    try:
        if req['mode']=='RESUME':
            source=_resume_source(req)
            prior,manifest,candidate,capabilities,plan,quality=_restore_static(
                Path(req['resume_from']),out,source)
            prior['_result_path']=str(Path(req['resume_from']).absolute())
            result['source']=source
            _stage(result,'source_snapshot','REUSED_VERIFIED','PINNED_SOURCE_FROM_PRIOR_RUN')
            _stage(result,'static_planning','REUSED_VERIFIED')
        else:
            with adapters.pinned_checkout(req['repository_url'],req['ref']) as (source,checkout):
                result['source']=source
                if not source_matches_url(source,req['repository_url']):
                    reject('REPOSITORY_IDENTITY_MISMATCH')
                _stage(result,'source_snapshot','PASS')
                manifest=adapters.inspect_source(checkout,source,out/'phase1')
                _stage(result,'project_intelligence','PASS')
                # Phase 5 reads only inert source, and can run while Phase 2/3
                # compile validated plans. No runtime VM is launched here.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    quality_task=pool.submit(adapters.analyze_quality,checkout,source,out/'phase5')
                    try:
                        candidate,capabilities=adapters.compile_phase2(manifest,out/'phase2')
                        if not _candidate_valid(candidate,source):
                            _limit(result,'RUNTIME_ADAPTER_UNSUPPORTED_OR_UNVERIFIED')
                        _stage(result,'runtime_capabilities','PASS')
                        plan=adapters.compile_phase3(manifest,candidate,checkout,out/'phase3')
                        if validate_source(plan.get('source'))!=source:
                            reject('PHASE3_SOURCE_MISMATCH')
                        _stage(result,'test_discovery','PASS')
                    except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                        result['error_codes'].append('PLANNING_'+_reason(exc))
                        _stage(result,'runtime_capabilities','FAILED','CAPABILITY_PLANNING_UNAVAILABLE')
                        # Phase 3 can still provide an inert discovery inventory
                        # without any Phase 2 candidate. Do not abandon it.
                        try:
                            plan=adapters.compile_phase3(manifest,None,checkout,out/'phase3')
                            if validate_source(plan.get('source'))!=source:
                                reject('PHASE3_SOURCE_MISMATCH')
                            _stage(result,'test_discovery','PARTIAL','RUNTIME_ORACLE_UNAVAILABLE')
                        except (ValueError,TypeError,KeyError,OSError,RuntimeError):
                            _stage(result,'test_discovery','FAILED','PLANNING_UNAVAILABLE')
                    try:
                        quality=quality_task.result()
                        if validate_source(quality.get('source'))!=source:
                            reject('PHASE5_SOURCE_MISMATCH')
                        _stage(result,'code_quality','PASS')
                    except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                        result['error_codes'].append('QUALITY_'+_reason(exc))
                        _stage(result,'code_quality','FAILED','STATIC_QUALITY_UNAVAILABLE')
        if manifest is None:
            reject('MISSING_SOURCE_MANIFEST')
        _static_snapshot(result,manifest,candidate,capabilities,plan,quality)
        source=result['source']
        requested=req['resume_target'] if req['mode']=='RESUME' else req['mode']
        if requested=='STATIC':
            _stage(result,'functional_testing','SKIPPED','STATIC_MODE_NO_VM')
            _limit(result,'FUNCTIONAL_BEHAVIOR_NOT_EXECUTED')
        else:
            _run_reviewed(req,op,out,result,plan,candidate,source,prior,phase6_runs)
        # Phase 6 accepts only trusted normalized reports. It must not override
        # a Phase 4 security hold or turn hypotheses into student defects.
        if plan is not None or quality is not None or phase6_runs:
            try:
                if plan is not None:receipts.append(_receipt(out/'phase3'/'test_plan.json',out,'PLAN'))
                if quality is not None:receipts.append(_receipt(out/'phase5'/'code_quality.json',out,'QUALITY'))
                for index,(report,label,vm) in enumerate(phase6_runs):
                    # Phase 4 run schema cannot be mutated to attach extra fields.
                    # Identify its saved artifact by validated label -> suite mapping.
                    matching=[x for x in result['suites'] if x.get('run_label')==label]
                    if matching:
                        path=out.joinpath(*matching[0]['report_path'].split('/'))
                        if path.is_file():receipts.append(_receipt(path,out,label))
                d=adapters.diagnose(plan=plan,runs=phase6_runs,quality=quality,
                                    receipts=receipts,dest=out/'phase6')
                result['diagnostics']={k:d.get(k) for k in ('status','summary','events','diagnoses',
                    'dependency_groups','rerun_comparisons','root_cause_hypotheses',
                    'static_context','security_context','limitations')}
                _stage(result,'failure_diagnosis','PASS')
            except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                result['error_codes'].append('DIAGNOSIS_'+_reason(exc))
                _limit(result,'PHASE6_DIAGNOSIS_UNAVAILABLE')
                _stage(result,'failure_diagnosis','FAILED','INVALID_OR_UNAVAILABLE_EVIDENCE')
        else:
            _stage(result,'failure_diagnosis','SKIPPED','NO_EVIDENCE_AVAILABLE')
        # Coverage is not success: a clean readiness smoke test does NOT prove
        # that unreviewed routes, unsupported tests or incomplete static scans
        # have been evaluated. Preserve these gaps at top level.
        if plan is not None:
            ps=plan.get('summary') or {}
            if ps.get('needs_oracle',0)>0:
                _limit(result,'FUNCTIONAL_ORACLES_STILL_REQUIRED')
            if ps.get('unsupported_by_runner',0)>0:
                _limit(result,'TEST_TARGETS_UNSUPPORTED_BY_RUNNER')
        if quality is not None and (quality.get('status')!='ANALYZED' or
               (quality.get('quality') or {}).get('status')!='PROVISIONAL'):
            _limit(result,'STATIC_QUALITY_COVERAGE_OR_CONFIDENCE_LIMITED')
        if result['diagnostics'].get('status')=='SECURITY_HOLD':
            result['status']='SECURITY_HOLD'
        elif result['diagnostics'].get('status')=='PARTIAL':
            _limit(result,'DIAGNOSIS_EVIDENCE_PARTIAL')
        _finalize_status(result,requested)
        result['recommendations']=_recommendations(result)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        result['error_codes'].append('ORCHESTRATION_'+_reason(exc))
        if result['status']!='SECURITY_HOLD':result['status']='FAILED'
        _stage(result,'orchestration','FAILED','SAFE_STOP')
    # Persist even partially successful reports for main orchestrator analysis.
    # Reconcile planned specifications and observed test IDs conservatively.
    # A missing or unverified VM witness can never become a passed test.
    result['test_case_matrix']=build_test_case_matrix(result.get('test_plan'),result['suites'],
                                                      effective_mode=result.get('effective_mode'))
    result['summary']={'suites':_summary_suites(result['suites']),
                       'test_case_counts':result['test_case_matrix']['counts'],
                       'reviewed_check_counts':result['test_case_matrix']['reviewed_check_counts'],
                       'source_pinned':result['source'] is not None,
                       'has_project_intelligence':bool(result['project_intelligence']),
                       'has_test_plan':bool(result['test_plan']),
                       'has_code_quality':bool(result['code_quality']),
                       'has_diagnosis':bool(result['diagnostics']),
                       'vm_attempts_scheduled_this_call':result['vm_attempts_scheduled'],
                       'verified_current_suites':sum(s.get('vm_verified') is True and not s.get('reused_from_prior') for s in result['suites'])}
    try:
        result['artifacts']=_collect_artifacts(out)
        report_path=out/'executioner_report.md'
        with report_path.open('x',encoding='utf-8',newline='\n') as f:
            f.write(render_markdown(result))
        result['report_markdown']='executioner_report.md'
        write_json(out/'executioner_result.json',result)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        # If writing failed, never claim that a normal report exists.
        raise OrchestrationError('PERSISTENCE_FAILED') from None
    return result


def _operator_suite_registry(op:OperatorContext,source:dict) -> list[dict]:
    """Exact SHA to independently approved catalog-file mapping.

    A registry entry does NOT approve any catalog. Every listed file is
    recompiled against the current operator VM config before execution.
    """
    if op.review_registry is None:return []
    obj=read_json(Path(op.review_registry),limit=256_000)
    profiles=obj.get('profiles')
    if obj.get('schema_version')!='0.1' or type(profiles) is not list or len(profiles)>128:
        reject('INVALID_OPERATOR_REVIEW_REGISTRY')
    matching=[]
    seen=set()
    for p in profiles:
        if type(p) is not dict or set(p)!={'repository','commit_sha','suites'}:
            reject('INVALID_OPERATOR_REVIEW_PROFILE')
        identity=(p['repository'],p['commit_sha'])
        if identity in seen:reject('DUPLICATE_OPERATOR_REVIEW_PROFILE')
        seen.add(identity)
        if identity==(source['repository'],source['commit_sha']):
            # Reuse the public strict suite schema; the registry is a mapping,
            # not a privileged mechanism for inventing extra runner types.
            probe=validate_request({'submission_id':'registry-review-check',
                 'repository_url':'https://github.com/'+source['repository'],
                 'ref':source['commit_sha'],'mode':'REVIEWED','suites':p['suites']})
            matching=probe['suites']
    return matching


def _run_reviewed(req:dict,op:OperatorContext,out:Path,result:dict,
                  plan:dict|None,candidate:dict|None,source:dict,prior:dict|None,
                  phase6_runs:list) -> None:
    # An operator registry enables a true single-argument main-orchestrator
    # call. The registry merely selects candidates; all approvals are checked
    # later by the original reviewed runner compilers.
    if not req['suites'] and op.review_registry is not None:
        try:
            registry_suites=_operator_suite_registry(op,source)
            if registry_suites:req={**req,'suites':registry_suites}
        except (OrchestrationError,OSError,TypeError,KeyError,ValueError):
            _review(result,'INVALID_OPERATOR_REVIEW_REGISTRY','functional_testing',source=source)
            _stage(result,'functional_testing','BLOCKED','REVIEW_REGISTRY_INVALID')
            return
    specs=_priority_suite_specs(req,plan,approved=False)
    if not _candidate_valid(candidate,source) or plan is None:
        _review(result,'SUPPORTED_ADAPTER_AND_PLAN_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','RUNTIME_PLAN_NOT_READY')
        return
    if op.vm_config is None:
        _review(result,'OPERATOR_VM_CONFIG_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','NO_OPERATOR_POLICY')
        return
    if op.review_root is None and op.review_registry is None and any(x['catalogs'] for x in specs):
        _review(result,'OPERATOR_REVIEW_ROOT_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','CATALOG_ROOT_MISSING')
        return
    if req['mode']=='REVIEWED' and (not req['ref'] or len(req['ref'])!=40
                                    or req['ref'].lower()!=source['commit_sha']):
        _review(result,'FULL_PINNED_SHA_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','EXACT_SHA_REQUIRED')
        return
    try:
        config=adapters.operator_vm_config(Path(op.vm_config))
    except (OSError,ValueError,TypeError,KeyError):
        _review(result,'INVALID_OPERATOR_VM_CONFIG','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','VM_CONFIG_INVALID')
        return
    identity=source['repository']+'@'+source['commit_sha']
    if identity not in config.get('approved_snapshots',[]):
        _review(result,'EXACT_SHA_OPERATOR_APPROVAL_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','SNAPSHOT_NOT_APPROVED')
        return
    if not specs:
        _review(result,'REVIEWED_TEST_CATALOGS_OR_ORACLES_REQUIRED','functional_testing',source=source)
        _stage(result,'functional_testing','BLOCKED','NO_REVIEWED_TESTS')
        return
    # Preflight ALL declared suites before any VM: typoed or unsupported
    # expectations never trigger accidental execution. Keep each independent.
    preflight=[]
    for spec in specs:
        kind=spec['kind']
        item={'kind':kind,'catalogs':{},'fingerprint':None}
        try:
            root=op.review_root or (Path(op.review_registry).absolute().parent
                                    if op.review_registry is not None else out)
            cats=runners.load_catalogs(spec,root)
            item['catalogs']=cats
            item['fingerprint']=_suite_fingerprint(kind,cats)
            check=runners.preflight(kind,plan=plan,candidate=candidate,config=config,catalogs=cats)
            if check['approved']:
                item['ready']=True
                item['scenario_links']=check.get('scenario_links',[])
            else:
                item.update(ready=False,reason=check['code'])
        except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
            codes=review_error_codes(exc)
            item.update(ready=False,reason=codes[0] if codes else 'CATALOG_REVIEW_INVALID_OR_UNAPPROVED',
                        review_error_codes=codes)
        preflight.append(item)
    cost=0
    for item in preflight:
        kind=item['kind']
        if result['status']=='SECURITY_HOLD':
            result['suites'].append({'kind':kind,'state':'BLOCKED','reason':'SECURITY_HOLD_ACTIVE',
                                     'approved_scenario_links':item.get('scenario_links',[]),
                                     'vm_verified':False,'fingerprint':item['fingerprint']})
            continue
        if not item.get('ready'):
            result['suites'].append({'kind':kind,'state':'REVIEW_REQUIRED',
                                     'reason':item.get('reason','REVIEW_MISSING'),
                                     'review_error_codes':item.get('review_error_codes',[]),
                                     'execution_state':'NOT_EXECUTED',
                                     'fingerprint':item['fingerprint'],'vm_verified':False})
            _review(result,'CATALOG_APPROVAL_OR_ORACLE_REQUIRED',kind,source=source)
            continue
        dir=out/'phase4'/kind
        try:
            restored_vm=None; reused=False
            if req['mode']=='RESUME':
                # Reuse is a zero-VM operation. Validate exact fingerprint,
                # report hashes, source, and guest proof BEFORE spending budget.
                report,restored_vm=_reuse_suite(prior,kind,item['fingerprint'],out,source,candidate['adapter'])
                reused=report is not None
            else:report=None
            if not reused:
                if cost+VM_COST[kind]>req['max_vm_launches']:
                    result['suites'].append({'kind':kind,'state':'BLOCKED',
                        'reason':'VM_ATTEMPT_BUDGET','fingerprint':item['fingerprint'],
                        'approved_scenario_links':item.get('scenario_links',[]),
                        'vm_verified':False})
                    _limit(result,'VM_BUDGET_EXHAUSTED')
                    continue
                # Runners own their fresh output directory (several reject an
                # already-existing directory even if empty). Refuse unsafe
                # destinations before reserving VM budget or calling a runner.
                if dir.exists() or not no_symlinks(dir):
                    result['suites'].append({'kind':kind,'state':'BLOCKED',
                        'reason':'OUTPUT_DESTINATION_NOT_FRESH','vm_verified':False,
                        'execution_state':'NOT_INVOKED','fingerprint':item['fingerprint']})
                    _review(result,'OUTPUT_DESTINATION_NOT_FRESH',kind,source=source)
                    continue
                # Each Phase 4 runner is responsible for its own reviewed
                # guest-only execution and independently verified cleanup.
                cost+=VM_COST[kind]
                result['vm_attempts_scheduled']=cost
                report=runners.invoke(kind,plan=plan,candidate=candidate,config=config,
                       catalogs=item['catalogs'],output_dir=dir,work_root=op.work_root)
            if not verified_run_shape(kind,report,source,candidate['adapter']):
                result['status']='SECURITY_HOLD'
                result['suites'].append({'kind':kind,'state':'INVALID_EVIDENCE',
                     'reason':'RUN_CONTRACT_OR_PROVENANCE_MISMATCH',
                     'fingerprint':item['fingerprint'],'vm_verified':False})
                _limit(result,'RUN_REPORT_PROVENANCE_INVALID')
                continue
            if reused:vm,hold=restored_vm,False
            else:vm,hold=_vm_evidence(kind,dir,report,source,candidate['adapter'])
            # A denied pre-execution VM is a setup blocker, not student failure
            # and not an attempted guest execution. Expose only a fixed safe
            # reason code, never raw worker output or operator configuration.
            vm_preflight_reason=None
            if (type(vm) is dict and vm.get('status')=='DENIED'
                    and vm.get('execution')=='NOT_PERFORMED'
                    and vm.get('reason')=='QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED'):
                vm_preflight_reason='QEMU_TOOLS_OR_IMMUTABLE_BASE_IMAGE_NOT_VERIFIED'
                _limit(result,'VM_HOST_TOOLCHAIN_OR_IMAGE_NOT_VERIFIED')
                _review(result,'VERIFY_VM_HOST_TOOLS_AND_PINNED_IMAGE',
                        'functional_testing',source=source)
            if hold:
                result['status']='SECURITY_HOLD'
                _limit(result,'VM_CLEANUP_OR_PROVENANCE_NOT_VERIFIED')
            state=_normalized_state(kind,report,vm)
            # A decisive result without an execution witness is never PASS/FAIL.
            vm_verified=bool(not hold and vm and vm.get('execution')==EXECUTED
                             and vm.get('cleanup')==CLEAN
                             and (kind=='scenario_isolation' or
                                  (vm.get('status')=='STARTUP_VERIFIED'
                                   and vm.get(VM_PROOF_FIELDS[kind])=='VALIDATED'))
                             and _verify_vm_provenance(vm,source,candidate['adapter']))
            if state in ('PASS','FAIL') and not vm_verified:
                result['status']='SECURITY_HOLD'
                state='INVALID_EVIDENCE'
                _limit(result,'DECISIVE_RESULT_LACKS_VM_PROOF')
            # Export bounded outcome metadata, not raw HTTP bodies, cookies,
            # submitted credentials, or diagnostic logs. The complete validated
            # source report stays available by artifact hash.
            observations=bounded_observations(kind,report,item.get('scenario_links',[])) if vm_verified else []
            entry={'kind':kind,'state':state,'result_status':report.get('status'),
                   'cleanup':report.get('cleanup'),'vm_verified':vm_verified,
                   'reused_from_prior':reused,'fingerprint':item['fingerprint'],
                   'vm_preflight_reason':vm_preflight_reason,
                   'report_path':'phase4/'+kind+'/'+runners.output_files(kind)[0],
                   'vm_path':('phase4/'+kind+'/'+runners.output_files(kind)[1])
                              if runners.output_files(kind)[1] else None,
                   'execution_state':'REUSED_VERIFIED' if reused else 'VERIFIED_EXECUTION' if vm_verified else
                       'NOT_EXECUTED' if report.get('execution')=='NOT_PERFORMED' else 'EXECUTION_NOT_VERIFIED',
                   'approved_scenario_links':item.get('scenario_links',[]),
                   'observed_metrics':workflow_metrics(kind,report) if vm_verified else {},
                   'result_count':len(observations),
                   'summary':{k:v for k,v in (report.get('summary') or {}).items()
                               if type(v) in (str,int,bool)},
                   'observations':observations}
            result['suites'].append(entry)
            if kind!='scenario_isolation':
                # Unrecognized stage shapes become explicitly unsupported in
                # Phase 6 rather than guessed as application defects.
                label='RUN'+str(len(phase6_runs)+1).zfill(3)
                entry['run_label']=label
                phase6_runs.append((report,label,vm))
            if hold:
                _review(result,'VM_SECURITY_HOLD_REQUIRES_OPERATOR_INVESTIGATION',kind,source=source)
        except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
            # A runner may have launched a VM before throwing; do not continue
            # unless its trusted cleanup witness is independently available.
            result['status']='SECURITY_HOLD'
            result['suites'].append({'kind':kind,'state':'INVALID_EVIDENCE',
                 'reason':'RUNNER_INTERRUPTED_OR_EVIDENCE_REJECTED',
                 'execution_state':'EXECUTION_OR_CLEANUP_UNKNOWN',
                 'error_category':_reason(exc),
                 'fingerprint':item['fingerprint'],'vm_verified':False})
            _limit(result,'VM_WORKER_EXCEPTION_CLEANUP_UNCONFIRMED')
            _review(result,'VM_SECURITY_HOLD_REQUIRES_OPERATOR_INVESTIGATION',kind,source=source)
    # Planning/approval success is not a functional PASS. In particular,
    # a denied VM has not tested anything, even if a runner returned a report.
    if not any(x.get('vm_verified') for x in result['suites']):
        _limit(result,'FUNCTIONAL_BEHAVIOR_NOT_EXECUTED')
    all_verified=bool(result['suites']) and all(
        x.get('vm_verified') and x.get('state') in ('PASS','FAIL')
        for x in result['suites'])
    _stage(result,'functional_testing','SECURITY_HOLD' if result['status']=='SECURITY_HOLD'
           else 'PASS' if all_verified and not result['review_requests'] else 'PARTIAL')


def _recommendations(result:dict) -> list[dict]:
    """Only grounded, previously issued actions; no invented LLM advice."""
    items=[];seen=set()
    for d in result.get('diagnostics',{}).get('diagnoses',[])[:200]:
        if type(d) is not dict:continue
        code=d.get('code');action=d.get('recommended_action')
        if (type(code) is not str or type(action) is not str or code in seen):continue
        seen.add(code)
        items.append({'source':'PHASE6','code':code,'priority':d.get('priority'),
             'recommendation':action,'evidence':d.get('evidence',[]),
             'causal_status':d.get('causal_status','UNPROVEN')})
    findings=result.get('code_quality',{}).get('findings',[])
    ranks={'HIGH':0,'MEDIUM':1,'LOW':2,'ADVISORY':3}
    for f in sorted(findings,key=lambda x:ranks.get(x.get('severity'),4))[:150]:
        if type(f) is not dict:continue
        code=f.get('code');path=f.get('path');action=f.get('recommendation')
        if type(code) is not str or type(path) is not str or type(action) is not str:continue
        k=('PHASE5',code) if code=='TRAILING_WHITESPACE' else ('PHASE5',code,path,f.get('line'))
        if k in seen:continue
        seen.add(k)
        items.append({'source':'PHASE5','code':code,'priority':f.get('severity'),
             'recommendation':action,'path':path,'line':f.get('line'),
             'causal_status':'ADVISORY_ONLY'})
    return items[:200]


def _finalize_status(result:dict,requested:str) -> None:
    if result['status']=='SECURITY_HOLD':return
    if result['error_codes'] and not result['project_intelligence']:
        result['status']='FAILED';return
    if result['review_requests']:
        result['status']='NEEDS_REVIEW';return
    if requested=='STATIC':
        result['status']='COMPLETED_WITH_LIMITATIONS';return
    if result['error_codes'] or result['limitations'] or not result['suites'] or any(
        x['state'] not in ('PASS','FAIL') for x in result['suites']):
        result['status']='COMPLETED_WITH_LIMITATIONS';return
    result['status']='COMPLETED'
