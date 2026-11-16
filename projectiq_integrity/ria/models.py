"""Lazy, cached access to the transformer models.

* :class:`Embedder` wraps sentence-transformers (``all-MiniLM-L6-v2``) and caches
  every embedding on disk in ``data/cache/`` so repeated runs are fast.
* :class:`NLIModel` wraps a cross-encoder NLI model. The label order is read from
  the model config (``id2label``), never hardcoded.

If a model cannot be loaded (no internet on first run, blocked download, missing
package) and ``Settings.allow_model_fallback`` is True, a clearly labelled
fallback is used instead and a loud warning is logged:

* Embedder fallback  -> hashed word+character n-gram vectors. These are LEXICAL,
  not semantic, so "semantic" results in that mode are NOT meaningful.
* NLI fallback       -> the keyword/rule baseline from ``evidence_analyzer``.

Every result records which backend produced it (``backend`` attribute), and the
reports print it, so a fallback run can never be mistaken for a real one.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .config import DEFAULT_SETTINGS, Settings

logger = logging.getLogger(__name__)

CANONICAL_NLI_LABELS = ("entailment", "neutral", "contradiction")


class ModelUnavailableError(RuntimeError):
    """Raised when a model cannot be loaded and fallback is disabled."""


def _safe_name(model_name: str) -> str:
    return model_name.replace("/", "__").replace(":", "_")


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
class Embedder:
    """Sentence embedder with on-disk cache and a labelled lexical fallback."""

    def __init__(self, settings: Settings = DEFAULT_SETTINGS) -> None:
        self.settings = settings
        self.model_name = settings.embedding_model
        self._model: Any = None
        self._loaded = False
        self.backend = "not-loaded"
        self._lock = threading.Lock()
        self._cache: dict[str, np.ndarray] = {}
        self._cache_dirty = False
        self._cache_path = Path(settings.cache_dir) / f"embeddings_{_safe_name(self.model_name)}.pkl"
        self._load_cache()

    # -- cache ---------------------------------------------------------------
    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                with open(self._cache_path, "rb") as fh:
                    self._cache = pickle.load(fh)
                logger.debug("Loaded %d cached embeddings from %s", len(self._cache), self._cache_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding cache unreadable (%s); starting fresh.", exc)
                self._cache = {}

    def save_cache(self) -> None:
        """Persist new embeddings to ``data/cache/``."""
        if not self._cache_dirty or self.backend != "sentence-transformers":
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self._cache, fh)
        tmp.replace(self._cache_path)
        self._cache_dirty = False

    # -- loading -------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model '%s' (CPU)...", self.model_name)
                self._model = SentenceTransformer(self.model_name, device="cpu")
                self.backend = "sentence-transformers"
            except Exception as exc:  # noqa: BLE001
                if not self.settings.allow_model_fallback:
                    raise ModelUnavailableError(
                        f"Could not load embedding model '{self.model_name}': {exc}. "
                        "Run 'python scripts/download_models.py' once (online) to put it in "
                        "models/, or set RIA_ALLOW_FALLBACK=1 for a lexical-only run."
                    ) from exc
                logger.warning(
                    "EMBEDDING MODEL UNAVAILABLE (%s). Falling back to hashed n-gram vectors. "
                    "These are LEXICAL, so 'semantic' scores in this run are not meaningful.",
                    str(exc).splitlines()[0][:200],
                )
                self.backend = "fallback-hashing"
            self._loaded = True

    @property
    def is_semantic(self) -> bool:
        """True only when a real sentence-embedding model is in use."""
        self._ensure_loaded()
        return self.backend == "sentence-transformers"

    # -- encoding ------------------------------------------------------------
    def _key(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_encode(texts: list[str]) -> np.ndarray:
        from sklearn.feature_extraction.text import HashingVectorizer

        word = HashingVectorizer(n_features=2**14, ngram_range=(1, 2), alternate_sign=False, norm=None)
        char = HashingVectorizer(n_features=2**14, analyzer="char_wb", ngram_range=(3, 5),
                                 alternate_sign=False, norm=None)
        mat = np.hstack([word.transform(texts).toarray(), 0.5 * char.transform(texts).toarray()])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (mat / norms).astype(np.float32)

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalised embeddings, shape (len(texts), dim)."""
        self._ensure_loaded()
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        if self.backend != "sentence-transformers":
            return self._hash_encode(texts)
        keys = [self._key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        if missing:
            vecs = self._model.encode(
                [texts[i] for i in missing],
                batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            for i, v in zip(missing, vecs):
                self._cache[keys[i]] = v.astype(np.float32)
            self._cache_dirty = True
            self.save_cache()
        return np.vstack([self._cache[k] for k in keys])

    def similarity(self, a: list[str], b: list[str]) -> np.ndarray:
        """Cosine similarity matrix between two lists of texts."""
        if not a or not b:
            return np.zeros((len(a), len(b)), dtype=np.float32)
        return self.encode(a) @ self.encode(b).T


# --------------------------------------------------------------------------- #
# NLI
# --------------------------------------------------------------------------- #
def _canonical(label: str) -> str:
    low = label.lower()
    if low.startswith("entail") or low == "support":
        return "entailment"
    if low.startswith("contra") or low == "refute":
        return "contradiction"
    if low.startswith("neutral") or low.startswith("not"):
        return "neutral"
    raise ValueError(f"Unrecognised NLI label '{label}' in model config.")


class NLIModel:
    """Cross-encoder NLI: premise = evidence passage, hypothesis = claim."""

    def __init__(self, settings: Settings = DEFAULT_SETTINGS) -> None:
        self.settings = settings
        self.model_name = settings.nli_model
        self._tok: Any = None
        self._model: Any = None
        self._index_to_label: dict[int, str] = {}
        self._loaded = False
        self.backend = "not-loaded"
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                import torch  # noqa: F401
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                logger.info("Loading NLI model '%s' (CPU)...", self.model_name)
                self._tok = AutoTokenizer.from_pretrained(self.model_name)
                self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
                self._model.eval()
                id2label = self._model.config.id2label
                self._index_to_label = {int(i): _canonical(str(l)) for i, l in id2label.items()}
                if set(self._index_to_label.values()) != set(CANONICAL_NLI_LABELS):
                    raise ValueError(f"Model labels {id2label} do not cover entailment/neutral/contradiction.")
                logger.info("NLI label order from model config: %s", self._index_to_label)
                self.backend = "transformers-nli"
            except Exception as exc:  # noqa: BLE001
                if not self.settings.allow_model_fallback:
                    raise ModelUnavailableError(
                        f"Could not load NLI model '{self.model_name}': {exc}. "
                        "Run 'python scripts/download_models.py' once (online)."
                    ) from exc
                logger.warning(
                    "NLI MODEL UNAVAILABLE (%s). Falling back to the keyword/rule baseline; "
                    "'NLI' labels in this run are rule predictions.",
                    str(exc).splitlines()[0][:200],
                )
                self.backend = "fallback-rules"
            self._loaded = True

    @property
    def is_neural(self) -> bool:
        """True only when a real NLI model is in use."""
        self._ensure_loaded()
        return self.backend == "transformers-nli"

    def predict(self, premises: list[str], hypotheses: list[str]) -> list[dict[str, float]]:
        """Return one {entailment, neutral, contradiction} probability dict per pair."""
        if len(premises) != len(hypotheses):
            raise ValueError("premises and hypotheses must have the same length")
        self._ensure_loaded()
        if not premises:
            return []
        if self.backend != "transformers-nli":
            from .evidence_analyzer import rule_nli_probs

            return [rule_nli_probs(p, h) for p, h in zip(premises, hypotheses)]
        import torch

        out: list[dict[str, float]] = []
        bs = self.settings.nli_batch_size
        with torch.no_grad():
            for i in range(0, len(premises), bs):
                enc = self._tok(premises[i : i + bs], hypotheses[i : i + bs], padding=True,
                                truncation=True, max_length=512, return_tensors="pt")
                probs = torch.softmax(self._model(**enc).logits, dim=-1).cpu().numpy()
                for row in probs:
                    out.append({self._index_to_label[j]: float(row[j]) for j in range(len(row))})
        return out


# --------------------------------------------------------------------------- #
# Process-wide singletons
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _embedder_for(model_name: str, cache_dir: str, allow_fallback: bool) -> Embedder:
    s = DEFAULT_SETTINGS.with_overrides(embedding_model=model_name, cache_dir=Path(cache_dir),
                                        allow_model_fallback=allow_fallback)
    return Embedder(s)


@lru_cache(maxsize=4)
def _nli_for(model_name: str, allow_fallback: bool) -> NLIModel:
    return NLIModel(DEFAULT_SETTINGS.with_overrides(nli_model=model_name, allow_model_fallback=allow_fallback))


def get_embedder(settings: Settings = DEFAULT_SETTINGS) -> Embedder:
    """Shared, lazily loaded embedder for these settings."""
    return _embedder_for(settings.embedding_model, str(settings.cache_dir), settings.allow_model_fallback)


def get_nli(settings: Settings = DEFAULT_SETTINGS) -> NLIModel:
    """Shared, lazily loaded NLI model for these settings."""
    return _nli_for(settings.nli_model, settings.allow_model_fallback)
