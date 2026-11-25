"""AI-generated text: how much of the student's OWN writing reads as machine-written.

Method: Binoculars (Hans et al., 2024, "Spotting LLMs With Binoculars"), run locally.
Two small language models that share a tokenizer read the same passage:

    observer   = a base model      (models/Qwen2.5-0.5B, or 1.5B / 3B)
    performer  = its chat version  (models/Qwen2.5-0.5B-Instruct, ...)

The size is chosen with PROJECTIQ_AI_MODEL_SIZE (config.AI_MODEL_PAIRS). Bigger models
read AI text better, which the method depends on, but are slower and need more memory.

    score = log-perplexity of the passage under the performer
            -----------------------------------------------------
            cross-perplexity: how surprised the performer is, on average,
            by what the observer expected at each position

Machine text is predictable to both models in the same way, so its score is LOW.
Dividing by the cross-perplexity cancels out how predictable the TOPIC is, which is
what made plain perplexity (distilgpt2) swing between 36 and 95 on AI text alone.

How the percentage is built:
1. Copied sentences are removed first (they are someone else's writing).
2. The remaining sentences are grouped into passages of about 100 to 300 words,
   following the document's paragraphs (or pages, for PDFs).
3. Each passage gets a Binoculars score; at or below `binoculars_ai_max` it is flagged.
4. AI % = words in flagged passages / words in scored passages.

Output matches the orchestrator's `ai_detection` contract (projectiq_core AIAnalysis):
status, estimated_percent, interpretation, confidence, flagged_passages, limitations.

Stock AI phrases (projectiq_summarizer.signals, read only) are shown per flagged
passage as the "why". They never decide on their own.

If the two models are not in models/, status is NOT_AVAILABLE and the older
distilgpt2 check (ai_indicators.py) still runs as before.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from projectiq_summarizer.signals import _AI_RES

from .config import DEFAULT, MODELS_DIR, IntegritySettings, ai_models
from .contracts import Evidence
from .evidence import Signal

logger = logging.getLogger(__name__)

METHOD = "Binoculars (observer/performer language models, run locally)"
LIMITATIONS = [
    "Advisory only: an AI estimate is a reason to talk with the student, never proof or grounds for a penalty.",
    "Copied sentences are excluded; the estimate covers the student's own writing only.",
    "Formal, formulaic or non-native writing can read as machine-like, and AI text edited by hand can read as human.",
    "Passages under the minimum length are not scored.",
]

# passage -> (score, tokens scored); None when the passage is too short to score
PassageScorer = Callable[[str], Optional[tuple]]


@dataclass
class Passage:
    location: str
    text: str
    words: int
    score: Optional[float] = None
    flagged: bool = False
    stock_phrases: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The two local models
# --------------------------------------------------------------------------- #
_LOADED: dict = {}   # (observer path, performer path, dtype) -> (tokenizer, observer, performer)


class Binoculars:
    """Observer and performer from models/, CPU only, loaded on first use and then kept
    in memory for the rest of the run (loading takes seconds and a few GB). Never downloads."""

    def __init__(self, settings: IntegritySettings = DEFAULT, models_dir: Path = MODELS_DIR):
        self.settings = settings
        observer, performer, self.dtype, self.threshold = ai_models(settings)
        self.observer_path = Path(models_dir) / observer
        self.performer_path = Path(models_dir) / performer
        self.available = self.observer_path.exists() and self.performer_path.exists()
        self._tok = self._observer = self._performer = None

    def missing(self) -> List[str]:
        return [str(p) for p in (self.observer_path, self.performer_path) if not p.exists()]

    def _load(self) -> None:
        if self._observer is not None:
            return
        key = (str(self.observer_path), str(self.performer_path), self.dtype)
        if key in _LOADED:
            self._tok, self._observer, self._performer = _LOADED[key]
            return
        import torch

        torch.set_num_threads(self.settings.ai_threads)   # leave cores free for the rest of the laptop
        from transformers import AutoModelForCausalLM, AutoTokenizer

        import transformers

        # transformers 4.56+ calls the argument "dtype"; older versions "torch_dtype".
        major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
        key = "dtype" if (major, minor) >= (4, 56) else "torch_dtype"
        kwargs = {"local_files_only": True, key: getattr(torch, self.dtype)}
        self._tok = AutoTokenizer.from_pretrained(self.observer_path, local_files_only=True)
        self._observer = AutoModelForCausalLM.from_pretrained(self.observer_path, **kwargs).eval()
        self._performer = AutoModelForCausalLM.from_pretrained(self.performer_path, **kwargs).eval()
        _LOADED.clear()                                    # keep one pair in memory, not several
        _LOADED[key] = (self._tok, self._observer, self._performer)

    def __call__(self, text: str) -> Optional[tuple]:
        import torch
        import torch.nn.functional as F

        self._load()
        ids = self._tok(text, return_tensors="pt", truncation=True,
                        max_length=self.settings.ai_max_tokens).input_ids
        if ids.shape[1] < 16:
            return None
        with torch.no_grad():
            obs = self._observer(ids).logits[0, :-1].float()
            perf = self._performer(ids).logits[0, :-1].float()
        targets = ids[0, 1:]
        log_ppl = F.cross_entropy(perf, targets)                                  # performer surprise
        x_ppl = (F.softmax(obs, dim=-1) * -F.log_softmax(perf, dim=-1)).sum(-1).mean()
        return float(log_ppl / x_ppl), int(targets.numel())


# --------------------------------------------------------------------------- #
# Passages
# --------------------------------------------------------------------------- #
def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))


def build_passages(sentences: Sequence[tuple], settings: IntegritySettings = DEFAULT) -> List[Passage]:
    """sentences: (index, location, text) of the student's own sentences, in document order.

    A passage ends at a gap (a copied sentence was removed), at a new paragraph/page once
    it is long enough, or when it reaches the maximum length. A short leftover joins the
    passage before it when they are next to each other."""
    groups: List[List[tuple]] = []
    for item in sentences:
        idx, loc, _ = item
        if not groups:
            groups.append([item])
            continue
        last = groups[-1]
        words = sum(_words(t) for _, _, t in last)
        gap = idx != last[-1][0] + 1                          # a copied sentence sat in between
        full = words >= settings.ai_passage_max_words
        new_para = loc != last[-1][1] and words >= settings.ai_passage_min_words
        if gap or full or new_para:
            groups.append([item])
        else:
            last.append(item)
    # a short last group joins its neighbour
    merged: List[List[tuple]] = []
    for g in groups:
        if (merged and sum(_words(t) for _, _, t in g) < settings.ai_passage_min_words
                and g[0][0] == merged[-1][-1][0] + 1):
            merged[-1].extend(g)
        else:
            merged.append(g)
    out = []
    for g in merged:
        locs = [loc for _, loc, _ in g]
        location = locs[0] if locs[0] == locs[-1] else f"{locs[0]} to {locs[-1]}"
        text = " ".join(t for _, _, t in g)
        out.append(Passage(location=location, text=text, words=_words(text)))
    return out


def stock_phrases(text: str) -> List[str]:
    return sorted({re.sub(r"\s+", " ", m.group(0).lower()) for rx in _AI_RES for m in rx.finditer(text)})


# --------------------------------------------------------------------------- #
# The estimate
# --------------------------------------------------------------------------- #
def _band(pct: float) -> str:
    if pct < 10:
        return "Little or no AI-like writing"
    if pct < 40:
        return "Some AI-like writing"
    return "A large share of AI-like writing"


def estimate(sentences: Sequence[tuple], scorer: Optional[PassageScorer] = None,
             settings: IntegritySettings = DEFAULT) -> tuple:
    """Returns (ai_detection dict, passages). sentences: (index, location, text) of own text."""
    passages = build_passages(sentences, settings)
    threshold = ai_models(settings)[3]
    if scorer is None:
        model = Binoculars(settings)
        if not model.available:
            return ({"status": "NOT_AVAILABLE", "estimated_percent": None, "interpretation": None,
                     "confidence": None, "flagged_passages": [],
                     "limitations": [f"AI-percentage models not found: {', '.join(model.missing())}. "
                                     "Run: python scripts/setup_offline.py"]}, passages)
        scorer = model

    try:
        for p in passages:
            if p.words < settings.ai_passage_min_words:
                continue
            result = scorer(p.text)
            if result is None:
                continue
            p.score = round(result[0], 4)
            p.flagged = p.score <= threshold
            if p.flagged:
                p.stock_phrases = stock_phrases(p.text)
    except Exception as exc:  # noqa: BLE001 - a failed AI estimate must not stop the similarity check
        logger.exception("AI estimate failed")
        return ({"status": "FAILED", "estimated_percent": None, "interpretation": None, "confidence": None,
                 "flagged_passages": [], "limitations": [f"AI estimate failed: {type(exc).__name__}: {exc}"]},
                passages)

    scored = [p for p in passages if p.score is not None]
    scored_words = sum(p.words for p in scored)
    flagged_words = sum(p.words for p in scored if p.flagged)
    limits = list(LIMITATIONS)
    limits.append(f"Threshold {threshold} ({ai_models(settings)[0]} pair) is PROVISIONAL until checked on "
                  "real student text (scripts/check_ai_thresholds.py).")
    skipped = sum(p.words for p in passages if p.score is None)
    if skipped:
        limits.append(f"{skipped} words in short passages were not scored.")

    if scored_words < settings.min_words_for_ai_check:
        return ({"status": "INCONCLUSIVE", "estimated_percent": None, "interpretation": None,
                 "confidence": None, "flagged_passages": [],
                 "limitations": [f"Only {scored_words} words of the student's own writing could be scored; "
                                 f"at least {settings.min_words_for_ai_check} are needed."] + limits},
                passages)

    pct = round(100.0 * flagged_words / scored_words, 1)
    confidence = "low" if scored_words < 400 else "medium"   # never "high" while the threshold is provisional
    interpretation = (f"{_band(pct)}: {pct}% of the student's own words "
                      f"({flagged_words} of {scored_words} scored, in {sum(p.flagged for p in scored)} of "
                      f"{len(scored)} passages) read as machine-generated. Advisory, not proof.")
    flagged = [{"location": p.location, "words": p.words, "score": p.score,
                "threshold": threshold, "stock_phrases": p.stock_phrases,
                "text": p.text[:600]}
               for p in sorted(scored, key=lambda p: p.score) if p.flagged][:settings.ai_max_passages_shown]
    return ({"status": "COMPLETED", "estimated_percent": pct, "interpretation": interpretation,
             "confidence": confidence, "flagged_passages": flagged, "limitations": limits}, passages)


def to_signal(detection: dict, location_prefix: str, settings: IntegritySettings = DEFAULT) -> List[Signal]:
    """One finding for the Rubric Agent when the AI share reaches `ai_report_percent`."""
    if detection.get("status") != "COMPLETED" or detection["estimated_percent"] < settings.ai_report_percent:
        return []
    evidence = [Evidence(source="report",
                         location=(f"{location_prefix} {p['location']}, Binoculars score {p['score']:.3f} "
                                   f"(flagged at <= {p['threshold']})"
                                   + (f", stock phrases: {', '.join(p['stock_phrases'])}" if p["stock_phrases"] else "")),
                         quote=p["text"])
                for p in detection["flagged_passages"]]
    margin = max((p["threshold"] - p["score"]) for p in detection["flagged_passages"])
    return [Signal(kind="ai_generated_share", summary=detection["interpretation"], evidence=evidence,
                   scores={"share": detection["estimated_percent"] / 100, "margin": margin,
                           "low_confidence": float(detection["confidence"] == "low")},
                   limitations=" ".join(LIMITATIONS[:3]))]
