from __future__ import annotations
import argparse, hashlib, zipfile
from pathlib import Path
from .campaign import campaign, verify_fixture_tree
from .corpus import write_tree, export_files, PUBLIC
from .audit import checked_file, require


def bundle_reports(root,out):
    root=Path(root).absolute();out=Path(out).absolute();require(root.is_dir() and not out.exists() and not out.is_relative_to(root),'BUNDLE_DESTINATION_INVALID')
    allowed={'campaign_result.json','coverage.md','execution_plan.json','runtime_plan.json','dispatch_result.json','runtime_result.json','pytest.txt','python-lock.json','npm-download-plan.json'}
    files=[];total=0
    for p in sorted(root.rglob('*')):
        if p.name not in allowed:continue
        r=checked_file(p,4_000_000);total+=r['bytes'];files.append((p,r));require(len(files)<=500 and total<=50_000_000,'REPORT_BUNDLE_LIMIT')
    require(bool(files),'NO_REPORTS')
    with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as z:
        for p,r in files:
            data=p.read_bytes();require(len(data)==r['bytes'] and hashlib.sha256(data).hexdigest()==r['sha256'] and checked_file(p,4_000_000)==r,'REPORT_CHANGED');z.writestr(p.relative_to(root).as_posix(),data)


def main(argv=None):
    p=argparse.ArgumentParser(description='Phase 8E generalization acceptance, reviewed execution only');sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('campaign');a.add_argument('--out',type=Path,required=True);a.add_argument('--local-only',action='store_true');a.add_argument('--skip-public',action='store_true');a.add_argument('--fixture-repo');a.add_argument('--fixture-ref');a.add_argument('--live-case',dest='live_cases',action='append',default=[]);a.add_argument('--vm-config',type=Path);a.add_argument('--max-vms',type=int,default=4);a.add_argument('--fetch',action='store_true');a.add_argument('--node-tarball',type=Path);a.add_argument('--node-sha256');a.add_argument('--pip-wheel',type=Path);a.add_argument('--pip-sha256');a.add_argument('--node-version',default='v24.22.3');a.add_argument('--python-version',default='3.12.3');a.add_argument('--enable-optional-toolchains',action='store_true');a.add_argument('--require-bounded-volume',action='store_true')
    e=sub.add_parser('export-fixtures');e.add_argument('--out',type=Path,required=True)
    b=sub.add_parser('bundle-reports');b.add_argument('--root',type=Path,required=True);b.add_argument('--out',type=Path,required=True)
    sub.add_parser('list-public')
    args=p.parse_args(argv)
    try:
        if args.cmd=='export-fixtures':write_tree(args.out,export_files());verify_fixture_tree(args.out);print('Exported operator-created smoke sources. No upload or approval performed.');return 0
        if args.cmd=='bundle-reports':bundle_reports(args.root,args.out);print(args.out);return 0
        if args.cmd=='list-public':
            for k,r,s,a,b,d in PUBLIC:print(k,r+'@'+s,a, 'negative control' if b else 'candidate hypothesis',d)
            return 0
        options=vars(args).copy();options.pop('cmd');result=campaign(**options);s=result['summary'];print('Phase 8E:',result['status']);print('Synthetic controls:',s['synthetic_controls_passed'],'/',s['synthetic_controls_total']);print('Synthetic adapter coverage:',s['synthetic_adapter_types_planned'],'/',s['adapter_types']);print('Public candidates:',s['public_candidates'],'expected blocks:',s['public_expected_blocks'],'gaps/unavailable:',s['public_gaps_or_unavailable']);print('Fresh reviewed smoke passes:',s['new_live_smoke_passes'],'adapter types:',s['new_live_adapter_types']);print('VM attempts observed:',s['live_attempts_observed'],'uncertain:',s['live_attempt_count_uncertain']);print('Automatic untrusted execution: BLOCKED');print('Report:',args.out/'campaign_result.json')
        if result.get('error_code'):print('Stop:',result['error_code']);return 2
        return 0
    except (Exception,KeyboardInterrupt) as exc:print('Phase 8E refused:',type(exc).__name__);return 2

if __name__=='__main__':raise SystemExit(main())
