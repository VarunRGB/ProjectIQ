"""Command line for the Integrity Agent.

One research document against its references:

    python -m projectiq_integrity --document paper.pdf --references refs/ --out outputs/doc

Many students, each folder holding one document and a references/ subfolder:

    python -m projectiq_integrity --submissions cohort/ --out outputs/docs
    (cohort/S101/report.pdf  +  cohort/S101/references/*.pdf)

Writes <id>_similarity.html (open it in a browser), <id>_similarity.json and <id>.json
(the AgentResult for the Rubric Agent). Batch mode also writes summary.csv.
"""
import argparse
import csv
import logging
import sys
from pathlib import Path

from .document_check import check_document, write_outputs
from .documents import DOCUMENT_SUFFIXES


def _check_one(doc: Path, refs: Path, sid: str, out: Path) -> dict:
    report = check_document(doc, refs)
    paths = write_outputs(report, out, sid)
    top = next(iter(report.percent_by_reference.items()), None)
    det = report.ai_detection
    ai = (f"AI-like writing {det['estimated_percent']}%" if det["status"] == "COMPLETED"
          else f"AI % {det['status'].lower().replace('_', ' ')}")
    print(f"{sid}: {report.overall_percent}% overlap "
          f"({', '.join(f'{c} {p}%' for c, p in report.percent_by_category.items())}); "
          f"{len(report.chunks)} copied chunk(s)" + (f"; most from {top[0]} ({top[1]}%)" if top else "")
          + f"; {ai}")
    for note in report.notes[:-1]:
        print("   note:", note)
    return {"submission_id": sid, "overall_percent": report.overall_percent,
            **{f"{c}_percent": p for c, p in report.percent_by_category.items()},
            "percent_in_chunks": report.percent_in_chunks, "chunks": len(report.chunks),
            "top_reference": top[0] if top else "", "top_reference_percent": top[1] if top else "",
            "ai_percent": det["estimated_percent"] if det["status"] == "COMPLETED" else "",
            "ai_status": det["status"], "html_report": str(paths["report_html"])}


def _write_csv(path: Path, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["submission_id"])
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m projectiq_integrity", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--document", type=Path, help="one research/project document (.pdf, .docx, .txt)")
    ap.add_argument("--references", type=Path, help="folder with the reference papers it uses")
    ap.add_argument("--id", help="submission id for the output files (default: document file name)")
    ap.add_argument("--submissions", type=Path,
                    help="folder of student folders, each with one document and a references/ subfolder")
    ap.add_argument("--out", type=Path, default=Path("outputs/integrity"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.document:
        if not args.references:
            ap.error("--document needs --references (the folder of papers it uses)")
        row = _check_one(args.document, args.references, args.id or args.document.stem, args.out)
        print(f"\nOpen {row['html_report']} in a browser to see every match.")
        return 0
    if args.submissions:
        rows = []
        for folder in sorted(p for p in args.submissions.iterdir() if p.is_dir()):
            docs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in DOCUMENT_SUFFIXES)
            refs = folder / "references"
            if not docs or not refs.is_dir():
                print(f"{folder.name}: skipped (needs one document and a references/ folder)")
                continue
            rows.append(_check_one(docs[0], refs, folder.name, args.out))
        args.out.mkdir(parents=True, exist_ok=True)
        _write_csv(args.out / "summary.csv", rows)
        print(f"\nResults in {args.out} (summary.csv, one HTML report per student)")
        return 0
    ap.error("give --document with --references, or --submissions")
    return 2


if __name__ == "__main__":
    sys.exit(main())
