"""Integrity Agent settings. Thresholds marked PROVISIONAL must be re-checked on the
dev set (planted controls) before results are reported; see scripts/validate.py."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.getenv("PROJECTIQ_MODELS_DIR", ROOT / "models"))
TOOLS_DIR = Path(os.getenv("PROJECTIQ_TOOLS_DIR", ROOT / "tools"))

# automate-jplag downloaded exactly this release; JPlag 6.1.0 needs Java 21 or newer.
JPLAG_VERSION = "6.1.0"
JPLAG_JAR = Path(os.getenv("PROJECTIQ_JPLAG_JAR", TOOLS_DIR / f"jplag-{JPLAG_VERSION}.jar"))
JPLAG_URL = (f"https://github.com/jplag/JPlag/releases/download/v{JPLAG_VERSION}/"
             f"jplag-{JPLAG_VERSION}-jar-with-dependencies.jar")
REQUIRED_JAVA = 21


@dataclass
class IntegritySettings:
    # ---- Code similarity (JPlag) ---------------------------------------------
    jplag_language: str = "python3"
    # JPlag's own default (None) is 12 tokens for Python. automate-jplag used 5,
    # which matches tiny fragments and floods the report with false positives.
    jplag_min_tokens: int = 0              # 0 = JPlag default
    code_similarity_threshold: float = 0.50   # PROVISIONAL: report pairs at or above this
    max_fragments_per_pair: int = 3
    max_quote_lines: int = 8

    # ---- Report similarity (overlap_analyzer) --------------------------------
    # A sentence found in at least this share of cohort reports is template text
    # (certificate, declaration, acknowledgement) and is ignored.
    template_share: float = 0.30
    template_min_reports: int = 3
    min_flagged_sentences: int = 2         # PROVISIONAL: per matched source document
    max_pairs_quoted: int = 5

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
