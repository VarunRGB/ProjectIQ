"""Render an EvaluationReport for the console (plain ASCII, safe on Windows),
as Markdown, and as JSON."""
import textwrap
from pathlib import Path

from .schemas import EvaluationReport

LABEL = {
    "ACCEPT": "Accept",
    "ACCEPT_WITH_MINOR_REVISIONS": "Accept with minor revisions",
    "REVISE_AND_RESUBMIT": "Revise and resubmit",
    "FLAG_FOR_REVIEW": "Flag for human review",
}
TYPE_LABEL = {
    "poorly_written": "Poorly written",
    "highly_technical": "Highly technical",
    "likely_ai_generated": "Likely AI-generated (advisory)",
    "standard": "Standard",
}


def _wrap(s: str, indent: str = "  ", width: int = 88) -> str:
    return textwrap.fill(s, width=width, initial_indent=indent, subsequent_indent=indent)


def to_console(r: EvaluationReport) -> str:
    c = r.confidence
    out = [
        "=" * 88,
        f" {r.name}",
        "=" * 88,
        f"  Recommendation : {LABEL[r.recommendation.value]}",
        f"  Confidence     : {c.score:.0f}/100 ({c.band})",
        f"  Content type   : {TYPE_LABEL[r.content_type]}"
        + ("" if r.judge_content_type == r.content_type
           else f"   (model alone said: {TYPE_LABEL[r.judge_content_type]})"),
        f"  Weighted score : {r.weighted_score}/10",
        f"  Words / chunks : {r.word_count} / {r.chunks}      "
        f"Model: {r.model}      Time: {r.elapsed_s}s",
        "",
        " SUMMARY",
        _wrap(r.summary.summary),
    ]
    out += [_wrap(f"- {k}", "    ") for k in r.summary.key_points]
    out += ["", " WHY THIS RECOMMENDATION", _wrap(r.recommendation_reason),
            _wrap(r.rationale), "",
            f" EVIDENCE COLLECTED ({len(r.concrete_details)} checkable details, "
            f"{len(r.generic_claims)} generic claims)"]
    out += [_wrap(f"+ {d}", "  ") for d in r.concrete_details[:5]]
    out += [_wrap(f"- {g}", "  ") for g in r.generic_claims[:3]]
    out += ["", " RUBRIC SCORES"]
    for cr in r.criteria:
        mark = "" if cr.evidence_found_in_text else "   [quote not found in text]"
        spread = f" (+/-{cr.score_spread})" if cr.score_spread else ""
        out.append(f"  {cr.name:<42} {cr.score:>5}/10{spread}  x{cr.weight}{mark}")
    out += ["", " CONFIDENCE BREAKDOWN",
            f"  self-consistency   : "
            f"{'n/a' if c.self_consistency is None else f'{c.self_consistency:.2f}'}",
            f"  signal agreement   : {c.signal_agreement:.2f}"
            f"   (text signals say: {r.signals['profile']})",
            f"  evidence grounding : {c.evidence_grounding:.2f}"]
    out += [_wrap(f"* {n}", "  ") for n in c.notes]
    if r.ai_style_indicators:
        out += ["", f" AI-STYLE INDICATORS (likelihood: {r.ai_style_likelihood}, advisory)"]
        out += [_wrap(f"- {i}", "  ") for i in r.ai_style_indicators]
    out.append("")
    return "\n".join(out)


def to_markdown(r: EvaluationReport) -> str:
    c = r.confidence
    s = r.signals
    md = [
        f"# {r.name}",
        "",
        "| | |", "|---|---|",
        f"| **Recommendation** | {LABEL[r.recommendation.value]} |",
        f"| **Confidence** | {c.score:.0f}/100 ({c.band}) |",
        f"| **Content type** | {TYPE_LABEL[r.content_type]} |",
        f"| Content type (model alone) | {TYPE_LABEL[r.judge_content_type]} |",
        f"| **Weighted score** | {r.weighted_score}/10 |",
        f"| Writing quality / technical depth | {r.writing_quality} / {r.technical_depth} |",
        f"| Model | `{r.model}` |",
        "",
        "## Summary", "", r.summary.summary, "",
        *[f"- {k}" for k in r.summary.key_points], "",
        "## Why this recommendation", "", r.recommendation_reason, "", r.rationale, "",
        "## Evidence collected", "",
        f"**Checkable details found ({len(r.concrete_details)}):**", "",
        *([f"- {d}" for d in r.concrete_details] or ["- none"]), "",
        f"**Generic claims ({len(r.generic_claims)}):**", "",
        *([f"- {g}" for g in r.generic_claims] or ["- none"]), "",
        "## Rubric scores", "",
        "| Criterion | Weight | Score | Justification | Evidence |",
        "|---|---|---|---|---|",
    ]
    for cr in r.criteria:
        ev = cr.evidence_quote.replace("|", "\\|")
        if not cr.evidence_found_in_text:
            ev += " *(not found in text)*"
        md.append(f"| {cr.name} | {cr.weight} | {cr.score} | "
                  f"{cr.justification.replace('|', '/')} | \"{ev}\" |")
    md += ["", "## Confidence breakdown", "",
           f"- Self-consistency: "
           f"{'n/a' if c.self_consistency is None else f'{c.self_consistency:.2f}'}",
           f"- Signal agreement: {c.signal_agreement:.2f} (text signals: `{s['profile']}`)",
           f"- Evidence grounding: {c.evidence_grounding:.2f}", ""]
    md += [f"- *{n}*" for n in c.notes]
    if r.ai_style_indicators:
        md += ["", f"## AI-style indicators (likelihood: {r.ai_style_likelihood}, advisory)", ""]
        md += [f"- {i}" for i in r.ai_style_indicators]
    md += ["", "## Text signals (computed without the model)", "",
           f"- Typo rate: {s['typo_rate']:.1%}  ·  mechanics issues per 100 words: "
           f"{s['mechanics_per_100w']}",
           f"- AI-style phrases per 100 words: {s['ai_phrases_per_100w']}"
           + (f" ({', '.join(s['ai_phrases_found'][:8])})" if s['ai_phrases_found'] else ""),
           f"- Specific details per 100 words: {s['specifics_per_100w']}  ·  "
           f"technical density: {s['technical_density']}",
           f"- Sentence length: avg {s['avg_sentence_words']} words, variation "
           f"{s['sentence_length_cv']}  ·  Flesch reading ease: {s['flesch_reading_ease']}",
           f"- Profile scores: {s['profile_scores']}", ""]
    return "\n".join(md)


def save(r: EvaluationReport, out_dir) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(r.name).stem
    (out / f"{stem}.json").write_text(r.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{stem}.md").write_text(to_markdown(r), encoding="utf-8")
    return out / f"{stem}.md"
