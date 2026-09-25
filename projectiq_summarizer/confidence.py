"""Confidence score (0-100) built from three independent checks.

1. Self-consistency: the judge scores the same text several times. If the
   runs agree on the recommendation, the content type and the score, the
   judgement is stable.
2. Signal agreement: plain-code text measurements (typos, AI-style phrases,
   technical density) reach their own verdict without the LLM. Agreement
   between the two is evidence the judgement is right.
3. Evidence grounding: every score must quote the text. Quotes that cannot
   be found in the text suggest the model is making things up.

The score says how much to trust the recommendation, not how good the work
is. A low score means: a human should look at this one carefully."""
import re
import statistics
from typing import List, Optional

from .schemas import ConfidenceBreakdown

W_CONSISTENCY, W_AGREEMENT, W_GROUNDING = 0.45, 0.35, 0.20


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r"[^a-z0-9%.' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_found(quote: str, text: str) -> float:
    """1.0 exact (ignoring case and punctuation), 0.75 near match, 0.0 not found."""
    q, t = _norm(quote).strip(" ."), _norm(text)
    if len(q) < 8:
        return 0.0
    if q in t:
        return 1.0
    qw = q.split()
    if len(qw) < 3:
        return 0.0
    grams = {" ".join(qw[i:i + 3]) for i in range(len(qw) - 2)}
    hit = sum(1 for g in grams if g in t) / len(grams)
    return 0.75 if hit >= 0.7 else 0.0


def self_consistency(sample_recs: List[str], final_rec: str,
                     sample_types: List[str], final_type: str,
                     sample_scores: List[float]) -> Optional[float]:
    k = len(sample_recs)
    if k < 2:
        return None
    rec_agree = sum(r == final_rec for r in sample_recs) / k
    type_agree = sum(t == final_type for t in sample_types) / k
    stability = max(0.0, 1 - statistics.pstdev(sample_scores) / 2.0)
    return round(0.4 * rec_agree + 0.3 * type_agree + 0.3 * stability, 3)


def signal_agreement(heur_profile: str, heur_margin: float, llm_type: str) -> float:
    if heur_profile == llm_type:
        return 1.0 if heur_margin >= 0.1 else 0.8
    if llm_type == "standard" and heur_margin >= 0.3:
        return 0.35      # signals are clear-cut, the model saw nothing: real disagreement
    if "standard" in (heur_profile, llm_type):
        return 0.55      # one side is simply unsure
    return 0.15          # both are sure, and they contradict each other


def combine(consistency: Optional[float], agreement: float, grounding: float,
            notes: List[str]) -> ConfidenceBreakdown:
    if consistency is None:
        score = 100 * (0.6 * agreement + 0.4 * grounding)
        notes = notes + ["Only one judge run, so self-consistency was not measured. "
                         "Use --samples 3 for a fuller confidence score."]
    else:
        score = 100 * (W_CONSISTENCY * consistency + W_AGREEMENT * agreement
                       + W_GROUNDING * grounding)
    score = round(score, 1)
    band = "High" if score >= 75 else "Medium" if score >= 50 else "Low"
    if band == "Low":
        notes = notes + ["Low confidence: this submission should get a careful human review."]
    return ConfidenceBreakdown(score=score, band=band, self_consistency=consistency,
                               signal_agreement=round(agreement, 3),
                               evidence_grounding=round(grounding, 3), notes=notes)
