"""PDF ingestion with PyMuPDF.

Turns a research-paper PDF into a structured :class:`Document`:
pages (with numbers), detected sections, sentences tagged with
(section, page, index), and a parsed reference list.

Cleaning steps: repeated header/footer removal, page-number removal,
hyphenation repair ("experi-\\nment" -> "experiment") and whitespace cleanup.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import SECTION_ALIASES
from .preprocessing import split_sentences

logger = logging.getLogger(__name__)

FRONT_MATTER = "Front Matter"


class PDFExtractionError(RuntimeError):
    """Raised when a PDF cannot be opened or read."""


class OCRRequiredError(PDFExtractionError):
    """Raised when a PDF has no selectable text (probably a scanned document)."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Page:
    """One PDF page after cleaning."""

    number: int  # 1-based
    text: str


@dataclass
class Sentence:
    """A sentence with its location in the document."""

    index: int
    text: str
    section: str          # standard label used by the analysis (e.g. "Related Work")
    page: int
    heading: str = ""     # the paper's own heading (e.g. "Literature Survey")


@dataclass
class Reference:
    """One entry of the paper's reference list."""

    number: int
    text: str
    first_author: str = ""
    year: str = ""


@dataclass
class Section:
    """A detected section and its cleaned text."""

    name: str
    heading: str
    start_page: int
    text: str = ""


@dataclass
class Document:
    """Structured representation of a research paper."""

    source: str
    title: str
    pages: list[Page]
    sections: list[Section]
    sentences: list[Sentence]
    references: dict[int, Reference] = field(default_factory=dict)
    reference_style: str = "none"  # "numbered" | "author-year" | "none"
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """All page text joined."""
        return "\n".join(p.text for p in self.pages)

    def section_names(self) -> list[str]:
        """Names of detected sections, in order."""
        return [s.name for s in self.sections]

    def heading_of(self, sentence_index: int) -> str:
        """The paper's own heading for a sentence (falls back to the standard section label)."""
        if 0 <= sentence_index < len(self.sentences):
            s = self.sentences[sentence_index]
            return s.heading or s.section
        return ""

    def body_sentences(self) -> list[Sentence]:
        """Sentences outside the References section and the front matter (title, certificate, TOC...)."""
        return [s for s in self.sentences if s.section not in ("References", FRONT_MATTER)]

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        d = asdict(self)
        d["references"] = {str(k): asdict(v) for k, v in self.references.items()}
        return d


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
_HEADING_NUM_RE = re.compile(r"^\s*(?:(?:\d+(?:\.\d+)*)|(?:[IVXLC]+))[.)]?\s+", re.IGNORECASE)
_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)


def _normalize_for_repeat(line: str) -> str:
    """Normalise a line so headers/footers differing only by page number match."""
    return re.sub(r"\d+", "#", line.strip().lower())


_CHAPTER_RE = re.compile(r"^\s*(?:chapter|ch\.|section|unit|part)\s+(?:\d+|[ivxlc]+)\s*[:.\-\u2013\u2014]?\s*", re.IGNORECASE)
_TOC_LINE_RE = re.compile(r"(?:\.\s*){4,}|\u2026{2,}|\s{2,}\d{1,3}\s*$")
_TOC_TITLE_RE = re.compile(r"^\s*(?:(?:table\s+of\s+)?contents|index(?:\s+table)?|index\s+of\s+contents)\s*$",
                           re.IGNORECASE)
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\S")

# Keyword fallback for headings that are not an exact alias ("CHAPTER 4: RESULTS AND DISCUSSION").
# Order matters: the first keyword found wins.
_HEADING_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("certificate", FRONT_MATTER), ("acknowledg", FRONT_MATTER), ("declaration", FRONT_MATTER),
    ("table of contents", FRONT_MATTER), ("list of figures", FRONT_MATTER), ("list of tables", FRONT_MATTER),
    ("abbreviation", FRONT_MATTER), ("nomenclature", FRONT_MATTER),
    ("abstract", "Abstract"), ("reference", "References"), ("bibliography", "References"),
    ("introduction", "Introduction"),
    ("literature", "Related Work"), ("related work", "Related Work"), ("background", "Related Work"),
    ("survey", "Related Work"),
    ("result", "Results"), ("evaluation", "Results"), ("experiment", "Results"), ("performance analysis", "Results"),
    ("discussion", "Discussion"), ("limitation", "Discussion"),
    ("conclusion", "Conclusion"), ("future scope", "Conclusion"), ("future work", "Conclusion"),
    ("method", "Methods"), ("design", "Methods"), ("implementation", "Methods"), ("proposed system", "Methods"),
    ("system architecture", "Methods"), ("dataset", "Methods"),
)


def clean_heading(line: str) -> str:
    """Readable form of a heading: "CHAPTER 2: LITERATURE SURVEY" -> "Literature Survey"."""
    h = _CHAPTER_RE.sub("", line.strip())
    h = _HEADING_NUM_RE.sub("", h).strip().rstrip(":.").strip()
    if not h.isupper():
        return h
    small = {"and", "of", "the", "for", "in", "on", "to", "a", "an", "with", "vs"}
    words = h.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _is_toc_line(line: str) -> bool:
    """True for table-of-contents entries such as "Introduction ........ 5"."""
    return bool(_TOC_LINE_RE.search(line))


def _match_heading(line: str) -> tuple[str, str] | None:
    """Return (canonical_section, remainder) if the line is a section heading.

    Handles "3. Results", "IV. DISCUSSION", "Abstract:", "Abstract - text...",
    "CHAPTER 4: RESULTS AND DISCUSSION" and front-matter headings such as
    "CERTIFICATE" or "ACKNOWLEDGEMENT" (mapped to "Front Matter").
    Table-of-contents lines ("Introduction ..... 5") are never headings.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 160 or _is_toc_line(stripped):
        return None
    chapter = bool(_CHAPTER_RE.match(stripped))
    no_num = _CHAPTER_RE.sub("", stripped)
    num_match = _HEADING_NUM_RE.match(no_num)
    numbered = bool(num_match)
    # "3.4 Dataset Description" is a subsection; only top-level numbers ("3." / "3 ") start a section.
    sub_numbered = bool(num_match and re.match(r"\s*\d+\.\d", num_match.group(0)))
    no_num = _HEADING_NUM_RE.sub("", no_num)
    # Inline form: "Abstract: We present ..." or "Abstract - We present ..."
    m = re.match(r"^([A-Za-z ]{4,40}?)\s*(?::|\u2014|\u2013| - )\s*(.+)$", no_num)
    if m and m.group(1).strip().lower() in SECTION_ALIASES:
        return SECTION_ALIASES[m.group(1).strip().lower()], m.group(2).strip()
    key = no_num.rstrip(":.").strip().lower()
    if key in SECTION_ALIASES and len(key.split()) <= 5:
        return SECTION_ALIASES[key], ""
    # Keyword fallback, only for lines that clearly look like headings.
    words = key.split()
    long_words = [w for w in no_num.split() if len(w) > 3 and w[0].isalpha()]
    title_case = bool(long_words) and sum(w[0].isupper() for w in long_words) / len(long_words) >= 0.7
    looks_like_heading = (chapter or no_num.isupper() or (numbered and not sub_numbered and title_case)) \
        and 0 < len(words) <= 8 \
        and not key.endswith((",", ";")) and not re.search(r"[.!?]\s*$", no_num.rstrip(":"))
    if looks_like_heading:
        for kw, name in _HEADING_KEYWORDS:
            if kw in key:
                return name, ""
    return None


def _open_pdf(source: str | Path | bytes) -> Any:
    """Open a PDF from a path or raw bytes with PyMuPDF."""
    try:
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24
        except ImportError:  # older PyMuPDF
            import fitz
    except ImportError as exc:  # pragma: no cover
        raise PDFExtractionError("PyMuPDF is not installed. Run: pip install pymupdf") from exc
    try:
        if isinstance(source, (bytes, bytearray)):
            return fitz.open(stream=bytes(source), filetype="pdf")
        path = Path(source)
        if not path.exists():
            raise PDFExtractionError(f"PDF not found: {path}")
        return fitz.open(str(path))
    except PDFExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFExtractionError(f"Could not open PDF ({exc}). Is it a valid, unencrypted PDF?") from exc


def _guess_title(pdf: Any) -> str:
    """Use the largest-font text on page 1 as the title."""
    try:
        blocks = pdf[0].get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return ""
    best_size, parts = 0.0, []
    for b in blocks:
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                if not txt:
                    continue
                size = round(span.get("size", 0), 1)
                if size > best_size + 0.5:
                    best_size, parts = size, [txt]
                elif abs(size - best_size) <= 0.5:
                    parts.append(txt)
    return " ".join(parts).strip()[:300]


def _raw_page_lines(pdf: Any) -> list[list[str]]:
    """Raw text lines for every page."""
    return [[_ZERO_WIDTH_RE.sub("", ln).rstrip() for ln in page.get_text("text").splitlines()] for page in pdf]


def _remove_headers_footers(pages_lines: list[list[str]]) -> list[list[str]]:
    """Drop lines repeated at the top/bottom of many pages, plus bare page numbers."""
    n_pages = len(pages_lines)
    candidates: Counter[str] = Counter()
    for lines in pages_lines:
        non_empty = [ln for ln in lines if ln.strip()]
        edge = set(_normalize_for_repeat(ln) for ln in non_empty[:2] + non_empty[-2:])
        candidates.update(edge)
    repeated = {k for k, c in candidates.items() if n_pages >= 2 and c >= max(2, (n_pages + 1) // 2) and len(k) < 120}
    cleaned: list[list[str]] = []
    for lines in pages_lines:
        out = []
        non_empty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
        edges = set(non_empty_idx[:2] + non_empty_idx[-2:])
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            if _PAGE_NUM_RE.match(ln):
                continue
            if i in edges and _normalize_for_repeat(ln) in repeated:
                continue
            out.append(ln.strip())
        cleaned.append(out)
    return cleaned


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines, repairing end-of-line hyphenation."""
    text = ""
    for ln in lines:
        if not text:
            text = ln
        elif re.search(r"[A-Za-z]-$", text) and ln[:1].islower():
            text = text[:-1] + ln  # "experi-" + "ment"
        else:
            text = text + " " + ln
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Reference list parsing
# --------------------------------------------------------------------------- #
_NUMBERED_BRACKET_RE = re.compile(r"(?:^|\s)\[(\d{1,3})\]\s+")
_NUMBERED_DOT_LINE_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})[a-z]?\b")
_AUTHOR_START_RE = re.compile(r"^[A-Z][A-Za-z'\-]+(?:\s[A-Z][A-Za-z'\-]+)?,\s+(?:[A-Z]\.|[A-Z][a-z]+)")


def _author_and_year(text: str) -> tuple[str, str]:
    """Extract the first author's surname and the year from a reference string."""
    body = re.sub(r"^\s*\[?\d{1,3}[\].)]?\s*", "", text)
    # IEEE style "S. Kumar, R. Shah" / "J.-P. Martin": skip the initials.
    m = re.match(r"\s*(?:[A-Z]\.[\s\-]*)+([A-Z][A-Za-z'\-]+)", body)
    if not m:
        m = re.match(r"\s*([A-Z][A-Za-z'\-]+)", body)
    author = m.group(1) if m else ""
    y = _YEAR_RE.search(text)
    return author, (y.group(1) if y else "")


def parse_references(lines: list[str]) -> tuple[dict[int, Reference], str]:
    """Parse the lines of a References section.

    Supports numbered styles ("[1] ...", "1. ...") and author-year styles
    ("Smith, J. (2020). ..."). Author-year entries are numbered in order of
    appearance so every entry still has an integer key.

    Returns:
        (references dict, style) where style is "numbered", "author-year" or "none".
    """
    joined = _join_lines(lines)
    if not joined:
        return {}, "none"

    # Style 1: [1] ... [2] ...
    parts = _NUMBERED_BRACKET_RE.split(" " + joined)
    if len(parts) >= 3:
        refs: dict[int, Reference] = {}
        for i in range(1, len(parts) - 1, 2):
            num, txt = int(parts[i]), parts[i + 1].strip()
            if txt:
                a, y = _author_and_year(txt)
                refs[num] = Reference(num, txt, a, y)
        if refs:
            return refs, "numbered"

    # Style 2: lines beginning "1. " / "1) "
    refs = {}
    current: int | None = None
    for ln in lines:
        m = _NUMBERED_DOT_LINE_RE.match(ln)
        if m and (current is None or int(m.group(1)) == current + 1):
            current = int(m.group(1))
            refs[current] = Reference(current, m.group(2).strip())
        elif current is not None:
            refs[current].text = _join_lines([refs[current].text, ln])
    if len(refs) >= 2:
        for r in refs.values():
            r.first_author, r.year = _author_and_year(r.text)
        return refs, "numbered"

    # Style 3: author-year, one entry starts with "Surname, X."
    entries: list[list[str]] = []
    for ln in lines:
        if _AUTHOR_START_RE.match(ln) or not entries:
            entries.append([ln])
        else:
            entries[-1].append(ln)
    refs = {}
    for i, chunk in enumerate(entries, start=1):
        txt = _join_lines(chunk)
        a, y = _author_and_year(txt)
        refs[i] = Reference(i, txt, a, y)
    style = "author-year" if refs and sum(1 for r in refs.values() if r.year) >= max(1, len(refs) // 2) else "none"
    return (refs if style != "none" else {}), style


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def extract_document(source: str | Path | bytes, name: str | None = None, is_reference: bool = False) -> Document:
    """Extract a structured :class:`Document` from a PDF path or bytes.

    Args:
        source: Path to the PDF or its raw bytes (e.g. a Streamlit upload).
        name: Optional display name (defaults to the file name).
        is_reference: True for reference PDFs; structure warnings are then logged at DEBUG level.

    Raises:
        PDFExtractionError: If the file cannot be opened.
        OCRRequiredError: If the PDF has no selectable text.
    """
    display = name or (Path(source).name if not isinstance(source, (bytes, bytearray)) else "uploaded.pdf")
    pdf = _open_pdf(source)
    try:
        if pdf.is_encrypted:
            raise PDFExtractionError(f"{display} is encrypted; please provide an unlocked PDF.")
        n_pages = len(pdf)
        if n_pages == 0:
            raise PDFExtractionError(f"{display} has no pages.")
        raw_lines = _raw_page_lines(pdf)
        title = _guess_title(pdf)
    finally:
        pdf.close()

    total_chars = sum(len("".join(lines).strip()) for lines in raw_lines)
    if total_chars < 30 * n_pages:
        raise OCRRequiredError(
            f"{display} contains almost no selectable text ({total_chars} characters over "
            f"{n_pages} pages). It is probably a scanned/image PDF. Run OCR first "
            f"(e.g. `ocrmypdf input.pdf output.pdf`) and try again."
        )

    pages_lines = _remove_headers_footers(raw_lines)
    pages = [Page(i + 1, _join_lines(lines)) for i, lines in enumerate(pages_lines)]

    # Walk lines, switching section at headings. Keep (section, page, lines) segments.
    # Table-of-contents pages are skipped for heading detection, and a heading that
    # appears again (e.g. "References" listed in the TOC and again at the end)
    # switches back to that section instead of being ignored.
    sections: list[Section] = []
    segments: list[tuple[str, int, list[str], str]] = []
    current = FRONT_MATTER
    current_heading = FRONT_MATTER
    seen: set[str] = set()
    for page_no, lines in enumerate(pages_lines, start=1):
        short_numbered = sum(bool(_NUMBERED_LINE_RE.match(ln)) and len(ln) < 90 for ln in lines)
        is_toc_page = (any(_TOC_TITLE_RE.match(ln) for ln in lines) or sum(_is_toc_line(ln) for ln in lines) >= 3
                       or (short_numbered >= 8 and short_numbered >= 0.6 * max(1, len(lines))))
        if is_toc_page:
            current, current_heading = FRONT_MATTER, FRONT_MATTER
        absorb = False
        for ln in lines:
            # Second line of a wrapped ALL-CAPS heading ("DETAILS OF DESIGN AND" / "TECHNOLOGY").
            if absorb and ln.strip().isupper() and len(ln.split()) <= 4 and not ln.rstrip().endswith("."):
                absorb = False
                extra = clean_heading(ln)
                if extra.lower() not in current_heading.lower():
                    current_heading = f"{current_heading} {extra}"
                continue
            absorb = False
            hit = None if is_toc_page else _match_heading(ln)
            if hit:
                absorb = ln.strip().isupper() and not hit[1]
                current = hit[0]
                current_heading = FRONT_MATTER if current == FRONT_MATTER else (clean_heading(ln) if not hit[1] else current)
                if current not in seen and current != FRONT_MATTER:
                    seen.add(current)
                    sections.append(Section(current, ln.strip(), page_no))
                if hit[1]:
                    segments.append((current, page_no, [hit[1]], current_heading))
                continue
            if segments and segments[-1][0] == current and segments[-1][1] == page_no \
                    and segments[-1][3] == current_heading:
                segments[-1][2].append(ln)
            else:
                segments.append((current, page_no, [ln], current_heading))

    if not sections:
        logger.warning("No section headings detected in %s; treating the whole text as one section.", display)
    if any(s[0] == FRONT_MATTER for s in segments):
        first_fm = next(s[1] for s in segments if s[0] == FRONT_MATTER)
        sections.insert(0, Section(FRONT_MATTER, "", first_fm))

    # Build section text and sentences with page numbers.
    sentences: list[Sentence] = []
    ref_lines: list[str] = []
    for sec in sections:
        segs = [s for s in segments if s[0] == sec.name]
        if sec.name == "References":
            ref_lines = [ln for s in segs for ln in s[2]]
        texts, spans, pos = [], [], 0
        for _, pg, lines, hd in segs:
            t = _join_lines(lines)
            texts.append(t)
            spans.append((pos, pos + len(t), pg, hd))
            pos += len(t) + 1
        sec.text = " ".join(texts)
        if sec.name == "References":
            continue
        cursor = 0
        for sent in split_sentences(sec.text):
            found = sec.text.find(sent[:40], cursor)
            if found >= 0:
                cursor = found
            span = next((sp for sp in spans if sp[0] <= cursor < sp[1] + 1), spans[-1] if spans else None)
            page = span[2] if span else sec.start_page
            heading = span[3] if span else sec.name
            sentences.append(Sentence(len(sentences), sent, sec.name, page, heading))

    references, style = parse_references(ref_lines)
    warnings: list[str] = []
    if "References" not in seen:
        warnings.append("No References section detected; citation mapping will be limited.")
    elif not references:
        warnings.append("A References section was found but its entries could not be parsed.")
    missing = [s for s in ("Abstract", "Results", "Conclusion") if s not in seen]
    if missing:
        warnings.append(f"Sections not detected: {', '.join(missing)} (heading heuristics may have missed them).")
    for w in warnings:
        logger.log(logging.DEBUG if is_reference else logging.WARNING, "%s: %s", display, w)

    logger.info(
        "Extracted %s: %d pages, %d sections, %d sentences, %d references (%s).",
        display, n_pages, len(sections), len(sentences), len(references), style,
    )
    return Document(display, title, pages, sections, sentences, references, style, warnings)


def extract_plain_text(source: str | Path | bytes) -> str:
    """Return cleaned full text of a PDF (used for reference PDFs)."""
    pdf = _open_pdf(source)
    try:
        raw = _raw_page_lines(pdf)
    finally:
        pdf.close()
    cleaned = _remove_headers_footers(raw)
    text = "\n".join(_join_lines(lines) for lines in cleaned)
    if len(text.strip()) < 30 * max(1, len(raw)):
        raise OCRRequiredError("Reference PDF has no selectable text; OCR is needed.")
    return text
