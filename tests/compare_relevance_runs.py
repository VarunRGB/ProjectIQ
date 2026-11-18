"""Compare two ProjectIQ Research Agent JSON outputs without fitting any model.

Usage: python tools/compare_relevance_runs.py old_analysis.json new_analysis.json
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="Original Phase 3 JSON file")
    parser.add_argument("new", type=Path, help="Updated Phase 3 JSON file")
    args = parser.parse_args()
    old = json.loads(args.old.read_text(encoding="utf-8"))
    new = json.loads(args.new.read_text(encoding="utf-8"))
    old_rows = {row["reference_index"]: row for row in old.get("relevance_results", [])}
    new_rows = {row["reference_index"]: row for row in new.get("relevance_results", [])}
    for label, rows in (("OLD", old_rows), ("NEW", new_rows)):
        counts = Counter(row["classification"] for row in rows.values())
        print(f"{label}: {len(rows)} references: {dict(sorted(counts.items()))}")
    print("\nReference classification changes:")
    changes = 0
    for index in sorted(old_rows.keys() | new_rows.keys()):
        a, b = old_rows.get(index), new_rows.get(index)
        if a is None or b is None:
            print(f"[{index+1:02}] {'missing in old' if a is None else 'missing in new'}")
            changes += 1
        elif a["classification"] != b["classification"]:
            title = (b.get("paper_title") or a.get("paper_title") or "")[:85]
            print(f"[{index+1:02}] {a['classification']:25} -> {b['classification']:25} {title}")
            changes += 1
    if changes == 0:
        print("No classification changes.")
    print("\nNote: changing labels is not proof of accuracy; manually review hard positive and negative examples.")


if __name__ == "__main__":
    main()
