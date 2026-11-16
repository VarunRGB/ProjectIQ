"""Offline tests for ProjectIQ Research Quality Agent - Phase 1."""

from pathlib import Path

import pytest

from projectiq_research.agent import analyze_document
from projectiq_research.citation_extractor import extract_citations
from projectiq_research.document_parser import load_document
from projectiq_research.models import ParsedDocument
from projectiq_research.reference_extractor import extract_references
from projectiq_research.section_detector import detect_sections


def test_detects_numbered_literature_review_and_bibliography_and_stops_at_next_heading():
    text = """1. Introduction
Background text.
2. Related Work
Prior work by Smith et al. (2023) [1] informs this project.
3. Methodology
This should not be part of related work.
4. Bibliography
[1] J. Smith, \"Deepfake Detection Using Vision Transformers,\" Journal, 2023.
5. Results
Not a reference.
"""
    doc = ParsedDocument("sample.txt", text)
    literature, references, notes = detect_sections(doc)

    assert literature.found and literature.heading == "2. Related Work"
    assert "Smith et al. (2023)" in literature.content
    assert "Methodology" not in literature.content
    assert references.found and references.heading == "4. Bibliography"
    assert "[1] J. Smith" in references.content
    assert "Results" not in references.content
    assert not notes


def test_detects_markdown_heading_aliases():
    text = """# Introduction
Intro.
## Literature Survey
Paper X [2] was discussed.
## References
[2] A. Author, \"Example Paper\", 2024.
"""
    document = ParsedDocument("sample.md", text, heading_lines={1, 3, 5})
    literature, references, _ = detect_sections(document)
    assert literature.found and "Paper X" in literature.content
    assert references.found and "Example Paper" in references.content



def test_numbered_reference_entries_are_not_mistaken_for_section_headings():
    text = """Literature Review
Prior work is discussed here.
References
1. Smith, J. (2023). Deepfake detection using transformers. Journal of AI.
2. Brown, P. (2024). Video manipulation detection. Conference.
Appendix
Supporting material.
"""
    document = ParsedDocument("numbered-refs.txt", text)
    literature, references, _ = detect_sections(document)
    assert literature.found and "Prior work" in literature.content
    assert references.found
    assert "1. Smith" in references.content
    assert "Appendix" not in references.content
    parsed = extract_references(references.content, section_start_line=references.start_line or 0)
    assert len(parsed) == 2
    assert parsed[0].year == 2023 and parsed[1].year == 2024



def test_detects_chapter_style_headings():
    text = """CHAPTER 1: INTRODUCTION
Intro.
CHAPTER 2: LITERATURE REVIEW
Earlier studies are summarised here.
CHAPTER 3: METHODOLOGY
Method details.
CHAPTER 4: REFERENCES
[1] Smith, J. (2024). Example paper.
"""
    document = ParsedDocument("chapter-report.txt", text)
    literature, references, _ = detect_sections(document)
    assert literature.found and "Earlier studies" in literature.content
    assert references.found and "Example paper" in references.content



def test_numbered_et_al_reference_lines_do_not_end_the_references_section():
    text = """References
1. Smith et al. (2023). A relevant paper. Journal.
2. Brown et al. (2024). Another paper. Conference.
Appendix
More content.
"""
    document = ParsedDocument("et-al-refs.txt", text)
    _, references, _ = detect_sections(document)
    assert references.found
    assert "2. Brown" in references.content
    assert "Appendix" not in references.content


def test_reports_when_both_sections_are_missing():
    document = ParsedDocument("missing.txt", "Title\nThis report has no expected sections.")
    literature, references, notes = detect_sections(document)
    assert not literature.found and not references.found
    assert any("Literature Review" in note for note in notes)
    assert any("References" in note for note in notes)


def test_reports_missing_literature_review_only():
    document = ParsedDocument("missing-literature.txt", "References\n[1] Example, 2024.")
    literature, references, notes = detect_sections(document)
    assert not literature.found and references.found
    assert any("Literature Review" in note for note in notes)


def test_reports_missing_references_only():
    document = ParsedDocument("missing-references.txt", "Literature Review\nPrior studies are discussed here.")
    literature, references, notes = detect_sections(document)
    assert literature.found and not references.found
    assert any("References" in note for note in notes)


def test_extracts_numeric_and_author_year_citations_with_line_numbers():
    text = """Prior work supports the approach [1, 2].
Smith et al. (2023) report similar results.
Other work (Jones and Lee, 2022) is also relevant.
"""
    citations = extract_citations(text)
    assert [(item.kind, item.line_number) for item in citations] == [
        ("numeric", 1), ("author_year", 2), ("author_year", 3)
    ]
    assert citations[0].text == "[1, 2]"


def test_extracts_numbered_reference_entries_and_metadata():
    text = """[1] J. Smith and A. Lee, \"Deepfake Detection Using Vision Transformers,\" Journal of AI, vol. 2, 2023. DOI: 10.1234/example.123
continued details on the next line.
[2] P. Brown, \"Robust Audiovisual Manipulation Detection,\" Conference Proceedings, 2025. https://example.org/paper
"""
    references = extract_references(text)
    assert len(references) == 2
    assert references[0].title == "Deepfake Detection Using Vision Transformers"
    assert references[0].year == 2023
    assert references[0].doi == "10.1234/example.123"
    assert "J. Smith" in (references[0].authors or "")
    assert references[0].end_line > references[0].start_line
    assert references[1].title == "Robust Audiovisual Manipulation Detection"
    assert references[1].year == 2025
    assert "https://example.org/paper" in references[1].urls


def test_extracts_apa_style_references_when_not_numbered():
    text = """Smith, J., & Lee, A. (2023). Deepfake detection using vision transformers. Journal of AI, 2(1), 10-20.
Brown, P. (2025). Robust audiovisual manipulation detection. Proceedings of ExampleConf.
"""
    references = extract_references(text)
    assert len(references) == 2
    assert references[0].year == 2023
    assert references[0].title == "Deepfake detection using vision transformers"
    assert references[1].year == 2025
    assert references[1].title == "Robust audiovisual manipulation detection"


def test_unrecognised_reference_block_is_preserved_instead_of_discarded():
    text = "An unusual citation format without a parseable year or title\nwith a wrapped continuation line."
    references = extract_references(text)
    assert len(references) == 1
    assert "wrapped continuation" in references[0].raw
    assert references[0].year is None
    assert references[0].title is None
    assert references[0].extraction_notes


def test_extracts_markdown_urls_from_text_file(tmp_path):
    file_path = tmp_path / "report.md"
    file_path.write_text("## References\n[Paper](https://example.org/paper)\n", encoding="utf-8")
    parsed = load_document(file_path)
    assert any(link.url == "https://example.org/paper" and link.text == "Paper" for link in parsed.hyperlinks)


def test_end_to_end_extraction_and_missing_section_notes(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text(
        """1. Introduction
An introduction.
2. Literature Review
Recent research by Smith et al. (2023) [1] addresses this problem: https://example.org/literature-paper
3. Methodology
Methods go here.
4. References
[1] J. Smith, \"Deepfake Detection Using Vision Transformers,\" Journal, 2023. https://example.org/smith
""",
        encoding="utf-8",
    )
    result = analyze_document(report)
    assert result.literature_review.found
    assert result.references.found
    assert len(result.literature_citations) == 2
    assert len(result.papers) == 1
    assert result.papers[0].year == 2023
    assert "https://example.org/smith" in result.papers[0].urls
    assert any(link.url == "https://example.org/literature-paper" for link in result.literature_links)
    assert any(link.url == "https://example.org/smith" for link in result.reference_links)
    payload = result.to_dict()
    assert payload["source"].endswith("report.txt")
    assert payload["papers"][0]["title"] == "Deepfake Detection Using Vision Transformers"


def test_empty_reference_section_is_reported(tmp_path):
    report = tmp_path / "empty.txt"
    report.write_text("Literature Review\nSome work [1].\nReferences\n", encoding="utf-8")
    result = analyze_document(report)
    assert result.references.found
    assert not result.papers
    assert any("no reference entries" in note.lower() for note in result.notes)


def test_unsupported_file_type_raises_clear_error(tmp_path):
    file_path = tmp_path / "report.rtf"
    file_path.write_text("text", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file type"):
        load_document(file_path)



def test_pdf_without_text_layer_is_handled_without_crashing(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    file_path = tmp_path / "scanned.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with file_path.open("wb") as handle:
        writer.write(handle)
    result = analyze_document(file_path)
    assert not result.literature_review.found
    assert not result.references.found
    assert any("not found" in note.lower() for note in result.notes)


def test_docx_headings_and_hyperlinks_are_preserved(tmp_path):
    docx = pytest.importorskip("docx")
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    file_path = tmp_path / "report.docx"
    document = docx.Document()
    document.add_paragraph("Introduction", style="Heading 1")
    document.add_paragraph("Intro text.")
    document.add_paragraph("Literature Review", style="Heading 1")
    document.add_paragraph("Prior study by Smith et al. (2023) [1].")
    document.add_paragraph("References", style="Heading 1")
    paragraph = document.add_paragraph("[1] J. Smith, \"Example Paper\", 2023. ")

    relationship_id = paragraph.part.relate_to(
        "https://example.org/paper",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Paper link"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    document.save(file_path)

    parsed = load_document(file_path)
    assert parsed.heading_lines
    assert any(link.url == "https://example.org/paper" for link in parsed.hyperlinks)
    result = analyze_document(file_path)
    assert result.literature_review.found and result.references.found
    assert result.papers and "https://example.org/paper" in result.papers[0].urls
