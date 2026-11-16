"""Extract common numeric and author-year in-text citation markers."""

import re
from typing import List

from .models import CitationMention

_NUMERIC_CITATION_RE = re.compile(r"\[(\s*\d{1,3}(?:\s*[-,;]\s*\d{1,3})*)\]")
_PAREN_AUTHOR_YEAR_RE = re.compile(
    r"\(([A-Z][A-Za-z'’\-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'’\-]+))?"
    r"(?:[^()]{0,65}?)\b(?:19|20)\d{2}[a-z]?[^()]*)\)"
)
_NARRATIVE_AUTHOR_YEAR_RE = re.compile(
    r"\b([A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.?)?)\s*\(\s*((?:19|20)\d{2}[a-z]?)\s*\)"
)


def extract_citations(text: str, line_offset: int = 0) -> List[CitationMention]:
    """Extract citation markers from literature-review text.

    This deliberately extracts citation markers, not full bibliographic records.
    Resolving markers to reference entries is a later integration step.
    """
    matches = []
    for match in _NUMERIC_CITATION_RE.finditer(text):
        matches.append((match.start(), match.group(0), "numeric"))
    for match in _PAREN_AUTHOR_YEAR_RE.finditer(text):
        matches.append((match.start(), match.group(0), "author_year"))
    for match in _NARRATIVE_AUTHOR_YEAR_RE.finditer(text):
        matches.append((match.start(), match.group(0), "author_year"))

    results: List[CitationMention] = []
    seen = set()
    for position, raw, kind in sorted(matches, key=lambda item: item[0]):
        key = (position, raw, kind)
        if key in seen:
            continue
        seen.add(key)
        line_number = line_offset + text.count("\n", 0, position) + 1
        results.append(CitationMention(raw, kind, line_number))
    return results
