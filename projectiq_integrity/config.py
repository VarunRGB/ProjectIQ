"""Integrity Agent settings. Thresholds marked PROVISIONAL must be re-checked on the
dev set (planted controls) before results are reported; see scripts/validate.py."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.getenv("PROJECTIQ_MODELS_DIR", ROOT / "models"))


@dataclass
class IntegritySettings:
    # ---- Evidence shown per finding ------------------------------------------
    max_pairs_quoted: int = 5

    # ---- Document vs its references (document_check.py) -----------------------
    # Verbatim = same words (hyphens, dashes, quotes, capitals and up to 2 misspelt words
    # ignored); see document_check.copy_overlap. Near-verbatim = word overlap of at least
    # the analyzer's near_verbatim_jaccard (0.45).
    paraphrase_candidate_cosine: float = 0.60  # PROVISIONAL: MiniLM similarity worth sending to DeBERTa
    paraphrase_entailment: float = 0.50        # PROVISIONAL: DeBERTa entailment needed in BOTH directions
    chunk_min_sentences: int = 3               # a copied chunk is at least this many sentences
    chunk_max_gap: int = 1                     # unmatched sentences allowed inside a chunk
    chunk_ref_window: int = 6                  # how far apart, in the reference, consecutive matches may be
    reference_report_percent: float = 5.0      # PROVISIONAL: report a reference from this share of words

    # ---- AI-style text --------------------------------------------------------
    min_words_for_ai_check: int = 150
    style_support: float = 0.35            # same value as the judge's AI_SIGNAL_SUPPORT
    poor_writing_veto: float = 0.50        # same value as the judge's POOR_SIGNAL_SLOPPY
    # PROVISIONAL, for distilgpt2: machine text tends to be both very predictable
    # (low perplexity) and very even (low burstiness).
    perplexity_ai_max: float = 35.0
    burstiness_ai_max: float = 0.45
    perplexity_model: str = "distilgpt2"   # folder name in models/
    max_sentences_scored: int = 120

    # ---- AI percentage (ai_detection.py) ---------------------------------------
    # Used instead of the distilgpt2 check above when the chosen detector's model is in models/.
    # Detector: "classifier" (Desklib, one trained DeBERTa-v3-large model, about 2.5 GB RAM)
    # or "binoculars" (two Qwen models compared). Set PROJECTIQ_AI_DETECTOR to choose.
    ai_detector: str = field(default_factory=lambda: os.getenv("PROJECTIQ_AI_DETECTOR", "classifier"))
    classifier_model: str = "ai-text-detector-v1.01"   # folder name in models/
    # A passage with an AI probability at or above this reads as AI. None = CLASSIFIER_THRESHOLD.
    classifier_ai_min: Optional[float] = None
    classifier_max_tokens: int = 768                    # the model's own limit
    # Binoculars size: set PROJECTIQ_AI_MODEL_SIZE=0.5B, 1.5B or 3B (see AI_MODEL_PAIRS below).
    ai_model_size: str = field(default_factory=lambda: os.getenv("PROJECTIQ_AI_MODEL_SIZE", "0.5B"))
    ai_observer_model: str = ""        # folder names in models/; empty = from ai_model_size
    ai_performer_model: str = ""
    ai_model_dtype: str = ""           # empty = from ai_model_size
    # A passage scoring at or below this reads as AI. None = the value for ai_model_size in
    # AI_THRESHOLDS. All of them are PROVISIONAL until checked on real student text with
    # scripts/check_ai_thresholds.py.
    binoculars_ai_max: Optional[float] = None
    ai_passage_min_words: int = 100   # shorter passages are not scored (longer = steadier score)
    ai_passage_max_words: int = 300
    ai_max_tokens: int = 512
    ai_report_percent: float = 20.0   # raise a finding for the Rubric Agent from this AI share
    # CPU cores the AI models may use. Default: half of them, so the laptop stays usable.
    ai_threads: int = field(default_factory=lambda: int(os.getenv(
        "PROJECTIQ_AI_THREADS", max(1, (os.cpu_count() or 2) // 2))))
    ai_max_passages_shown: int = 5


# Binoculars model pairs: (observer = base model, performer = its Instruct version, dtype).
# bfloat16 halves memory; 0.5B is small enough for float32.
#   0.5B: about 2 GB on disk, 4 GB RAM.  1.5B: about 6 GB, 7 GB RAM.  3B: about 12 GB, 13 GB RAM.
AI_MODEL_PAIRS = {
    "0.5B": ("Qwen2.5-0.5B", "Qwen2.5-0.5B-Instruct", "float32"),
    "1.5B": ("Qwen2.5-1.5B", "Qwen2.5-1.5B-Instruct", "bfloat16"),
    "3B": ("Qwen2.5-3B", "Qwen2.5-3B-Instruct", "bfloat16"),
}
# PROVISIONAL: 0.90 is the original paper's value for 7B models. Replace each with the
# value scripts/check_ai_thresholds.py suggests on real student text for that size.
AI_THRESHOLDS = {"0.5B": 0.90, "1.5B": 0.90, "3B": 0.90}


# PROVISIONAL: set on 25 Nov 2026 from 30 calibration files (15 AI, 15 human) with
# scripts/check_ai_thresholds.py --detector classifier: at 0.91 no human passage was flagged
# and 5 of 15 AI passages were caught (at the model card's 0.5: 9 of 15, but 2 human flagged).
# Recheck with more human text before relying on it.
CLASSIFIER_THRESHOLD = 0.91


def ai_detector_info(settings: "IntegritySettings") -> dict:
    """Which AI detector is in use, its model folders, threshold and which way it reads."""
    if settings.ai_detector == "classifier":
        threshold = settings.classifier_ai_min if settings.classifier_ai_min is not None else CLASSIFIER_THRESHOLD
        return {"name": "classifier", "label": "Desklib AI-text classifier (DeBERTa-v3-large)",
                "folders": [settings.classifier_model], "threshold": threshold, "higher_is_ai": True,
                "score_name": "AI probability", "rule": ">="}
    observer, performer, _, threshold = ai_models(settings)
    return {"name": "binoculars", "label": f"Binoculars ({observer} pair)", "folders": [observer, performer],
            "threshold": threshold, "higher_is_ai": False, "score_name": "Binoculars score", "rule": "<="}


def ai_models(settings: "IntegritySettings") -> tuple:
    """(observer folder, performer folder, dtype, threshold) for the settings."""
    size = settings.ai_model_size if settings.ai_model_size in AI_MODEL_PAIRS else "0.5B"
    obs, perf, dtype = AI_MODEL_PAIRS[size]
    threshold = settings.binoculars_ai_max if settings.binoculars_ai_max is not None else AI_THRESHOLDS[size]
    return (settings.ai_observer_model or obs, settings.ai_performer_model or perf,
            settings.ai_model_dtype or dtype, threshold)


DEFAULT = IntegritySettings()
