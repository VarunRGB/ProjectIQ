"""Extract candidate bibliography entries and lightweight metadata."""

import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import Hyperlink, PaperReference

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b")
_DOI_RE = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)|\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\]\[(){}]+")
_NUMBERED_START_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)])\s+\S")
_APA_START_RE = re.compile(
    r"^\s*[A-Z][A-Za-z'’\-]+(?:,\s*[^.]{1,30})?\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)"
)
_IEEE_START_RE = re.compile(r"^\s*(?:[A-Z]\.\s*)?[A-Z][A-Za-z'’\-]+,\s*(?:[A-Z]\.\s*)")
_QUOTED_TITLE_RE = re.compile(r'[\"“](.+?)[\"”]')
_APA_TITLE_RE = re.compile(r"\)\s*\.\s*(.+?)(?:\.\s|$)")


def _trim_url(value: str) -> str:
    return value.rstrip(".,;:!?\"'`")


def _reference_start(line: str) -> bool:
    return bool(_NUMBERED_START_RE.match(line) or _APA_START_RE.match(line) or _IEEE_START_RE.match(line))


def _split_entries(section_text: str) -> List[Tuple[str, int, int]]:
    """Return (entry text, local start line, local end line), retaining wrapped lines."""
    lines = section_text.splitlines()
    if not lines:
        return []

    starts = [i for i, line in enumerate(lines) if line.strip() and _reference_start(line)]
    if len(starts) >= 2:
        # Ignore any preamble before the first recognised bibliography item.
        ranges = [(start, starts[i + 1] if i + 1 < len(starts) else len(lines))
                  for i, start in enumerate(starts)]
        return [("\n".join(lines[a:b]).strip(), a + 1, b)
                for a, b in ranges if "\n".join(lines[a:b]).strip()]
    if len(starts) == 1:
        start = starts[0]
        return [("\n".join(lines[start:]).strip(), start + 1, len(lines))]

    # If entries are separated by blank lines, use those paragraphs. Otherwise
    # keep the entire unrecognised block together instead of splitting wrapped text.
    paragraphs = []
    current = []
    start_line = None
    for index, line in enumerate(lines, start=1):
        if line.strip():
            if start_line is None:
                start_line = index
            current.append(line.strip())
        elif current:
            paragraphs.append((" ".join(current), start_line, index - 1))
            current, start_line = [], None
    if current:
        paragraphs.append((" ".join(current), start_line, len(lines)))
    return paragraphs


def _extract_doi(raw: str) -> Optional[str]:
    match = _DOI_RE.search(raw)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return value.rstrip(".,;:)") if value else None


def _extract_urls(raw: str) -> List[str]:
    urls = []
    for match in _URL_RE.finditer(raw):
        value = _trim_url(match.group(0))
        if value.startswith("www."):
            value = "https://" + value
        if value not in urls:
            urls.append(value)
    return urls


def _extract_year(raw: str) -> Optional[int]:
    match = _YEAR_RE.search(raw)
    return int(match.group(1)) if match else None


def _extract_title(raw: str) -> Optional[str]:
    match = _QUOTED_TITLE_RE.search(raw)
    if match:
        return match.group(1).strip().strip(" ,.;:")
    match = _APA_TITLE_RE.search(raw)
    if match:
        candidate = match.group(1).strip().strip('"“”')
        return candidate or None
    # Last-resort: a title may follow the year and a full stop.
    year_match = _YEAR_RE.search(raw)
    if year_match:
        tail = raw[year_match.end():].lstrip(" .,:;)")
        candidate = re.split(r"\.\s+", tail, maxsplit=1)[0].strip()
        if 3 <= len(candidate.split()) <= 25 and not candidate.lower().startswith("https://"):
            return candidate.rstrip(".")
    return None


def _extract_authors(raw: str) -> Optional[str]:
    cleaned = re.sub(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)])\s*", "", raw)
    # In IEEE-like references, the quoted title appears before the publication
    # year. Prefer it as the author/title boundary when available.
    quote_match = _QUOTED_TITLE_RE.search(cleaned)
    if quote_match:
        prefix = cleaned[:quote_match.start()].strip(" .,:;")
        return prefix or None
    # In APA-like references, the year appears before the title.
    year_match = _YEAR_RE.search(cleaned)
    if year_match:
        prefix = cleaned[:year_match.start()].strip(" .,:;()")
        if 1 <= len(prefix.split()) <= 18:
            return prefix or None
    return None


def extract_references(section_text: str, hyperlinks: Sequence[Hyperlink] = (),
                       section_start_line: int = 0) -> List[PaperReference]:
    """Extract candidate reference records.

    Metadata extraction is intentionally heuristic. The raw citation is always
    retained so later paper-resolution code can validate rather than trust it.
    """
    papers: List[PaperReference] = []
    for raw, local_start, local_end in _split_entries(section_text):
        absolute_start = section_start_line + local_start
        absolute_end = section_start_line + local_end
        entry_links = [link.url for link in hyperlinks
                       if absolute_start <= link.line_number <= absolute_end]
        urls = _extract_urls(raw)
        for link in entry_links:
            if link not in urls:
                urls.append(link)

        doi = _extract_doi(raw)
        if doi:
            doi_url = f"https://doi.org/{doi}"
            if doi_url not in urls:
                urls.append(doi_url)

        year = _extract_year(raw)
        title = _extract_title(raw)
        authors = _extract_authors(raw)
        notes = []
        if not title:
            notes.append("Title could not be extracted confidently; review the raw reference.")
        if year is None:
            notes.append("Publication year was not found.")
        if not doi and not urls:
            notes.append("No DOI or URL was found in this reference entry.")

        papers.append(PaperReference(
            raw=raw,
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            urls=urls,
            start_line=absolute_start,
            end_line=absolute_end,
            extraction_notes=notes,
        ))
    return papers
