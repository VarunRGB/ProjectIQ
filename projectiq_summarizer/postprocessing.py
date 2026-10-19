"""Coverage-aware, extractive compression for ProjectIQ summaries.

This module shortens FinalSummary.summary without making another LLM call.
It uses MiniLM embeddings for sentence diversity and gives extra priority to
sentences that preserve distinct facts represented by the reduce-stage
key_points. It also removes duplicate key points after compression.

The compression limits are best-effort: complete sentences are preserved, so
an individual sentence that is longer than the remaining word budget is skipped
rather than truncated.
"""

from functools import lru_cache
import re
import time
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np

from .schemas import FinalSummary

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MMR_LAMBDA = 0.72


@lru_cache(maxsize=1)
def _get_embedding_model():
    """Load and cache MiniLM so it is not reloaded for every summary."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "MiniLM post-processing requires sentence-transformers. "
            "Install it in your project venv with: pip install sentence-transformers"
        ) from exc
    return SentenceTransformer(MODEL_NAME)

def _normalise_spacing(text: str) -> str:
    """Collapse whitespace and repair common missing spaces after punctuation."""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"([.!?])(?=[A-Z\"'(])", r"\1 ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> List[str]:
    """Split generated prose into sentence-like units; not a full parser."""
    text = _normalise_spacing(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    return [part.strip() for part in parts if part.strip()]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _normalise_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


_STOP_WORDS = set("""
    a an and are as at be been being by can could did do does for from had has
    have he her hers him his how i if in into is it its may might must not of
    on or our she should so than that the their them then there these they this
    those through to was we were what when where which while who will with would
    you your system application project report includes include including
    described discusses states provides provide uses use used using
""".split())

def _content_tokens(text: str) -> Set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", text.casefold()))
    result: Set[str] = set()
    for token in tokens:
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
        if token not in _STOP_WORDS and (len(token) > 2 or token in {"ai", "ui", "db"}):
            result.add(token)
    return result


def _lexically_duplicate(left: str, right: str) -> bool:
    """Conservative overlap check, useful even when embeddings are not loaded."""
    left_tokens = _content_tokens(left)
    right_tokens = _content_tokens(right)
    if not left_tokens or not right_tokens:
        return _normalise_spacing(left).casefold() == _normalise_spacing(right).casefold()
    shared = len(left_tokens & right_tokens)
    if shared < 5:
        return _normalise_spacing(left).casefold() == _normalise_spacing(right).casefold()
    overlap = shared / min(len(left_tokens), len(right_tokens))
    jaccard = shared / len(left_tokens | right_tokens)
    return (overlap >= 0.80 and jaccard >= 0.45) or jaccard >= 0.62


def _clean_key_point(point: str) -> str:
    return re.sub(r"^\s*[-*•]\s*", "", _normalise_spacing(str(point))).strip()

def _select_mmr_sentences(
    sentences: Sequence[str],
    embeddings: np.ndarray,
    *,
    min_words: int,
    max_words: int,
    min_sentences: int,
    max_sentences: int,
    mmr_lambda: float,
    key_point_embeddings: Optional[np.ndarray] = None,
) -> List[int]:
    """Select sentence indices using MMR plus a key-point coverage bonus.

    Each key point acts as a lightweight coverage anchor. A sentence receives
    a bonus when it is one of the best semantic matches for a key point that
    has not yet been represented in the selected paragraph.
    """
    if not sentences:
        return []

    embeddings = _normalise_rows(np.asarray(embeddings, dtype=np.float32))
    paragraph_vector = _normalise_rows(embeddings.mean(axis=0, keepdims=True))[0]
    relevance = embeddings @ paragraph_vector

    # Ignore key points that have no reasonably related sentence in the source
    # summary. This avoids forcing an unrelated sentence into the paragraph.
    point_similarity = None
    point_cutoffs = None
    covered_points: Set[int] = set()
    if key_point_embeddings is not None and len(key_point_embeddings):
        point_vectors = _normalise_rows(np.asarray(key_point_embeddings, dtype=np.float32))
        point_similarity = embeddings @ point_vectors.T
        feasible = np.array([_word_count(s) <= max_words for s in sentences])
        if feasible.any():
            best_similarity = point_similarity[feasible].max(axis=0)
            valid_points = best_similarity >= 0.45
            point_cutoffs = np.maximum(0.35, best_similarity - 0.08)
            point_cutoffs[~valid_points] = np.inf

    selected: List[int] = []
    selected_set: Set[int] = set()
    selected_words = 0

    # Preserve the first sentence as the project/context opener when possible.
    first_words = _word_count(sentences[0])
    if first_words <= max_words:
        selected.append(0)
        selected_set.add(0)
        selected_words += first_words
        if point_similarity is not None and point_cutoffs is not None:
            covered_points.update(
                int(j) for j in np.where(point_similarity[0] >= point_cutoffs)[0]
                if np.isfinite(point_cutoffs[j])
            )

    if not selected:
        feasible_indices = [i for i, s in enumerate(sentences) if _word_count(s) <= max_words]
        if not feasible_indices:
            return [0]
        best = max(feasible_indices, key=lambda i: float(relevance[i]))
        selected = [best]
        selected_set = {best}
        selected_words = _word_count(sentences[best])
        if point_similarity is not None and point_cutoffs is not None:
            covered_points.update(
                int(j) for j in np.where(point_similarity[best] >= point_cutoffs)[0]
                if np.isfinite(point_cutoffs[j])
            )

    while len(selected) < max_sentences:
        enough = len(selected) >= min_sentences and selected_words >= min_words
        if enough and (point_cutoffs is None or len(covered_points) >= int(np.isfinite(point_cutoffs).sum())):
            break

        candidates = []
        selected_embeddings = embeddings[selected]

        for index, sentence in enumerate(sentences):
            if index in selected_set:
                continue
            words = _word_count(sentence)
            if words == 0 or selected_words + words > max_words:
                continue

            similarities = embeddings[index] @ selected_embeddings.T
            redundancy = float(np.max(similarities))
            score = mmr_lambda * float(relevance[index]) - (1.0 - mmr_lambda) * redundancy

            if point_similarity is not None and point_cutoffs is not None:
                new_points = [
                    int(j) for j in np.where(point_similarity[index] >= point_cutoffs)[0]
                    if np.isfinite(point_cutoffs[j]) and int(j) not in covered_points
                ]
                # Coverage can beat a sentence that only repeats the dominant topic.
                score += min(len(new_points), 3) * 0.14

            # Tiny tie-breaker: when scores are close, prefer concise sentences.
            score += 0.015 / (max(1, words) ** 0.5)
            candidates.append((score, -index, index, words))

        if not candidates:
            break

        _, _, chosen_index, chosen_words = max(candidates)
        selected.append(chosen_index)
        selected_set.add(chosen_index)
        selected_words += chosen_words

        if point_similarity is not None and point_cutoffs is not None:
            covered_points.update(
                int(j) for j in np.where(point_similarity[chosen_index] >= point_cutoffs)[0]
                if np.isfinite(point_cutoffs[j])
            )

    # Return source order for a coherent paragraph rather than selection order.
    return sorted(selected_set)

def shorten_summary(
    text: str,
    *,
    min_words: int = 100,
    max_words: int = 140,
    min_sentences: int = 5,
    max_sentences: int = 7,
    mmr_lambda: float = MMR_LAMBDA,
    key_points: Sequence[str] = (),
) -> str:
    """Compress with MiniLM, using key points as semantic coverage anchors."""
    normalised = _normalise_spacing(text)
    if not normalised:
        return normalised

    sentences = list(dict.fromkeys(_split_sentences(normalised)))
    if not sentences:
        return normalised

    original_words = _word_count(normalised)
    if original_words <= max_words and len(sentences) <= max_sentences:
        return " ".join(sentences)

    if not 0.0 <= mmr_lambda <= 1.0:
        raise ValueError("mmr_lambda must be between 0.0 and 1.0")

    clean_points = [_clean_key_point(p) for p in key_points if _clean_key_point(p)]
    texts_to_encode = sentences + clean_points

    start = time.perf_counter()
    model = _get_embedding_model()
    all_embeddings = model.encode(
        texts_to_encode,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    embedding_elapsed = time.perf_counter() - start

    sentence_embeddings = all_embeddings[:len(sentences)]
    point_embeddings = all_embeddings[len(sentences):] if clean_points else None
    selected_indices = _select_mmr_sentences(
        sentences,
        sentence_embeddings,
        min_words=min_words,
        max_words=max_words,
        min_sentences=min_sentences,
        max_sentences=max_sentences,
        mmr_lambda=mmr_lambda,
        key_point_embeddings=point_embeddings,
    )
    result = " ".join(sentences[i] for i in selected_indices).strip()

    print(
        f"[POSTPROCESS] MiniLM: {len(sentences)} sentences + {len(clean_points)} key points "
        f"embedded in {embedding_elapsed:.3f}s",
        flush=True,
    )
    print(
        f"[POSTPROCESS] Summary: {original_words} -> {_word_count(result)} words; "
        f"{len(sentences)} -> {len(selected_indices)} sentences",
        flush=True,
    )
    return result or normalised

def _deduplicate_key_points(
    key_points: Sequence[str],
    summary_text: str,
    *,
    semantic: bool,
) -> List[str]:
    """Remove key points that repeat the summary or an earlier key point.

    Semantic comparisons are only run when compression was needed, so short
    summaries retain the existing low-overhead path. The similarity thresholds
    are intentionally conservative to avoid discarding related but distinct facts.
    """
    points: List[str] = []
    for raw_point in key_points:
        point = _clean_key_point(raw_point)
        if not point or _word_count(point) < 3:
            continue
        if any(_lexically_duplicate(point, earlier) for earlier in points):
            continue
        points.append(point)

    summary_sentences = _split_sentences(summary_text)
    if not semantic or not points:
        return [
            point for point in points
            if not any(_lexically_duplicate(point, sentence) for sentence in summary_sentences)
        ]

    model = _get_embedding_model()
    all_texts = summary_sentences + points
    embeddings = _normalise_rows(model.encode(
        all_texts,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ))
    sentence_embeddings = embeddings[:len(summary_sentences)]
    point_embeddings = embeddings[len(summary_sentences):]

    kept_points: List[str] = []
    kept_vectors: List[np.ndarray] = []
    for index, point in enumerate(points):
        vector = point_embeddings[index]

        duplicate = any(_lexically_duplicate(point, sentence) for sentence in summary_sentences)
        if not duplicate and len(sentence_embeddings):
            sims = sentence_embeddings @ vector
            for sentence_index, similarity in enumerate(sims):
                if similarity >= 0.89 or (
                    similarity >= 0.81 and _lexically_duplicate(point, summary_sentences[sentence_index])
                ):
                    duplicate = True
                    break

        if not duplicate:
            for kept_point, kept_vector in zip(kept_points, kept_vectors):
                similarity = float(vector @ kept_vector)
                if _lexically_duplicate(point, kept_point) or similarity >= 0.89 or (
                    similarity >= 0.81 and _lexically_duplicate(point, kept_point)
                ):
                    duplicate = True
                    break

        if not duplicate:
            kept_points.append(point)
            kept_vectors.append(vector)

    return kept_points


def postprocess_summary(summary: FinalSummary) -> FinalSummary:
    """Compress the paragraph, retain distinct points, and return FinalSummary."""
    original_text = _normalise_spacing(summary.summary)
    original_sentences = _split_sentences(original_text)
    needs_compression = (
        _word_count(original_text) > 140 or len(original_sentences) > 7
    )

    shortened = shorten_summary(
        summary.summary,
        key_points=list(summary.key_points or []),
    )
    cleaned_points = _deduplicate_key_points(
        list(summary.key_points or []),
        shortened,
        semantic=needs_compression,
    )
    if len(cleaned_points) != len(summary.key_points or []):
        print(
            f"[POSTPROCESS] Key points: {len(summary.key_points or [])} -> {len(cleaned_points)} "
            "after duplicate removal",
            flush=True,
        )

    return FinalSummary(
        summary=shortened,
        key_points=cleaned_points,
    )