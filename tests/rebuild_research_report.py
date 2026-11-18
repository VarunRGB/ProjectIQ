"""Rebuild only Phase 4A and final Markdown/JSON from a saved Research Agent JSON.

No PDF parsing, online lookups, or MiniLM/cross-encoder inference are performed.
The script preserves all Phase 1–3 results from the input file.

Usage:
  python tools/rebuild_research_report.py outputs/report_analysis.json
  python tools/rebuild_research_report.py outputs/report_analysis.json --as-of 2026-11-18
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
from pathlib import Path
import sys

# Works when called as ``python tools/rebuild_research_report.py`` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projectiq_research.models import (
    CitationMention, Hyperlink, PaperReference, ResearchExtraction, Section,
)
from projectiq_research.verification_models import PaperVerification
from projectiq_research.relevance_models import PaperRelevance
from projectiq_research.recency_analyzer import analyze_recency
from projectiq_research.research_report import build_research_report, render_research_report_markdown


def _decode(cls, values: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (values or {}).items() if k in names})


def rebuild(data: dict, as_of_date: str | None = None):
    """Return (updated JSON dict, Markdown, summary), without altering the input."""
    sections = {name: _decode(Section, data.get(name) or {"key": name, "found": False})
                for name in ("literature_review", "references")}
    papers = [_decode(PaperReference, item) for item in data.get("papers", [])]
    verifications = [_decode(PaperVerification, item) for item in data.get("verification_results", [])]
    relevances = [_decode(PaperRelevance, item) for item in data.get("relevance_results", [])]
    if papers and not relevances:
        raise ValueError("The JSON does not contain Phase 3 relevance_results; run the Research Agent first.")
    if len(relevances) not in (0, len(papers)):
        raise ValueError("Phase 3 relevance_results length does not match bibliography entries.")
    extraction = ResearchExtraction(
        source=data.get("source", "unknown_report"),
        literature_review=sections["literature_review"], references=sections["references"],
        literature_citations=[_decode(CitationMention, x) for x in data.get("literature_citations", [])],
        papers=papers,
        literature_links=[_decode(Hyperlink, x) for x in data.get("literature_links", [])],
        reference_links=[_decode(Hyperlink, x) for x in data.get("reference_links", [])],
        hyperlinks=[_decode(Hyperlink, x) for x in data.get("hyperlinks", [])],
        notes=list(data.get("notes", [])), verification_results=verifications,
        document_citations=[_decode(CitationMention, x) for x in data.get("document_citations", [])],
        citation_reference_matches=data.get("citation_reference_matches", []),
        relevance_results=relevances,
    )
    if as_of_date is None:
        as_of_date = (data.get("recency_summary") or {}).get("as_of_date")
    extraction.recency_results, extraction.recency_summary = analyze_recency(extraction, as_of_date=as_of_date)
    extraction.research_report = build_research_report(extraction, extraction.recency_summary)
    updated = dict(data)
    updated["recency_results"] = [asdict(x) for x in extraction.recency_results]
    updated["recency_summary"] = asdict(extraction.recency_summary)
    updated["research_report"] = asdict(extraction.research_report)
    markdown = render_research_report_markdown(extraction.research_report)
    return updated, markdown, extraction.recency_summary


def main():
    parser = argparse.ArgumentParser(description="Polish Phase 4A and final report from existing analysis JSON (offline).")
    parser.add_argument("analysis_json", type=Path, help="Path to a previous *_analysis.json")
    parser.add_argument("--as-of", dest="as_of", help="Override assessment date YYYY-MM-DD")
    args = parser.parse_args()
    if not args.analysis_json.is_file():
        parser.error(f"File not found: {args.analysis_json}")
    try:
        data = json.loads(args.analysis_json.read_text(encoding="utf-8-sig"))
        updated, markdown, summary = rebuild(data, as_of_date=args.as_of)
    except (ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    original = args.analysis_json.stem
    base = original[:-len("_analysis")] if original.endswith("_analysis") else original
    json_path = args.analysis_json.with_name(base + "_final_analysis.json")
    markdown_path = args.analysis_json.with_name(base + "_final_research_report.md")
    json_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    print(f"Distinct works: {summary.distinct_references}; confirmed duplicates: {summary.duplicate_reference_count}")
    print(f"Updated JSON: {json_path}")
    print(f"Polished report: {markdown_path}")
    print("Phase 1–3 results were reused unchanged. No models or APIs were called.")


if __name__ == "__main__":
    main()
