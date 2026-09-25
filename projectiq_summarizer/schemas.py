"""Data models. The LLM-facing models double as JSON schemas that Ollama uses
to force structured output; the result models are what the module hands to
the rest of ProjectIQ."""
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Rubric
# --------------------------------------------------------------------------
class Criterion(BaseModel):
    id: str
    name: str
    weight: float = 1.0
    descriptor: str


class Rubric(BaseModel):
    name: str
    mode: Literal["academic", "industry"] = "academic"
    scale_max: int = 10
    criteria: List[Criterion]


# --------------------------------------------------------------------------
# What the LLM must return
# --------------------------------------------------------------------------
class ChunkSummary(BaseModel):
    summary: str
    key_points: List[str]


class FinalSummary(BaseModel):
    summary: str
    key_points: List[str]


ContentType = Literal["poorly_written", "highly_technical",
                      "likely_ai_generated", "standard"]


# Field order matters: the model writes the JSON top to bottom, so it has to
# collect evidence before it scores, and reason before it gives a number.
class CriterionJudgement(BaseModel):
    criterion_id: str
    evidence_quote: str = Field(description="copied word for word from the text")
    justification: str = Field(description="your own words: why this score")
    score: int = Field(description="0 to 10")


class AIStyleAssessment(BaseModel):
    indicators: List[str]
    likelihood: Literal["low", "medium", "high"]


class Judgement(BaseModel):
    concrete_details: List[str] = Field(
        description="checkable specifics copied from the text: numbers, measurements, "
                    "parameter values, named tools or versions, specific results or failures")
    generic_claims: List[str] = Field(
        description="vague or promotional sentences copied from the text that could "
                    "describe almost any project")
    criteria: List[CriterionJudgement]
    writing_quality: Literal["poor", "fair", "good", "excellent"]
    technical_depth: Literal["low", "moderate", "high"]
    ai_style: AIStyleAssessment
    content_type: ContentType
    rationale: str


# --------------------------------------------------------------------------
# What this module produces
# --------------------------------------------------------------------------
class Recommendation(str, Enum):
    ACCEPT = "ACCEPT"
    ACCEPT_WITH_MINOR_REVISIONS = "ACCEPT_WITH_MINOR_REVISIONS"
    REVISE_AND_RESUBMIT = "REVISE_AND_RESUBMIT"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"


class CriterionResult(BaseModel):
    criterion_id: str
    name: str
    weight: float
    score: float                 # mean across judge samples, 0-10
    score_spread: float          # standard deviation across samples
    justification: str
    evidence_quote: str
    evidence_found_in_text: bool


class ConfidenceBreakdown(BaseModel):
    score: float                 # 0-100
    band: Literal["High", "Medium", "Low"]
    self_consistency: Optional[float]   # None when only one sample was run
    signal_agreement: float
    evidence_grounding: float
    notes: List[str]


class AgentResult(BaseModel):
    """The shared contract every ProjectIQ agent returns."""
    agent: str
    score: float
    justification: str
    evidence: List[str]
    confidence: float            # 0-1


class EvaluationReport(BaseModel):
    name: str
    model: str
    word_count: int
    chunks: int
    summary: FinalSummary
    content_type: ContentType
    judge_content_type: ContentType      # what the LLM alone said
    concrete_details: List[str]          # verified to appear in the text
    generic_claims: List[str]
    recommendation: Recommendation
    recommendation_reason: str
    weighted_score: float
    criteria: List[CriterionResult]
    writing_quality: str
    technical_depth: str
    ai_style_likelihood: str
    ai_style_indicators: List[str]
    rationale: str
    confidence: ConfidenceBreakdown
    signals: dict
    agent_result: AgentResult
    elapsed_s: float
