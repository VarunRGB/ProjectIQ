"""Strict versioned input and output contracts for the faculty rubric builder."""
from __future__ import annotations
from typing import Literal, Annotated
from pydantic import Field, model_validator, JsonValue
from ..contracts import StrictModel

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
EvidenceKind = Literal['REPORT_EXCERPT','STATIC_SOURCE','RESEARCH_RECORD','TEST_OBSERVATION','TEST_PLAN',
                       'REPORT_SUMMARY','LEGACY_JUDGEMENT','INTEGRITY_ADVISORY','METRIC']
GradingKind = Literal['REPORT_EXCERPT','STATIC_SOURCE','RESEARCH_RECORD','TEST_OBSERVATION']

class Evidence(StrictModel):
    id: str
    agent: Literal['report','summarizer','integrity','research','executioner']
    kind: EvidenceKind
    label: str
    text: str
    artifact_sha256: Digest
    locator: str
    provenance: Literal['REPORT_SNAPSHOT','NATIVE_RECEIPT_MATCHED','IMPORTED_ACKNOWLEDGED','IMPORTED_UNCONFIRMED']
    eligible_for_review: bool = False
    # Eligibility means a reviewer may consider the item, not that the claim is true.
    limitations: list[str] = Field(default_factory=list)
    data: dict[str, JsonValue] = Field(default_factory=dict)

class EvidenceBundle(StrictModel):
    schema_version: Literal['2.0'] = '2.0'
    evaluation_sha256: Digest
    submission_id: Identifier
    report_sha256: Digest
    extracted_text_sha256: Digest | None = None
    report_extraction: Literal['COMPLETE','TRUNCATED','UNAVAILABLE']
    repository_commit: str | None = None
    agent_checks: dict[str,str]
    items: list[Evidence]
    observations: dict[str,JsonValue]
    limitations: list[str]

class Level(StrictModel):
    id: Identifier
    label: Text
    fraction: float = Field(ge=0,le=1)
    descriptor: Text
    requires_failed_observation: bool = False

class Criterion(StrictModel):
    id: Identifier
    title: Text
    description: Text
    group: Identifier
    weight: float = Field(gt=0,le=1000000)
    applicability: Literal['REQUIRED','OPTIONAL','EXCLUDED'] = 'REQUIRED'
    method: Literal['JUDGMENT','TEST_RATIO','REVIEWED_RATIO','REQUIREMENT_COVERAGE'] = 'JUDGMENT'
    allowed_evidence: list[GradingKind] = Field(default_factory=lambda:['REPORT_EXCERPT'],min_length=1,max_length=4)
    required_evidence: list[GradingKind] = Field(default_factory=list,max_length=4)
    minimum_evidence: int = Field(default=1,ge=1,le=20)
    levels: list[Level] = Field(default_factory=list,max_length=10)
    test_ids: list[str] = Field(default_factory=list,max_length=100)
    requirement_map: dict[Identifier, list[str]] = Field(default_factory=dict, max_length=100)
    feedback_required: bool = False
    focus: Literal['GENERAL','REPORT','DESIGN','IMPLEMENTATION','FUNCTIONAL','RESEARCH','TEST_STRATEGY','STYLE','MAINTAINABILITY','SECURITY'] = 'GENERAL'
    @model_validator(mode='after')
    def validate_policy(self):
        for field in (self.allowed_evidence,self.required_evidence,self.test_ids):
            if len(set(field))!=len(field): raise ValueError('duplicate evidence kind or test ID')
        if not set(self.required_evidence)<=set(self.allowed_evidence):raise ValueError('required evidence must be allowed')
        if self.method=='JUDGMENT':
            if self.test_ids or self.requirement_map or len(self.levels)<2:raise ValueError('judgment requires levels and no test IDs')
            fractions=[x.fraction for x in self.levels]
            if len(set(x.id for x in self.levels))!=len(self.levels) or sorted(set(fractions))!=fractions or fractions[0]!=0 or fractions[-1]!=1:
                raise ValueError('levels must have unique IDs and increasing fractions from 0 to 1')
        else:
            if self.levels or self.allowed_evidence!=['TEST_OBSERVATION'] or self.required_evidence!=['TEST_OBSERVATION'] or self.minimum_evidence!=1 or self.feedback_required:
                raise ValueError('objective methods require TEST_OBSERVATION only and no levels')
            if self.method=='REQUIREMENT_COVERAGE':
                if self.test_ids or not self.requirement_map or any(not ids or len(ids)>100 or len(ids)!=len(set(ids)) or any(not (k.startswith('exact:') and len(k)>6 or k.startswith('reviewed:') and len(k.split(':'))>=3 and all(k.split(':')[1:])) for k in ids) for ids in self.requirement_map.values()):
                    raise ValueError('coverage requires a nonempty explicit requirement mapping')
            elif not self.test_ids or self.requirement_map:
                raise ValueError('ratio requires explicit test IDs')
            if self.method=='REVIEWED_RATIO' and any(':' not in t or not all(t.split(':',1)) for t in self.test_ids):raise ValueError('reviewed ratio IDs must be suite:check_id')
        return self

class Rubric(StrictModel):
    schema_version: Literal['2.0']='2.0'
    id: Identifier
    title: Text
    revision: Identifier = '1'
    total_points: float = Field(gt=0,le=1000000)
    group_weights: dict[Identifier, float] = Field(default_factory=dict, max_length=40)
    criteria: list[Criterion] = Field(min_length=1,max_length=40)
    @model_validator(mode='after')
    def unique(self):
        if self.group_weights and (set(self.group_weights)!={c.group for c in self.criteria} or any(not 0<w<=1000000 for w in self.group_weights.values())):raise ValueError('group weights must cover every group and be positive')
        if len({c.id for c in self.criteria})!=len(self.criteria):raise ValueError('duplicate criterion ID')
        if not any(c.applicability!='EXCLUDED' for c in self.criteria):raise ValueError('no active criteria')
        return self

class Feedback(StrictModel):
    strength: Annotated[str, Field(min_length=1,max_length=220)]
    limitation: Annotated[str, Field(min_length=1,max_length=220)]
    improvement: Annotated[str, Field(min_length=1,max_length=220)]
    observed_failure: bool

class Decision(StrictModel):
    criterion_id: Identifier
    action: Literal['SCORE','EXCLUDE','DEFER','CONFIRM_TEST_RATIO']
    source: Literal['TEACHER','MODEL']
    level_id: Identifier | None = None
    evidence_ids: list[str] = Field(default_factory=list,max_length=100)
    rationale: Text
    feedback: Feedback | None = None
    @model_validator(mode='after')
    def shape(self):
        if len(set(self.evidence_ids))!=len(self.evidence_ids):raise ValueError('duplicate evidence ID')
        if not self.rationale.strip():raise ValueError('rationale required')
        if self.action=='SCORE':
            if not self.level_id or not self.evidence_ids:raise ValueError('score requires a level and evidence')
        elif self.level_id is not None or self.evidence_ids or self.feedback is not None:raise ValueError('non-score decisions cannot select level or evidence')
        if self.source=='MODEL' and self.action not in ('SCORE','DEFER'):raise ValueError('model cannot exclude or confirm')
        return self

class DecisionSet(StrictModel):
    schema_version: Literal['2.0']='2.0'
    evidence_sha256: Digest
    rubric_sha256: Digest
    decisions: list[Decision] = Field(default_factory=list,max_length=40)
    @model_validator(mode='after')
    def unique(self):
        if len(set(x.criterion_id for x in self.decisions))!=len(self.decisions):raise ValueError('duplicate criterion decision')
        return self

class CriterionResult(StrictModel):
    id: Identifier
    group: Identifier
    title: str
    applicability: str
    status: Literal['UNASSESSED','EXCLUDED','PROPOSED','TEACHER_REVIEWED','OBJECTIVE_PROVISIONAL']
    configured_weight: float
    effective_weight_percent: float
    max_points: float
    earned_points: float | None
    fraction: float | None
    level_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    feedback: Feedback | None = None
    review_flags: list[str] = Field(default_factory=list)
    objective_details: dict[str,JsonValue] = Field(default_factory=dict)
    evidence_details: list[dict[str,JsonValue]] = Field(default_factory=list)

class Assessment(StrictModel):
    schema_version: Literal['2.0']='2.0'
    evidence_sha256: Digest
    rubric_sha256: Digest
    decisions_sha256: Digest
    status: Literal['INSUFFICIENT_EVIDENCE','READY_FOR_TEACHER_REVIEW','TEACHER_REVIEWED']
    max_points: float
    proposed_points: float | None
    reviewed_points: float | None
    assessed_subtotal_points: float
    assessed_max_points: float
    assessed_weight_percent: float
    reviewed_weight_percent: float
    unresolved_criterion_ids: list[str]
    criteria: list[CriterionResult]
    groups: list[dict[str,JsonValue]]
    limitations: list[str]
