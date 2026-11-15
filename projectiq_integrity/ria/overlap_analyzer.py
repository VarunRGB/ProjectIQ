"""Textual overlap analysis: lexical versus semantic similarity.

For every paper sentence (outside the reference list, with quoted text and
citation markers removed) we find the closest reference sentence and compute:
  * word 3-gram Jaccard (lexical, order-aware),
  * TF-IDF cosine (lexical, bag-of-words),
  * embedding cosine (semantic).

Categories:
  * near-verbatim            - high n-gram overlap
  * possible paraphrase      - high semantic similarity, low-to-moderate n-gram overlap
  * topical similarity only  - same topic, different statement
  * no notable overlap

Output is "textual overlap flags for review". It is never a plagiarism verdict:
properly attributed paraphrase is normal academic practice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .citation_analyzer import strip_citations
from .config import DEFAULT_SETTINGS, Settings
from .evidence_analyzer import ReferenceCorpus
from .pdf_extractor import Document
from .preprocessing import looks_like_heading, tokenize, word_ngram_set

logger = logging.getLogger(__name__)

NEAR_VERBATIM = "near-verbatim"
PARAPHRASE = "possible paraphrase"
TOPICAL = "topical similarity only"
NONE = "no notable overlap"
FLAG_CATEGORIES = (NEAR_VERBATIM, PARAPHRASE)

_QUOTE_RE = re.compile(r"[\"“][^\"“”]{3,}?[\"”]")


def remove_quoted(text: str) -> tuple[str, bool]:
    """Remove text inside double quotes. Returns (remaining text, had_quote)."""
    stripped = _QUOTE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", stripped).strip(), stripped != text


def jaccard(a: set[Any], b: set[Any]) -> float:
    """Jaccard similarity of two sets (0 if both empty)."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def ngram_jaccard(a: str, b: str, n: int = 3) -> float:
    """Word n-gram Jaccard between two texts."""
    return jaccard(word_ngram_set(a, n), word_ngram_set(b, n))


def containment(a: str, b: str, n: int = 3) -> float:
    """Share of ``a``'s n-grams that also appear in ``b`` (robust to length differences)."""
    ga = word_ngram_set(a, n)
    return len(ga & word_ngram_set(b, n)) / len(ga) if ga else 0.0


def lexical_overlap(a: str, b: str, n: int = 3) -> float:
    """Lexical overlap score: max of n-gram Jaccard and 0.8 x two-way containment.

    Containment catches near-verbatim copying when one text adds or drops a few words.
    """
    return max(ngram_jaccard(a, b, n), 0.8 * max(containment(a, b, n), containment(b, a, n)))


def categorize(lexical: float, semantic: float, settings: Settings = DEFAULT_SETTINGS) -> str:
    """Map lexical (n-gram Jaccard/containment) and semantic (cosine) scores to a category."""
    if lexical >= settings.near_verbatim_jaccard:
        return NEAR_VERBATIM
    if semantic >= settings.paraphrase_semantic:
        return PARAPHRASE
    if semantic >= settings.topical_semantic:
        return TOPICAL
    return NONE


def lexical_only_category(lexical: float, tfidf_cos: float, settings: Settings = DEFAULT_SETTINGS) -> str:
    """Baseline that only sees lexical signals (n-grams and TF-IDF)."""
    if lexical >= settings.near_verbatim_jaccard:
        return NEAR_VERBATIM
    if tfidf_cos >= 0.5:
        return PARAPHRASE
    if tfidf_cos >= 0.2:
        return TOPICAL
    return NONE


@dataclass
class OverlapResult:
    """Best-matching reference sentence for one paper sentence."""

    flag_id: str
    sentence_index: int
    section: str
    page: int
    paper_text: str
    reference_text: str
    reference_file: str
    reference_number: int | None
    ngram_jaccard: float
    ngram_containment: float
    tfidf_cosine: float
    embedding_cosine: float
    category: str
    lexical_only_category: str
    quoted_text_removed: bool
    embedding_backend: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_overlap(document: Document, corpus: ReferenceCorpus,
                    settings: Settings = DEFAULT_SETTINGS) -> list[OverlapResult]:
    """Compare each eligible paper sentence with every reference sentence."""
    from .models import get_embedder

    if corpus.is_empty or not corpus.sentences:
        logger.info("Overlap analysis skipped: no reference text.")
        return []
    paper: list[tuple[Any, str, bool]] = []
    for s in document.body_sentences():
        if s.section == "Front Matter":
            continue
        if looks_like_heading(s.text):
            continue  # headings and captions are not sentences worth comparing
        cleaned, had_quote = remove_quoted(strip_citations(s.text))
        if len(tokenize(cleaned)) < settings.min_sentence_tokens:
            continue
        paper.append((s, cleaned, had_quote))
    if not paper:
        return []
    refs = corpus.sentences
    ref_texts = [r.text for r in refs]
    p_texts = [p[1] for p in paper]

    vec = TfidfVectorizer(lowercase=True, stop_words="english").fit(p_texts + ref_texts)
    tf = cosine_similarity(vec.transform(p_texts), vec.transform(ref_texts))
    if settings.use_embeddings:
        emb = get_embedder(settings)
        em = emb.similarity(p_texts, ref_texts)
        backend = emb.backend
    else:
        em, backend = np.zeros_like(tf), "disabled"
    ref_grams = [word_ngram_set(t, settings.ngram_n) for t in ref_texts]

    results: list[OverlapResult] = []
    for i, (s, cleaned, had_quote) in enumerate(paper):
        grams = word_ngram_set(cleaned, settings.ngram_n)
        jac = np.array([jaccard(grams, g) for g in ref_grams])
        cont = np.array([max(len(grams & g) / len(grams) if grams else 0.0, len(grams & g) / len(g) if g else 0.0)
                         for g in ref_grams])
        lexical = np.maximum(jac, cont * 0.8)  # two-way containment helps when lengths differ
        # Best match: strongest lexical evidence, otherwise strongest semantic evidence.
        j = int(np.argmax(lexical)) if lexical.max() >= settings.near_verbatim_jaccard else int(np.argmax(em[i] + 0.25 * tf[i]))
        cat = categorize(float(lexical[j]), float(em[i][j]), settings)
        results.append(OverlapResult(
            flag_id="", sentence_index=s.index, section=s.section, page=s.page, paper_text=s.text,
            reference_text=refs[j].text, reference_file=refs[j].source_file, reference_number=refs[j].ref_number,
            ngram_jaccard=round(float(jac[j]), 4), ngram_containment=round(float(cont[j]), 4),
            tfidf_cosine=round(float(tf[i][j]), 4), embedding_cosine=round(float(em[i][j]), 4),
            category=cat, lexical_only_category=lexical_only_category(float(lexical[j]), float(tf[i][j]), settings),
            quoted_text_removed=had_quote, embedding_backend=backend,
        ))
    for k, r in enumerate(sorted(results, key=lambda r: r.sentence_index), 1):
        r.flag_id = f"O{k:03d}"
    counts = {c: sum(r.category == c for r in results) for c in (NEAR_VERBATIM, PARAPHRASE, TOPICAL, NONE)}
    logger.info("Overlap categories: %s", counts)
    return results
