"""Candidate claim extraction.

Rule/pattern based detection of four claim types:
  * finding       - "we show", "demonstrate", "outperform", "achieve", ...
  * causal        - "leads to", "due to", "results in", "because", ...
  * comparison    - "better than", "higher than", "compared with", ...
  * quantitative  - numbers, percentages, p-values, sample sizes

Hedging ("may", "might", "suggest") lowers confidence and is reported.
Questions, headings and very short fragments are excluded.

An optional ML-assisted score uses embedding similarity to seed claim
prototypes versus non-claim prototypes; :func:`compare_rule_vs_ml` puts the
rule-only and ML-assisted rankings side by side.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .citation_analyzer import CitationReport, find_citations
from .config import DEFAULT_SETTINGS, Settings
from .pdf_extractor import Document, Sentence

logger = logging.getLogger(__name__)

CLAIM_PATTERNS: dict[str, list[str]] = {
    "comparison": [
        r"\b(better|higher|lower|worse|greater|smaller|faster|slower|larger|stronger|weaker) than\b",
        r"\bcompared (with|to)\b", r"\boutperform\w*\b", r"\bexceed\w*\b", r"\bsuperior to\b",
        r"\binferior to\b", r"\bversus\b|\bvs\.", r"\bmore (accurate|effective|efficient|robust)\b",
    ],
    "causal": [
        r"\blead(s)? to\b", r"\bled to\b", r"\bdue to\b", r"\bresult(s|ed)? in\b", r"\bcaus(e|es|ed|ing)\b",
        r"\bbecause\b", r"\btherefore\b", r"\bas a result\b", r"\bcontribut(e|es|ed) to\b",
        r"\bdriv(es|en) by\b", r"\bproduc(e|es|ed)\b",
    ],
    "finding": [
        r"\b(we|our|this (study|paper|work)|results?|findings?|experiments?|analysis)\b.{0,40}\b(show|shows|showed|shown|demonstrat\w*|reveal\w*|indicat\w*|confirm\w*|establish\w*|find|found|prove\w*)\b",
        r"\b(show|shows|showed|demonstrat\w*|reveal\w*)\s+that\b",
        r"\boutperform\w*\b", r"\bachiev\w*\b", r"\bimprov(e|es|ed|ement)\b", r"\breduc(e|es|ed)\b",
        r"\bincreas(e|es|ed)\b", r"\bsignificant(ly)?\b", r"\beffective\b", r"\bconsistent(ly)?\b",
        r"\bis known to\b", r"\bha(ve|s) (also )?been (shown|reported|used|found)\b",
    ],
    "quantitative": [
        r"\d+(?:\.\d+)?\s?%", r"\bp\s*[<=>≤≥]\s*0?\.\d+", r"\bn\s*=\s*\d", r"\b\d+\.\d+\b",
        r"\b\d+(?:,\d{3})+\b", r"\b(accuracy|precision|recall|f1|auc|error|bleu|rouge)\b.{0,25}\d",
    ],
}
HEDGE_PATTERNS: list[str] = [
    r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bsuggest(s|ed)?\b", r"\bpossibl[ey]\b", r"\blikely\b",
    r"\bappear(s|ed)? to\b", r"\bseem(s|ed)?\b", r"\bpotentially\b", r"\bperhaps\b", r"\bunclear\b",
    r"\bwe (believe|hypothesi[sz]e|speculate)\b",
]
TYPE_PRIORITY = ("comparison", "causal", "finding", "quantitative")
STRONG_SECTIONS = {"Abstract", "Results", "Discussion", "Conclusion"}

# Prototype sentences for the optional ML-assisted ranking.
CLAIM_PROTOTYPES: list[str] = [
    "Our method outperforms the baseline by a large margin.",
    "The results show that the proposed model improves accuracy.",
    "We demonstrate that pretraining leads to significant gains.",
    "The treatment group had a higher survival rate than the control group.",
    "This approach reduces error by 12% compared with prior work.",
    "The improvement was statistically significant (p < 0.01).",
    "Increased exposure causes a decline in performance.",
    "Prior work has shown that data augmentation improves robustness.",
    "Model A achieves an F1 score of 0.91 on the test set.",
    "These findings indicate that the effect is driven by sample size.",
]
NON_CLAIM_PROTOTYPES: list[str] = [
    "We describe the dataset in the next section.",
    "The rest of this paper is organised as follows.",
    "Figure 2 shows the architecture of the system.",
    "Each sample is split into training and test sets.",
    "We thank the reviewers for their helpful comments.",
    "Details of the hyperparameters are given in the appendix.",
    "The code will be made publicly available.",
    "In this section we introduce the notation used throughout.",
]


@dataclass
class Claim:
    """A candidate claim sentence."""

    claim_id: str
    text: str
    claim_type: str
    types: list[str]
    section: str
    page: int
    sentence_index: int
    rule_confidence: float
    hedged: bool
    hedge_terms: list[str]
    cues: list[str]
    citation_markers: list[str] = field(default_factory=list)
    cited_references: list[int] = field(default_factory=list)
    ml_score: float | None = None
    confidence: float = 0.0  # final (rule, or rule+ML blend)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_heading_or_fragment(text: str, min_tokens: int) -> bool:
    words = text.split()
    if len(words) < min_tokens:
        return True
    if not re.search(r"[a-z]", text):
        return True  # ALL CAPS heading
    if not text.rstrip().endswith((".", "!", ")", "]", '"', "'")) and len(words) < 12:
        return True
    return False


def score_sentence(text: str, section: str) -> tuple[list[str], list[str], list[str], float]:
    """Apply claim and hedge patterns.

    Returns:
        (types found, cue strings, hedge terms, rule confidence 0..1)
    """
    low = text.lower()
    types, cues = [], []
    for ctype in TYPE_PRIORITY:
        for pat in CLAIM_PATTERNS[ctype]:
            m = re.search(pat, low)
            if m:
                if ctype not in types:
                    types.append(ctype)
                cues.append(m.group(0).strip()[:40])
    hedges = [m.group(0) for pat in HEDGE_PATTERNS for m in [re.search(pat, low)] if m]
    if not types:
        return [], [], hedges, 0.0
    conf = 0.30 + 0.15 * len(types) + min(0.15, 0.05 * (len(cues) - len(types)))
    if section in STRONG_SECTIONS:
        conf += 0.10
    if re.search(r"\b(we|our)\b", low) and "finding" in types:
        conf += 0.05
    if types == ["quantitative"]:
        conf -= 0.15  # bare numbers (e.g. hyperparameters) are weak claims
    if hedges:
        conf -= 0.15
    return types, list(dict.fromkeys(cues)), hedges, float(max(0.0, min(1.0, conf)))


def _ml_scores(texts: list[str], settings: Settings) -> np.ndarray | None:
    """Similarity-to-prototype score in [0, 1] for each text, or None if disabled."""
    if not texts or not settings.use_ml_claim_ranking or not settings.use_embeddings:
        return None
    from .models import get_embedder

    emb = get_embedder(settings)
    pos = emb.similarity(texts, CLAIM_PROTOTYPES).max(axis=1)
    neg = emb.similarity(texts, NON_CLAIM_PROTOTYPES).max(axis=1)
    return 1.0 / (1.0 + np.exp(-8.0 * (pos - neg)))


def extract_claims(document: Document, citations: CitationReport | None = None,
                   settings: Settings = DEFAULT_SETTINGS) -> list[Claim]:
    """Extract candidate claims from a document's body sentences.

    Args:
        document: Parsed document.
        citations: Optional citation report (to attach resolved references).
        settings: Thresholds and ML-ranking toggle.
    """
    candidates: list[tuple[Sentence, list[str], list[str], list[str], float]] = []
    for s in document.body_sentences():
        if s.section == "Front Matter" or s.text.rstrip().endswith("?"):
            continue
        if _is_heading_or_fragment(s.text, settings.min_sentence_tokens):
            continue
        types, cues, hedges, conf = score_sentence(s.text, s.section)
        if types:
            candidates.append((s, types, cues, hedges, conf))

    ml = _ml_scores([c[0].text for c in candidates], settings)
    claims: list[Claim] = []
    for i, (s, types, cues, hedges, conf) in enumerate(candidates):
        markers = find_citations(s.text)
        sc = citations.for_sentence(s.index) if citations else None
        ml_score = float(ml[i]) if ml is not None else None
        final = conf if ml_score is None else 0.6 * conf + 0.4 * ml_score
        if final < settings.min_claim_confidence:
            continue
        claims.append(Claim(
            claim_id=f"C{len(claims) + 1:03d}", text=s.text,
            claim_type=next(t for t in TYPE_PRIORITY if t in types), types=types,
            section=s.section, page=s.page, sentence_index=s.index,
            rule_confidence=round(conf, 3), hedged=bool(hedges), hedge_terms=hedges, cues=cues,
            citation_markers=[m.raw for m in markers],
            cited_references=sc.reference_numbers if sc else [],
            ml_score=None if ml_score is None else round(ml_score, 3), confidence=round(final, 3),
        ))
    logger.info("Claims: %d candidates kept from %d body sentences.", len(claims), len(document.body_sentences()))
    return claims


def classify_sentence_rule(text: str, section: str = "Results", threshold: float = 0.40) -> bool:
    """Rule-only claim decision for a single sentence (used in evaluation)."""
    if text.rstrip().endswith("?") or _is_heading_or_fragment(text, 5):
        return False
    return score_sentence(text, section)[3] >= threshold


def classify_sentences_ml(texts: list[str], settings: Settings = DEFAULT_SETTINGS,
                          threshold: float = 0.40) -> list[bool]:
    """ML-assisted decision: 0.6 * rule confidence + 0.4 * prototype similarity."""
    ml = _ml_scores(texts, settings.with_overrides(use_ml_claim_ranking=True))
    out = []
    for i, t in enumerate(texts):
        if t.rstrip().endswith("?") or _is_heading_or_fragment(t, 5):
            out.append(False)
            continue
        rule = score_sentence(t, "Results")[3]
        score = rule if ml is None else 0.6 * rule + 0.4 * float(ml[i])
        out.append(score >= threshold)
    return out


def compare_rule_vs_ml(claims: list[Claim]) -> list[dict[str, Any]]:
    """Side-by-side rank of each claim under rule-only vs ML-assisted scoring."""
    by_rule = sorted(claims, key=lambda c: -c.rule_confidence)
    by_ml = sorted(claims, key=lambda c: -c.confidence)
    rule_rank = {c.claim_id: r for r, c in enumerate(by_rule, 1)}
    ml_rank = {c.claim_id: r for r, c in enumerate(by_ml, 1)}
    return [{
        "claim_id": c.claim_id, "text": c.text, "rule_confidence": c.rule_confidence,
        "ml_score": c.ml_score, "combined": c.confidence, "rule_rank": rule_rank[c.claim_id],
        "ml_assisted_rank": ml_rank[c.claim_id], "rank_change": rule_rank[c.claim_id] - ml_rank[c.claim_id],
    } for c in by_ml]
