"""Extract paper references from IEEE, numbered, APA and other common bibliographies.

The original citation text is preserved. Parsed metadata is heuristic and may be
missing rather than guessed, so Phase 2 can verify it against academic databases.
"""

import re
from typing import List, Optional, Sequence, Tuple

from .models import Hyperlink, PaperReference
from .url_utils import extend_wrapped_url

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


# PDF fonts commonly encode Latin ligatures as single glyphs (e.g. "ﬁ").
# Normalize only those glyphs for matching; PaperReference.raw is never changed.
_PDF_LIGATURES = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
})

# In some IEEE/ACM proceedings, references are written as
#   [2] A. Author, B. Author: Unquoted title, Proc. Conference, 2007.
# These are strong venue transitions, not arbitrary commas within a title.
_COLON_TITLE_VENUE_RE = re.compile(
    r",\s*(?=(?:in\s+)?(?:proc(?:eedings)?\.?\b|ieee\b|acm\b|"
    r"international\b|journal\b|transactions?\b|trans\.?\s|"
    r"conference\b|conf\.?\s|workshop\b|symposium\b|"
    r"\d+(?:st|nd|rd|th)\b|vol(?:ume)?\.?\s|pp?\.?\s|arxiv\b))",
    re.I,
)
_INITIAL_RE = re.compile(r"(?<![A-Za-z])(?:[A-Z]\.\s*){1,4}")
_NAME_WORD_RE = re.compile(r"^[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+$")
_NON_AUTHOR_PREFIX = {
    "available", "online", "doi", "url", "source", "github", "website",
    "accessed", "proceedings", "conference", "international", "journal",
    "publisher", "publication", "university", "institute", "copyright",
}


def _split_colon_author_title(text: str) -> Optional[Tuple[str, str]]:
    """Extract a conservative author/title boundary for unquoted colon styles.

    A colon in a DOI, URL, venue or paper title is not sufficient. The prefix
    must look like a list of people. Any uncertain item is left unparsed.
    """
    match = re.match(r"^(?P<authors>[^:]{3,180}):\s+(?P<rest>.+)$", text, re.S)
    if match is None:
        return None
    authors = match.group("authors").strip(" .,:;")
    remainder = match.group("rest").strip()
    if (not authors or len(authors.split()) > 28
            or re.search(r"(?:https?://|www\.|\bdoi\b|\b(?:19|20)\d{2}\b|\d)", authors, re.I)):
        return None
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ\s.,'’&\-]+", authors):
        return None
    if authors.split()[0].lower().rstrip(".") in _NON_AUTHOR_PREFIX:
        return None

    # Most conventional proceedings entries include initials ("R. Smith").
    # Also support fully spelled names ("John Smith and Jane Doe") when each
    # component is name-like. This deliberately excludes institutional labels.
    if _INITIAL_RE.search(authors):
        if not re.search(r"\b[A-Z][a-zÀ-ÖØ-öø-ÿ'’\-]{1,}\b", authors):
            return None
    else:
        name_tokens = re.split(r"\s+(?:and|&)\s+|,\s*|\s+", authors)
        if not (2 <= len(name_tokens) <= 8 and all(
            token in {"", "and", "&"} or _NAME_WORD_RE.fullmatch(token)
            for token in name_tokens
        )):
            return None
        if any(token.lower() in _NON_AUTHOR_PREFIX for token in name_tokens):
            return None

    venue = _COLON_TITLE_VENUE_RE.search(remainder)
    if venue:
        title = remainder[:venue.start()]
    else:
        # No explicit venue: only remove a *terminal* publication-year field.
        title = re.sub(r",\s*(?:\(?[12]\d{3}\)?[a-z]?\.?\s*)$", "", remainder, flags=re.I)
    title = title.strip(" .,:;\"“”")
    if not _is_plausible_title(title):
        return None
    if _YEAR_RE.search(title) and len(title.split()) > 30:
        return None
    return authors, title


def _looks_like_person_author_list(prefix: str) -> bool:
    """Validate the author side of a period-separated bibliography item.

    Supports long lists of full names and initials, without assuming a research
    domain or matching a paper title from external metadata.  Do not parse an
    organization or arbitrary prose as an academic author by default.
    """
    if (not prefix or len(prefix) > 300 or len(prefix.split()) > 55
            or re.search(r"(?:https?://|www\.|\bdoi\b|\d)", prefix, re.I)):
        return False
    if re.search(r"[!?;:]", prefix):
        return False
    parts = re.split(r"\s*,\s*(?:and|&)\s+|\s+(?:and|&)\s+|\s*,\s*", prefix, flags=re.I)
    parts = [part.strip() for part in parts if part.strip()]
    if not parts or len(parts) > 18:
        return False
    forbidden = _NON_AUTHOR_PREFIX | {
        "abstract", "methods", "background", "introduction", "overview", "algorithm",
        "research", "review", "detecting", "training", "model", "study",
        "paper", "pages", "system", "network", "security", "data", "volume",
        "chapter", "available", "technical", "proceeding", "study", "table",
    }
    for person in parts:
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:[-'’][A-Za-zÀ-ÖØ-öø-ÿ]+)*", person)
        if not (2 <= len(words) <= 8):
            return False
        if any(word.casefold() in forbidden for word in words):
            return False
        # PDF text extraction sometimes splits proper names into 'Y uval'
        # or 'V an'. A majority must still look like actual name components.
        capitalized = sum(word[0].isupper() for word in words)
        if capitalized / len(words) < 0.60:
            return False
        # An intermediate initial is fine; a trailing initial usually signals
        # that the author list is incomplete (e.g. 'A. L.' before 'Spitz').
        if len(words[-1]) == 1 and words[-1].upper() not in {"I", "V"}:
            # PDF extractors can split a surname into 'Y u'. Permit the
            # malformed terminal token only when it follows a single initial
            # and an otherwise credible full given name.
            broken_surname = (len(words) >= 3 and words[-1].islower()
                              and len(words[-2]) == 1 and words[-2].isupper()
                              and len(words[-3]) >= 2 and words[-3][0].isupper())
            if not broken_surname:
                return False
    return True


def _split_period_author_title(text: str) -> Optional[Tuple[str, str]]:
    """Parse unquoted 'Authors. Title. Venue, year' references.

    Distinguishes author initials from an author-list delimiter; supports
    titles containing colons and question marks, and conference/arXiv venues.
    The pair is returned only when the names and title have credible bounds.
    """
    for match in re.finditer(r"\.\s+(?=\S)", text):
        authors = text[:match.start()].strip(" .,;:")
        if not _looks_like_person_author_list(authors):
            continue
        remainder = text[match.end():].strip()
        if (not remainder or remainder[0].isdigit()
                or remainder.startswith(("(", "https://", "http://"))):
            continue
        # A year or edition number often follows a finished title. For a
        # venue, the first full stop marks the end of the unquoted title.
        # Internal question marks and colons remain part of the title.
        title_end = re.search(r"\.\s+(?=\S)", remainder)
        if title_end:
            title = remainder[:title_end.start()]
        else:
            title = remainder
        title = re.sub(r",\s*(?:19|20)\d{2}[a-z]?\.?\s*$", "", title, flags=re.I)
        title = title.strip(" .,:;\"“”")
        if not _is_plausible_title(title):
            continue
        if len(title.split()) > 32 or re.search(r"https?://|\bdoi\s*:", title, re.I):
            continue
        if re.match(
            r"^(?:In |Proceedings |(?:IEEE|ACM) (?:International |Symposium |Conference |Transactions ))",
            title, re.I,
        ):
            continue
        return authors, title
    return None


def _looks_like_web_resource(raw: str) -> bool:
    """Recognise obvious named web/software entries without inventing papers."""
    text = _strip_label(_normalise_entry(raw))
    if not _URL_RE.search(text) or _extract_year(raw) is not None:
        return False
    label = _URL_RE.sub("", text).strip(" .,:;()")
    return bool(label and 2 <= len(label.split()) <= 16
                and not _QUOTED_TITLE_RE.search(label)
                and not _split_colon_author_title(label))


def _normalise_entry(raw: str) -> str:
    # PDF line wrapping can split a DOI (10.1007/s10032-\n019-00320-5),
    # hyphenated words, or titles. Keep the original raw text unchanged.
    text = re.sub(r"(?<=-)\s*\n\s*(?=[a-z0-9])", "", raw.translate(_PDF_LIGATURES), flags=re.I)
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
    lines = raw.translate(_PDF_LIGATURES).splitlines()
    for index, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            value = match.group(0)
            if match.end() == len(line.rstrip()):
                value = extend_wrapped_url(value, lines, index)
            value = _trim_url(value)
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

    # Numbered, unquoted colon style: A. Author: Title, Proc. ... 2007.
    colon = _split_colon_author_title(text)
    if colon:
        return colon[1]

    # IEEE/USENIX/ACM unquoted style: Authors. Title. Venue / arXiv, year.
    # A URL-only, undated software or web citation must not be promoted into
    # an academic-paper lookup solely because it names a human author.
    period = None if (_URL_RE.search(text) and _extract_year(raw) is None) else _split_period_author_title(text)
    if period:
        return period[1]

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
    # Initials-first author lists followed by a parenthesized publication year
    # are not period-title format. Validate the whole candidate name list.
    year_boundary = re.match(r"^(.{3,300}?)\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\)\s*[.,]?\s+", text)
    if year_boundary:
        prefix = year_boundary.group(1).strip(" .,:;")
        if _looks_like_person_author_list(prefix):return prefix
    quote = _QUOTED_TITLE_RE.search(text)
    if quote:
        prefix = text[:quote.start()].strip(" .,:;")
        return prefix or None
    colon = _split_colon_author_title(text)
    if colon:
        return colon[0]
    period = None if (_URL_RE.search(text) and _extract_year(raw) is None) else _split_period_author_title(text)
    if period:
        return period[0]
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
        if _looks_like_web_resource(raw):
            notes.append("Likely a web or software resource, not an academic paper; retain its URL for manual review.")
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
