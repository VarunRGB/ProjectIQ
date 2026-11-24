"""Phase 2 local CLI: collect summary-only, normalize, assess, propose, schemas."""
from __future__ import annotations
import argparse,sys
from pathlib import Path
from urllib.parse import urlparse
from ..contracts import AGENTS
from ..collector import make_submission,collect,live_sources,load_json,json_bytes,write_new
from .contracts import Rubric,DecisionSet,EvidenceBundle,Assessment
from .evidence import build_evidence,digest
from .scoring import assess,empty_decisions,reweight
from .intelligence import intelligence_report


def dump(path,value):
    if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
    write_new(path,json_bytes(value))


def load_rubric(path):return Rubric.model_validate(load_json(path))


def main(argv=None):
    p=argparse.ArgumentParser(description='ProjectIQ v1.5 Phase 2 — evidence and rubric engine')
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('collect',help='Collect agents with no legacy report judge')
    for name in ('report','out'):c.add_argument('--'+name,type=Path,required=True)
    for name in ('title','submission-id'):c.add_argument('--'+name,required=True)
    c.add_argument('--repo');c.add_argument('--ref');c.add_argument('--references',type=Path)
    c.add_argument('--agents',nargs='+',choices=AGENTS,default=AGENTS);c.add_argument('--verify-research',action='store_true')
    for name in ('assess','propose','propose-all','reweight'):
        q=sub.add_parser(name,help='Rebuild evidence from saved run; no agent rerun')
        q.add_argument('--evaluation',type=Path,required=True);q.add_argument('--rubric',type=Path,required=True)
        q.add_argument('--decisions',type=Path);q.add_argument('--out',type=Path,required=True)
        q.add_argument('--acknowledge-imported',action='store_true',help='Operator confirms historical submission association; not authenticity')
        if name in ('propose','propose-all'):
            q.add_argument('--criteria',nargs='+',required=name=='propose',help='Select qualitative criteria; all unresolved for propose-all when omitted')
            if name=='propose-all':q.add_argument('--batch-size',type=int,default=3)
            q.add_argument('--preview',action='store_true',help='Inspect candidates without contacting Ollama')
            q.add_argument('--retry-deferred',action='store_true',help='Retry selected MODEL deferrals; preserve all other decisions')
        if name=='reweight':q.add_argument('--old-rubric',type=Path,required=True)
    q=sub.add_parser('schemas');q.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        if args.command=='schemas':
            args.out.mkdir(parents=True,exist_ok=False)
            for name,m in [('rubric',Rubric),('decisions',DecisionSet),('evidence',EvidenceBundle),('assessment',Assessment)]:dump(args.out/(name+'.schema.json'),m.model_json_schema())
            return 0
        if args.command=='collect':
            submission,data=make_submission(args.report,args.title,args.submission_id,args.repo,args.ref)
            if args.references and not args.references.is_dir():raise ValueError('REFERENCE_DIRECTORY_NOT_FOUND')
            sources=live_sources(submission,references=args.references,agents=args.agents,verify_research=args.verify_research,summary_only=True)
            if not sources:raise ValueError('NO_AGENT_INPUTS')
            r=collect(submission=submission,report_bytes=data,out=args.out,mode='LIVE',sources=sources)
            print('Evaluation:',args.out/'evaluation.json')
            return 2 if any(a.status=='FAILED' for a in r.agents.values()) else 0
        rubric=load_rubric(args.rubric)
        bundle=build_evidence(args.evaluation,acknowledge_imported=args.acknowledge_imported)
        decisions=DecisionSet.model_validate(load_json(args.decisions)) if args.decisions else empty_decisions(bundle,rubric)
        if args.command=='reweight':
            if not args.decisions:raise ValueError('REWEIGHT_REQUIRES_DECISIONS')
            decisions=reweight(bundle,load_rubric(args.old_rubric),rubric,decisions)
        assess(bundle,rubric,decisions,include_details=False)  # validate before any model activity
        if args.out.exists():raise ValueError('OUTPUT_ALREADY_EXISTS')
        diagnostics=None
        if args.command in ('propose','propose-all'):
            from .suggest import propose,prepare,plan_batches,recover_rejected
            from projectiq_summarizer.config import Settings
            settings=Settings(judge_samples=1)
            budget=min(18000,max(2000,(settings.num_ctx-2500)*3))
            common=dict(max_chars=budget,seed=settings.seed,retry_deferred=args.retry_deferred,progress=lambda m:print(m,flush=True))
            batches=([args.criteria] if args.command=='propose' else plan_batches(bundle,rubric,existing=decisions,criterion_ids=args.criteria,max_chars=budget,batch_size=args.batch_size,retry_deferred=args.retry_deferred))
            # Prepare all batches before loading the model. Objective-only or pre-deferred runs use no model.
            prepared=[prepare(bundle,rubric,criterion_ids=b,existing=decisions,max_chars=budget,retry_deferred=args.retry_deferred) for b in batches]
            diagnostics={} if args.command=='propose' else {'schema_version':'2.0','mode':'PREVIEW' if args.preview else 'PROPOSE_ALL','batches':[],'planned_batches':batches}
            llm=None;checked=False;failure=None
            try:
                if not args.preview and any(p[2] for p in prepared):
                    from projectiq_summarizer.llm import OllamaClient
                    from ..resources import release_ollama_model
                    u=urlparse(settings.ollama_url)
                    if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1','::1') or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:raise ValueError('OLLAMA_MUST_BE_LOCAL')
                    print('Rubric: checking local Ollama model...',flush=True)
                    llm=OllamaClient(settings);llm.check();checked=True
                for i,ids in enumerate(batches):
                    log=diagnostics if args.command=='propose' else {}
                    if args.command=='propose-all':
                        diagnostics['batches'].append(log)
                        print(f'Rubric: batch {i+1}/{len(batches)}',flush=True)
                    decisions=propose(bundle,rubric,llm,criterion_ids=ids,existing=decisions,preview=args.preview,diagnostics=log,**common)
                    if not args.preview:
                        decisions,recovery_failed=recover_rejected(bundle,rubric,llm,decisions,log,max_chars=budget,seed=settings.seed,progress=common['progress'])
                        if recovery_failed:
                            diagnostics['status']='INFERENCE_FAILED';failure='RECOVERY_INFERENCE_FAILED';break
            except (ValueError,RuntimeError,OSError) as exc:
                diagnostics['status']='INFERENCE_FAILED';failure=type(exc).__name__
                # Save completed decisions and partial assessment, even if a later batch fails.
            finally:
                if llm is not None:
                    try:
                        if checked:print('Rubric model release:',release_ollama_model(llm),flush=True)
                    finally:llm.http.close()
        else:failure=None
        result=assess(bundle,rubric,decisions)
        args.out.mkdir(parents=True,exist_ok=False)
        dump(args.out/'evidence.json',bundle);dump(args.out/'rubric.json',rubric);dump(args.out/'decisions.json',decisions);dump(args.out/'assessment.json',result)
        dump(args.out/'evidence_intelligence.json',intelligence_report(bundle))
        if diagnostics is not None:dump(args.out/'proposal_diagnostics.json',diagnostics)
        print('Assessment:',result.status,'| coverage:',result.assessed_weight_percent,'%')
        print('Assessed subtotal:',result.assessed_subtotal_points,'/',result.assessed_max_points)
        print('Proposed total:',result.proposed_points if result.proposed_points is not None else 'WITHHELD (unresolved criteria)')
        print('Unresolved:',', '.join(result.unresolved_criterion_ids) or 'none')
        print('Output:',args.out)
        # Incomplete marks are an ordinary result, not a CLI exception.
        if failure:
            print('Rubric inference failed; completed decisions and diagnostics saved:',failure,file=sys.stderr)
            return 1
        return 0
    except (ValueError,OSError,RuntimeError,ImportError) as exc:
        # Avoid echoing arbitrary student text in exception messages.
        message=str(exc)
        print('Phase 2 failed:',message if message.isascii() and len(message)<150 and all(x.isalnum() or x=='_' for x in message) else type(exc).__name__,file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
