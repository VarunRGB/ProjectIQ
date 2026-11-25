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

    # Secondary NLI retrieval: advisory only.
    # Additional candidates do not contribute to confirmed overlap.
    secondary_candidate_cosine: float = 0.45
    max_secondary_candidates: int = 60

    # ---- AI percentage (ai_detection.py) ---------------------------------------
    # Desklib AI-text classifier (DeBERTa-v3-large), run locally from models/.
    classifier_model: str = "ai-text-detector-v1.01"   # folder name in models/
    # A passage with an AI probability at or above this reads as AI. None = CLASSIFIER_THRESHOLD.
    classifier_ai_min: Optional[float] = None
    classifier_max_tokens: int = 768                    # the model's own limit
    min_words_for_ai_check: int = 150  # fewer scored words than this: INCONCLUSIVE
    ai_passage_min_words: int = 100    # shorter passages are not scored (longer = steadier score)
    ai_passage_max_words: int = 300
    ai_report_percent: float = 20.0   # raise a finding for the Rubric Agent from this AI share
    # CPU cores the AI models may use. Default: half of them, so the laptop stays usable.
    ai_threads: int = field(default_factory=lambda: int(os.getenv(
        "PROJECTIQ_AI_THREADS", max(1, (os.cpu_count() or 2) // 2))))
    ai_max_passages_shown: int = 5


# PROVISIONAL: set on 25 Nov 2026 from 30 calibration files (15 AI, 15 human) with
# scripts/check_ai_thresholds.py: at 0.91 no human passage was flagged
# and 5 of 15 AI passages were caught (at the model card's 0.5: 9 of 15, but 2 human flagged).
# Recheck with more human text before relying on it.
CLASSIFIER_THRESHOLD = 0.91


def ai_detector_info(settings: "IntegritySettings") -> dict:
    """The AI detector's label, model folder and threshold (higher probability = more AI-like)."""
    threshold = settings.classifier_ai_min if settings.classifier_ai_min is not None else CLASSIFIER_THRESHOLD
    return {"name": "classifier", "label": "Desklib AI-text classifier (DeBERTa-v3-large)",
            "folders": [settings.classifier_model], "threshold": threshold,
            "score_name": "AI probability", "rule": ">="}


DEFAULT = IntegritySettings()
