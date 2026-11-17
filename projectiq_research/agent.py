"""Full orchestration for the ProjectIQ Research Quality Agent."""

from pathlib import Path
from typing import Union

from .citation_extractor import extract_citations, extract_document_citations
from .citation_linker import match_citations_to_references
from .document_parser import load_document
from .models import ParsedDocument, ResearchExtraction
from .reference_extractor import extract_references
from .section_detector import detect_sections
from .verification import verify_papers
from .relevance_scorer import assess_relevance
from .recency_analyzer import analyze_recency
from .research_report import build_research_report


def _analyse_parsed_document(document: ParsedDocument, verify: bool) -> ResearchExtraction:
    """Shared Phase 1/2 implementation; accept a parsed document to avoid re-reading files."""
    literature, references, notes = detect_sections(document)

    # This list is strictly limited to the Literature Review section.
    literature_citations = (
        extract_citations(
            literature.content,
            line_offset=(literature.start_line or 0),
        )
        if literature.found
        else []
    )

    papers = (
        extract_references(
            references.content,
            hyperlinks=document.hyperlinks,
            section_start_line=(references.start_line or 0),
        )
        if references.found
        else []
    )

    # These are separate from literature_citations and exclude bibliography text.
    document_citations = extract_document_citations(document, references)
    citation_reference_matches = match_citations_to_references(
        document_citations, papers
    )

    def links_in(section):
        if not section.found or section.start_line is None:
            return []
        start_line = section.start_line + 1
        end_line = section.end_line if section.end_line is not None else start_line
        return [
            link
            for link in document.hyperlinks
            if start_line <= link.line_number <= end_line
        ]

    literature_links = links_in(literature)
    reference_links = links_in(references)

    if literature.found and not literature.content.strip():
        notes.append(
            "Literature Review heading exists, but no content was extracted beneath it."
        )
    if references.found and not papers:
        notes.append(
            "References heading exists, but no reference entries were extracted."
        )
    if not document.text.strip():
        notes.append(
            "No text was extracted. The report may be scanned or image-only; "
            "OCR is not included."
        )

    result = ResearchExtraction(
        source=document.source,
        literature_review=literature,
        references=references,
        literature_citations=literature_citations,
        papers=papers,
        literature_links=literature_links,
        reference_links=reference_links,
        hyperlinks=document.hyperlinks,
        notes=notes,
        document_citations=document_citations,
        citation_reference_matches=citation_reference_matches,
    )

    # --------------------------------------------------
    # PHASE 2: ACADEMIC PAPER VERIFICATION
    # --------------------------------------------------
    if verify and papers:
        result.verification_results = verify_papers(result.papers)
    elif verify:
        result.verification_results = []
        result.notes.append(
            "Phase 2 skipped: no extracted paper references were available."
        )

    return result


def analyze_document(path: Union[str, Path], verify: bool = True) -> ResearchExtraction:
    """Backwards-compatible Phase 1/2 entry point (existing tests unchanged)."""
    return _analyse_parsed_document(load_document(path), verify=verify)


def analyze_research_document(
    path: Union[str, Path],
    verify: bool = True,
    *,
    encoder=None,
    cross_encoder=None,
    paper_abstracts=None,
    as_of_date=None,
) -> ResearchExtraction:
    """Full Research Agent: Phases 1–4A, in one future-orchestrator call.

    Unlike analyze_document, Phase 3 ALWAYS runs here. `verify=False` skips only
    online Phase 2, useful when evaluating the local neural scorer offline.
    Optional encoder/reranker injection enables tests and reuse by an orchestrator.
    """
    document = load_document(path)
    result = _analyse_parsed_document(document, verify=verify)
    result.relevance_results = assess_relevance(
        result, document, encoder=encoder, cross_encoder=cross_encoder,
        paper_abstracts=paper_abstracts,
    )
    result.recency_results, result.recency_summary = analyze_recency(result, as_of_date=as_of_date)
    result.research_report = build_research_report(result, result.recency_summary)
    return result
