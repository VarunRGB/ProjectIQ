"""Citation marker detection and mapping to the reference list.

Detects numeric markers ([3], [1,2], [1-3], [3; 5]) and author-year markers
((Author, 2020), (Author et al., 2020), (A and B, 2019; C, 2021), and narrative
"Author et al. (2020)"). Ranges are expanded and every marker is mapped to the
paper's parsed reference list; unmatched markers are reported as unresolved.

Important: a resolved marker only means "this marker points at a reference-list
entry". It never means the citation has been verified to support the sentence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .pdf_extractor import Document, Reference

logger = logging.getLogger(__name__)

RESOLVED_NOTE = "marker matched a reference-list entry (content NOT verified)"
UNCITED_CAVEAT = (
    "No citation marker detected. This may be acceptable (common knowledge, the authors' "
    "own result) or a parser limitation; review manually."
)

_NUMERIC_RE = re.compile(r"\[\s*(\d{1,3}(?:\s*[-–—,;]\s*\d{1,3})*)\s*\]")
_PAREN_RE = re.compile(r"\(([^()]{2,200}?)\)")
_AY_PART_RE = re.compile(
    r"(?P<author>[A-Z][A-Za-z'\-]+)"
    r"(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'\-]+|&\s*[A-Z][A-Za-z'\-]+))?"
    r",?\s+(?P<year>(?:19|20)\d{2})[a-z]?"
)
_NARRATIVE_RE = re.compile(
    r"(?P<author>[A-Z][A-Za-z'\-]+)(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'\-]+))?\s+\((?P<year>(?:19|20)\d{2})[a-z]?\)"
)


@dataclass
class CitationMarker:
    """One citation occurrence in a sentence."""

    raw: str
    kind: str  # "numeric" | "author-year"
    numbers: list[int] = field(default_factory=list)       # numeric markers (ranges expanded)
    author_year: list[tuple[str, str]] = field(default_factory=list)  # (author, year) pairs
    start: int = 0
    end: int = 0
    resolved: list[int] = field(default_factory=list)      # reference-list numbers matched
    unresolved: list[str] = field(default_factory=list)    # parts that matched nothing

    @property
    def status(self) -> str:
        if self.unresolved and not self.resolved:
            return "unresolved"
        if self.unresolved:
            return "partially resolved"
        return "resolved"


def expand_numeric(body: str) -> list[int]:
    """Expand "1-3, 5; 7" into [1, 2, 3, 5, 7] (ranges capped at 50 items)."""
    nums: list[int] = []
    for part in re.split(r"[,;]", body):
        part = part.strip()
        if not part:
            continue
        rng = re.split(r"\s*[-–—]\s*", part)
        if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit():
            a, b = int(rng[0]), int(rng[1])
            if a <= b and b - a <= 50:
                nums.extend(range(a, b + 1))
                continue
        if part.isdigit():
            nums.append(int(part))
    return list(dict.fromkeys(nums))


def find_citations(text: str) -> list[CitationMarker]:
    """Find all citation markers in a piece of text."""
    markers: list[CitationMarker] = []
    for m in _NUMERIC_RE.finditer(text):
        markers.append(CitationMarker(m.group(0), "numeric", numbers=expand_numeric(m.group(1)),
                                      start=m.start(), end=m.end()))
    taken = [(mk.start, mk.end) for mk in markers]
    for m in _PAREN_RE.finditer(text):
        inner = m.group(1)
        pairs = [(p.group("author"), p.group("year")) for p in _AY_PART_RE.finditer(inner)]
        if pairs:
            markers.append(CitationMarker(m.group(0), "author-year", author_year=pairs,
                                          start=m.start(), end=m.end()))
            taken.append((m.start(), m.end()))
    for m in _NARRATIVE_RE.finditer(text):
        if any(a <= m.start() < b or a < m.end() <= b for a, b in taken):
            continue
        markers.append(CitationMarker(m.group(0), "author-year",
                                      author_year=[(m.group("author"), m.group("year"))],
                                      start=m.start(), end=m.end()))
    markers.sort(key=lambda mk: mk.start)
    return markers


def strip_citations(text: str) -> str:
    """Remove citation markers from text (useful before NLI / overlap)."""
    out = text
    for mk in sorted(find_citations(text), key=lambda m: -m.start):
        out = out[: mk.start] + out[mk.end :]
    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


def resolve_marker(marker: CitationMarker, references: dict[int, Reference]) -> CitationMarker:
    """Fill ``marker.resolved`` / ``marker.unresolved`` against the reference list."""
    marker.resolved, marker.unresolved = [], []
    if marker.kind == "numeric":
        for n in marker.numbers:
            (marker.resolved if n in references else marker.unresolved).append(n if n in references else str(n))
        return marker
    for author, year in marker.author_year:
        hits = [num for num, ref in references.items()
                if ref.year == year and (ref.first_author.lower() == author.lower()
                                         or author.lower() in ref.text.lower()[:120])]
        if hits:
            marker.resolved.extend(h for h in hits if h not in marker.resolved)
        else:
            marker.unresolved.append(f"{author} {year}")
    return marker


@dataclass
class SentenceCitations:
    """Citation markers found in one sentence."""

    sentence_index: int
    section: str
    page: int
    text: str
    markers: list[CitationMarker]
    heading: str = ""  # the paper's own heading for this sentence

    @property
    def reference_numbers(self) -> list[int]:
        return list(dict.fromkeys(n for m in self.markers for n in m.resolved))


@dataclass
class CitationReport:
    """Document-level citation analysis."""

    per_sentence: dict[int, SentenceCitations]
    unresolved: list[dict[str, Any]]
    never_cited: list[int]
    style: str

    def for_sentence(self, idx: int) -> SentenceCitations | None:
        return self.per_sentence.get(idx)

    def summary(self) -> dict[str, Any]:
        markers = [m for sc in self.per_sentence.values() for m in sc.markers]
        return {
            "sentences_with_citations": len(self.per_sentence),
            "total_markers": len(markers),
            "numeric_markers": sum(m.kind == "numeric" for m in markers),
            "author_year_markers": sum(m.kind == "author-year" for m in markers),
            "unresolved_markers": len(self.unresolved),
            "references_never_cited": self.never_cited,
            "reference_style": self.style,
            "note": "Resolved = " + RESOLVED_NOTE,
        }

    def to_rows(self) -> list[dict[str, Any]]:
        rows = []
        for sc in self.per_sentence.values():
            for m in sc.markers:
                rows.append({
                    "sentence_index": sc.sentence_index, "section": sc.heading or sc.section, "page": sc.page,
                    "marker": m.raw, "kind": m.kind, "resolved_to": m.resolved,
                    "unresolved": m.unresolved, "status": m.status, "sentence": sc.text,
                })
        return rows


def analyze_citations(document: Document) -> CitationReport:
    """Detect and resolve citation markers for every body sentence."""
    per_sentence: dict[int, SentenceCitations] = {}
    unresolved: list[dict[str, Any]] = []
    cited: set[int] = set()
    for s in document.body_sentences():
        markers = [resolve_marker(m, document.references) for m in find_citations(s.text)]
        if not markers:
            continue
        per_sentence[s.index] = SentenceCitations(s.index, s.section, s.page, s.text, markers, s.heading)
        for m in markers:
            cited.update(m.resolved)
            for u in m.unresolved:
                unresolved.append({"marker": m.raw, "missing": u, "sentence_index": s.index,
                                   "section": s.heading or s.section, "page": s.page, "sentence": s.text})
    never = sorted(set(document.references) - cited)
    report = CitationReport(per_sentence, unresolved, never, document.reference_style)
    logger.info("Citations: %s", {k: v for k, v in report.summary().items() if k != "note"})
    return report


def marker_to_dict(m: CitationMarker) -> dict[str, Any]:
    """JSON-friendly marker."""
    d = asdict(m)
    d["status"] = m.status
    return d
