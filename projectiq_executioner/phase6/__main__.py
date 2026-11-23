"""Offline CLI for Phase 6. No network, guest execution or cloud model required."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .contracts import (EvidenceError, MAX_RUNS, MAX_TOTAL_INPUT_BYTES,
                        fail, read_json, write_json_atomic)
from .pipeline import build
from .report import markdown, validate_report


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description='ProjectIQ Phase 6: read-only evidence diagnosis')
    p.add_argument('--plan',type=Path,help='Existing Phase 3 deterministic test plan JSON')
    p.add_argument('--run',type=Path,action='append',default=[],help='Phase 4 run report JSON; repeat for historical runs')
    p.add_argument('--vm',type=Path,action='append',default=[],help='VM execution report corresponding to each --run, in order')
    p.add_argument('--standalone-vm',type=Path,help='Standalone Phase 2 VM report (with no --run)')
    p.add_argument('--phase5',type=Path,help='Phase 5 code_quality.json (optional)')
    p.add_argument('--dependencies',type=Path,help='Optional operator-reviewed dependency edge JSON')
    p.add_argument('--out',type=Path,required=True,help='New output directory (must not contain existing reports)')
    args=p.parse_args(argv)
    if args.vm and len(args.vm)!=len(args.run):
        p.error('Supply exactly one --vm for each --run, or omit all --vm inputs.')
    if args.standalone_vm and (args.vm or args.run):
        p.error('--standalone-vm cannot be combined with --run/--vm.')
    if len(args.run)>MAX_RUNS:
        p.error('Too many run reports.')
    if not (args.plan or args.run or args.standalone_vm or args.phase5):
        p.error('Supply at least one evidence source.')
    output=args.out
    try:
        if output.exists() and (output/'phase6_diagnosis.json').exists() or output.exists() and (output/'phase6_diagnosis.md').exists():
            fail('OUTPUT_ALREADY_EXISTS')
        inputs=[]
        if args.plan:inputs.append(('PLAN',args.plan))
        for i,v in enumerate(args.run):inputs.append(('RUN',v))
        for i,v in enumerate(args.vm):inputs.append(('VM',v))
        if args.standalone_vm:inputs.append(('VM',args.standalone_vm))
        if args.phase5:inputs.append(('QUALITY',args.phase5))
        if args.dependencies:inputs.append(('DEPENDENCIES',args.dependencies))
        if len(inputs)>2*MAX_RUNS+4:fail('EVIDENCE_COUNT_LIMIT')
        parsed=[];receipts=[];total=0;labels={}
        counters={'RUN':0,'VM':0}
        for kind,path in inputs:
            doc,sha,n=read_json(path)
            total+=n
            if total>MAX_TOTAL_INPUT_BYTES:fail('EVIDENCE_TOTAL_BYTE_LIMIT')
            if kind in counters:
                counters[kind]+=1
                label=f'{kind}{counters[kind]:03d}'
            else:label=kind
            parsed.append((kind,doc,label))
            receipts.append({'id':label,'role':kind,'sha256':sha,'bytes':n})
        def get_one(kind):
            found=[(doc,label) for k,doc,label in parsed if k==kind]
            return found[0] if found else None
        runs=[(doc,label) for kind,doc,label in parsed if kind=='RUN']
        vms=[doc for kind,doc,label in parsed if kind=='VM'] if args.vm else []
        result=build(plans=[get_one('PLAN')] if get_one('PLAN') else [],
               run_inputs=[(doc,label,vms[i] if args.vm else None) for i,(doc,label) in enumerate(runs)],
               vm_standalone=get_one('VM') if args.standalone_vm else None,
               quality_input=get_one('QUALITY'),
               dependency_input=get_one('DEPENDENCIES'),receipts=receipts)
        validate_report(result)
        md=markdown(result)
        # Write Markdown second; both locations must be absent and symlink-safe.
        if output.is_symlink() or any(x.is_symlink() for x in output.parents):fail('UNSAFE_OUTPUT_PATH')
        if (output/'phase6_diagnosis.json').exists() or (output/'phase6_diagnosis.md').exists():fail('OUTPUT_ALREADY_EXISTS')
        output.mkdir(parents=True,exist_ok=True)
        write_json_atomic(result,output/'phase6_diagnosis.json')
        with (output/'phase6_diagnosis.md').open('x',encoding='utf-8',newline='\n') as f:
            f.write(md)
    except EvidenceError as exc:
        print('Phase 6 stopped safely: '+exc.code,file=sys.stderr)
        return 2
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Phase 6 stopped safely: INTERNAL_OR_IO_ERROR',file=sys.stderr)
        return 2
    print('ProjectIQ Phase 6 — Failure Diagnosis & Triage')
    print('  Status:        '+result['status'])
    print('  Runs examined: '+str(result['summary']['runs']))
    print('  Diagnoses:     '+str(result['summary']['diagnoses']))
    print('  Comparisons:   '+str(result['summary']['comparisons']))
    print('  Student fault: NOT ESTABLISHED')
    print('  Execution:     NOT PERFORMED')
    print('  JSON:          '+str(output/'phase6_diagnosis.json'))
    print('  Markdown:      '+str(output/'phase6_diagnosis.md'))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
