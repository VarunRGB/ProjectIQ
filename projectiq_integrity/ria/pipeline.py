"""End-to-end analysis pipeline shared by the CLI and the Streamlit app."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .citation_analyzer import CitationReport, analyze_citations
from .claim_extractor import Claim, extract_claims
from .config import DEFAULT_SETTINGS, Settings, ensure_dirs
from .evidence_analyzer import (ClaimEvidence, ReferenceCorpus, analyze_evidence, build_reference_corpus,
                                load_reference_dir, read_mapping_csv)
from .numeric_checker import NumericReport, check_document
from .overlap_analyzer import OverlapResult, analyze_overlap
from .pdf_extractor import Document, extract_document

logger = logging.getLogger(__name__)

STEPS = ("Extract PDF", "Citations", "Claims", "Load references", "Evidence & NLI", "Overlap", "Numbers", "Flags")
ProgressFn = Callable[[str, float], None]


@dataclass
class AnalysisResult:
    """Everything produced for one paper."""

    settings: Settings
    document: Document
    citations: CitationReport
    claims: list[Claim]
    corpus: ReferenceCorpus | None
    evidence: list[ClaimEvidence]
    overlap: list[OverlapResult]
    numeric: NumericReport
    flags: list[Any] = field(default_factory=list)
    backends: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)


def run_analysis(
    paper: str | Path | bytes,
    references: Path | Iterable[tuple[str, Path | bytes]] | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    paper_name: str | None = None,
    manual_mapping: dict[str, int] | None = None,
    progress: ProgressFn | None = None,
) -> AnalysisResult:
    """Analyse one paper.

    Args:
        paper: PDF path or bytes.
        references: A directory of reference PDFs, or (name, path/bytes) pairs, or None.
        settings: Runtime settings.
        paper_name: Display name for uploaded bytes.
        manual_mapping: Optional {reference file name: reference number}.
        progress: Callback ``(step name, fraction done)``.
    """
    from .models import get_embedder, get_nli
    from .report_generator import build_flags

    ensure_dirs(settings)
    timings: dict[str, float] = {}

    def step(i: int) -> float:
        if progress:
            progress(STEPS[i], i / len(STEPS))
        return time.perf_counter()

    t = step(0)
    doc = extract_document(paper, name=paper_name)
    timings["extract"] = time.perf_counter() - t

    t = step(1)
    citations = analyze_citations(doc)
    timings["citations"] = time.perf_counter() - t

    t = step(2)
    claims = extract_claims(doc, citations, settings)
    timings["claims"] = time.perf_counter() - t

    t = step(3)
    corpus: ReferenceCorpus | None = None
    if isinstance(references, (str, Path)):
        corpus = load_reference_dir(Path(references), doc.references, settings)
    elif references is not None:
        refs = list(references)
        if refs:
            corpus = build_reference_corpus(refs, doc.references, settings, manual_mapping)
    corpus = corpus or ReferenceCorpus([], {}, {})
    timings["references"] = time.perf_counter() - t

    t = step(4)
    evidence = analyze_evidence(claims, corpus, settings)
    timings["evidence"] = time.perf_counter() - t

    t = step(5)
    overlap = analyze_overlap(doc, corpus, settings)
    timings["overlap"] = time.perf_counter() - t

    t = step(6)
    numeric = check_document(doc, settings)
    timings["numbers"] = time.perf_counter() - t

    t = step(7)
    backends = {
        "embeddings": get_embedder(settings).backend if settings.use_embeddings else "disabled",
        "nli": get_nli(settings).backend if (settings.use_nli and not corpus.is_empty) else
               ("disabled" if not settings.use_nli else "not used (no references)"),
        "spacy": settings.spacy_model,
    }
    result = AnalysisResult(settings, doc, citations, claims, corpus, evidence, overlap, numeric,
                            backends=backends, timings=timings)
    result.flags = build_flags(result)
    timings["flags"] = time.perf_counter() - t
    if progress:
        progress("Done", 1.0)
    logger.info("Analysis finished: %d flags in %.1fs", len(result.flags), sum(timings.values()))
    return result


__all__ = ["AnalysisResult", "run_analysis", "read_mapping_csv", "STEPS"]
