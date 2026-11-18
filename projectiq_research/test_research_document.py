"""CLI for the complete ProjectIQ Research Agent (Phases 1–4A)."""
import argparse
import json
from pathlib import Path

from .agent import analyze_research_document
from .research_report import render_research_report_markdown


def _percentage(value):
    return "N/A" if value is None else f"{value:.1f}%"


def main():
    parser = argparse.ArgumentParser(description="Analyze a research report (Phases 1–4A).")
    parser.add_argument("file", help="Path to a PDF, DOCX, TXT, or MD report")
    parser.add_argument("--no-verify", action="store_true", help="Skip live Phase 2 API lookups; Phase 3 still runs")
    parser.add_argument("--as-of", dest="as_of_date", help="Assessment date YYYY-MM-DD (default today)")
    parser.add_argument("--details", action="store_true", help="Also print each paper's Phase 2 and Phase 3 results")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.is_file():
        parser.error(f"File not found: {path}")
    try:
        result = analyze_research_document(path, verify=not args.no_verify, as_of_date=args.as_of_date)
    except ValueError as exc:
        parser.error(f"Invalid assessment date: {exc}")

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    json_path = output_dir / f"{path.stem}_analysis.json"
    report_path = output_dir / f"{path.stem}_research_report.md"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(render_research_report_markdown(result.research_report), encoding="utf-8")

    report = result.research_report
    summary = result.recency_summary
    print(f"\nProjectIQ — Research Quality Report | {path.name} | {summary.as_of_date}")
    print("-" * 68)
    print(f"Bibliography: {len(result.papers)} entries / {summary.distinct_references} distinct works")
    counts = report.phase_2["status_counts"]
    print(f"Phase 2 identity: {counts.get('verified', 0)} verified, {counts.get('needs_review', 0)} need review, "
          f"{counts.get('not_found', 0)} not found, {counts.get('metadata_mismatch', 0)} mismatches")
    labels = report.phase_3["classification_counts"]
    print(f"Phase 3 relevance: {labels.get('directly_relevant', 0)} direct, "
          f"{labels.get('methodologically_relevant', 0)} methodological, "
          f"{labels.get('partially_relevant', 0)} partial, "
          f"{labels.get('possibly_unrelated', 0)} possibly unrelated")
    print(f"Phase 4A direct recency: {summary.verified_direct_recent_count}/{summary.verified_direct_count} "
          f"confirmed direct works within five years ({_percentage(summary.verified_direct_recent_percent)})")
    print(f"Literature Review recency: {summary.literature_review_recent_reliable_count}/"
          f"{summary.literature_review_reliable_count} confirmed works "
          f"({_percentage(summary.literature_review_recent_percent)})")
    print(f"Flagged references: {len(report.attention_items)}; confirmed duplicate entries: {summary.duplicate_reference_count}")
    print(f"JSON: {json_path}")
    print(f"Markdown: {report_path}")

    if args.details:
        print("\n--- Phase 2 (all references) ---")
        for i, v in enumerate(result.verification_results, 1):
            print(f"[{i}] {v.status}: {v.submitted_title}")
        print("\n--- Phase 3 (all references) ---")
        for r in result.relevance_results:
            print(f"[{r.reference_index + 1}] {r.classification} [{r.confidence}]: {r.paper_title}")
    if result.notes:
        print("\nProcessing notes:")
        for note in result.notes:
            print(f"- {note}")


if __name__ == "__main__":
    main()
