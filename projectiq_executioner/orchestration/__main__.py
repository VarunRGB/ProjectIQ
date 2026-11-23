"""CLI for the exact same API used by the main ProjectIQ orchestrator."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from .contracts import OrchestrationError,read_json
from .core import evaluate,OperatorContext


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='One-call ProjectIQ Executioner (STATIC/REVIEWED/RESUME)')
    parser.add_argument('--request',required=True,type=Path,help='Versioned request JSON')
    parser.add_argument('--out',required=True,type=Path,help='Fresh, immutable evidence directory')
    parser.add_argument('--vm-config',type=Path,help='Operator-owned Phase 2 reviewed VM policy')
    parser.add_argument('--review-root',type=Path,help='Operator-owned root for relative reviewed catalog paths')
    parser.add_argument('--review-registry',type=Path,help='Operator-owned exact SHA reviewed-suite registry')
    parser.add_argument('--work-root',type=Path,help='Operator-owned managed VM workspace')
    args=parser.parse_args(argv)
    try:
        request=read_json(args.request,limit=32_768)
        report=evaluate(request,output_dir=args.out,operator=OperatorContext(
            vm_config=args.vm_config,review_root=args.review_root,
            review_registry=args.review_registry,work_root=args.work_root))
    except (OrchestrationError,OSError,ValueError,TypeError) as e:
        print('Executioner refused input/output safely: '+(
            e.code if isinstance(e,OrchestrationError) else 'INVALID_INPUT_OR_OUTPUT'),file=sys.stderr)
        return 2
    print('ProjectIQ Executioner — '+report['status'])
    print('  Source: '+str((report.get('source') or {}).get('repository','NOT_RESOLVED')))
    print('  SHA: '+str((report.get('source') or {}).get('commit_sha','NOT_RESOLVED')))
    print('  Mode: '+report['mode']+' / '+report['effective_mode'])
    print('  Functional suites: '+str(len(report['suites'])))
    print('  VM attempts scheduled: '+str(report['vm_attempts_scheduled']))
    counts=(report.get('test_case_matrix') or {}).get('counts') or {}
    print('  Planned-case outcomes: '+', '.join(
        str(counts.get(k,0))+' '+k for k in
        ('PASSED','FAILED','INCONCLUSIVE','BLOCKED','NOT_TESTED')))
    additional=(report.get('test_case_matrix') or {}).get('reviewed_only_checks') or []
    if additional:
        print('  Additional reviewed checks: '+str(len(additional))+
              ' (listed separately; no invented Phase 3 linkage)')
    print('  JSON: '+str(args.out/'executioner_result.json'))
    print('  Markdown: '+str(args.out/'executioner_report.md'))
    return 3 if report['status'] in ('NEEDS_REVIEW','COMPLETED_WITH_LIMITATIONS') else 2 if report['status'] in ('FAILED','SECURITY_HOLD') else 0

if __name__=='__main__':
    raise SystemExit(main())
