"""Rubric judge and the recommendation policy.

The LLM does the part that needs judgement: reading the text, collecting
evidence, and scoring each rubric criterion with a reason. The final
recommendation is then decided by fixed rules that combine those scores with
the plain-code text signals, so the same inputs always give the same answer
and the decision can be explained line by line."""
from typing import List, Tuple

from .schemas import Judgement, Recommendation, Rubric

SYSTEM = (
    "You are a strict, experienced academic evaluator helping faculty review "
    "project reports. You reward concrete, checkable work and you are not "
    "impressed by confident or polished wording on its own. You never invent "
    "content. You are a decision-support tool: a human makes the final decision. "
    "Reply with JSON only.")

PROMPT = """Evaluate the project report text below. Fill the JSON fields in order.

STEP 1: COLLECT EVIDENCE (copy word for word from the text)
- "concrete_details": every checkable specific: numbers, measurements, parameter
  values, named libraries/tools/versions, datasets, specific results, specific
  failures. A general technique name alone ("facial recognition", "AI",
  "database") is NOT a concrete detail. Use an empty list if there are none.
- "generic_claims": sentences that are vague or promotional and could describe
  almost any project ("ensures high accuracy", "user-friendly and scalable",
  "robust measures have been implemented").

STEP 2: SCORE EACH CRITERION (integer 0 to 10)
RUBRIC: {rubric_name}
{criteria}

Scale: 0-3 poor, 4-6 adequate, 7-8 good, 9-10 excellent.
Hard rules:
- Evidence and specificity: fewer than 2 concrete details means at most 3.
- Technical depth: claims without any explanation of how or why mean at most 4.
- Authentic voice: text made mostly of generic claims means at most 3.
- Clarity: frequent spelling or grammar errors mean at most 4.
For each criterion: first "evidence_quote" (copied exactly, at most 25 words),
then "justification" in your own words explaining the score (do NOT just repeat
the quote), then "score".

STEP 3: OVERALL
- "writing_quality": poor | fair | good | excellent
- "technical_depth": low | moderate | high
- "ai_style": first "indicators" (observable features), then "likelihood" low |
  medium | high. Typical AI-generated writing is fluent and error-free, full of
  stock phrases ("in today's rapidly evolving", "delve", "leverage", "seamless",
  "cutting-edge", "plays a crucial role", "a testament to", "furthermore",
  "moreover", "in conclusion"), has an even sentence rhythm, and makes broad
  claims with no concrete details. IMPORTANT: spelling mistakes, grammar errors
  and informal phrasing are signs of HUMAN writing, not AI. Vague content alone
  is not evidence of AI. This is a style observation, not an accusation.
- "content_type": one of
    "poorly_written"      (spelling, grammar or structure problems dominate)
    "highly_technical"    (many concrete details: numbers, parameters, results)
    "likely_ai_generated" (fluent, stock phrases, generic claims, no concrete details)
    "standard"            (none of these stands out)
  Technical vocabulary without concrete details is NOT "highly_technical".
- "rationale": 2 to 3 sentences with your overall view.

Return one entry in "criteria" for EVERY criterion id listed above.

TEXT:
\"\"\"
{text}
\"\"\""""


def build_prompt(text: str, rubric: Rubric) -> str:
    crit = "\n".join(f'- id "{c.id}": {c.name} (weight {c.weight}). {c.descriptor}'
                     for c in rubric.criteria)
    return PROMPT.format(rubric_name=rubric.name, criteria=crit, text=text.strip())


def judge_once(text: str, rubric: Rubric, llm, temperature: float, seed: int) -> Judgement:
    j = llm.chat_json(SYSTEM, build_prompt(text, rubric), Judgement,
                      temperature=temperature, seed=seed)
    # Clean up: clamp scores, drop unknown ids, keep the first entry per id.
    valid = {c.id for c in rubric.criteria}
    seen, kept = set(), []
    for c in j.criteria:
        cid = c.criterion_id.strip().lower()
        if cid in valid and cid not in seen:
            c.criterion_id = cid
            c.score = max(0, min(10, int(c.score)))
            kept.append(c)
            seen.add(cid)
    j.criteria = kept
    return j


# ---------------------------------------------------------------------------
# Deterministic policy
# ---------------------------------------------------------------------------
SPECIFICITY_CAP = 3          # max specificity score with < MIN_DETAILS details
MIN_DETAILS = 2
AI_SIGNAL_STRONG = 0.60      # text signals alone are enough to flag
AI_SIGNAL_SUPPORT = 0.35     # needed to back up a "high" rating from the judge
POOR_SIGNAL_SLOPPY = 0.50    # heavy typos/mechanics: clearly human writing


def weighted_score(scores: dict, rubric: Rubric) -> float:
    num = sum(c.weight * scores[c.id] for c in rubric.criteria if c.id in scores)
    den = sum(c.weight for c in rubric.criteria if c.id in scores)
    return round(num / den, 2) if den else 0.0


def apply_evidence_cap(scores: dict, verified_details: int) -> Tuple[dict, bool]:
    """A text with almost no checkable details cannot score well on specificity,
    however the model rated it."""
    if verified_details < MIN_DETAILS and scores.get("specificity", 0) > SPECIFICITY_CAP:
        capped = dict(scores)
        capped["specificity"] = SPECIFICITY_CAP
        return capped, True
    return scores, False


def ai_flag(judge_likelihood: str, judge_type: str, ai_signal: float,
            poor_signal: float) -> bool:
    """Flag for review only when the evidence is strong:
    - the text signals are strongly AI-like on their own, or
    - the judge rates AI style high (or calls it AI with medium likelihood)
      AND the text signals lean the same way.
    Error-heavy writing is never flagged: that is a human pattern."""
    if poor_signal >= POOR_SIGNAL_SLOPPY:
        return False
    if ai_signal >= AI_SIGNAL_STRONG:
        return True
    judge_says_ai = judge_likelihood == "high" or (
        judge_type == "likely_ai_generated" and judge_likelihood == "medium")
    return judge_says_ai and ai_signal >= AI_SIGNAL_SUPPORT


def decide(weighted: float, scores: dict, flagged: bool) -> Tuple[Recommendation, str]:
    """Fixed rules, checked in order."""
    if flagged:
        return (Recommendation.FLAG_FOR_REVIEW,
                "The writing shows strong AI-style patterns. This is advisory, "
                "not proof: a faculty member should check it with the student "
                "before any grade is released.")
    clarity = scores.get("clarity", weighted)
    if weighted < 5.0 or clarity < 4:
        return (Recommendation.REVISE_AND_RESUBMIT,
                f"Weighted score {weighted}/10"
                + (f" and clarity {clarity}/10" if clarity < 4 else "")
                + " are below the acceptable standard.")
    if weighted < 7.0:
        return (Recommendation.ACCEPT_WITH_MINOR_REVISIONS,
                f"Weighted score {weighted}/10 meets the standard but specific "
                f"criteria need improvement.")
    return (Recommendation.ACCEPT,
            f"Weighted score {weighted}/10 with no integrity concerns.")


def majority(values: List[str], order: List[str] = None) -> str:
    """Most common value; ties go to the later item in `order` (the more
    cautious one), so a split vote never hides a concern."""
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    tied = [v for v, c in counts.items() if c == best]
    if order:
        tied.sort(key=lambda v: order.index(v) if v in order else -1)
        return tied[-1]
    return tied[0]
