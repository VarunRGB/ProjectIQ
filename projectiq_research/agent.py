"""Phase 1 orchestration for the ProjectIQ Research Quality Agent."""

from pathlib import Path
from typing import Union

from .citation_extractor import extract_citations
from .document_parser import load_document
from .models import ResearchExtraction
from .reference_extractor import extract_references
from .section_detector import detect_sections


def analyze_document(path: Union[str, Path]) -> ResearchExtraction:
    """Parse a report and extract its Literature Review and References sections.

    Phase 1 does not judge recency or topical relevance and does not verify links
    against external databases. It only extracts report evidence for later phases.
    """
    document = load_document(path)
    literature, references, notes = detect_sections(document)

    citations = extract_citations(
        literature.content,
        line_offset=(literature.start_line or 0),
    ) if literature.found else []

    papers = extract_references(
        references.content,
        hyperlinks=document.hyperlinks,
        section_start_line=(references.start_line or 0),
    ) if references.found else []

    def links_in(section):
        if not section.found or section.start_line is None:
            return []
        start_line = section.start_line + 1  # content begins immediately after the heading
        end_line = section.end_line if section.end_line is not None else start_line
        return [link for link in document.hyperlinks if start_line <= link.line_number <= end_line]

    literature_links = links_in(literature)
    reference_links = links_in(references)

    if literature.found and not literature.content.strip():
        notes.append("Literature Review heading exists, but no content was extracted beneath it.")
    if references.found and not papers:
        notes.append("References heading exists, but no reference entries were extracted.")
    if not document.text.strip():
        notes.append("No text was extracted. The report may be scanned or image-only; OCR is not included in Phase 1.")

    return ResearchExtraction(
        source=document.source,
        literature_review=literature,
        references=references,
        literature_citations=citations,
        papers=papers,
        literature_links=literature_links,
        reference_links=reference_links,
        hyperlinks=document.hyperlinks,
        notes=notes,
    )
