"""Stable, JSON-serialisable Phase 1 contracts (no runtime execution)."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass
class Evidence:
    path: str
    line: int | None
    reason: str  # Descriptions, never raw code or secret values


@dataclass
class Technology:
    name: str
    category: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Service:
    name: str
    kind: str
    technology: str
    root: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Route:
    path: str
    method: str
    kind: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Entrypoint:
    path: str
    kind: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Feature:
    name: str
    origin: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class TestArtifact:
    path: str
    kind: str
    framework: str | None = None


@dataclass
class RepositorySource:
    kind: str  # github or local
    repository: str
    url: str | None = None
    requested_ref: str | None = None
    resolved_ref: str | None = None
    commit_sha: str | None = None


@dataclass
class Manifest:
    schema_version: str
    phase: int
    created_utc: str
    source: RepositorySource
    project_name: str
    architecture: str
    architecture_confidence: str
    languages: dict[str, int]
    technologies: list[Technology]
    services: list[Service]
    routes: list[Route]
    pages: list[str]
    entrypoints: list[Entrypoint]
    features: list[Feature]
    tests: list[TestArtifact]
    setup_files: list[str]
    package_scripts: dict[str, dict[str, str]]
    environment_variables: list[str]
    database_schema_files: list[str]
    findings: list[Finding]
    unknowns: list[str]
    proposed_test_targets: list[str]
    inventory: dict[str, Any]
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
