"""Numerical consistency checks.

1. Extract numbers with their unit (%, points, ...), kind (percentage, p-value,
   sample size, metric value, count) and the metric they describe (accuracy,
   F1, n, ...), plus a qualifier (main result, baseline, ablation, prior work).
2. Compare values for the same metric:
   * ``rule_based_check``   - every pair of same-metric values anywhere in the text.
   * ``section_aware_check``- only headline values (no baselines, ablations or
     cited prior work), compared ACROSS sections (Abstract vs Results vs
     Conclusion, Methods vs Results for sample sizes). Fewer false alarms.
3. Arithmetic: "improved from 80% to 90%, a 10% improvement" -> absolute
   (percentage points) vs relative change ambiguity, or a plain mismatch.

All flags show both contexts so a reviewer can judge. Different values can be
legitimate (different test sets, rounding, conditions).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

from .citation_analyzer import find_citations
from .config import DEFAULT_SETTINGS, Settings
from .pdf_extractor import Document

logger = logging.getLogger(__name__)

METRICS: dict[str, list[str]] = {
    "mAP": [r"\bmap(?:\s*@\s*|\s*)(?:0?\.5|50)?(?:\s*[:\-\u2013]\s*(?:0?\.95|95))?(?![\w])", r"mean average precision"],
    "accuracy": [r"\baccuracy\b", r"\bacc\b"],
    "f1": [r"\bmacro[- ]?f1\b", r"\bmicro[- ]?f1\b", r"\bf1(?:[- ]score)?\b", r"\bf-?measure\b"],
    "precision": [r"\bprecision\b"],
    "recall": [r"\brecall\b", r"\bsensitivity\b"],
    "auc": [r"\bauc\b", r"\broc[- ]auc\b"],
    "error rate": [r"\berror rate\b", r"\bwer\b"],
    "bleu": [r"\bbleu\b"],
    "rouge": [r"\brouge(?:-\w+)?\b"],
    "loss": [r"\bloss\b"],
    "parameters": [r"\bparameters\b", r"\bparams\b"],
    "epochs": [r"\bepochs?\b"],
    "learning rate": [r"\blearning rate\b", r"\blr\b"],
    "batch size": [r"\bbatch size\b"],
}
PERCENT_METRICS = {"accuracy", "f1", "precision", "recall", "auc", "error rate", "bleu", "rouge",
                   "mAP", "mAP@50", "mAP@50-95"}
# Only headline performance metrics and explicit sample sizes ("n = 120") are compared across sections.
# Loss weights, epochs, batch sizes, parameter counts and dataset sizes legitimately differ between
# sections (settings, subsets, iterations), so comparing them produces false alarms.
CROSS_SECTION_METRICS = PERCENT_METRICS | {"sample size (n)"}
KEY_SECTIONS = ("Abstract", "Methods", "Results", "Discussion", "Conclusion")
_ABLATION_RE = re.compile(r"\b(removing|without|ablat\w*|w/o|when we (removed|dropped)|reduced (\w+ )?to|variant)\b", re.I)
_CHANGE_AFTER_RE = re.compile(r"^\s*(?:\w+\s){0,2}?(improvement|increase|gain|boost|reduction|decrease|drop|rise|"
                              r"higher|lower|better|worse|faster|slower)\b", re.I)
_CHANGE_BEFORE_RE = re.compile(r"\b(by|boost of|gain of|increase of|improvement of|reduction of|drop of)\s*"
                               r"(about|around|approximately|roughly|nearly|almost|over)?\s*$", re.I)
_SETTING_RE = re.compile(r"\b(iteration\s*\d|epoch\s*\d|after (the first )?\d+ epochs|fold\s*\d|run\s*\d"
                         r"|(?:training|validation|train|val)\s+(?:map|accuracy|loss|precision|recall|f1))\b", re.I)
_CHANGE_CONTEXT_RE = re.compile(r"\b(improvement|increase|gain|boost|reduction|decrease|drop|rise)\b[^.;]{0,25}$", re.I)
_SUBSET_RE = re.compile(r"\bper-class\b|\bclass(?:es)? (?:like|such as)\b|\bsuch as\b|\b(?:for|of|on)\s+(?:the\s+)?['\u2018\"]?[A-Z][\w-]*['\u2019\"]?\s+class\b"
                        r"|['\u2018\"][A-Z][\w-]*['\u2019\"]\s*\(", re.I)
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|\.\d+)(\s?%| ?percentage points| ?pp\b| ?points)?")


@dataclass
class NumberMention:
    """A number found in the text, with what it describes."""

    value: float
    raw: str
    unit: str
    kind: str        # percentage | p_value | sample_size | metric_value | count
    metric: str
    qualifier: str   # main | baseline | ablation | prior_work | other
    sentence_index: int
    section: str
    page: int
    context: str

    def comparable_value(self) -> float:
        """Percent-scale value for percent metrics (0.924 -> 92.4)."""
        if self.metric in PERCENT_METRICS and self.unit != "%" and self.value <= 1.0:
            return self.value * 100
        return self.value


@dataclass
class NumericFlag:
    """A potential numeric inconsistency for human review."""

    flag_id: str
    kind: str        # cross-section mismatch | within-text mismatch | arithmetic ambiguity | arithmetic mismatch
    method: str      # rule-based | section-aware | arithmetic
    metric: str
    values: list[str]
    sections: list[str]
    pages: list[int]
    contexts: list[str]
    difference: float
    severity: str
    reason: str
    sentence_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _metric_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, metric name) for every metric word in the text. mAP variants get their own name."""
    spans: list[tuple[int, int, str]] = []
    for name, pats in METRICS.items():
        for pat in pats:
            for m in re.finditer(pat, text, re.I):
                if any(a < m.end() and m.start() < b for a, b, _ in spans):
                    continue
                label = name
                if name == "mAP":
                    w = m.group(0).lower()
                    label = "mAP@50-95" if "95" in w else ("mAP@50" if ("50" in w or ".5" in w) else "mAP")
                spans.append((m.start(), m.end(), label))
    return sorted(spans)


def _bind_metric(text: str, a: int, b: int, spans: list[tuple[int, int, str]]) -> str:
    """The metric a number belongs to, only if the metric word sits right next to it.

    Before the number: at most 28 characters, no other number and no sentence break in between
    ("accuracy of 92.4%", "Precision: 0.519", "AUC of the system was 0.90").
    After the number: at most 12 characters ("92.4% accuracy", "0.89 F1").
    """
    before = [(s, e, n) for s, e, n in spans if e <= a]
    if before:
        s_, e_, name = before[-1]
        gap = text[e_:a]
        if len(gap) <= 28 and not re.search(r"\d", gap) and not re.search(r"[;.!?](\s|$)|\|", gap):
            return name
    after = [(s, e, n) for s, e, n in spans if s >= b]
    if after:
        s_, e_, name = after[0]
        gap = text[b:s_]
        if len(gap) <= 12 and not re.search(r"[\d,;.|]", gap.replace("%", "")):
            return name
    return ""


def _looks_like_code_or_table(sentence: str) -> bool:
    """Training scripts, config dumps and table rows: numbers there are not prose claims."""
    nums = len(_NUM_RE.findall(sentence))
    return (nums >= 6 or "|" in sentence or sentence.count("=") >= 2
            or bool(re.search(r"\d\.\d+%?\s+\d*\.\d+", sentence))  # "0.491 0.511": a table row
            or bool(re.search(r"\b\w+\.\w+\(|\bimport\b|\w+\(\s*\w+\s*=", sentence)))


def _qualifier(sentence: str, start: int, end: int, metric_gap: str = "") -> str:
    """Classify the number's role from its sentence.

    table      - code, config or table row
    prior_work - a cited result from another paper
    change     - a difference, not a level ("a 10% improvement", "250% boost", "from 80% to 90%")
    baseline   - another system's value
    ablation   - a reduced variant of the model
    subset     - one class / category only ("accuracy for Bat", "'Mascot' (0.92)")
    setting    - one iteration / epoch / fold
    main       - a headline value
    """
    if _looks_like_code_or_table(sentence):
        return "table"
    if find_citations(sentence) and re.search(r"\b(reported|prior|previous|earlier|et al)\b", sentence, re.I):
        return "prior_work"
    before = sentence[max(0, start - 30) : start]
    after = sentence[end : end + 30]
    if (_FROM_TO_RE.search(sentence) or _CHANGE_AFTER_RE.match(after) or _CHANGE_BEFORE_RE.search(before)
            or _CHANGE_CONTEXT_RE.search(sentence[max(0, start - 40) : start]) or "\u2192" in sentence):
        return "change"
    if re.search(r"\bbaseline\b", sentence[end : end + 45], re.I) or re.search(r"\bbaseline\b", sentence[max(0, start - 45) : start], re.I) \
            or re.search(r"\b(compared (with|to)|than|versus|vs\.?)\s*$", before, re.I):
        return "baseline"
    if _ABLATION_RE.search(sentence[:start]):
        return "ablation"
    if _SUBSET_RE.search(sentence[max(0, start - 90) : end + 10]) or re.search(r"\bfor (the )?[A-Z]\w+", metric_gap):
        return "subset"
    if _SETTING_RE.search(sentence):
        return "setting"
    return "main"


def extract_numbers(text: str, sentence_index: int = 0, section: str = "", page: int = 0) -> list[NumberMention]:
    """Extract the meaningful numbers in one sentence, each tied to the metric right next to it."""
    mentions: list[NumberMention] = []
    spans = _metric_spans(text)
    used: list[tuple[int, int]] = [(a, b) for a, b, _ in spans]  # "50" in "mAP@50" is not a value
    cite_spans = [(m.start, m.end) for m in find_citations(text)]

    def _overlaps(a: int, b: int) -> bool:
        return any(x < b and a < y for x, y in used + cite_spans)

    for m in re.finditer(r"\bp\s*([<=>\u2264\u2265])\s*(0?\.\d+|\d(?:\.\d+)?)", text):
        mentions.append(NumberMention(float(m.group(2)), m.group(0), m.group(1), "p_value", "p-value",
                                      "main", sentence_index, section, page, text))
        used.append((m.start(), m.end()))
    for m in re.finditer(r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)", text):
        q = "table" if _looks_like_code_or_table(text) else ("setting" if _SETTING_RE.search(text) else "main")
        mentions.append(NumberMention(float(m.group(1).replace(",", "")), m.group(0), "", "sample_size",
                                      "sample size (n)", q, sentence_index, section, page, text))
        used.append((m.start(), m.end()))
    for m in _NUM_RE.finditer(text):
        a, b = m.start(), m.end()
        if _overlaps(a, b):
            continue
        num_s, unit = m.group(1), (m.group(2) or "").strip()
        nxt, prv = text[b : b + 1], text[a - 1 : a] if a else ""
        if nxt in "/x\u00d7" and nxt or prv in "/x\u00d7" and prv or prv in "-\u2013" and text[a - 2 : a - 1].isdigit():
            continue  # splits like 80/10/10, sizes like 640x640, ranges like 11-24
        val = float(num_s.replace(",", ""))
        if not unit and "." not in num_s and 1900 <= val <= 2099:
            continue  # a year
        metric = _bind_metric(text, a, b, spans)
        if not metric and unit != "%":
            continue  # bare numbers without a metric are not compared
        prev_metric_end = max((e for s_, e, _ in spans if e <= a), default=a)
        kind = "percentage" if unit == "%" else "metric_value"
        mentions.append(NumberMention(val, num_s + (unit if unit == "%" else (" " + unit if unit else "")),
                                      unit, kind, metric or "percentage (unlabelled)",
                                      _qualifier(text, a, b, text[prev_metric_end:a] if metric else ""),
                                      sentence_index, section, page, text))
        used.append((a, b))
    return mentions


def extract_document_numbers(document: Document) -> list[NumberMention]:
    """Extract numbers from every body sentence of a document."""
    out: list[NumberMention] = []
    for s in document.body_sentences():
        if s.section == "Front Matter":
            continue
        out.extend(extract_numbers(s.text, s.index, s.section, s.page))
    return out


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #
def _differs(a: NumberMention, b: NumberMention, settings: Settings) -> tuple[bool, float]:
    va, vb = a.comparable_value(), b.comparable_value()
    diff = abs(va - vb)
    if a.kind in ("sample_size", "count") or a.metric in ("parameters", "sample size (n)", "participants"):
        tol = max(settings.numeric_rel_tolerance * max(va, vb), 0.0)
    elif a.metric in PERCENT_METRICS or a.unit == "%":
        tol = settings.numeric_tolerance
    elif max(va, vb) <= 1.0:
        tol = settings.numeric_tolerance / 100
    else:
        tol = settings.numeric_rel_tolerance * max(va, vb)
    return diff > tol + 1e-9, round(diff, 4)


def _comparable_groups(mentions: list[NumberMention], cross_section: bool = False) -> dict[tuple[str, str], list[NumberMention]]:
    groups: dict[tuple[str, str], list[NumberMention]] = {}
    for m in mentions:
        if m.metric in ("percentage (unlabelled)", "p-value", "learning rate", "epochs", "batch size"):
            continue
        if cross_section and m.metric not in CROSS_SECTION_METRICS:
            continue
        groups.setdefault((m.metric, m.qualifier), []).append(m)
    return groups


def _make_flag(kind: str, method: str, a: NumberMention, b: NumberMention, diff: float, severity: str,
               reason: str) -> NumericFlag:
    return NumericFlag("", kind, method, a.metric, [a.raw, b.raw], [a.section, b.section], [a.page, b.page],
                       [a.context, b.context], diff, severity, reason, [a.sentence_index, b.sentence_index])


def rule_based_check(mentions: list[NumberMention], settings: Settings = DEFAULT_SETTINGS) -> list[NumericFlag]:
    """Flag every pair of differing values for the same metric (any section, any role)."""
    flags: list[NumericFlag] = []
    by_metric: dict[str, list[NumberMention]] = {}
    for key, ms in _comparable_groups(mentions).items():
        by_metric.setdefault(key[0], []).extend(ms)
    for metric, ms in by_metric.items():
        for a, b in combinations(ms, 2):
            if a.sentence_index == b.sentence_index:
                continue
            differs, diff = _differs(a, b, settings)
            if differs:
                flags.append(_make_flag("within-text mismatch", "rule-based", a, b, diff, "low",
                                        f"'{metric}' is reported as {a.raw} and {b.raw} (difference {diff:g})."))
    return flags


def section_aware_check(mentions: list[NumberMention], settings: Settings = DEFAULT_SETTINGS,
                        labels: dict[int, str] | None = None) -> list[NumericFlag]:
    """Compare headline performance values for the same metric across key sections only.

    Args:
        labels: optional {sentence index: the paper's own heading}, used in the messages.
    """
    labels = labels or {}
    flags: list[NumericFlag] = []
    for (metric, qualifier), ms in _comparable_groups(mentions, cross_section=True).items():
        if qualifier != "main":
            continue
        ms = [m for m in ms if m.section in KEY_SECTIONS]
        seen_pairs: set[tuple[float, float, str, str]] = set()
        for a, b in combinations(ms, 2):
            if a.section == b.section:
                continue
            differs, diff = _differs(a, b, settings)
            key = (a.comparable_value(), b.comparable_value(), a.section, b.section)
            if not differs or key in seen_pairs:
                continue
            seen_pairs.add(key)
            summary = {"Abstract", "Conclusion"}
            severity = "high" if ({a.section, b.section} & summary and {a.section, b.section} & {"Results"}) else "medium"
            la, lb = labels.get(a.sentence_index, a.section), labels.get(b.sentence_index, b.section)
            flag = _make_flag(
                "cross-section mismatch", "section-aware", a, b, diff, severity,
                f"The {metric} is {a.raw} in {la} (p.{a.page}) but {b.raw} in {lb} (p.{b.page}). "
                f"These may refer to different settings, but a reader would expect them to match.",
            )
            flag.sections = [la, lb]
            flags.append(flag)
    return flags


_FROM_TO_RE = re.compile(r"from\s+(\d+(?:\.\d+)?)\s?%\s+to\s+(\d+(?:\.\d+)?)\s?%", re.I)
_CHANGE_RE = re.compile(
    r"(?:(?:an?|by)\s+)?(\d+(?:\.\d+)?)\s?(%|percent|percentage points|pp)\s*(?:relative\s+|absolute\s+)?"
    r"(improvement|increase|gain|reduction|decrease|drop|rise|higher|lower)?", re.I)


def arithmetic_check(mentions_text: list[tuple[int, str, int, str]], settings: Settings = DEFAULT_SETTINGS) -> list[NumericFlag]:
    """Check "from X% to Y% ... Z% improvement" statements.

    Args:
        mentions_text: (sentence_index, section, page, text) tuples.
    """
    flags: list[NumericFlag] = []
    for idx, section, page, text in mentions_text:
        ft = _FROM_TO_RE.search(text)
        if not ft:
            continue
        x, y = float(ft.group(1)), float(ft.group(2))
        rest = text[ft.end():]
        ch = next((m for m in _CHANGE_RE.finditer(rest) if m.group(3) or "point" in m.group(2).lower()), None)
        if not ch or x == 0:
            continue
        z, unit = float(ch.group(1)), ch.group(2).lower()
        absolute, relative = abs(y - x), abs(y - x) / x * 100
        tol = settings.numeric_tolerance
        if "point" in unit or unit == "pp":
            if abs(z - absolute) > tol:
                flags.append(NumericFlag("", "arithmetic mismatch", "arithmetic", "change", [ft.group(0), ch.group(0)],
                                         [section], [page], [text], round(abs(z - absolute), 3), "medium",
                                         f"From {x:g}% to {y:g}% is {absolute:g} percentage points, but the text says {z:g}.",
                                         [idx]))
            continue
        if abs(z - absolute) <= tol and abs(z - relative) > tol:
            flags.append(NumericFlag("", "arithmetic ambiguity", "arithmetic", "change", [ft.group(0), ch.group(0).strip()],
                                     [section], [page], [text], round(relative - absolute, 3), "low",
                                     f"'{z:g}%' matches the ABSOLUTE change ({absolute:g} percentage points). The RELATIVE "
                                     f"change from {x:g}% to {y:g}% is {relative:.1f}%. Consider writing "
                                     f"'{absolute:g} percentage points' to avoid ambiguity.", [idx]))
        elif abs(z - relative) > tol and abs(z - absolute) > tol:
            flags.append(NumericFlag("", "arithmetic mismatch", "arithmetic", "change", [ft.group(0), ch.group(0).strip()],
                                     [section], [page], [text], round(min(abs(z - absolute), abs(z - relative)), 3),
                                     "medium",
                                     f"From {x:g}% to {y:g}% is {absolute:g} points absolute or {relative:.1f}% relative, "
                                     f"but the text states {z:g}%.", [idx]))
    return flags


@dataclass
class NumericReport:
    """All numbers found plus flags from each method."""

    mentions: list[NumberMention]
    rule_based: list[NumericFlag]
    section_aware: list[NumericFlag]
    arithmetic: list[NumericFlag]

    @property
    def flags(self) -> list[NumericFlag]:
        """Flags used in the final report: section-aware + arithmetic."""
        return self.section_aware + self.arithmetic


def check_texts(items: list[tuple[str, str]], settings: Settings = DEFAULT_SETTINGS) -> NumericReport:
    """Run all checks on a list of (section, sentence) pairs (used in evaluation and tests)."""
    mentions: list[NumberMention] = []
    sent_info = []
    for i, (section, text) in enumerate(items):
        mentions.extend(extract_numbers(text, i, section, 0))
        sent_info.append((i, section, 0, text))
    return NumericReport(mentions, rule_based_check(mentions, settings), section_aware_check(mentions, settings),
                         arithmetic_check(sent_info, settings))


def check_document(document: Document, settings: Settings = DEFAULT_SETTINGS) -> NumericReport:
    """Run all numeric checks on a parsed document."""
    mentions = extract_document_numbers(document)
    sents = [(s.index, s.section, s.page, s.text) for s in document.body_sentences() if s.section != "Front Matter"]
    labels = {s.index: s.heading or s.section for s in document.sentences}
    report = NumericReport(mentions, rule_based_check(mentions, settings),
                           section_aware_check(mentions, settings, labels), arithmetic_check(sents, settings))
    for k, f in enumerate(report.rule_based, 1):
        f.flag_id = f"NR{k:03d}"
    for k, f in enumerate(report.flags, 1):
        f.flag_id = f"N{k:03d}"
    logger.info("Numbers: %d mentions; rule-based flags=%d, section-aware flags=%d, arithmetic flags=%d",
                len(mentions), len(report.rule_based), len(report.section_aware), len(report.arithmetic))
    return report
