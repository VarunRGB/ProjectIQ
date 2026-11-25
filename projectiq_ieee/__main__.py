"""Run as: python -m projectiq_ieee path/to/paper.docx --json report.json"""
import argparse
import json
from pathlib import Path
from .formats import analyze_document


def main():
    parser = argparse.ArgumentParser(description="IEEE-style paper formatting checker (capstone demo)")
    parser.add_argument("document", type=Path, help="Research paper: PDF, DOCX, DOC, TXT, MD, RTF or ODT")
    parser.add_argument("--json", dest="json_file", type=Path, help="Optional JSON output file")
    args = parser.parse_args()
    try:
        result = analyze_document(args.document)
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    page_description = (f"{result['pages']} page(s)" if result["pages"] is not None
                        else "page count unavailable")
    print(f"\n{result['filename']} | {result['source_format']} | {page_description}")
    print(f"{('Structure' if result['assessment_scope'] == 'structure_only' else 'IEEE-format')} "
          f"score: {result['score']}/100 | {result['rating']}")
    print(f"Evaluated weight: {result['evaluated_weight']}/100")
    for item in result["checks"]:
        mark = "+" if item["status"] == "pass" else "-" if item["status"] == "not_applicable" else "!"
        earned = "N/A" if not item["applicable"] else f"{item['earned']:g}/{item['possible']}"
        print(f"{mark} {item['criterion']}: {earned} - {item['detail']}")
    if args.json_file:
        args.json_file.parent.mkdir(parents=True, exist_ok=True)
        args.json_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved JSON: {args.json_file}")


if __name__ == "__main__":
    main()
