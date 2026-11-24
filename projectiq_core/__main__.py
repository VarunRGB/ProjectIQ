"""python -m projectiq_core {collect,bundle,schema} ..."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .contracts import AGENTS, Evaluation
from .collector import make_submission, collect, live_sources, load_json

def main(argv=None):
    p=argparse.ArgumentParser(description='ProjectIQ v1.5 unified local evaluation')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('collect','bundle'):
        q=sub.add_parser(name)
        q.add_argument('--report',type=Path,required=True)
        q.add_argument('--title',required=True);q.add_argument('--submission-id',required=True)
        q.add_argument('--repo');q.add_argument('--ref');q.add_argument('--out',type=Path,required=True)
        if name=='collect':
            q.add_argument('--references',type=Path);q.add_argument('--rubric',type=Path)
            q.add_argument('--agents',nargs='+',choices=AGENTS,default=AGENTS)
            q.add_argument('--samples',type=int,default=1)
            q.add_argument('--verify-research',action='store_true',help='Opt into online bibliography metadata verification')
        else:
            for a in AGENTS:q.add_argument('--'+a,type=Path)
    q=sub.add_parser('schema');q.add_argument('--out',type=Path)
    args=p.parse_args(argv)
    if args.command=='schema':
        s=json.dumps(Evaluation.model_json_schema(),indent=2)+'\n'
        if args.out:
            with args.out.open('x',encoding='utf-8') as f:f.write(s)
        else:print(s,end='')
        return 0
    try:
        submission,data=make_submission(args.report,args.title,args.submission_id,args.repo,args.ref)
        if args.command=='collect':
            if args.references and not args.references.is_dir():raise ValueError('REFERENCE_DIRECTORY_NOT_FOUND')
            if args.rubric and not args.rubric.is_file():raise ValueError('RUBRIC_NOT_FOUND')
            funcs=live_sources(submission,references=args.references,agents=args.agents,rubric=args.rubric,samples=args.samples,verify_research=args.verify_research)
            mode='LIVE'
        else:
            funcs={a:(lambda report,out,path=getattr(args,a):load_json(path)) for a in AGENTS if getattr(args,a)}
            if 'executioner' in funcs and (not args.repo or not args.ref):raise ValueError('IMPORTED_EXECUTIONER_REQUIRES_REPO_AND_SHA')
            mode='IMPORTED'
        if not funcs:raise ValueError('NO_AGENT_INPUTS')
        r=collect(submission=submission,report_bytes=data,out=args.out,mode=mode,sources=funcs)
        print('Evaluation:',args.out/'evaluation.json')
        return 2 if any(a.status=='FAILED' for a in r.agents.values()) else 0
    except (ValueError,OSError) as exc:
        print('Collection failed:',type(exc).__name__,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
