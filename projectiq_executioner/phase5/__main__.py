"""python -m projectiq_executioner.phase5 --source PATH --out OUTPUT"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from .contracts import validate_report

from .pipeline import analyze_project
from .report import write_reports


def main(argv=None):
    parser=argparse.ArgumentParser(description='ProjectIQ Phase 5 — read-only code quality analysis')
    parser.add_argument('--source',type=str,required=True,help='Local directory or public GitHub repository URL')
    parser.add_argument('--ref',help='Optional GitHub ref; resolved to immutable SHA')
    parser.add_argument('--out',type=Path,required=True,help='Reports destination')
    args=parser.parse_args(argv)
    try:
        out=args.out.resolve()
        if args.source.startswith(('http://','https://','git@')):
            if not args.source.startswith('https://github.com/'):
                raise ValueError('Only public HTTPS GitHub repository URLs are supported')
            # Reuse the existing Phase 1 safe, immutable ingestion boundary.
            from ..github_client import GitHubClient,DownloadLimits
            from ..safe_archive import extract_snapshot,ArchiveLimits
            with tempfile.TemporaryDirectory(prefix='projectiq_phase5_') as tmp:
                base=Path(tmp)
                client=GitHubClient(timeout=25,limits=DownloadLimits(30*1024*1024))
                source=client.resolve(args.source,args.ref)
                client.download(source,base/'snapshot.zip')
                checkout=extract_snapshot(base/'snapshot.zip',base/'checkout',
                    ArchiveLimits(max_uncompressed_bytes=120*1024*1024))
                report=analyze_project(checkout,repository=source.repository)
                report['source']={'kind':'github_pinned','repository':source.repository,
                                  'commit_sha':source.commit_sha}
                validate_report(report)
                a,b=write_reports(report,out)
        else:
            if args.ref:
                raise ValueError('--ref is only supported for GitHub URLs')
            root=Path(args.source).resolve()
            if out==root or out.is_relative_to(root):
                raise ValueError('Reports must be outside source directory to avoid self-analysis')
            report=analyze_project(root)
            a,b=write_reports(report,out)
    except (OSError,ValueError,RuntimeError) as exc:
        # Do not leak source content, path-laden exception repr, or traceback.
        print('PHASE5_ERROR: source or output unavailable, or invalid static report',file=sys.stderr)
        return 2
    print('ProjectIQ Phase 5 — Code Quality & Complexity')
    print('  Status:',report['status'])
    print('  Analyzed files:',report['summary']['analyzed_files'])
    print('  Functions:',report['summary']['functions_found'])
    print('  Function-body coverage:',f"{100*report['coverage']['function_body_ratio']:.1f}%")
    if any(report['coverage']['limits'].values()):
        print('  Evidence truncated:',', '.join(k for k,v in report['coverage']['limits'].items() if v))
    print('  Findings:',report['summary']['findings'])
    print('  Big-O estimated:',report['coverage']['big_o']['estimated_functions'],'of',report['summary']['functions_found'])
    quality=report['quality']
    print('  Score:',quality['score'] if quality['score'] is not None else 'INSUFFICIENT_COVERAGE')
    print('  Score status:',quality['status'],'(confidence:',quality['confidence']+')')
    if quality['status']=='PROVISIONAL_LIMITED':
        measured=', '.join(k for k,v in quality['dimensions'].items() if v is not None)
        excluded=', '.join(k for k,v in quality['dimensions'].items() if v is None)
        print('  Score scope: MEASURED DIMENSIONS ONLY — NOT an overall quality grade')
        print('  Measured:',measured or 'none','| Excluded:',excluded or 'none')
    elif quality['status']=='PROVISIONAL':
        print('  Score scope: PROVISIONAL MAINTAINABILITY INDICATOR — NOT a student grade')
    else:
        print('  Score scope: NOT SCORED — analysis coverage is insufficient')
    print('  Credential exposure indicators:',len(report['security']['findings']),
          '(unverified pattern matches)')
    if report['quality']['reason_codes']:
        print('  Score limitations:',', '.join(report['quality']['reason_codes']))
    print('  Execution: NOT PERFORMED')
    print('  JSON:',a)
    print('  Markdown:',b)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
