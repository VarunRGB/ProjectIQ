"""Typed data structures used by ProjectIQ Research Agent (Phase 1)."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set
from .verification_models import PaperVerification


@dataclass(frozen=True)
class Hyperlink:
    text: str
    url: str
    line_number: int
    source: str = "document"


@dataclass
class ParsedDocument:
    source: str
    text: str
    hyperlinks: List[Hyperlink] = field(default_factory=list)
    # 1-based line numbers known to be headings (for example, DOCX Heading 1).
    heading_lines: Set[int] = field(default_factory=set)


@dataclass
class Section:
    key: str
    found: bool
    heading: Optional[str] = None
    content: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass
class CitationMention:
    text: str
    kind: str  # numeric, author_year
    line_number: int


@dataclass
class PaperReference:
    raw: str
    title: Optional[str]
    authors: Optional[str]
    year: Optional[int]
    doi: Optional[str]
    urls: List[str]
    start_line: int
    end_line: int
    extraction_notes: List[str] = field(default_factory=list)


@dataclass
class ResearchExtraction:
    source: str
    literature_review: Section
    references: Section
    literature_citations: List[CitationMention]
    papers: List[PaperReference]
    literature_links: List[Hyperlink]
    reference_links: List[Hyperlink]
    hyperlinks: List[Hyperlink]
    notes: List[str] = field(default_factory=list)
    verification_results: List[PaperVerification] = field(default_factory=list)
    document_citations: List[CitationMention] = field(default_factory=list)
    citation_reference_matches: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return asdict(self)
