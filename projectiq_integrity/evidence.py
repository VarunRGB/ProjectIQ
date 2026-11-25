"""Evidence builder and confidence estimator.

Each check produces raw Signals. The builder keeps a signal only if it is backed
by at least one quoted passage or code fragment, then the estimator gives it a
confidence between 0 and 1. Confidence means "how strongly the evidence supports
this signal", never "how guilty the student is"; nothing here changes a mark.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .contracts import Evidence, Finding

MAX_EVIDENCE = 8


@dataclass
class Signal:
    kind: str                      # "similar_report_text" | "copied_chunk" | "ai_generated_share"
    summary: str
    evidence: List[Evidence]
    scores: Dict[str, float] = field(default_factory=dict)
    limitations: str = ""
    claim_id: Optional[str] = None


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# Confidence estimator. Plain formulas so a reviewer can follow them; each one is
# checked against the planted controls in scripts/validate.py.
# --------------------------------------------------------------------------- #
def report_confidence(s: Dict[str, float]) -> float:
    """Several near-copied sentences are stronger than a few reworded ones."""
    near, para = s.get("near_verbatim", 0.0), s.get("paraphrase", 0.0)
    conf = 0.35 + 0.12 * near + 0.05 * para + 0.6 * s.get("share_of_report", 0.0)
    if s.get("lexical_backend", 0.0):   # embeddings missing: paraphrase scores are not semantic
        conf *= 0.7
    return round(_clip(conf, 0.05, 0.95), 2)


def ai_share_confidence(s: Dict[str, float]) -> float:
    """Larger AI share and scores further below the threshold give more confidence.
    Capped at 0.7, and lower still when few words could be scored."""
    conf = 0.3 + 0.3 * _clip(s.get("share", 0.0)) + 0.1 * _clip(s.get("margin", 0.0) / 0.1)
    if s.get("low_confidence", 0.0):
        conf *= 0.8
    return round(_clip(conf, 0.05, 0.7), 2)


ESTIMATORS = {
    "similar_report_text": report_confidence,
    "copied_chunk": report_confidence,
    "ai_generated_share": ai_share_confidence,
}


def estimate_confidence(signal: Signal) -> float:
    fn = ESTIMATORS.get(signal.kind)
    return fn(signal.scores) if fn else 0.3


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
def clean_evidence(items: List[Evidence]) -> List[Evidence]:
    """Drop empty quotes and exact duplicates, keep at most MAX_EVIDENCE."""
    out, seen = [], set()
    for ev in items:
        quote = (ev.quote or "").strip()
        if not quote:
            continue
        key = (ev.location, " ".join(quote.split()).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(Evidence(source=ev.source, location=ev.location, quote=quote))
    return out[:MAX_EVIDENCE]


def build_findings(signals: List[Signal]) -> List[Finding]:
    """Signals with no quoted fragment are dropped: a flag a reviewer cannot check
    is not shown at all."""
    findings = []
    for sig in signals:
        evidence = clean_evidence(sig.evidence)
        if not evidence:
            continue
        findings.append(Finding(
            claim_id=sig.claim_id, kind=sig.kind, summary=sig.summary, evidence=evidence,
            confidence=estimate_confidence(sig), limitations=sig.limitations,
        ))
    findings.sort(key=lambda f: f.confidence, reverse=True)
    return findings
