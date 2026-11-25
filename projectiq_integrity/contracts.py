"""The shared result object (plan, "Decide on Day 1", decision 1).

Every ProjectIQ agent returns an AgentResult made of Findings, and every Finding
rests on Evidence: an exact quote with the place it came from. Only the Rubric
Agent turns findings into marks.

TODO(team): move this file to a shared projectiq/contracts.py once agreed, and
rename the older projectiq_summarizer.schemas.AgentResult (single score, string
evidence) so the two do not clash.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    source: str = Field(description='"report", "code", "cohort", "corpus" or "execution"')
    location: str = Field(description='e.g. "document p.12", "paper_x para 4"')
    quote: str = Field(description="the exact passage, code fragment or log line")


class Finding(BaseModel):
    claim_id: Optional[str] = Field(default=None, description='"C3", issued by the Summarizer')
    kind: str = Field(description='"copied_chunk", "similar_report_text" or "ai_generated_share"')
    summary: str
    evidence: List[Evidence]
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: str = ""


class AgentResult(BaseModel):
    agent: str
    submission_id: str
    findings: List[Finding]
    notes: List[str] = Field(default_factory=list,
                             description="what was checked, what was skipped and why")
