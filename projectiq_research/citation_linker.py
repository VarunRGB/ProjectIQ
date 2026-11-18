"""Link extracted in-text citations to parsed bibliography entries.

Matching is conservative: numeric references use explicitly numbered entries;
author-year references need a unique first-author surname + year match.
"""

import re
from typing import List, Optional

from .citation_extractor import expand_numeric_citation
from .models import CitationMention, PaperReference

_BIB_NUMBER_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])\s+")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_AUTHOR_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*")


def _author_year_candidates(citation: CitationMention, papers: List[PaperReference]) -> List[int]:
    """Conservative fallback for (Smith, 2020) and Smith (2020)."""
    years = _YEAR_RE.findall(citation.text)
    if len(years) != 1:
        return []
    cited_year = int(years[0][:4])
    prefix = citation.text.lstrip("( ")
    author = _AUTHOR_RE.match(prefix)
    if not author:
        return []
    surname = author.group(0).casefold()
    if surname in {"in", "the", "see", "figure", "table", "chapter"}:
        return []
    candidates = []
    for index, paper in enumerate(papers):
        if paper.year != cited_year or not paper.authors:
            continue
        # Only inspect the author field, never the title or publication venue.
        # A first surname at the start (Smith, J) or following initials (J. Smith)
        # is enough for a *candidate*, not proof of reference identity.
        first_author = re.split(r",\s+(?=[A-Z](?:\.|[a-z]))|\s+and\s+|\s*&\s*", paper.authors, maxsplit=1)[0]
        name_tokens = [word.casefold() for word in _AUTHOR_RE.findall(first_author)]
        if surname in name_tokens:
            candidates.append(index)
    return candidates


def match_citations_to_references(
    citations: List[CitationMention], papers: List[PaperReference]
) -> List[dict]:
    """Produce JSON-compatible per-mention reference matches.

    `matched_paper_indices` are zero-based indices into the `papers` array.
    `reference_numbers` are the numbers appearing in a numeric marker.
    """
    numbers = {}
    for index, paper in enumerate(papers):
        match = _BIB_NUMBER_RE.match(paper.raw)
        if match:
            number = int(match.group(1) or match.group(2))
            numbers.setdefault(number, []).append(index)

    results = []
    for citation in citations:
        if citation.kind == "numeric":
            cited_numbers = expand_numeric_citation(citation.text)
            matched_indices = []
            unresolved = []
            ambiguous = []
            for number in cited_numbers:
                indices = numbers.get(number, [])
                if len(indices) == 1:
                    matched_indices.append(indices[0])
                elif len(indices) == 0:
                    unresolved.append(number)
                else:
                    ambiguous.append(number)
            status = ("ambiguous" if ambiguous else "unresolved" if unresolved or not cited_numbers
                      else "matched")
            results.append({
                "citation_text": citation.text,
                "line_number": citation.line_number,
                "kind": citation.kind,
                "reference_numbers": cited_numbers,
                "matched_paper_indices": matched_indices,
                "unresolved_reference_numbers": unresolved,
                "ambiguous_reference_numbers": ambiguous,
                "status": status,
            })
        else:
            indices = _author_year_candidates(citation, papers)
            results.append({
                "citation_text": citation.text,
                "line_number": citation.line_number,
                "kind": citation.kind,
                "reference_numbers": [],
                "matched_paper_indices": indices if len(indices) == 1 else [],
                "unresolved_reference_numbers": [],
                "ambiguous_reference_numbers": [],
                "status": "matched" if len(indices) == 1 else "ambiguous" if indices else "unresolved",
            })
    return results
