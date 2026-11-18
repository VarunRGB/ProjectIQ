"""Regression tests for document-wide citations and bibliography mapping."""
from projectiq_research.citation_extractor import (
    extract_citations, extract_document_citations, expand_numeric_citation,
)
from projectiq_research.citation_linker import match_citations_to_references
from projectiq_research.models import ParsedDocument, PaperReference, Section


def ref(number, year=2020, authors="A. Smith"):
    return PaperReference(raw=f"[{number}] {authors}, Paper title, {year}.", title="Paper title",
                          authors=authors, year=year, doi=None, urls=[],
                          start_line=10+number, end_line=10+number)


def test_numeric_range_expansion():
    assert expand_numeric_citation("[20]-[22]") == [20, 21, 22]
    assert expand_numeric_citation("[2–4]") == [2, 3, 4]
    assert expand_numeric_citation("[1, 3; 5]") == [1, 3, 5]
    assert expand_numeric_citation("[1]") == [1]


def test_citations_from_document_excludes_bibliography():
    doc = ParsedDocument(source="test.txt", text=(
        "INTRODUCTION\nWe cite [1], [20]-[22].\n"
        "LITERATURE REVIEW\nSmith (2020) described this.\n"
        "REFERENCES\n[1] A. Smith, a title (2020).\n"
        "[20] A. Smith, paper (2020).\n[21] B. Author (2020).\n[22] C. Author (2020).\n"
        "APPENDIX\nNo citations here.\n"))
    section = Section(key="references", found=True, heading="REFERENCES", start_line=5, end_line=9)
    citations = extract_document_citations(doc, section)
    assert [c.text for c in citations] == ["[1]", "[20]-[22]", "Smith (2020)"]
    assert [c.line_number for c in citations] == [2, 2, 4]


def test_non_literature_doc_still_has_citations():
    doc = ParsedDocument(source="test.txt", text="INTRODUCTION\nPrior work [1] supports our work.\nREFERENCES\n[1] A. Smith, A Study, 2020.")
    section = Section(key="references", found=True, start_line=3, end_line=4)
    citations = extract_document_citations(doc, section)
    assert len(citations) == 1 and citations[0].text == "[1]"


def test_matches_numeric_ranges_to_correct_reference_indices():
    citations = extract_citations("Prior research [1] and [20]-[22].")
    papers = [ref(1), ref(20), ref(21), ref(22)]
    results = match_citations_to_references(citations, papers)
    assert results[0]["matched_paper_indices"] == [0]
    assert results[1]["matched_paper_indices"] == [1, 2, 3]
    assert results[1]["status"] == "matched"


def test_unresolved_numeric_reference_is_reported():
    result = match_citations_to_references(extract_citations("Reference [9]"), [ref(1)])[0]
    assert result["status"] == "unresolved"
    assert result["unresolved_reference_numbers"] == [9]


def test_simple_author_year_link():
    citations = extract_citations("Smith (2020) examined this.")
    results = match_citations_to_references(citations, [ref(1, 2020, "J. Smith")])
    assert len(results) == 1
    assert results[0]["matched_paper_indices"] == [0]
