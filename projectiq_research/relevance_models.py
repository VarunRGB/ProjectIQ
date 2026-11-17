"""Structured, serializable evidence for Phase 3 relevance decisions."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PaperRelevance:
    reference_index: int                  # 0-based position in ResearchExtraction.papers
    paper_title: Optional[str]
    verification_status: str
    classification: str                   # direct, supporting, partial, possible_unrelated, insufficient_evidence
    confidence: str                       # high, medium, low (evidence strength, not probability)
    topical_similarity: Optional[float] = None
    methodological_similarity: Optional[float] = None
    citation_context_similarity: Optional[float] = None
    cross_encoder_scores: Dict[str, float] = field(default_factory=dict)
    citation_lines: List[int] = field(default_factory=list)
    evidence_sources: List[str] = field(default_factory=list)
    explanation: str = ""
    notes: List[str] = field(default_factory=list)
    # Separate diagnostic: similarity to methodology outside citation-bearing lines.
    independent_methodological_similarity: Optional[float] = None

    # Phase 3 v1.3: independently computed task/role diagnostics.
    research_problem_similarity: Optional[float] = None
    research_problem_overlap: int = 0
    technique_led: bool = False
    method_use_evidence: List[str] = field(default_factory=list)
    # v1.4: distinguish independent exact technique evidence from student
    # citation claims and keep bibliographic reliability separate from relevance.
    method_evidence_strength: str = "none"  # none / moderate / strong
    method_evidence_traces: List[str] = field(default_factory=list)
    same_research_task_family: bool = False
    evidence_reliability: str = "unverified_reference"
