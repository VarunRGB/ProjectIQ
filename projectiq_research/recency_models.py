"""Phase 4A data contracts. No external APIs, LLMs or search services."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PaperRecency:
    reference_index: int
    paper_title: Optional[str]
    submitted_year: Optional[int]
    publication_year: Optional[int]
    year_source: str  # verified_metadata, submitted_unverified, disputed, unknown
    year_conflict: bool
    age_years: Optional[int]
    age_band: str  # very_recent, recent, older, historical, future_dated, unknown
    relevance_classification: str
    verification_status: str
    literature_review_cited: bool
    document_cited: bool
    role_assessment: str  # current_direct, older_direct_review, foundational_candidate, ...
    notes: List[str] = field(default_factory=list)


@dataclass
class RecencySummary:
    as_of_date: str
    as_of_year: int
    total_references: int = 0
    distinct_references: int = 0
    duplicate_reference_count: int = 0
    duplicate_groups: List[dict] = field(default_factory=list)
    possible_version_groups: List[dict] = field(default_factory=list)
    known_year_count: int = 0
    reliable_year_count: int = 0
    uncertain_year_count: int = 0
    unknown_year_count: int = 0
    future_dated_count: int = 0
    median_reliable_year: Optional[float] = None
    newest_reliable_year: Optional[int] = None
    oldest_reliable_year: Optional[int] = None
    recent_reliable_count: int = 0
    recent_reliable_percent: Optional[float] = None
    verified_direct_count: int = 0
    verified_direct_recent_count: int = 0
    verified_direct_recent_percent: Optional[float] = None
    literature_review_reference_count: int = 0
    literature_review_reliable_count: int = 0
    literature_review_recent_reliable_count: int = 0
    literature_review_recent_percent: Optional[float] = None
    document_cited_reference_count: int = 0
    uncited_reference_count: int = 0
    unresolved_citation_mention_count: int = 0
    ambiguous_citation_mention_count: int = 0
    age_distribution: dict = field(default_factory=dict)
    recency_assessment: str = "insufficient_evidence"
    findings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)


@dataclass
class ResearchQualityReport:
    """Short, front-end-friendly final report; full evidence remains in other fields."""
    schema_version: str
    source: str
    as_of_date: str
    headline: str
    overview: str
    phase_1: dict
    phase_2: dict
    phase_3: dict
    phase_4: dict
    key_findings: List[str]
    attention_items: List[dict]
    methodological_notes: List[str]
    recommended_checks: List[str]
    reference_overview: List[dict] = field(default_factory=list)
