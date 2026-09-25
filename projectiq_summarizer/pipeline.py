"""End-to-end evaluation of one submission:
signals -> summary -> k judge runs -> evidence checks -> policy -> confidence."""
import statistics
import time
from typing import Callable, List, Optional

from .confidence import combine, quote_found, self_consistency, signal_agreement
from .config import Settings
from .judge import (MIN_DETAILS, SPECIFICITY_CAP, ai_flag, apply_evidence_cap,
                    decide, judge_once, majority, weighted_score)
from .rubric import load_rubric
from .schemas import AgentResult, CriterionResult, EvaluationReport, Judgement, Rubric
from .signals import compute_signals
from .summariser import summarise

AI_ORDER = ["low", "medium", "high"]
TYPE_ORDER = ["standard", "highly_technical", "poorly_written", "likely_ai_generated"]
QUALITY_ORDER = ["excellent", "good", "fair", "poor"]
DEPTH_ORDER = ["high", "moderate", "low"]


def _judge_input(text: str, summary_text: str, settings: Settings) -> str:
    if len(text) <= settings.max_judge_chars:
        return text
    head, tail = text[:5000], text[-3000:]
    return (f"[Report is long; you are given a summary plus its opening and closing.]\n\n"
            f"SUMMARY: {summary_text}\n\nOPENING:\n{head}\n\n[...]\n\nCLOSING:\n{tail}")


def _verified(items: List[str], text: str) -> List[str]:
    """Keep only quotes that really appear in the text, without duplicates."""
    out, seen = [], set()
    for it in items:
        key = " ".join(it.lower().split())
        if it.strip() and key not in seen and quote_found(it, text) >= 0.75:
            out.append(it.strip())
            seen.add(key)
    return out


def evaluate(text: str, name: str, llm, settings: Settings,
             rubric: Optional[Rubric] = None,
             progress: Callable[[str], None] = lambda m: None) -> EvaluationReport:
    t0 = time.time()
    rubric = rubric or load_rubric()
    if not text or not text.strip():
        raise ValueError(f"'{name}' is empty")

    progress("measuring text signals")
    sig = compute_signals(text)
    ai_sig = sig.profile_scores["likely_ai_generated"]
    poor_sig = sig.profile_scores["poorly_written"]

    progress("summarising")
    summary, n_chunks = summarise(text, llm, settings)

    k = max(1, settings.judge_samples)
    judge_text = _judge_input(text, summary.summary, settings)
    samples: List[Judgement] = []
    for i in range(k):
        progress(f"judging (run {i + 1} of {k})")
        temp = 0.0 if i == 0 else settings.sample_temperature
        samples.append(judge_once(judge_text, rubric, llm, temp, settings.seed + i))

    # ---- per-run decisions (used for self-consistency) ----------------------
    sample_weighted, sample_recs, sample_types = [], [], []
    for j in samples:
        s = {c.criterion_id: c.score for c in j.criteria}
        s, _ = apply_evidence_cap(s, len(_verified(j.concrete_details, text)))
        w = weighted_score(s, rubric)
        flagged = ai_flag(j.ai_style.likelihood, j.content_type, ai_sig, poor_sig)
        sample_weighted.append(w)
        sample_types.append(j.content_type)
        sample_recs.append(decide(w, s, flagged)[0].value)

    # ---- aggregate the runs ------------------------------------------------
    notes = []
    per_crit = {c.id: [] for c in rubric.criteria}
    for j in samples:
        for c in j.criteria:
            per_crit[c.criterion_id].append(c.score)
    missing = [cid for cid, v in per_crit.items() if not v]
    if missing:
        notes.append(f"The judge never scored: {', '.join(missing)}. "
                     f"They are left out of the weighted score.")
    raw_means = {cid: round(statistics.mean(v), 2) for cid, v in per_crit.items() if v}

    details = _verified([d for j in samples for d in j.concrete_details], text)
    mean_scores, capped = apply_evidence_cap(raw_means, len(details))
    if capped:
        notes.append(f"Specificity capped at {SPECIFICITY_CAP}/10: fewer than "
                     f"{MIN_DETAILS} checkable details were found in the text.")

    final_weighted = weighted_score(mean_scores, rubric)
    judge_ai = majority([j.ai_style.likelihood for j in samples], AI_ORDER)
    judge_type = majority(sample_types, TYPE_ORDER)
    flagged = ai_flag(judge_ai, judge_type, ai_sig, poor_sig)
    rec, reason = decide(final_weighted, mean_scores, flagged)

    if flagged:
        reason += (f" Evidence: {len(sig.ai_phrases_found)} distinct stock phrases "
                   f"({sig.ai_phrases_per_100w} per 100 words), {len(details)} checkable "
                   f"details, and the model rated AI style '{judge_ai}'.")
        final_type = "likely_ai_generated"
    elif poor_sig >= 0.6 and sig.profile == "poorly_written":
        final_type = "poorly_written"
    else:
        final_type = judge_type
    judge_says_ai = judge_ai == "high" or judge_type == "likely_ai_generated"
    if judge_says_ai and not flagged:
        notes.append(f"The model suggested AI-style writing, but the text signals do not "
                     f"support it (AI-style score {ai_sig:.2f}, typo rate "
                     f"{sig.typo_rate:.1%}), so the submission was not flagged.")

    rep = next((j for j, r in zip(samples, sample_recs) if r == rec.value), samples[0])
    rep_by_id = {c.criterion_id: c for c in rep.criteria}
    any_by_id = {}
    for j in samples:
        for c in j.criteria:
            any_by_id.setdefault(c.criterion_id, c)

    criteria, grounding_hits = [], []
    for c in rubric.criteria:
        if c.id not in mean_scores:
            continue
        src = rep_by_id.get(c.id) or any_by_id[c.id]
        g = quote_found(src.evidence_quote, text)
        grounding_hits.append(g)
        vals = per_crit[c.id]
        criteria.append(CriterionResult(
            criterion_id=c.id, name=c.name, weight=c.weight, score=mean_scores[c.id],
            score_spread=round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
            justification=src.justification, evidence_quote=src.evidence_quote,
            evidence_found_in_text=g > 0))

    # ---- confidence --------------------------------------------------------
    grounding = statistics.mean(grounding_hits) if grounding_hits else 0.0
    ungrounded = sum(1 for g in grounding_hits if g == 0)
    if ungrounded:
        notes.append(f"{ungrounded} of {len(grounding_hits)} evidence quotes could not "
                     f"be found in the text.")
    consistency = self_consistency(sample_recs, rec.value, sample_types, judge_type,
                                   sample_weighted)
    if consistency is not None and len(set(sample_recs)) > 1:
        notes.append(f"Judge runs disagreed on the recommendation: "
                     f"{', '.join(sample_recs)}.")
    agreement = signal_agreement(sig.profile, sig.profile_margin, judge_type)
    if sig.profile != judge_type:
        notes.append(f"The model said '{judge_type}' while the text signals say "
                     f"'{sig.profile}'.")
    conf = combine(consistency, agreement, grounding, notes)

    indicators = []
    for j in [rep] + [s for s in samples if s is not rep]:
        for ind in j.ai_style.indicators:
            if ind.strip() and ind.lower().strip() not in {x.lower() for x in indicators}:
                indicators.append(ind.strip())

    agent = AgentResult(
        agent="summariser_rubric_evaluator",
        score=final_weighted,
        justification=f"{rec.value}: {reason} {rep.rationale}".strip(),
        evidence=[c.evidence_quote for c in criteria if c.evidence_quote],
        confidence=round(conf.score / 100, 3))

    return EvaluationReport(
        name=name, model=settings.model, word_count=sig.words, chunks=n_chunks,
        summary=summary, content_type=final_type, judge_content_type=judge_type,
        concrete_details=details[:10],
        generic_claims=_verified(rep.generic_claims, text)[:6],
        recommendation=rec, recommendation_reason=reason,
        weighted_score=final_weighted, criteria=criteria,
        writing_quality=majority([j.writing_quality for j in samples], QUALITY_ORDER),
        technical_depth=majority([j.technical_depth for j in samples], DEPTH_ORDER),
        ai_style_likelihood=judge_ai, ai_style_indicators=indicators[:8],
        rationale=rep.rationale, confidence=conf, signals=sig.to_dict(),
        agent_result=agent, elapsed_s=round(time.time() - t0, 1))
