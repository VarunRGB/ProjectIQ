"""Classic NLP preprocessing pipeline (the "syllabus" pipeline).

Exposes sentence segmentation, tokenization, lowercasing, stop-word removal,
Porter stemming, lemmatization, POS tagging, NER, n-grams (1-3), Bag-of-Words
and TF-IDF. :func:`analyze_text` returns all of them for one sentence/paragraph.

Sentence segmentation uses spaCy and then repairs common wrong splits in
scientific text ("et al.", "Fig.", "e.g.", "i.e.", decimals such as 0.95).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Iterable

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer, TfidfVectorizer

from .config import DEFAULT_SETTINGS, NLTK_DATA_DIR, OFFLINE

logger = logging.getLogger(__name__)

# Abbreviations after which a sentence must NOT end.
_ALWAYS_JOIN_ABBREVS: tuple[str, ...] = (
    "e.g.", "i.e.", "fig.", "figs.", "eq.", "eqs.", "vs.", "cf.", "tab.", "sec.",
    "no.", "approx.", "dr.", "prof.", "mr.", "ms.", "resp.", "ref.", "refs.", "al.,",
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']*[A-Za-z]|[A-Za-z]|\d+(?:\.\d+)?%?")
_PLACEHOLDER = "⁣"  # invisible separator used to protect abbreviations


# --------------------------------------------------------------------------- #
# Resource loading (lazy + cached)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_spacy(model_name: str = DEFAULT_SETTINGS.spacy_model) -> Any:
    """Load the spaCy model once, from models/ when it is there.

    Offline (the default) nothing is downloaded. If the model is missing, a blank
    English pipeline with a rule-based sentencizer is used and a clear warning is
    logged (POS/NER will be empty). Fix: python scripts/download_models.py
    """
    import spacy

    try:
        return spacy.load(model_name)
    except OSError:
        if not OFFLINE:
            logger.warning("spaCy model '%s' not found; trying to download it...", model_name)
            try:
                from spacy.cli import download

                download(model_name)
                return spacy.load(model_name)
            except Exception as exc:  # noqa: BLE001 - we want a soft fallback
                logger.error("spaCy download failed (%s).", exc)
        logger.error(
            "Could not load spaCy model '%s'. Using a blank pipeline: POS tags, lemmas and "
            "NER will be unavailable. Fix: python scripts/download_models.py", model_name,
        )
        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        return nlp


def _ensure_nltk(resource_path: str, package: str) -> bool:
    """Make sure an NLTK resource exists, downloading it if needed."""
    import nltk

    if str(NLTK_DATA_DIR) not in nltk.data.path:
        nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    try:
        nltk.data.find(resource_path)
        return True
    except LookupError:
        if OFFLINE:
            logger.warning("NLTK resource '%s' not in %s (offline, not downloading).",
                           package, NLTK_DATA_DIR)
            return False
        logger.info("Downloading NLTK resource '%s'...", package)
        try:
            return bool(nltk.download(package, quiet=True))
        except Exception as exc:  # noqa: BLE001
            logger.warning("NLTK download of '%s' failed: %s", package, exc)
            return False


@lru_cache(maxsize=1)
def get_stopwords() -> frozenset[str]:
    """English stop-words from NLTK, falling back to scikit-learn's list."""
    if _ensure_nltk("corpora/stopwords", "stopwords"):
        try:
            from nltk.corpus import stopwords

            return frozenset(stopwords.words("english"))
        except LookupError:
            pass
    logger.warning("Using scikit-learn stop-word list (NLTK stopwords unavailable).")
    return frozenset(ENGLISH_STOP_WORDS)


@lru_cache(maxsize=1)
def get_stemmer() -> Any:
    """Porter stemmer (pure Python, no download needed)."""
    from nltk.stem import PorterStemmer

    return PorterStemmer()


# --------------------------------------------------------------------------- #
# Sentence segmentation
# --------------------------------------------------------------------------- #
def _should_join(prev: str, nxt: str) -> bool:
    """Decide whether two neighbouring 'sentences' were wrongly split."""
    p = prev.rstrip()
    n = nxt.lstrip()
    if not p or not n:
        return False
    last_word = p.split()[-1].lower()
    if last_word in _ALWAYS_JOIN_ABBREVS:
        return True
    if last_word in ("al.", "et al."):
        # "Smith et al. (2020) show" or "et al. report" -> same sentence.
        return n[0] in "([,;" or n[0].islower() or n[0].isdigit()
    # Decimal split: "accuracy of 0." + "95 on ..."
    if re.search(r"\d\.$", p) and n[0].isdigit():
        return True
    # Next "sentence" starting in lowercase is almost always a bad split.
    if n[0].islower():
        return True
    # A citation bracket or closing parenthesis glued to the next part.
    if n[0] in ")]":
        return True
    # A citation marker split off from the end of its sentence: "... models" + "[1]."
    if re.match(r"^\[[\d,;\s\-\u2013]+\][.;,]?$", n) or re.match(r"^\([A-Z][^()]{0,60}\d{4}[a-z]?\)[.;,]?$", n):
        return True
    return False


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, robust to scientific abbreviations and decimals.

    Args:
        text: Raw paragraph or page text.

    Returns:
        A list of cleaned sentence strings (empty strings removed).
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    # Protect "et al." and "e.g." style abbreviations from the splitter.
    protected = re.sub(
        r"\b(e\.g|i\.e|et al|Fig|Figs|Eq|Eqs|vs|cf|Tab|Sec|No|approx|resp)\.",
        lambda m: m.group(0)[:-1] + _PLACEHOLDER,
        text,
        flags=re.IGNORECASE,
    )
    nlp = get_spacy()
    raw = [s.text.strip() for s in nlp(protected).sents if s.text.strip()]
    raw = [s.replace(_PLACEHOLDER, ".") for s in raw]

    merged: list[str] = []
    for sent in raw:
        if merged and _should_join(merged[-1], sent):
            sep = "" if re.search(r"\d\.$", merged[-1]) and sent[:1].isdigit() else " "
            merged[-1] = merged[-1] + sep + sent
        else:
            merged.append(sent)
    return merged


def looks_like_heading(text: str) -> bool:
    """True for heading-like lines that are not real sentences.

    Examples: "CHAPTER 3: DETAILS OF DESIGN", "3.1. Filters, Kernels, and Feature Maps",
    "Filters, Kernels, and Feature Maps in YOLOv8".
    """
    t = text.strip()
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return True  # mostly capitals
    if re.match(r"^\d+(\.\d+)*\.?\s", t) and len(t.split()) <= 12:
        return True  # numbered sub-heading
    if not re.search(r"[.!?:;\])\"']$", t) and len(t.split()) <= 12:
        return True  # short line with no sentence-ending punctuation
    return False


# --------------------------------------------------------------------------- #
# Token-level steps
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> list[str]:
    """Simple regex word tokenizer keeping numbers like 0.95 and 12%."""
    return _TOKEN_RE.findall(text or "")


def lowercase(tokens: Iterable[str]) -> list[str]:
    """Lowercase every token."""
    return [t.lower() for t in tokens]


def remove_stopwords(tokens: Iterable[str]) -> list[str]:
    """Remove English stop-words (expects lowercase tokens)."""
    sw = get_stopwords()
    return [t for t in tokens if t not in sw]


def stem(tokens: Iterable[str]) -> list[str]:
    """Porter-stem each token."""
    stemmer = get_stemmer()
    return [stemmer.stem(t) for t in tokens]


def normalize_tokens(text: str) -> list[str]:
    """Lowercased, stop-word-free tokens: the standard lexical representation."""
    return remove_stopwords(lowercase(tokenize(text)))


def ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    """Contiguous n-grams of a token list."""
    if n <= 0:
        raise ValueError("n must be positive")
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def word_ngram_set(text: str, n: int = 3) -> set[tuple[str, ...]]:
    """Set of lowercase word n-grams (stop-words kept, as in classic overlap detection)."""
    return set(ngrams(lowercase(tokenize(text)), n))


def bag_of_words(texts: list[str]) -> tuple[list[str], list[list[int]]]:
    """Bag-of-Words counts for a list of texts.

    Returns:
        (vocabulary, count matrix as nested lists)
    """
    vec = CountVectorizer(lowercase=True, token_pattern=r"(?u)\b\w[\w.\-%]*\b")
    mat = vec.fit_transform(texts)
    return list(vec.get_feature_names_out()), mat.toarray().tolist()


def tfidf(texts: list[str]) -> tuple[list[str], list[list[float]]]:
    """TF-IDF weights for a list of texts (English stop-words removed)."""
    vec = TfidfVectorizer(lowercase=True, stop_words="english")
    mat = vec.fit_transform(texts)
    return list(vec.get_feature_names_out()), mat.toarray().round(4).tolist()


# --------------------------------------------------------------------------- #
# One-call pipeline
# --------------------------------------------------------------------------- #
def analyze_text(text: str) -> dict[str, Any]:
    """Run the full classic pipeline on a sentence or paragraph.

    Returns a dict with keys: sentences, tokens, lowercased, no_stopwords,
    stems, lemmas, pos, entities, ngrams (1/2/3), bow, tfidf.
    """
    sentences = split_sentences(text)
    tokens = tokenize(text)
    lower = lowercase(tokens)
    no_sw = remove_stopwords(lower)
    doc = get_spacy()(text)
    has_tagger = doc.has_annotation("TAG") or doc.has_annotation("POS")
    lemmas = [t.lemma_.lower() for t in doc if t.is_alpha] if has_tagger else []
    pos = [(t.text, t.pos_) for t in doc if not t.is_space] if has_tagger else []
    ents = [(e.text, e.label_) for e in doc.ents]
    units = sentences if len(sentences) > 1 else [text]
    try:
        bow_vocab, bow_counts = bag_of_words(units)
        tf_vocab, tf_weights = tfidf(units)
    except ValueError:  # empty vocabulary (e.g. only stop-words)
        bow_vocab, bow_counts, tf_vocab, tf_weights = [], [], [], []
    return {
        "sentences": sentences,
        "tokens": tokens,
        "lowercased": lower,
        "no_stopwords": no_sw,
        "stems": stem(no_sw),
        "lemmas": lemmas,
        "pos": pos,
        "entities": ents,
        "ngrams": {
            "unigrams": [" ".join(g) for g in ngrams(no_sw, 1)],
            "bigrams": [" ".join(g) for g in ngrams(no_sw, 2)],
            "trigrams": [" ".join(g) for g in ngrams(no_sw, 3)],
        },
        "bow": {"vocabulary": bow_vocab, "counts": bow_counts, "units": units},
        "tfidf": {"vocabulary": tf_vocab, "weights": tf_weights, "units": units},
    }
