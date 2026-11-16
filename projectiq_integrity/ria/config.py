"""Central configuration for the Research Document Integrity project.

Every tunable setting (model names, thresholds, top-k, paths) lives here so the
rest of the code never hardcodes them. Values can be overridden at runtime by
creating a new :class:`Settings` object (e.g. from the CLI or Streamlit UI) and
passing it to the pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Vendored inside ProjectIQ at projectiq_integrity/ria/: the project root is three levels up,
# so ProjectIQ and this analyzer share one models/ folder.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = PROJECT_ROOT / "data"
PAPERS_DIR: Path = DATA_DIR / "papers"
REFERENCES_DIR: Path = DATA_DIR / "references"
CACHE_DIR: Path = DATA_DIR / "cache"
EVAL_DIR: Path = DATA_DIR / "eval"
OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"
EVAL_OUTPUTS_DIR: Path = OUTPUTS_DIR / "eval"

# --------------------------------------------------------------------------- #
# Offline models
# --------------------------------------------------------------------------- #
# Every model lives in models/ inside the project, put there once by
# scripts/download_models.py. With RIA_OFFLINE=1 (the default) nothing is ever
# downloaded at run time: a missing model is an error, not a silent download.
MODELS_DIR: Path = Path(os.getenv("RIA_MODELS_DIR", PROJECT_ROOT / "models"))
OFFLINE: bool = os.getenv("RIA_OFFLINE", "1") == "1"
if OFFLINE:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

# Folder name in models/  ->  where download_models.py fetches it from (once).
MODEL_SOURCES: dict[str, str] = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "nli-deberta-v3-small": "cross-encoder/nli-deberta-v3-small",
    "en_core_web_sm": "en_core_web_sm",
}
NLTK_DATA_DIR: Path = MODELS_DIR / "nltk_data"


def local_model(folder: str) -> str:
    """Path of a model stored in models/, or its download name if it is not there yet.

    Returning the name (not failing) keeps a first, online setup working; with
    RIA_OFFLINE=1 the libraries then refuse to download and raise a clear error.
    """
    path = MODELS_DIR / folder
    return str(path) if path.exists() else MODEL_SOURCES[folder]

DISCLAIMER: str = (
    "DISCLAIMER: This report is produced by automated NLP heuristics and machine-learning "
    "models. It does NOT detect fraud and does NOT prove plagiarism or misconduct. Every "
    "item is a flag for human review only. Similarity scores and NLI labels are model "
    "predictions, not ground truth, and may be wrong. Uncited sentences may be common "
    "knowledge, and missed citations may be parser limitations."
)


@dataclass(frozen=True)
class Settings:
    """All runtime settings. Immutable; use :meth:`with_overrides` to change values."""

    # ---- Models --------------------------------------------------------------
    embedding_model: str = field(default_factory=lambda: local_model("all-MiniLM-L6-v2"))
    nli_model: str = field(default_factory=lambda: local_model("nli-deberta-v3-small"))
    spacy_model: str = field(default_factory=lambda: local_model("en_core_web_sm"))
    # If a transformer model cannot be loaded, the system can fall back to lexical
    # methods. Off by default: a silent fallback would make "semantic" results lexical
    # without anyone noticing. Set RIA_ALLOW_FALLBACK=1 only for quick experiments.
    allow_model_fallback: bool = os.getenv("RIA_ALLOW_FALLBACK", "0") == "1"
    use_embeddings: bool = True
    use_nli: bool = True
    embedding_batch_size: int = 32
    nli_batch_size: int = 16

    # ---- Retrieval -----------------------------------------------------------
    top_k: int = 3
    chunk_sentences: int = 3          # sentences per reference passage
    chunk_overlap: int = 1            # sentences shared between neighbouring passages

    # ---- Evidence / NLI ------------------------------------------------------
    nli_support_threshold: float = 0.55
    nli_contradict_threshold: float = 0.55
    min_retrieval_score: float = 0.25  # below this, evidence is "insufficient"
    # Relevance gate: a passage can only SUPPORT or CONTRADICT a claim if its meaning is
    # at least this similar to the claim (embedding cosine). Stops NLI from "contradicting"
    # a claim with an unrelated passage that merely has different numbers.
    min_evidence_similarity: float = 0.50          # claim cites the paper being searched
    min_evidence_similarity_uncited: float = 0.60  # claim has no citation, all papers searched
    min_evidence_tfidf: float = 0.25               # gate used when embeddings are switched off

    # ---- Overlap -------------------------------------------------------------
    ngram_n: int = 3
    near_verbatim_jaccard: float = 0.45
    lexical_low_jaccard: float = 0.20
    paraphrase_semantic: float = 0.75
    topical_semantic: float = 0.50
    min_sentence_tokens: int = 6

    # ---- Claims --------------------------------------------------------------
    use_ml_claim_ranking: bool = True
    min_claim_confidence: float = 0.40

    # ---- Numbers -------------------------------------------------------------
    numeric_tolerance: float = 0.5     # absolute tolerance for percentages/metrics
    numeric_rel_tolerance: float = 0.01

    # ---- Paths ---------------------------------------------------------------
    papers_dir: Path = PAPERS_DIR
    references_dir: Path = REFERENCES_DIR
    cache_dir: Path = CACHE_DIR
    eval_dir: Path = EVAL_DIR
    outputs_dir: Path = OUTPUTS_DIR
    eval_outputs_dir: Path = EVAL_OUTPUTS_DIR

    # ---- Misc ----------------------------------------------------------------
    log_level: str = "INFO"
    extra: dict = field(default_factory=dict)

    def with_overrides(self, **kwargs: object) -> "Settings":
        """Return a copy of these settings with the given fields replaced."""
        return replace(self, **kwargs)


DEFAULT_SETTINGS = Settings()

SECTION_NAMES: tuple[str, ...] = (
    "Abstract",
    "Introduction",
    "Related Work",
    "Methods",
    "Results",
    "Discussion",
    "Conclusion",
    "References",
)

# Heading aliases -> canonical section name.
SECTION_ALIASES: dict[str, str] = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "background": "Related Work",
    "related work": "Related Work",
    "literature review": "Related Work",
    "prior work": "Related Work",
    "method": "Methods",
    "methods": "Methods",
    "methodology": "Methods",
    "materials and methods": "Methods",
    "approach": "Methods",
    "experimental setup": "Methods",
    "experiments": "Results",
    "results": "Results",
    "results and discussion": "Results",
    "evaluation": "Results",
    "findings": "Results",
    "discussion": "Discussion",
    "limitations": "Discussion",
    "conclusion": "Conclusion",
    "conclusions": "Conclusion",
    "conclusion and future work": "Conclusion",
    "concluding remarks": "Conclusion",
    "references": "References",
    "bibliography": "References",
    "works cited": "References",
}


def setup_logging(level: str = DEFAULT_SETTINGS.log_level) -> None:
    """Configure root logging once with a readable format."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet very chatty third-party loggers.
    for noisy in ("transformers", "sentence_transformers", "urllib3", "httpx", "filelock", "fontTools", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def ensure_dirs(settings: Settings = DEFAULT_SETTINGS) -> None:
    """Create the data and output directories if they do not exist."""
    for d in (
        settings.papers_dir,
        settings.references_dir,
        settings.cache_dir,
        settings.eval_dir,
        settings.outputs_dir,
        settings.eval_outputs_dir,
    ):
        Path(d).mkdir(parents=True, exist_ok=True)
