"""Inspect reference-title recovery using an existing JSON report (offline)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from projectiq_research.reference_extractor import extract_references


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Previous ProjectIQ *_analysis.json file")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    original = report.get("papers", [])
    parsed = []
    for item in original:
        papers = extract_references(item["raw"])
        if len(papers) != 1:
            raise ValueError("Unable to reconstruct a single bibliography entry")
        parsed.append(papers[0])
    recovered = [(i + 1, paper) for i, paper in enumerate(parsed) if paper.title]
    print(f"Entries: {len(parsed)}")
    print(f"Originally usable titles: {sum(bool(p.get('title')) for p in original)}")
    print(f"Now extracted titles: {len(recovered)}")
    print("\nRecovered titles:")
    for index, paper in recovered:
        print(f"  [{index}] {paper.title}")
    print("\nThis is an offline extraction check; it does not verify titles with OpenAlex.")


if __name__ == "__main__":
    main()
