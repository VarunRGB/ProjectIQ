"""Evidence retrieval and inference for claims.

Pipeline per claim:
  1. Build a passage corpus from reference PDFs (2-3 sentence windows with overlap).
  2. Retrieve top-k passages with (a) a TF-IDF baseline and (b) embedding cosine.
     If the claim cites references that have PDFs, retrieval is restricted to
     those papers ("cited scope"); otherwise it searches every reference.
  3. Run NLI on the retrieved passages (premise = passage, hypothesis = claim)
     -> Supports / Contradicts / Insufficient evidence, with probabilities.
  4. Run the keyword/rule baseline on the same pairs for comparison.

High similarity is never treated as support: only the NLI entailment
probability can produce "Supports".
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .citation_analyzer import strip_citations
from .config import DEFAULT_SETTINGS, Settings
from .pdf_extractor import Document, PDFExtractionError, Reference, extract_document
from .preprocessing import normalize_tokens, stem

logger = logging.getLogger(__name__)

SUPPORTS, CONTRADICTS, INSUFFICIENT = "Supports", "Contradicts", "Insufficient evidence"
NO_REFERENCE_TEXT = "No reference text available"
LABEL_TO_VERDICT = {"entailment": SUPPORTS, "contradiction": CONTRADICTS, "neutral": INSUFFICIENT}
VERDICT_TO_SHORT = {SUPPORTS: "supports", CONTRADICTS: "contradicts", INSUFFICIENT: "insufficient"}


# --------------------------------------------------------------------------- #
# Rule / keyword baseline for 3-way NLI
# --------------------------------------------------------------------------- #
_NEGATIONS = {"not", "no", "never", "none", "neither", "nor", "without", "fail", "failed", "fails",
              "cannot", "lack", "lacks", "unable", "didn't", "doesn't", "don't", "isn't", "wasn't"}
_ANTONYMS: list[tuple[set[str], set[str]]] = [
    ({"improv", "increas", "boost", "gain", "enhanc", "rais", "help"},
     {"reduc", "decreas", "degrad", "worsen", "harm", "drop", "lower", "hurt", "declin"}),
    ({"higher", "better", "more", "greater", "larger", "faster", "outperform", "superior"},
     {"lower", "wors", "less", "fewer", "smaller", "slower", "underperform", "inferior"}),
    ({"consist", "reliabl", "robust", "effect", "signific"},
     {"inconsist", "unreliabl", "fragil", "ineffect", "insignific"}),
    ({"support", "confirm"}, {"refut", "contradict"}),
]


def _stems(text: str) -> list[str]:
    return stem(normalize_tokens(text))


def _polarity_hits(stems: Iterable[str]) -> tuple[set[int], set[int]]:
    s = set(stems)
    pos, neg = set(), set()
    for i, (a, b) in enumerate(_ANTONYMS):
        if any(any(t.startswith(x) for x in a) for t in s):
            pos.add(i)
        if any(any(t.startswith(x) for x in b) for t in s):
            neg.add(i)
    return pos, neg


def _rule_label_single(premise: str, h_stems: list[str], neg_h: bool) -> tuple[str, float]:
    """Rule label for one premise sentence, plus the hypothesis coverage it reached."""
    p_stems = _stems(premise)
    if not p_stems:
        return "neutral", 0.0
    coverage = len(set(h_stems) & set(p_stems)) / len(set(h_stems))
    neg_p = bool(_NEGATIONS & set(re.findall(r"[a-z']+", premise.lower())))
    hp, hn = _polarity_hits(h_stems)
    pp, pn = _polarity_hits(p_stems)
    flip = bool((hp & pn) - pp) or bool((hn & pp) - pn)
    if coverage >= 0.4 and (neg_p != neg_h or flip):
        return "contradiction", coverage
    if coverage >= 0.6:
        return "entailment", coverage
    return "neutral", coverage


def rule_nli_label(premise: str, hypothesis: str) -> str:
    """Keyword baseline: entailment / contradiction / neutral.

    The premise is split into sentences and only the sentence that covers the
    most hypothesis content words is used:
      * coverage = share of the hypothesis' content stems found in that sentence
      * contradiction if coverage >= 0.4 and negation or antonym polarity differs
      * entailment if coverage >= 0.6 and polarity agrees
      * neutral otherwise
    """
    from .preprocessing import split_sentences

    hyp = strip_citations(hypothesis)
    h_stems = _stems(hyp)
    if not h_stems:
        return "neutral"
    neg_h = bool(_NEGATIONS & set(re.findall(r"[a-z']+", hyp.lower())))
    sents = split_sentences(premise) or [premise]
    scored = [_rule_label_single(s, h_stems, neg_h) for s in sents]
    return max(scored, key=lambda x: x[1])[0]


_NAME_RE = re.compile(r"\b[A-Za-z]{2,}[-]?\d+[A-Za-z0-9.]*\b|\b[A-Z]\d[A-Za-z0-9]+\b")
_NAME_STOP = ("map", "top", "fp", "int", "float", "epoch", "iter", "page", "fig", "table", "eq", "sec", "chapter")


def specific_names(text: str) -> set[str]:
    """Versioned model / module names in a text: YOLOv5, YOLOv8, GPT-4, ResNet50, C2f, C3K2.

    Used to check that a passage is about the SAME thing as the claim. Numbers alone, metric names
    (mAP50) and figure references are ignored.
    """
    out = set()
    for m in _NAME_RE.finditer(text):
        tok = m.group(0).rstrip(".").lower().replace("-", "")
        if not tok.startswith(_NAME_STOP):
            out.add(tok)
    return out


def subject_mismatch(claim: str, passage: str) -> str:
    """Non-empty explanation if the claim and passage name different specific models/versions.

    Example: claim about "YOLOv5", passage only about "YOLOv6, YOLOv7, YOLOv8" -> they describe
    different things, so the passage can neither support nor contradict the claim.
    """
    c, p = specific_names(claim), specific_names(passage)
    if c and p and not (c & p):
        def show(names: set[str]) -> str:
            return ", ".join(sorted(n.upper() if len(n) <= 3 else n.replace("yolo", "YOLO") for n in names))
        return f"different subject: {show(c)} in the claim vs {show(p)} in the passage"
    return ""


def rule_nli_probs(premise: str, hypothesis: str) -> dict[str, float]:
    """Pseudo-probabilities for the rule baseline (so it plugs into the NLI interface)."""
    label = rule_nli_label(premise, hypothesis)
    probs = {"entailment": 0.15, "neutral": 0.15, "contradiction": 0.15}
    probs[label] = 0.70
    return probs


# --------------------------------------------------------------------------- #
# Reference corpus
# --------------------------------------------------------------------------- #
@dataclass
class Passage:
    """A window of consecutive sentences from a reference PDF."""

    passage_id: str
    source_file: str
    ref_number: int | None
    section: str
    page: int
    text: str


@dataclass
class ReferenceCorpus:
    """All reference passages plus the file -> reference-number mapping."""

    passages: list[Passage]
    file_to_ref: dict[str, int | None]
    titles: dict[str, str]
    sentences: list[Passage] = field(default_factory=list)  # single sentences (for overlap)
    errors: list[str] = field(default_factory=list)

    def passages_for_refs(self, refs: Iterable[int]) -> list[int]:
        wanted = set(refs)
        return [i for i, p in enumerate(self.passages) if p.ref_number in wanted]

    @property
    def is_empty(self) -> bool:
        return not self.passages


def read_mapping_csv(path: Path) -> dict[str, int]:
    """Read an optional ``file,ref_number`` CSV for manual file-to-reference mapping."""
    mapping: dict[str, int] = {}
    if not path.exists():
        return mapping
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            f, n = (row.get("file") or "").strip(), (row.get("ref_number") or "").strip()
            if f and n.isdigit():
                mapping[f] = int(n)
    logger.info("Loaded %d manual reference mappings from %s", len(mapping), path)
    return mapping


def match_file_to_reference(filename: str, title: str, references: dict[int, Reference]) -> int | None:
    """Guess which reference-list entry a reference PDF corresponds to.

    Tries, in order: a number in the file name ("ref_3_...", "3-smith.pdf"),
    first-author surname + year in the file name, then title-word overlap.
    """
    stem_name = Path(filename).stem.lower()
    m = re.match(r"^(?:ref|reference|r)?[_\-\s]?(\d{1,3})(?:[_\-\s.]|$)", stem_name)
    if m and int(m.group(1)) in references:
        return int(m.group(1))
    for num, ref in references.items():
        if ref.first_author and ref.year and ref.first_author.lower() in stem_name and ref.year in stem_name:
            return num
    title_words = set(normalize_tokens(title))
    best, best_score = None, 0.0
    if len(title_words) >= 3:
        for num, ref in references.items():
            ref_words = set(normalize_tokens(ref.text))
            score = len(title_words & ref_words) / len(title_words)
            if score > best_score:
                best, best_score = num, score
    return best if best_score >= 0.6 else None


def chunk_sentences(texts: list[str], size: int, overlap: int) -> list[tuple[int, int]]:
    """(start, end) index windows of ``size`` sentences sharing ``overlap`` sentences."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    step = max(1, size - max(0, overlap))
    n = len(texts)
    if n == 0:
        return []
    windows = []
    for start in range(0, n, step):
        end = min(n, start + size)
        windows.append((start, end))
        if end == n:
            break
    return windows


def build_reference_corpus(
    sources: Iterable[tuple[str, Path | bytes]],
    references: dict[int, Reference],
    settings: Settings = DEFAULT_SETTINGS,
    manual_mapping: dict[str, int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> ReferenceCorpus:
    """Extract and chunk reference PDFs.

    Args:
        sources: (display name, path or bytes) for each reference PDF.
        references: The paper's parsed reference list.
        manual_mapping: Optional {file name: reference number} overrides.
    """
    manual_mapping = manual_mapping or {}
    passages: list[Passage] = []
    sentences: list[Passage] = []
    file_to_ref: dict[str, int | None] = {}
    titles: dict[str, str] = {}
    errors: list[str] = []
    for name, src in sources:
        try:
            doc: Document = extract_document(src, name=name, is_reference=True)
        except PDFExtractionError as exc:
            logger.error("Skipping reference %s: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue
        ref_no = manual_mapping.get(name) or match_file_to_reference(name, doc.title, references)
        file_to_ref[name], titles[name] = ref_no, doc.title
        body = [s for s in doc.sentences if s.section not in ("References", "Front Matter")] or doc.sentences
        for s in body:
            sentences.append(Passage(f"{Path(name).stem}#s{s.index}", name, ref_no, s.section, s.page, s.text))
        for a, b in chunk_sentences([s.text for s in body], settings.chunk_sentences, settings.chunk_overlap):
            passages.append(Passage(f"{Path(name).stem}#p{len(passages)}", name, ref_no, body[a].section,
                                    body[a].page, " ".join(s.text for s in body[a:b])))
        logger.info("Reference %s -> ref [%s], %d sentences", name, ref_no, len(body))
        if progress:
            progress(name)
    return ReferenceCorpus(passages, file_to_ref, titles, sentences, errors)


def load_reference_dir(refs_dir: Path, references: dict[int, Reference],
                       settings: Settings = DEFAULT_SETTINGS,
                       progress: Callable[[str], None] | None = None) -> ReferenceCorpus:
    """Load every PDF in ``refs_dir`` (plus ``mapping.csv`` if present)."""
    refs_dir = Path(refs_dir)
    if not refs_dir.exists():
        logger.warning("Reference directory %s does not exist; no evidence retrieval.", refs_dir)
        return ReferenceCorpus([], {}, {})
    files = sorted(refs_dir.glob("*.pdf"))
    mapping = read_mapping_csv(refs_dir / "mapping.csv")
    return build_reference_corpus([(f.name, f) for f in files], references, settings, mapping, progress)


# --------------------------------------------------------------------------- #
# Retrievers
# --------------------------------------------------------------------------- #
class TfidfRetriever:
    """Lexical baseline retriever (TF-IDF, cosine)."""

    name = "tfidf"

    def __init__(self, texts: list[str]) -> None:
        self.vectorizer = TfidfVectorizer(lowercase=True, stop_words="english", ngram_range=(1, 2),
                                          sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(texts) if texts else None

    def scores(self, queries: list[str]) -> np.ndarray:
        if self.matrix is None:
            return np.zeros((len(queries), 0))
        return cosine_similarity(self.vectorizer.transform(queries), self.matrix)


class EmbeddingRetriever:
    """Dense retriever using sentence embeddings (cosine)."""

    name = "embedding"

    def __init__(self, texts: list[str], settings: Settings = DEFAULT_SETTINGS) -> None:
        from .models import get_embedder

        self.embedder = get_embedder(settings)
        self.matrix = self.embedder.encode(texts) if texts else None

    def scores(self, queries: list[str]) -> np.ndarray:
        if self.matrix is None:
            return np.zeros((len(queries), 0))
        return self.embedder.encode(queries) @ self.matrix.T


def top_k(scores: np.ndarray, k: int, allowed: list[int] | None = None) -> list[tuple[int, float]]:
    """Indices and scores of the k best items, optionally restricted to ``allowed``."""
    idx = np.array(allowed if allowed is not None else range(len(scores)), dtype=int)
    if idx.size == 0:
        return []
    order = idx[np.argsort(-scores[idx])][:k]
    return [(int(i), float(scores[i])) for i in order]


# --------------------------------------------------------------------------- #
# Per-claim evidence
# --------------------------------------------------------------------------- #
@dataclass
class EvidenceHit:
    """One retrieved passage with all its scores."""

    passage_id: str
    source_file: str
    ref_number: int | None
    text: str
    tfidf_score: float
    embedding_score: float
    retrieved_by: list[str]
    nli_probs: dict[str, float] = field(default_factory=dict)
    nli_label: str = ""
    rule_label: str = ""
    relevant: bool = True  # passed the relevance gate (can support/contradict)
    ignore_reason: str = ""  # why it was not allowed to support/contradict
    contradiction_confirmed: bool = False  # contradiction held on the closest sentence, both directions


@dataclass
class ClaimEvidence:
    """Final evidence verdict for one claim."""

    claim_id: str
    claim_text: str
    scope: str                       # "cited references" | "all references" | "none"
    cited_references: list[int]
    cited_refs_with_pdf: list[int]
    tfidf_hits: list[tuple[str, float]]
    embedding_hits: list[tuple[str, float]]
    hits: list[EvidenceHit]
    verdict: str
    nli_label: str
    nli_score: float
    nli_probs: dict[str, float]
    rule_verdict: str
    best_passage: EvidenceHit | None
    reason: str
    nli_backend: str
    embedding_backend: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aggregate(hits: list[EvidenceHit], settings: Settings) -> tuple[str, EvidenceHit | None, float]:
    """Pick the verdict and best passage from NLI results over the RELEVANT hits.

    Only passages that passed the relevance gate may produce Supports or Contradicts;
    an unrelated passage can never contradict a claim.
    """
    if not hits:
        return INSUFFICIENT, None, 0.0
    relevant = [h for h in hits if h.relevant]
    if relevant:
        best_e = max(relevant, key=lambda h: h.nli_probs.get("entailment", 0.0))
        e = best_e.nli_probs["entailment"]
        confirmed = [h for h in relevant if h.contradiction_confirmed]
        if confirmed:
            best_c = max(confirmed, key=lambda h: h.nli_probs.get("contradiction", 0.0))
            c = best_c.nli_probs["contradiction"]
            if c >= settings.nli_contradict_threshold and c > best_c.nli_probs["entailment"] and c >= e:
                return CONTRADICTS, best_c, c
        if e >= settings.nli_support_threshold:
            return SUPPORTS, best_e, e
    best_rel = max(hits, key=lambda h: (h.embedding_score, h.tfidf_score))
    return INSUFFICIENT, best_rel, best_rel.nli_probs.get("neutral", 0.0)


def _rule_aggregate(hits: list[EvidenceHit]) -> str:
    labels = {h.rule_label for h in hits if h.relevant}
    if "contradiction" in labels:
        return CONTRADICTS
    if "entailment" in labels:
        return SUPPORTS
    return INSUFFICIENT


def _reason(verdict: str, best: EvidenceHit | None, scope: str, nli_backend: str,
            retrieval_max: float, settings: Settings, hits: list[EvidenceHit] | None = None) -> str:
    model = "The NLI model" if nli_backend == "transformers-nli" else "The rule-based fallback (NLI model unavailable)"
    if best is None:
        return "No reference passages were available to compare against."
    where = f"'{best.source_file}'" + (f" (reference [{best.ref_number}])" if best.ref_number else "")
    if verdict == SUPPORTS:
        return (f"{model} predicts that a passage in {where} entails the claim "
                f"(p_entail={best.nli_probs.get('entailment', 0):.2f}, similarity {best.embedding_score:.2f}). "
                f"This is a model prediction; a reviewer should confirm the passage really says this.")
    if verdict == CONTRADICTS:
        return (f"{model} predicts that a passage in {where} contradicts the claim "
                f"(p_contra={best.nli_probs.get('contradiction', 0):.2f}, similarity {best.embedding_score:.2f}). "
                f"Check whether the source actually says the opposite, or whether the context differs.")
    hits = hits or []
    ignored = [h for h in hits if (not h.relevant or (h.nli_label == "contradiction" and not h.contradiction_confirmed))
               and h.nli_label in ("contradiction", "entailment")]
    if hits and not any(h.relevant for h in hits):
        note = (" Some passages were labelled support/contradiction by the model but were ignored because they "
                "are about something else." if ignored else "")
        return (f"None of the retrieved passages (searched {scope}) is close enough in meaning to the claim to judge "
                f"it (best similarity {max(h.embedding_score for h in hits):.2f}).{note} The references probably do "
                f"not discuss this point; if it is the authors' own result, that is expected.")
    low = " Retrieval similarity was also low." if retrieval_max < settings.min_retrieval_score else ""
    if ignored:
        why = ignored[0].ignore_reason
        low += (f" {len(ignored)} passage(s) labelled support/contradiction by the model were set aside"
                + (f" ({why})." if why else "."))
    return (f"No relevant passage was predicted to entail or contradict the claim (searched {scope}). "
            f"The most related passage is in {where}, but related wording is not support.{low}")


def _confirm_contradiction(passage: str, claim: str, nli: Any, embedder: Any, settings: Settings) -> bool:
    """Re-check a "contradiction" on the single passage sentence closest to the claim, in both directions.

    A 3-sentence window often gets a high contradiction score because ONE of its sentences mentions
    a different detail (another model, other numbers). A real contradiction should still hold when
    the claim is compared with the sentence that is actually about it, and also when premise and
    hypothesis are swapped (contradiction is symmetric; "different detail" usually is not).
    """
    from .preprocessing import split_sentences

    sents = [x for x in split_sentences(passage) if len(x.split()) >= 4] or [passage]
    if embedder is not None:
        best = sents[int(embedder.similarity([claim], sents)[0].argmax())]
    else:
        cs = set(_stems(claim))
        best = max(sents, key=lambda x: len(cs & set(_stems(x))))
    if subject_mismatch(claim, best):
        return False
    if nli is not None:
        fwd, rev = nli.predict([best, claim], [claim, best])
    else:
        fwd, rev = rule_nli_probs(best, claim), rule_nli_probs(claim, best)
    return fwd["contradiction"] >= settings.nli_contradict_threshold and rev["contradiction"] >= 0.5


def analyze_evidence(claims: list[Any], corpus: ReferenceCorpus, settings: Settings = DEFAULT_SETTINGS,
                     progress: Callable[[int, int], None] | None = None) -> list[ClaimEvidence]:
    """Retrieve evidence and run NLI + rule baseline for each claim.

    Args:
        claims: :class:`~src.claim_extractor.Claim` objects.
        corpus: Reference passages from :func:`load_reference_dir`.
    """
    from .models import get_embedder, get_nli

    if corpus.is_empty or not claims:
        if claims:
            logger.warning("No reference passages available; evidence analysis skipped.")
        return [ClaimEvidence(c.claim_id, c.text, "none", c.cited_references, [], [], [], [],
                              NO_REFERENCE_TEXT, "", 0.0, {}, NO_REFERENCE_TEXT, None,
                              "No reference PDFs were provided, so the claim could not be checked.",
                              "none", "none") for c in claims]

    texts = [p.text for p in corpus.passages]
    tfidf = TfidfRetriever(texts)
    emb_ret = EmbeddingRetriever(texts, settings) if settings.use_embeddings else None
    nli = get_nli(settings) if settings.use_nli else None
    hypotheses = [strip_citations(c.text) for c in claims]
    tf_all = tfidf.scores(hypotheses)
    em_all = emb_ret.scores(hypotheses) if emb_ret else np.zeros_like(tf_all)
    refs_with_pdf = {r for r in corpus.file_to_ref.values() if r is not None}

    results: list[ClaimEvidence] = []
    for ci, claim in enumerate(claims):
        cited_pdf = [r for r in claim.cited_references if r in refs_with_pdf]
        allowed = corpus.passages_for_refs(cited_pdf) if cited_pdf else None
        scope = "cited references" if cited_pdf else "all references"
        tf_hits = top_k(tf_all[ci], settings.top_k, allowed)
        em_hits = top_k(em_all[ci], settings.top_k, allowed) if emb_ret else []
        merged: dict[int, list[str]] = {}
        for i, _ in tf_hits:
            merged.setdefault(i, []).append("tfidf")
        for i, _ in em_hits:
            merged.setdefault(i, []).append("embedding")
        hits = [EvidenceHit(corpus.passages[i].passage_id, corpus.passages[i].source_file,
                            corpus.passages[i].ref_number, corpus.passages[i].text,
                            round(float(tf_all[ci][i]), 4), round(float(em_all[ci][i]), 4), by)
                for i, by in merged.items()]
        if hits:
            probs = (nli.predict([h.text for h in hits], [hypotheses[ci]] * len(hits)) if nli
                     else [rule_nli_probs(h.text, hypotheses[ci]) for h in hits])
            gate = settings.min_evidence_similarity if cited_pdf else settings.min_evidence_similarity_uncited
            semantic = emb_ret is not None and emb_ret.embedder.backend == "sentence-transformers"
            for h, p in zip(hits, probs):
                h.nli_probs = {k: round(v, 4) for k, v in p.items()}
                h.nli_label = max(p, key=p.get)
                h.relevant = (h.embedding_score >= gate) if semantic else (h.tfidf_score >= settings.min_evidence_tfidf)
                if not h.relevant:
                    h.ignore_reason = f"not about the same thing (similarity {h.embedding_score:.2f})"
                mismatch = subject_mismatch(hypotheses[ci], h.text)
                if h.relevant and mismatch:
                    h.relevant, h.ignore_reason = False, mismatch
                if h.relevant and h.nli_label == "contradiction":
                    h.contradiction_confirmed = _confirm_contradiction(
                        h.text, hypotheses[ci], nli, emb_ret.embedder if semantic else None, settings)
                    if not h.contradiction_confirmed:
                        h.ignore_reason = ("contradiction not confirmed: the closest sentence in this passage "
                                           "does not contradict the claim")
                h.rule_label = rule_nli_label(h.text, hypotheses[ci])
        verdict, best, score = _aggregate(hits, settings)
        retrieval_max = max([max(h.tfidf_score, h.embedding_score) for h in hits], default=0.0)
        backend = nli.backend if nli else "disabled (rule baseline)"
        results.append(ClaimEvidence(
            claim_id=claim.claim_id, claim_text=claim.text, scope=scope,
            cited_references=claim.cited_references, cited_refs_with_pdf=cited_pdf,
            tfidf_hits=[(corpus.passages[i].passage_id, round(s, 4)) for i, s in tf_hits],
            embedding_hits=[(corpus.passages[i].passage_id, round(s, 4)) for i, s in em_hits],
            hits=sorted(hits, key=lambda h: -max(h.nli_probs.values(), default=0)),
            verdict=verdict, nli_label=best.nli_label if best else "", nli_score=round(score, 4),
            nli_probs=best.nli_probs if best else {}, rule_verdict=_rule_aggregate(hits),
            best_passage=best, reason=_reason(verdict, best, scope, backend, retrieval_max, settings, hits),
            nli_backend=backend,
            embedding_backend=emb_ret.embedder.backend if emb_ret else "disabled",
        ))
        if progress:
            progress(ci + 1, len(claims))
    get_embedder(settings).save_cache()
    counts = {v: sum(r.verdict == v for r in results) for v in (SUPPORTS, CONTRADICTS, INSUFFICIENT)}
    logger.info("Evidence verdicts: %s", counts)
    return results
