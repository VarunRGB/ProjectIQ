"""Corpus/integration tests for the synthetic research-report fixtures.

Run from the ProjectIQ root after copying test_cases/research and this file:
    python -m pytest tests/test_research_fixture_corpus.py -v
"""

import json
from pathlib import Path

import pytest

from projectiq_research.agent import analyze_document
from projectiq_research.document_parser import load_document

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "test_cases" / "research"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
FIXTURES = MANIFEST["fixtures"]


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["file"])
def test_fixture_matches_expected_sections_and_paper_count(case):
    """Every supplied document should produce its documented extraction shape."""
    result = analyze_document(FIXTURE_DIR / case["file"])

    assert result.literature_review.found is case["literature_review"]
    assert result.references.found is case["references"]
    assert len(result.papers) == case["expected_papers"]


@pytest.mark.parametrize(
    "filename, expected_alias",
    [
        ("02_author_year_apa.md", "## Related Work"),
        ("03_chapter_uppercase.txt", "CHAPTER 2: LITERATURE REVIEW"),
        ("04_markdown_heading_aliases.md", "## Review of Literature"),
        ("10_apa_style_references.txt", "LITERATURE SURVEY"),
        ("13_noisy_and_unrecognised_references.txt", "2. PREVIOUS STUDIES"),
        ("20_sources_alias.md", "## Background and Related Work"),
    ],
)
def test_heading_aliases_are_preserved_in_extracted_section(filename, expected_alias):
    result = analyze_document(FIXTURE_DIR / filename)
    assert result.literature_review.found
    assert result.literature_review.heading == expected_alias


def test_missing_section_cases_return_explicit_notes():
    missing_lit = analyze_document(FIXTURE_DIR / "05_missing_literature_review.txt")
    assert not missing_lit.literature_review.found
    assert any("Literature Review" in note for note in missing_lit.notes)

    missing_refs = analyze_document(FIXTURE_DIR / "06_missing_references.md")
    assert not missing_refs.references.found
    assert any("References" in note for note in missing_refs.notes)

    missing_both = analyze_document(FIXTURE_DIR / "07_both_sections_missing.txt")
    assert not missing_both.literature_review.found
    assert not missing_both.references.found
    assert len(missing_both.notes) >= 2


def test_empty_references_heading_is_not_mistaken_for_populated_bibliography():
    result = analyze_document(FIXTURE_DIR / "08_empty_references_section.txt")
    assert result.references.found
    assert result.papers == []
    assert any("no reference entries" in note.lower() for note in result.notes)


def test_wrapped_reference_entries_keep_continuation_lines_and_metadata():
    result = analyze_document(FIXTURE_DIR / "09_wrapped_ieee_references.txt")
    assert len(result.papers) == 2
    first, second = result.papers
    assert first.year == 2022
    assert first.doi == "10.2468/netsec.2022.17"
    assert "International Journal of Network Security" in first.raw
    assert "\n" in first.raw  # wrapped continuation remains part of the same entry
    assert "International Journal of Network Security" in first.raw
    assert second.year == 2024
    assert "https://example.org/transformer-ids" in second.urls


def test_unknown_reference_entries_are_retained_for_later_review():
    result = analyze_document(FIXTURE_DIR / "13_noisy_and_unrecognised_references.txt")
    assert len(result.papers) == 2
    assert all(paper.raw.strip() for paper in result.papers)
    assert any(paper.year is None for paper in result.papers)
    assert any(paper.extraction_notes for paper in result.papers)


def test_citation_extraction_handles_numeric_ranges_and_author_year_forms():
    result = analyze_document(FIXTURE_DIR / "12_dense_citations_and_link_variants.md")
    values = [(item.text, item.kind) for item in result.literature_citations]
    assert ("[1, 2, 4-6]", "numeric") in values
    assert ("Kim et al. (2021)", "author_year") in values
    assert ("(Garcia and Miller, 2023)", "author_year") in values


def test_report_can_have_references_without_any_in_text_citation_markers():
    result = analyze_document(FIXTURE_DIR / "14_no_citations_but_references_present.md")
    assert result.references.found
    assert len(result.papers) == 1
    assert result.literature_citations == []


def test_docx_heading_styles_and_clickable_links_are_extracted():
    result = analyze_document(FIXTURE_DIR / "16_docx_heading_styles_and_clickable_links.docx")
    assert result.literature_review.found
    assert result.references.found
    assert any(link.url == "https://example.org/adaptive-signals" for link in result.literature_links)
    assert any(link.url == "https://example.org/traffic-paper" for link in result.reference_links)
    assert result.papers[0].year == 2022


def test_docx_chapter_headings_and_numbered_references_are_extracted():
    result = analyze_document(FIXTURE_DIR / "17_docx_chapter_headings_numbered_refs.docx")
    assert result.literature_review.found
    assert result.references.found
    assert len(result.papers) == 2
    assert [paper.year for paper in result.papers] == [2020, 2023]


def test_selectable_pdf_text_and_link_annotations_are_extracted():
    parsed = load_document(FIXTURE_DIR / "18_pdf_selectable_text_and_links.pdf")
    result = analyze_document(FIXTURE_DIR / "18_pdf_selectable_text_and_links.pdf")

    assert "LITERATURE REVIEW" in parsed.text
    assert result.literature_review.found
    assert result.references.found
    assert len(result.papers) == 2
    # PDF annotations are page-level in the current parser. Verify they were
    # collected, but do not assert exact association with an individual section.
    assert any(
        link.url == "https://example.org/forest-publisher"
        and link.source.startswith("pdf-page-")
        for link in result.hyperlinks
    )
    assert any(link.url == "https://example.org/forest-dataset" for link in result.literature_links)


def test_image_only_pdf_reports_ocr_limitation_without_crashing():
    result = analyze_document(FIXTURE_DIR / "19_scanned_style_image_only.pdf")
    assert not result.literature_review.found
    assert not result.references.found
    assert any("OCR is not included" in note for note in result.notes)


def test_duplicate_literature_sections_are_reported_and_first_is_used():
    result = analyze_document(FIXTURE_DIR / "11_multiple_literature_sections.txt")
    assert result.literature_review.found
    assert "first literature review" in result.literature_review.content.lower()
    assert "second literature-review-style" not in result.literature_review.content.lower()
    assert any("Multiple Literature Review headings" in note for note in result.notes)


def test_manifest_covers_all_supported_report_formats():
    formats = {case["format"] for case in FIXTURES}
    assert formats == {"txt", "md", "docx", "pdf"}


def test_all_manifest_files_exist():
    for case in FIXTURES:
        assert (FIXTURE_DIR / case["file"]).is_file()


def test_sources_heading_is_recognised_as_the_references_section():
    result = analyze_document(FIXTURE_DIR / "20_sources_alias.md")
    assert result.references.found
    assert result.references.heading == "## Sources"
    assert len(result.papers) == 1
