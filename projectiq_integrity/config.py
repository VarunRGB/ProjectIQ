"""Integrity Agent settings. Thresholds marked PROVISIONAL must be re-checked on the
dev set (planted controls) before results are reported; see scripts/validate.py."""
import os
from dataclasses import dataclass
from pathlib import Path

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


DEFAULT = IntegritySettings()
