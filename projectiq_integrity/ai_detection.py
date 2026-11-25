"""AI-generated text: how much of the student's OWN writing reads as machine-written.

Detector: Desklib's AI-text classifier (desklib/ai-text-detector-v1.01, MIT licence), a
DeBERTa-v3-large model fine-tuned on the RAID benchmark (human vs AI text from 11
generators, 8 domains and 11 attacks such as paraphrasing). It runs locally from
models/ai-text-detector-v1.01 (1.75 GB on disk, about 2.5 GB RAM) and gives the
probability that a passage is AI-written: HIGH = machine-like.

How the percentage is built:
1. Copied sentences are removed first (they are someone else's writing).
2. The remaining sentences are grouped into passages of about 100 to 300 words,
   following the document's paragraphs (or pages, for PDFs).
3. Each passage gets an AI probability; at or above CLASSIFIER_THRESHOLD it is flagged.
4. AI % = words in flagged passages / words in scored passages.

Output matches the orchestrator's `ai_detection` contract (projectiq_core AIAnalysis):
status, estimated_percent, interpretation, confidence, flagged_passages, limitations.

Stock AI phrases (projectiq_summarizer.signals, read only) are shown per flagged
passage as the "why". They never decide on their own.

If the model is not in models/, status is NOT_AVAILABLE: nothing is guessed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from projectiq_summarizer.signals import _AI_RES

from .config import DEFAULT, MODELS_DIR, IntegritySettings, ai_detector_info
from .contracts import Evidence
from .evidence import Signal

logger = logging.getLogger(__name__)

LIMITATIONS = [
    "Advisory only: an AI estimate is a reason to talk with the student, never proof or grounds for a penalty.",
    "Copied sentences are excluded; the estimate covers the student's own writing only.",
    "Formal, formulaic or non-native writing can read as machine-like, and AI text edited by hand can read as human.",
    "Passages under the minimum length are not scored.",
]

# passage -> (AI probability, tokens scored); None when the passage is too short to score
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
# The local model
# --------------------------------------------------------------------------- #
_LOADED: dict = {}   # model path -> (tokenizer, model): loaded once per run


_DESKLIB_CLASS = None


def desklib_model_class():
    """The model class from the Desklib model card (MIT licence): DeBERTa-v3-large,
    mean pooling over tokens, one linear layer, sigmoid = probability of AI.
    Built on first use so importing this module does not import torch."""
    global _DESKLIB_CLASS
    if _DESKLIB_CLASS is None:
        import torch
        import torch.nn as nn
        from transformers import AutoConfig, AutoModel, PreTrainedModel

        class DesklibAIDetectionModel(PreTrainedModel):
            config_class = AutoConfig

            def __init__(self, config):
                super().__init__(config)
                self.model = AutoModel.from_config(config)
                self.classifier = nn.Linear(config.hidden_size, 1)
                self.post_init()

            def forward(self, input_ids, attention_mask=None):
                hidden = self.model(input_ids, attention_mask=attention_mask)[0]
                mask = attention_mask.unsqueeze(-1).expand(hidden.size()).float()
                pooled = torch.sum(hidden * mask, dim=1) / torch.clamp(mask.sum(dim=1), min=1e-9)
                return {"logits": self.classifier(pooled)}

        _DESKLIB_CLASS = DesklibAIDetectionModel
    return _DESKLIB_CLASS


class Classifier:
    """Desklib's AI-text detector from models/, CPU only, loaded once per run. Never downloads."""

    def __init__(self, settings: IntegritySettings = DEFAULT, models_dir: Path = MODELS_DIR):
        self.settings = settings
        self.path = Path(models_dir) / settings.classifier_model
        self.available = self.path.exists()
        self._tok = self._model = None

    def missing(self) -> List[str]:
        return [] if self.path.exists() else [str(self.path)]

    def _load(self) -> None:
        if self._model is not None:
            return
        key = ("classifier", str(self.path))
        if key in _LOADED:
            self._tok, self._model = _LOADED[key]
            return
        import torch
        from transformers import AutoTokenizer

        torch.set_num_threads(self.settings.ai_threads)
        self._tok = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
        self._model = desklib_model_class().from_pretrained(self.path, local_files_only=True).eval()
        _LOADED.clear()                                     # keep one detector in memory
        _LOADED[key] = (self._tok, self._model)

    def __call__(self, text: str) -> Optional[tuple]:
        import torch

        self._load()
        enc = self._tok(text, truncation=True, max_length=self.settings.classifier_max_tokens,
                        return_tensors="pt")
        if enc["input_ids"].shape[1] < 16:
            return None
        with torch.no_grad():
            logit = self._model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"])["logits"]
        return float(torch.sigmoid(logit).item()), int(enc["input_ids"].shape[1])


def make_detector(settings: IntegritySettings = DEFAULT) -> Classifier:
    return Classifier(settings)


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
    info = ai_detector_info(settings)
    threshold = info["threshold"]
    if scorer is None:
        model = make_detector(settings)
        if not model.available:
            return ({"status": "NOT_AVAILABLE", "estimated_percent": None, "interpretation": None,
                     "confidence": None, "flagged_passages": [],
                     "limitations": [f"AI-percentage model not found: {', '.join(model.missing())}. "
                                     "Run: python scripts/setup_offline.py"]},
                    passages)
        scorer = model

    try:
        for p in passages:
            if p.words < settings.ai_passage_min_words:
                continue
            result = scorer(p.text)
            if result is None:
                continue
            p.score = round(result[0], 4)
            p.flagged = p.score >= threshold
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
    limits.append(f"Detector: {info['label']}. Threshold {info['score_name']} {info['rule']} {threshold} is "
                  "PROVISIONAL until checked on real student text (scripts/check_ai_thresholds.py).")
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
                "threshold": threshold, "detector": info["name"], "score_name": info["score_name"],
                "rule": info["rule"], "stock_phrases": p.stock_phrases,
                "text": p.text[:600]}
               for p in sorted(scored, key=lambda p: p.score, reverse=True)
               if p.flagged][:settings.ai_max_passages_shown]
    return ({"status": "COMPLETED", "estimated_percent": pct, "interpretation": interpretation,
             "confidence": confidence, "flagged_passages": flagged, "limitations": limits}, passages)


def to_signal(detection: dict, location_prefix: str, settings: IntegritySettings = DEFAULT) -> List[Signal]:
    """One finding for the Rubric Agent when the AI share reaches `ai_report_percent`."""
    if detection.get("status") != "COMPLETED" or detection["estimated_percent"] < settings.ai_report_percent:
        return []
    evidence = [Evidence(source="report",
                         location=(f"{location_prefix} {p['location']}, AI probability "
                                   f"{p['score']:.3f} (flagged at >= {p['threshold']})"
                                   + (f", stock phrases: {', '.join(p['stock_phrases'])}" if p["stock_phrases"] else "")),
                         quote=p["text"])
                for p in detection["flagged_passages"]]
    margin = max(p["score"] - p["threshold"] for p in detection["flagged_passages"])
    return [Signal(kind="ai_generated_share", summary=detection["interpretation"], evidence=evidence,
                   scores={"share": detection["estimated_percent"] / 100, "margin": margin,
                           "low_confidence": float(detection["confidence"] == "low")},
                   limitations=" ".join(LIMITATIONS[:3]))]
