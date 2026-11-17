"""Manual CLI for the complete ProjectIQ Research Agent (Phases 1-3)."""
import argparse
import json
from pathlib import Path

from .agent import analyze_research_document


def main():
    parser = argparse.ArgumentParser(description="Analyze a research report (Phases 1-3).")
    parser.add_argument("file", help="Path to a PDF, DOCX, TXT, or MD report")
    parser.add_argument("--no-verify", action="store_true", help="Skip live Phase 2 API lookups; Phase 3 still runs")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        parser.error(f"File not found: {path}")
    print(f"Analysing: {path.name}")
    result = analyze_research_document(path, verify=not args.no_verify)
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{path.stem}_analysis.json"
    output_path.write_text(json.dumps(result.to_dict(), indent=4, ensure_ascii=False), encoding="utf-8")
    print(f"\nAnalysis complete!\nOutput saved to: {output_path}")
    print("\n--- Phase 1 Summary ---")
    print(f"Literature Review found: {result.literature_review.found}")
    print(f"References found: {result.references.found}")
    print(f"Literature Review citations: {len(result.literature_citations)}")
    print(f"Document-wide citation mentions: {len(result.document_citations)}")
    print(f"Citation-reference match records: {len(result.citation_reference_matches)}")
    print(f"Paper references extracted: {len(result.papers)}")
    print(f"Hyperlinks extracted: {len(result.hyperlinks)}")
    print("\n--- Phase 2 Summary ---")
    for verification in result.verification_results:
        print(f"{verification.submitted_title} -> {verification.status}")
    print("\n--- Phase 3 Summary ---")
    for relevance in result.relevance_results:
        print(f"[{relevance.reference_index + 1}] {relevance.classification} [{relevance.confidence}] - {relevance.paper_title}")
    if result.notes:
        print("\n--- Notes ---")
        for note in result.notes:
            print(f"- {note}")


if __name__ == "__main__":
    main()
