
import argparse
import json
from pathlib import Path

from projectiq_research.agent import analyze_document

def main():
    parser = argparse.ArgumentParser(
        description="Test the ProjectIQ Research Quality Agent."
    )
    parser.add_argument(
        "file",
        help="Path to the PDF/DOCX report to analyse."
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Run Phase 1 only; skip online academic verification."
    )
    args = parser.parse_args()

    document_path = Path(args.file)

    if not document_path.exists():
        print(f"File not found: {document_path}")
        return

    print(f"Analysing: {document_path.name}")

    result = analyze_document(
        document_path,
        verify=not args.no_verify,
    )

    # Save the complete result as JSON.
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / f"{document_path.stem}_analysis.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            result.to_dict(),
            file,
            indent=4,
            ensure_ascii=False,
        )

    print("\nAnalysis complete!")
    print(f"Output saved to: {output_path}")

    print("\n--- Phase 1 Summary ---")
    print(f"Literature Review found: {result.literature_review.found}")
    print(f"References found: {result.references.found}")
    print(f"Literature Review citations: {len(result.literature_citations)}")
    print(f"Document-wide citations: {len(result.document_citations)}")
    print(f"Citation-reference matches: {len(result.citation_reference_matches)}")
    print(f"Paper references extracted: {len(result.papers)}")
    print(f"Hyperlinks extracted: {len(result.hyperlinks)}")

    print("\n--- Phase 2 Summary ---")
    for paper in result.verification_results:
        print(f"{paper.submitted_title} -> {paper.status}")

    if result.notes:
        print("\n--- Notes ---")
        for note in result.notes:
            print(f"- {note}")


if __name__ == "__main__":
    main()
