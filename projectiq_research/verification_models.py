"""Structured results returned by Phase 2 academic metadata verification."""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AcademicPaperMetadata:
    provider: str
    provider_id: Optional[str]
    title: Optional[str]
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    landing_page_url: Optional[str] = None
    abstract: Optional[str] = None


@dataclass
class PaperVerification:
    submitted_title: Optional[str]
    submitted_authors: Optional[str]
    submitted_year: Optional[int]
    submitted_doi: Optional[str]
    status: str
    provider: Optional[str] = None
    verified_title: Optional[str] = None
    verified_authors: List[str] = field(default_factory=list)
    verified_year: Optional[int] = None
    verified_doi: Optional[str] = None
    verified_url: Optional[str] = None
    title_similarity: Optional[float] = None
    title_match: Optional[bool] = None
    year_match: Optional[bool] = None
    author_match: Optional[bool] = None
    notes: List[str] = field(default_factory=list)
    # Retained from verified academic metadata for Phase 3; optional for older callers.
    verified_abstract: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
