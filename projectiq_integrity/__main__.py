"""Command line for the Integrity Agent.

    python -m projectiq_integrity --code samples/code --base-code samples/starter \
        --reports samples/reports --references samples/references --out outputs/integrity

Writes one <submission>.json (the AgentResult) per submission and summary.csv.
"""
import argparse
import csv
import logging
import sys
from pathlib import Path

from .agent import IntegrityAgent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m projectiq_integrity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code", type=Path, help="folder with one code submission per student (folder, .zip, .py or .ipynb)")
    ap.add_argument("--base-code", type=Path, help="starter code given to every student (excluded)")
    ap.add_argument("--old-code", type=Path, nargs="*", help="earlier years' submissions to compare against")
    ap.add_argument("--reports", type=Path, help="folder with one report per student (.pdf, .docx, .txt)")
    ap.add_argument("--references", type=Path, help="folder with reference documents")
    ap.add_argument("--template", type=Path, help="department report template (its text is ignored)")
    ap.add_argument("--only", nargs="*", help="only these submission ids")
    ap.add_argument("--out", type=Path, default=Path("outputs/integrity"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not args.code and not args.reports:
        ap.error("give --code, --reports or both")

    agent = IntegrityAgent(work_dir=args.out / "_work", code_dir=args.code, reports_dir=args.reports,
                           references_dir=args.references, base_code=args.base_code,
                           old_code_dirs=args.old_code, template_report=args.template).prepare()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for sid in args.only or agent.submission_ids():
        result = agent.analyze(sid)
        (args.out / f"{sid}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        top = result.findings[0] if result.findings else None
        rows.append({"submission_id": sid, "findings": len(result.findings),
                     "kinds": ";".join(sorted({f.kind for f in result.findings})),
                     "top_confidence": top.confidence if top else "",
                     "top_summary": top.summary if top else ""})
        print(f"{sid}: {len(result.findings)} finding(s)" + (f", top: {top.summary}" if top else ""))
    with open(args.out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["submission_id"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nResults in {args.out}")
    for note in agent.notes:
        print("-", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
