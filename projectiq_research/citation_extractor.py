"""Extract in-text citations from a section or a document body.

The document-wide extractor retains source line numbering and deliberately
excludes the References section. It does NOT treat an Introduction as a
Literature Review.
"""

import re
from typing import List

from .models import CitationMention, ParsedDocument, Section

# [1], [1, 3], [2-5], [20]–[22]. For grouped bracket ranges, one
# CitationMention is returned; use expand_numeric_citation to expand it.
_NUMERIC_CITATION_RE = re.compile(
    r"\[\s*\d{1,3}(?:\s*[-\u2013\u2014,;]\s*\d{1,3})*\s*\]"
    r"(?:\s*[-\u2013\u2014]\s*\[\s*\d{1,3}\s*\])?"
)
_PAREN_AUTHOR_YEAR_RE = re.compile(
    r"\(([A-Z][A-Za-z'’\-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'’\-]+))?"
    r"(?:[^()]{0,65}?)\b(?:19|20)\d{2}[a-z]?[^()]*)\)"
)
_NARRATIVE_AUTHOR_YEAR_RE = re.compile(
    r"\b([A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.?)?)\s*\(\s*((?:19|20)\d{2}[a-z]?)\s*\)"
)
_BRACKETED_RANGE_RE = re.compile(
    r"^\[\s*(\d{1,3})\s*\]\s*[-\u2013\u2014]\s*\[\s*(\d{1,3})\s*\]$"
)


def extract_citations(text: str, line_offset: int = 0) -> List[CitationMention]:
    """Extract numeric and author-year citation mentions from any text.

    `line_offset` is the number of document lines preceding `text`.
    Citations are returned in occurrence order, with 1-based source lines.
    """
    matches = []
    for match in _NUMERIC_CITATION_RE.finditer(text):
        matches.append((match.start(), match.end(), match.group(0), "numeric"))
    for match in _PAREN_AUTHOR_YEAR_RE.finditer(text):
        matches.append((match.start(), match.end(), match.group(0), "author_year"))
    for match in _NARRATIVE_AUTHOR_YEAR_RE.finditer(text):
        matches.append((match.start(), match.end(), match.group(0), "author_year"))

    results: List[CitationMention] = []
    seen = set()
    for start, end, raw, kind in sorted(matches, key=lambda item: (item[0], item[1])):
        key = (start, end, raw, kind)
        if key in seen:
            continue
        seen.add(key)
        line_number = line_offset + text.count("\n", 0, start) + 1
        results.append(CitationMention(raw, kind, line_number))
    return results


def expand_numeric_citation(marker: str) -> List[int]:
    """Expand [3], [1, 4], [2-5], and [20]–[22] into reference numbers.

    Invalid / reversed ranges stay unresolved rather than producing wrong IDs.
    """
    marker = marker.strip()
    outer_range = _BRACKETED_RANGE_RE.fullmatch(marker)
    if outer_range:
        start, end = map(int, outer_range.groups())
        return list(range(start, end + 1)) if start <= end and end - start <= 100 else []
    if not (marker.startswith("[") and marker.endswith("]")):
        return []
    content = marker[1:-1]
    numbers: List[int] = []
    for part in re.split(r"[,;]", content):
        part = part.strip()
        range_match = re.fullmatch(r"(\d{1,3})\s*[-\u2013\u2014]\s*(\d{1,3})", part)
        if range_match:
            start, end = map(int, range_match.groups())
            if start > end or end - start > 100:
                return []
            values = range(start, end + 1)
        elif re.fullmatch(r"\d{1,3}", part):
            values = [int(part)]
        else:
            return []
        for number in values:
            if number not in numbers:
                numbers.append(number)
    return numbers


def extract_document_citations(document: ParsedDocument, references: Section) -> List[CitationMention]:
    """Extract citations across the report while excluding the bibliography.

    Replaces bibliography characters with spaces rather than deleting lines,
    so document-wide citation line numbers remain accurate.
    """
    lines = document.text.splitlines(keepends=True)
    if references.found and references.start_line is not None:
        start = references.start_line
        end = references.end_line if references.end_line is not None else len(lines)
        for index in range(max(0, start - 1), min(len(lines), end)):
            lines[index] = re.sub(r"[^\r\n]", " ", lines[index])
    return extract_citations("".join(lines))
