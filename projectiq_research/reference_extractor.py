"""Extract paper references from IEEE, numbered, APA and other common bibliographies.

The original citation text is preserved. Parsed metadata is heuristic and may be
missing rather than guessed, so Phase 2 can verify it against academic databases.
"""

import re
from typing import List, Optional, Sequence, Tuple

from .models import Hyperlink, PaperReference

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b", re.I)
_PAREN_YEAR_RE = re.compile(r"\(\s*((?:19|20)\d{2})[a-z]?\s*\)", re.I)
_DOI_RE = re.compile(
    r"(?i)(?<![\w/])(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?"
    r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)"
)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\]\[{}]+")
_BRACKET_LABEL_RE = re.compile(r"^\s*\[(\d{1,4})\]\s+(\S.*)$")
_PLAIN_LABEL_RE = re.compile(r"^\s*(\d{1,4})[.)]\s+(\S.*)$")
_QUOTED_TITLE_RE = re.compile(r'[“\"]([^”\"]+)[”\"]', re.S)
_APA_YEAR_TITLE_RE = re.compile(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)\s*\.\s*(.+)", re.I | re.S)


def _normalise_entry(raw: str) -> str:
    # PDF line wrapping can split a DOI (10.1007/s10032-\n019-00320-5),
    # hyphenated words, or titles. Keep the original raw text unchanged.
    text = re.sub(r"(?<=-)\s*\n\s*(?=[a-z0-9])", "", raw, flags=re.I)
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_label(value: str) -> str:
    return re.sub(r"^\s*(?:\[\d{1,4}\]|\d{1,4}[.)])\s+", "", value, count=1)


def _trim_url(value: str) -> str:
    value = value.rstrip(".,;:!?\"'`")
    # Discard only unmatched closing parentheses; parentheses can be legitimate
    # inside some URLs.
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def _looks_like_author_start(line: str) -> bool:
    """Recognise unnumbered author-year bibliography entries, conservatively."""
    value = line.strip()
    if not value or re.match(r"^(?:doi\s*:|https?://|www\.)", value, re.I):
        return False
    if _BRACKET_LABEL_RE.match(value) or _PLAIN_LABEL_RE.match(value):
        return False
    if not re.search(r"\b(?:19|20)\d{2}\b", value):
        return False
    # Smith, J. (2023); van der Waals, A. et al. (2020);
    # Smith, J., & Jones, L. 2024. Title...
    surname_initials = bool(re.match(r"^[A-ZÀ-ÖØ-Þ][\w'’\-]*(?:\s+(?:de|van|von|der|[A-Z][\w'’\-]*)){0,4},\s+[A-Z]", value))
    # Institutional author, e.g. "World Health Organization. (2023)."
    institutional = bool(re.match(r"^[A-Z][\w&\- ]{4,90}\.\s*\(\s*(?:19|20)\d{2}", value))
    return surname_initials or institutional


def _entries_from_starts(lines: List[str], starts: List[int]) -> List[Tuple[str, int, int]]:
    records = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        # PDF page numbers ("10" on its own line) sometimes occur mid-entry.
        kept = [line for line in lines[start:end] if not re.fullmatch(r"\s*\d{1,3}\s*", line)]
        raw = "\n".join(kept).strip()
        if raw:
            records.append((raw, start + 1, end))
    return records


def _split_entries(section_text: str) -> List[Tuple[str, int, int]]:
    """Return (raw, local start line, local end line), retaining wrapped lines."""
    lines = section_text.splitlines()
    if not lines:
        return []

    # A recognised numbered style takes priority; do not split an IEEE entry
    # at a continuation such as "446. doi: 10.1109/DAS.2018.75".
    bracket_starts = [i for i, line in enumerate(lines) if _BRACKET_LABEL_RE.match(line)]
    if bracket_starts:
        return _entries_from_starts(lines, bracket_starts)

    numbered_starts: List[int] = []
    last_number: Optional[int] = None
    for i, line in enumerate(lines):
        match = _PLAIN_LABEL_RE.match(line)
        if not match:
            continue
        number, remainder = int(match.group(1)), match.group(2)
        if number > 999 or re.match(r"^(?:doi\s*:|https?://|www\.|pp?\.?\s)", remainder, re.I):
            continue
        # Sequential reference numbers are much more reliable than assuming
        # that every line beginning with "123." starts a new bibliography item.
        if last_number is None:
            if number > 200:
                continue
        elif number != last_number + 1:
            continue
        numbered_starts.append(i)
        last_number = number
    if numbered_starts:
        return _entries_from_starts(lines, numbered_starts)

    author_starts = [i for i, line in enumerate(lines) if _looks_like_author_start(line)]
    if author_starts:
        return _entries_from_starts(lines, author_starts)

    # Unknown formats: preserve blank-line-separated paragraphs rather than
    # splitting on arbitrary uppercase words, punctuation or year numbers.
    paragraphs: List[Tuple[str, int, int]] = []
    start: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip() and start is None:
            start = idx
        if start is not None and (not line.strip() or idx == len(lines) - 1):
            end = idx if not line.strip() else idx + 1
            raw = "\n".join(lines[start:end]).strip()
            if raw:
                paragraphs.append((raw, start + 1, end))
            start = None
    return paragraphs


def _extract_doi(raw: str) -> Optional[str]:
    match = _DOI_RE.search(_normalise_entry(raw))
    if not match:
        return None
    doi = match.group(1).rstrip(".,;:!?")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    return doi or None


def _extract_urls(raw: str) -> List[str]:
    urls: List[str] = []
    for match in _URL_RE.finditer(_normalise_entry(raw)):
        value = _trim_url(match.group(0))
        if value.startswith("www."):
            value = "https://" + value
        if value not in urls:
            urls.append(value)
    return urls


def _extract_year(raw: str) -> Optional[int]:
    text = _normalise_entry(raw)
    paren = _PAREN_YEAR_RE.search(text)
    if paren:
        return int(paren.group(1))
    # Exclude DOI and hyperlinks before finding a publication year. Otherwise
    # an identifier like 10.1109/...2017... may be mistaken for the year.
    cleaned = _DOI_RE.sub(" ", text)
    cleaned = _URL_RE.sub(" ", cleaned)
    match = _YEAR_RE.search(cleaned)
    return int(match.group(1)) if match else None


def _is_plausible_title(value: str) -> bool:
    candidate = value.strip(" .,:;\"“”")
    return (bool(candidate) and len(candidate.split()) >= 2 and len(candidate) <= 400
            and not re.match(r"^(?:doi\s*:|https?://|www\.|available\s*:|online\b)", candidate, re.I)
            and not re.match(r"^10\.\d{4,9}/", candidate))


def _extract_title(raw: str) -> Optional[str]:
    text = _strip_label(_normalise_entry(raw))
    # IEEE / MLA / Chicago: title is inside “curly” or "straight" quotes.
    quoted = _QUOTED_TITLE_RE.search(text)
    if quoted:
        title = quoted.group(1).strip(" .,:;\"“”")
        if _is_plausible_title(title):
            return title

    # APA / Harvard: Smith, J. (2023). Title of the study. Journal ...
    apa = _APA_YEAR_TITLE_RE.search(text)
    if apa:
        candidate = re.split(r"\.\s+(?=[A-Z])|\.\s*$", apa.group(1), maxsplit=1)[0]
        candidate = candidate.strip(" .,:;\"“”")
        if _is_plausible_title(candidate):
            return candidate

    # Fallback for unquoted author-year variants, but only when the prefix
    # actually resembles an author; this avoids treating a DOI as a title.
    if _looks_like_author_start(text):
        year = _YEAR_RE.search(text)
        if year:
            tail = text[year.end():].lstrip(" .,:;)")
            candidate = re.split(r"\.\s+(?=[A-Z])|\.\s*$", tail, maxsplit=1)[0]
            candidate = candidate.strip(" .,:;\"“”")
            if _is_plausible_title(candidate):
                return candidate
    return None


def _extract_authors(raw: str) -> Optional[str]:
    text = _strip_label(_normalise_entry(raw))
    quote = _QUOTED_TITLE_RE.search(text)
    if quote:
        prefix = text[:quote.start()].strip(" .,:;")
        return prefix or None
    if _looks_like_author_start(text):
        year = _YEAR_RE.search(text)
        if year:
            prefix = text[:year.start()].strip(" .,:;()")
            if 1 <= len(prefix.split()) <= 45:
                return prefix
    return None


def extract_references(
    section_text: str,
    hyperlinks: Sequence[Hyperlink] = (),
    section_start_line: int = 0,
) -> List[PaperReference]:
    """Extract bibliography entries with source-line provenance and safe metadata.

    ``section_start_line`` is the 1-based line of the References heading.
    A missing title/DOI is reported, never fabricated from a guessed API match.
    """
    papers: List[PaperReference] = []
    for raw, local_start, local_end in _split_entries(section_text):
        absolute_start = section_start_line + local_start
        absolute_end = section_start_line + local_end
        entry_links = [link.url for link in hyperlinks
                       if absolute_start <= link.line_number <= absolute_end]
        urls = _extract_urls(raw)
        for url in entry_links:
            if url not in urls:
                urls.append(url)
        doi = _extract_doi(raw)
        if doi:
            doi_url = f"https://doi.org/{doi}"
            if doi_url not in urls:
                urls.append(doi_url)
        year = _extract_year(raw)
        title = _extract_title(raw)
        authors = _extract_authors(raw)
        notes: List[str] = []
        if not title:
            notes.append("Title could not be extracted confidently; review the raw reference.")
        if year is None:
            notes.append("Publication year was not found.")
        if not doi and not urls:
            notes.append("No DOI or URL was found in this reference entry.")
        papers.append(PaperReference(
            raw=raw, title=title, authors=authors, year=year, doi=doi, urls=urls,
            start_line=absolute_start, end_line=absolute_end, extraction_notes=notes,
        ))
    return papers
