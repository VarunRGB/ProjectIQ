"""Versioned, teacher-facing evaluation contract. No grading or execution here."""
from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator, JsonValue

AgentName = Literal['summarizer', 'integrity', 'research', 'executioner']
Status = Literal['COMPLETED', 'PARTIAL', 'FAILED', 'NOT_RUN', 'REVIEW_REQUIRED']
AGENTS = ('summarizer', 'integrity', 'research', 'executioner')

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)

class AIAnalysis(StrictModel):
    status: Literal['NOT_AVAILABLE', 'COMPLETED', 'INCONCLUSIVE', 'FAILED'] = 'NOT_AVAILABLE'
    estimated_percent: float | None = Field(default=None, ge=0, le=100)
    interpretation: str | None = None
    confidence: str | None = None
    flagged_passages: list[JsonValue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'COMPLETED':
            if self.estimated_percent is None or not (self.interpretation or '').strip():
                raise ValueError('completed AI estimate requires value and interpretation')
        elif self.estimated_percent is not None:
            raise ValueError('unavailable AI estimate must be null')
        return self

class Metric(StrictModel):
    id: str
    label: str
    value: float | int | None
    unit: str
    scope: str

class Receipt(StrictModel):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    origin: Literal['LIVE', 'IMPORTED']
    # Not an authentication or independent verification of the producing agent.

class AgentResult(StrictModel):
    agent: AgentName
    status: Status
    native_status: str | None = None
    metrics: list[Metric] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    error_code: str | None = None
    native: Receipt | None = None
    ai_detection: AIAnalysis | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.agent == 'integrity' and self.ai_detection is None:
            raise ValueError('integrity requires AI availability section')
        if self.agent != 'integrity' and self.ai_detection is not None:
            raise ValueError('AI estimate belongs to integrity')
        if len({m.id for m in self.metrics}) != len(self.metrics):
            raise ValueError('duplicate metric IDs')
        if self.status in ('FAILED', 'NOT_RUN') and self.metrics:
            raise ValueError('failed or skipped agent cannot supply headline metrics')
        return self

class Submission(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
    project_title: str = Field(min_length=1, max_length=240)
    report_name: str
    report_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    repository_url: str | None = None
    requested_ref: str | None = None

class Evaluation(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    submission: Submission
    created_utc: str
    mode: Literal['LIVE', 'IMPORTED']
    agents: dict[AgentName, AgentResult]
    # Reserved for Phase 2; never derive a whole-project mark from agent metrics.
    final_assessment: None = None
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def complete_agents(self):
        if set(self.agents) != set(AGENTS) or any(k != v.agent for k,v in self.agents.items()):
            raise ValueError('exactly one result for each of the four agents is required')
        return self
