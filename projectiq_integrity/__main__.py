"""Command line for the Integrity Agent.

A) One research document against its references (main use):

    python -m projectiq_integrity --document paper.pdf --references refs/ --out outputs/doc

B) Many students, each folder holding one document and a references/ subfolder:

    python -m projectiq_integrity --submissions cohort/ --out outputs/docs

   cohort/S101/report.pdf  +  cohort/S101/references/*.pdf

C) A whole class compared with each other (code with JPlag, reports, AI-style text):

    python -m projectiq_integrity --code samples/code --base-code samples/starter \
        --reports samples/reports --references samples/references --out outputs/integrity

A and B write <id>_similarity.html (open it in a browser), <id>_similarity.json and
<id>.json (the AgentResult). C writes <id>.json per submission and summary.csv.
"""
import argparse
import csv
import logging
import sys
from pathlib import Path

from .agent import IntegrityAgent
from .document_check import check_document, write_outputs
from .report_similarity import REPORT_SUFFIXES


def _document_mode(doc: Path, refs: Path, sid: str, out: Path) -> dict:
    report = check_document(doc, refs)
    paths = write_outputs(report, out, sid)
    top = next(iter(report.percent_by_reference.items()), None)
    print(f"{sid}: {report.overall_percent}% overlap "
          f"({', '.join(f'{c} {p}%' for c, p in report.percent_by_category.items())}); "
          f"{len(report.chunks)} copied chunk(s)" + (f"; most from {top[0]} ({top[1]}%)" if top else ""))
    for note in report.notes[:-1]:
        print("   note:", note)
    return {"submission_id": sid, "overall_percent": report.overall_percent,
            **{f"{c}_percent": p for c, p in report.percent_by_category.items()},
            "percent_in_chunks": report.percent_in_chunks, "chunks": len(report.chunks),
            "top_reference": top[0] if top else "", "top_reference_percent": top[1] if top else "",
            "html_report": str(paths["report_html"])}


def _write_csv(path: Path, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["submission_id"])
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m projectiq_integrity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--document", type=Path, help="(A) one research/project document (.pdf, .docx, .txt)")
    ap.add_argument("--id", help="(A) submission id for the output files (default: document file name)")
    ap.add_argument("--submissions", type=Path,
                    help="(B) folder of student folders, each with one document and a references/ subfolder")
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
    if args.document:
        if not args.references:
            ap.error("--document needs --references (the folder of papers it uses)")
        row = _document_mode(args.document, args.references, args.id or args.document.stem, args.out)
        print(f"\nOpen {row['html_report']} in a browser to see every match.")
        return 0
    if args.submissions:
        rows = []
        for folder in sorted(p for p in args.submissions.iterdir() if p.is_dir()):
            docs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in REPORT_SUFFIXES)
            refs = folder / "references"
            if not docs or not refs.is_dir():
                print(f"{folder.name}: skipped (needs one document and a references/ folder)")
                continue
            rows.append(_document_mode(docs[0], refs, folder.name, args.out))
        args.out.mkdir(parents=True, exist_ok=True)
        _write_csv(args.out / "summary.csv", rows)
        print(f"\nResults in {args.out} (summary.csv, one HTML report per student)")
        return 0
    if not args.code and not args.reports:
        ap.error("give --document, --submissions, --code or --reports")

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
    _write_csv(args.out / "summary.csv", rows)
    print(f"\nResults in {args.out}")
    for note in agent.notes:
        print("-", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
