"""Phase 3 v1.4: evidence-weighted research problem versus method relevance.

No online searches, no changes to the summary agent. MiniLM retrieves relevant
short passages; the cross-encoder compares separately selected pairs. Thresholds are
*heuristics*, not probabilities, and still require labeled-set calibration.
"""
from functools import lru_cache
import re
from typing import Dict, Mapping, Optional

import numpy as np

from .models import ParsedDocument, ResearchExtraction
from .relevance_context import ReportEvidence, prepare_relevance_context, is_method_section
from .relevance_models import PaperRelevance

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"

# Domain overlap is an *additional* signal. Generic model/method terms must not
# make a broadly related CNN paper count as the same research problem.
_GENERIC_TITLE_WORDS = {
    "a", "an", "the", "on", "in", "and", "with", "for", "using", "use", "of", "by",
    "via", "based", "from", "to", "new", "study", "studies", "approach", "approaches",
    "analysis", "survey", "review", "method", "methods", "system", "systems", "research",
    "pre", "trained", "training", "learning", "machine", "deep", "neural", "network",
    "networks", "model", "models", "pretrained", "large", "scale", "novel", "recent",
    "advances", "future", "trends", "cnn", "classification", "data", "using",
}
_METHOD_TERMS = re.compile(
    r"\b(?:transfer learning|algorithm|architecture|optimizer|optimization|gradient|"
    r"backpropagation|softmax|dataset|augmentation|training|classifier|weights|"
    r"residual|convolutional|cnn|vgg|resnet|alexnet|densenet|feature extraction|"
    r"loss function|pretrained|pre-trained)\b", re.I,
)
# Generic ML terms are *not* evidence of adopting the same specific method.
# Domain-neutral: these exclusions apply regardless of the subject of a report.
_GENERIC_METHOD_TERMS = {
    "algorithm", "architecture", "approach", "automatic", "automated", "classify",
    "classification", "classifer", "classifier", "cnn", "convolution", "convolutional",
    "data", "dataset", "deep", "detection", "feature", "image", "imaging", "input",
    "learning", "machine", "method", "model", "network", "neural", "output",
    "pattern", "prediction", "recognition", "research", "study", "system", "task",
    "technique", "text", "training", "visual", "result", "performance", "analysis",
    "modeling", "using", "general", "type", "based", "process", "processing", "with",
}
_METHOD_SECTION_NAMES = {"method", "methods", "methodology", "materials and methods",
                         "proposed methodology", "proposed system", "system architecture",
                         "implementation", "experimental setup"}
_TOPIC_SECTION_NAMES = {"introduction", "background", "literature review",
                        "review of literature", "related work", "abstract"}



# Domain-neutral task words distinguish the *problem being studied* from
# architectures/algorithms used to solve it. These are structural cues, not
# a dictionary of accepted OCR papers or research subjects.
_TASK_RE = re.compile(
    r"\b(?:recogniti?on|detect(?:ion|ing)|classif(?:ication|y)|predict(?:ion|ing)|"
    r"estimat(?:ion|ing)|segment(?:ation|ing)|forecast(?:ing)|translat(?:ion|ing)|"
    r"retriev(?:al|ing)|generat(?:ion|ing)|diagnos(?:is|ing)|monitor(?:ing)|"
    r"identif(?:ication|y)|optimis(?:ation|ing)|optimiz(?:ation|ing))\b", re.I
)
_METHOD_LED_RE = re.compile(
    r"^\s*(?:an overview of\s+|a survey of\s+)?(?:deep\s+|very deep\s+|"
    r"densely connected\s+|convolutional\s+|neural\s+|residual\s+|"
    r"transfer learning\s+|knowledge transfer\s+|gradient\s+|"
    r"boosting\s+|freezeout\s*:?|transformer\s+|"
    r"(?:a|an)\s+(?:new\s+)?(?:algorithm|architecture|network)\s+)", re.I
)
_METHOD_SPLIT_RE = re.compile(r"\s+(?:using|via|through|based on|with)\s+", re.I)


def _problem_phrase(title: str) -> str:
    """A title's task clause, minus a trailing method clause when possible.

    Applied uniformly to report and reference titles. Never references topic
    vocabulary (e.g. OCR, medicine, economics) or particular paper names.
    """
    original = _clean_text(title, 230)
    if not original:
        return ""
    # Report subtitle often describes implementation rather than the problem.
    prefix = re.split(r"\s*[:;—]\s*", original, maxsplit=1)[0]
    parts = _METHOD_SPLIT_RE.split(prefix, maxsplit=1)
    if len(parts) > 1 and _TASK_RE.search(parts[0]):
        prefix = parts[0]
    # Method-first titles often state the target after 'for':
    # 'Deep residual learning for image recognition' -> 'image recognition'.
    if _is_technique_led(original) or re.search(r"\bapproaches to\b", prefix, re.I):
        m = re.search(r"\b(?:for|approaches to)\s+(.+)", original, flags=re.I)
        if m and _TASK_RE.search(m.group(1)):
            return _clean_text(re.split(r"\s*[:;—]\s*", m.group(1), maxsplit=1)[0], 130)
    return _clean_text(prefix, 170)


def _specific_subject_terms(title: str) -> set[str]:
    """Non-generic subject words; broad tasks and AI methods do not count."""
    broad = _GENERIC_METHOD_TERMS | {
        "image", "imaging", "recognition", "detection", "classification",
        "prediction", "estimation", "segmentation", "generation", "retrieval",
        "forecast", "forecasting", "automatic", "automated", "optimization",
        "study", "approach", "large", "scale", "trained", "pretrained", "pre",
        "natural", "general", "robust", "proposed", "new", "advanced",
    }
    return _domain_terms(_problem_phrase(title)) - broad


def _is_technique_led(title: str) -> bool:
    """Detect architectural/algorithm-first wording, not specific subjects."""
    if re.search(r"\b(?:review|survey|approaches to)\b", title, re.I):
        # Survey of a *research problem* is topical, even if its title starts
        # with the name of a popular technique.
        task = re.search(r"\b(?:to|for)\s+(.+)", title, re.I)
        if task and _TASK_RE.search(task.group(1)):
            return False
    return (bool(_METHOD_LED_RE.search(title)) or bool(re.search(
        r"^\s*[^ ]+[- ]level\s+(?:convolutional|neural|transformer|"
        r"network|algorithm|language\s+model)", title, re.I)))


def _relevant_method_ngrams(text: str) -> set[str]:
    """Shared multiword technical identifiers, excluding generic AI-only pairs."""
    words = re.findall(r"[a-z0-9]+", (text or "").casefold())
    ignore = _GENERIC_METHOD_TERMS | {"using", "use", "with", "for", "of", "and", "the", "in", "on", "to"}
    # A geographic place, species, pottery type, or dataset domain is NOT a
    # method merely because it forms a rare bi-gram in a cited paragraph.
    technical_heads = {"filter", "filtering", "gradient", "descent", "loss",
                       "optimizer", "optimization", "regression", "residual",
                       "kalman", "bayesian", "transformer", "kernel", "backpropagation",
                       "entropy", "freeze", "freezing", "attention", "solver",
                       "embedding", "embeddings", "segmentation", "augmentation",
                       "transfer", "feature", "extractor", "learning"}
    grams = set()
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            phrase = words[i:i + n]
            if (any(w in technical_heads for w in phrase)
                    and sum(w not in ignore and len(w) >= 4 for w in phrase) >= 1
                    and not all(w in ignore for w in phrase)):
                grams.add(" ".join(phrase))
    return grams


# Task families cover common *actions* across disciplines, not research domains.
# No known OCR, bridge, medicine, or other paper names are encoded.
_TASK_FAMILIES = {
    "recognition": r"\b(?:recognis\w*|recogniz\w*|recognition)\b",
    "detection": r"\b(?:detect\w*|localiz\w*|localis\w*)\b",
    "classification": r"\b(?:classif\w*|categoris\w*|categoriz\w*)\b",
    "forecast": r"\b(?:forecast\w*|predict\w*)\b",
    "estimation": r"\b(?:estimat\w*|infer\w*)\b",
    "segmentation": r"\bsegment\w*\b",
    "translation": r"\btranslat\w*\b",
    "retrieval": r"\bretriev\w*\b",
    "generation": r"\bgenerat\w*\b",
    "diagnosis": r"\bdiagnos\w*\b",
    "monitoring": r"\bmonitor\w*\b",
}


def _task_families(problem_phrase: str) -> set[str]:
    return {name for name, pattern in _TASK_FAMILIES.items()
            if re.search(pattern, problem_phrase, flags=re.I)}


def _general_method_paper(title: str) -> bool:
    """True for general architecture/algorithm contributions, not merely
    application studies that share a broad technique such as neural networks.
    This never looks at the test report's discipline."""
    if not _is_technique_led(title):
        return False
    subject = _specific_subject_terms(title)
    # Common adjectives referring to a mathematical/network method, not to a
    # separate application domain. Do not equate all rare words with methods.
    method_descriptors = {
        "very", "densely", "dense", "connected", "residual", "skip", "layer",
        "layers", "convolution", "convolutional", "gradient", "descent",
        "network", "neural", "weight", "weighted", "optimiz", "optimizer",
        "large", "scale", "deep", "architecture", "image", "visual",
        "pre", "progressively", "freezing", "freezeout", "transfer",
    }
    return not (subject - method_descriptors)


def _canonical_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _corroborated_alias(alias: str, topic_passages: list[str],
                        uncited_methods: list[str]) -> bool:
    """A named model must also occur outside the single cited Methods claim.
    The second occurrence establishes *report use*, not author/paper identity."""
    normalized = _canonical_identifier(alias)
    if not normalized:
        return False
    for passage in [*topic_passages, *uncited_methods]:
        if any(_canonical_identifier(m.group(0)) == normalized
               for m in re.finditer(
                   r"\b(?:[A-Za-z]{2,}(?:Net|Former)(?:[- ]?\d{1,4})?"
                   r"|[A-Za-z]{2,}[- ]?\d{1,4})\b", passage, re.I)):
            return True
    return False


def _specific_method_support(report_methods: list[str], title: str,
                             citation_claims: list[str], citation_aliases: list[str],
                             topic_passages: list[str], uncited_methods: list[str],
                             section_names: list[str], paper_abstract: str,
                             verification_status: str, methodological: Optional[float],
                             independent_method: Optional[float]) -> tuple[str, list[str], list[str]]:
    """Find corroborated method adoption without using citation placement as proof.

    Strong: a discriminative phrase from the paper matches an actual method-use
    statement. Moderate: a report claims to use a specific model beside a
    citation, the model appears elsewhere in the report, and a reliably matched
    *general-method* paper supplies the architecture. An unverified or
    application-specific paper does not acquire a positive label from that claim.
    """
    used, hits = _method_adoption_evidence(
        report_methods, title, citation_claims, section_names, independent_method)
    if used and hits:
        exact = [x for x in hits if x != "explicit named-model implementation at the citation"]
        if exact:
            strength = "moderate" if all(x in {"transfer learning", "feature extraction"} for x in exact) else "strong"
            return strength, [f"Explicit use of '{x}' also named by the reference" for x in exact[:3]], exact[:3]
    if verification_status not in {"verified", "needs_review"} or not _general_method_paper(title):
        return "none", [], []
    if methodological is None or methodological < 0.39:
        return "none", [], []
    if independent_method is None or independent_method < 0.16:
        return "none", [], []
    for alias in citation_aliases:
        if not _corroborated_alias(alias, topic_passages, uncited_methods):
            continue
        if not any(_ADOPTION_ACTION_RE.search(claim) and not _NON_ADOPTION_RE.search(claim)
                   for claim in citation_claims):
            continue
        if paper_abstract and _canonical_identifier(alias) in _canonical_identifier(paper_abstract):
            return "strong", [f"Cited implementation of '{alias}' corroborated by verified abstract"], [alias]
        return "moderate", [
            f"Cited implementation of '{alias}' also named in report topic/methods; "
            "paper-title alias is inferred, not independently verified"
        ], [alias]
    return "none", [], []


def _method_adoption_evidence(
    report_methodology: list[str], paper_title: str, citation_claims: list[str],
    section_names: list[str], independent_method: Optional[float],
) -> tuple[bool, list[str]]:
    """Require a technique used in the report, not merely a paper cited there.

    Strong: exact discriminative 2/3-gram shared in an adoption statement.
    Moderate: explicit implementation at this *numbered citation*, only when
    a semantic method connection independently exists (never context alone).
    """
    paper_grams = _relevant_method_ngrams(paper_title)
    named_hits = set()
    for paragraph in report_methodology:
        # A sentence can span arbitrary physical PDF lines; sentence-level
        # handling avoids dropping the part containing 'gradient descent'.
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if not _ADOPTION_ACTION_RE.search(sentence) or _NON_ADOPTION_RE.search(sentence):
                continue
            named_hits.update(paper_grams & _relevant_method_ngrams(sentence))
    if named_hits:
        return True, sorted(named_hits)[:5]
    # A paper can be known by an alias absent from its printed title (e.g. an
    # architecture introduced in a foundational article). Require both an
    # explicit adoption claim and independent semantic method similarity.
    if (independent_method is not None and independent_method >= 0.45
            and _is_technique_led(paper_title)
            and any(is_method_section(s) for s in section_names)):
        named_model = re.compile(r"\b(?:[A-Z]{2,}[-]?\d+|[A-Z][a-z]+(?:Net|Former)[-]?\d+)\b")
        for claim in citation_claims:
            if (_ADOPTION_ACTION_RE.search(claim) and not _NON_ADOPTION_RE.search(claim)
                    and named_model.search(claim)):
                return True, ["explicit named-model implementation at the citation"]
    return False, []


@lru_cache(maxsize=1)
def _load_embedder():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError("Phase 3 requires sentence-transformers; install project requirements.") from exc
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _load_reranker():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ImportError("Phase 3 requires sentence-transformers; install project requirements.") from exc
    return CrossEncoder(CROSS_ENCODER_MODEL)


def _cosine(x: np.ndarray, y: np.ndarray) -> float:
    denom = float(np.linalg.norm(x) * np.linalg.norm(y))
    return max(0.0, min(1.0, float(np.dot(x, y)) / denom)) if denom else 0.0


def _clean_text(text: Optional[str], max_chars: int = 1250) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:max_chars].rstrip()


def _domain_terms(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    def stem(token):
        if len(token) >= 5 and token.endswith("ies"):
            return token[:-3] + "y"
        return token[:-1] if len(token) >= 5 and token.endswith("s") else token
    return {stem(t) for t in tokens if len(t) >= 3 and t not in _GENERIC_TITLE_WORDS}


def _domain_overlap(report_title: str, paper_title: str) -> int:
    return len(_domain_terms(report_title) & _domain_terms(paper_title))


def _best_pair(query: list[str], paper_texts: list[str], vectors: dict) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Choose the best report *and paper* passage for this particular axis.

    Each dimension must choose its own paper counterpart. Previously the best
    topic counterpart was reused for method and citation comparisons.
    """
    if not query or not paper_texts:
        return None, None, None
    best = (-1.0, None, None)
    for q in query:
        for p in paper_texts:
            score = _cosine(vectors[q], vectors[p])
            if score > best[0]:
                best = (score, q, p)
    return best


def _method_anchors(independent_methods: list[str], paper_texts: list[str]) -> set[str]:
    """Non-generic terms shared by the paper and *uncited* method descriptions.

    A single named method can be valid evidence when accompanied by independent
    semantic similarity; generic shared words such as CNN and classification
    are not enough. This is report- and discipline-agnostic.
    """
    if not independent_methods:
        return set()
    report_terms = _domain_terms(" ".join(independent_methods)) - _GENERIC_METHOD_TERMS
    paper_terms = _domain_terms(" ".join(paper_texts)) - _GENERIC_METHOD_TERMS
    # Uncommon *domain objects* (e.g. 'ceramics', 'bird', 'reef') are not method
    # anchors. Only broadly recognizable technique vocabulary or named model
    # identifiers can corroborate methodological adoption independently.
    technical = {
        "gradient", "descent", "optimization", "optimizer", "kalman",
        "bayesian", "residual", "transformer", "kernel", "filter",
        "regression", "embedding", "augmentation", "entropy", "softmax",
        "backpropagation", "dropout", "regularization", "regularisation",
        "freezeout", "freezing", "attention", "solver", "transfer",
        "bootstrap", "contrastive", "diffusion", "segmentation", "lstm",
    }
    return {term for term in report_terms & paper_terms
            if len(term) >= 4 and (term in technical or re.fullmatch(
                r"(?:[a-z]+net|[a-z]+former|[a-z]+\d+)", term))}


_ADOPTION_ACTION_RE = re.compile(
    r"\b(?:use(?:d|s|ing)?|adopt(?:ed|ing|s)?|apply|applied|implement(?:ed|s|ing)?|"
    r"employ(?:ed|s|ing)?|adapt(?:ed|s|ing)?|train(?:ed|s|ing)?|"
    r"evaluat(?:ed|e|es|ing)|benchmark(?:ed|s|ing)?|compar(?:ed|e|es|ing))\b", re.I)
_NON_ADOPTION_RE = re.compile(
    r"\b(?:as motivation|as inspiration|cited as|as precedent|suggests?|"
    r"informs?|illustrates?|draws on|borrows?|analog(?:y|ies)|"
    r"motivates?|hypothesiz(?:ed|e|es)|might|could|may)\b", re.I)


def _cited_method_adoption(
    section_names: list[str], citation_claims: list[str], paper_texts: list[str],
) -> bool:
    """Require a *specific* technique and a concrete adoption statement.

    This supplements independent method passages without treating any mention
    in a Methods/Literature Review paragraph as evidence. The paper still needs
    an independent model-based method score (checked in `_classification`).
    """
    if not any(is_method_section(s) for s in section_names):
        return False
    paper_terms = _domain_terms(" ".join(paper_texts)) - _GENERIC_METHOD_TERMS
    for claim in citation_claims:
        if _NON_ADOPTION_RE.search(claim) or not _ADOPTION_ACTION_RE.search(claim):
            continue
        specific = (_domain_terms(claim) - _GENERIC_METHOD_TERMS) & paper_terms
        if any(len(word) >= 4 for word in specific):
            return True
    return False


def _classification(
    topical: float, method: Optional[float], context: Optional[float],
    *, domain_overlap: int, section_names: list[str], context_snippets: list[str],
    crosses: Dict[str, float], has_abstract: bool, verified: bool, paper_title: str = "",
    independent_method: Optional[float] = None, method_anchor_count: int = 0,
    adopted_named_method: bool = False,
    problem_similarity: Optional[float] = None, subject_overlap: int = 0,
    technique_led: bool = False, method_usage: bool = False,
    method_evidence_strength: str = "none", same_task_family: bool = False,
) -> tuple[str, str]:
    """Hierarchical classification, without using citation position as proof.

    1. Same research task: report/paper *problem clauses* align, independently
       of reference placement; generic model words cannot supply task identity.
    2. Method contribution: report uses a specific technique from the paper.
    3. Partial/uncertain/unrelated otherwise. Thresholds are heuristics, not
       probabilities. Cross-encoder ranking is supporting evidence, not truth.

    problem_similarity=None retains compatibility for legacy direct unit tests;
    all normal pipeline calls supply a real problem similarity.
    """
    topic_cross = crosses.get("topic")
    method_cross = crosses.get("independent_methodology", crosses.get("methodology"))
    topic_advantage = (topic_cross - method_cross if topic_cross is not None
                       and method_cross is not None else None)
    if problem_similarity is None:
        # Compatibility with pre-v1.3 callers testing only three-axis scores;
        # this is not the path used by assess_relevance.
        same_problem = (
            (topical >= 0.62 and (domain_overlap >= 1 or (topic_advantage is not None and topic_advantage >= 0.5)))
            or (topical >= 0.47 and domain_overlap >= 2)
            or (topical >= 0.45 and domain_overlap >= 1 and topic_advantage is not None and topic_advantage >= 0.35)
        )
    else:
        # Require a match in research task/target, not just generic 'CNN' or
        # 'image recognition'. High semantic affinity can bridge paraphrases;
        # a shared *specific* subject lowers that requirement.
        same_problem = (
            topical >= 0.38
            and (problem_similarity >= 0.65
                 or (problem_similarity >= 0.52 and subject_overlap >= 1)
                 or (problem_similarity >= 0.59 and not technique_led
                     and topic_advantage is not None and topic_advantage >= 0.65))
            and (not technique_led or (problem_similarity >= 0.72 and subject_overlap >= 1))
        )

    # A shared research action (recognizing, forecasting, detecting, ...)
    # can bridge rephrased titles when both neural signals independently agree.
    # Architecture-led publications cannot get a direct label from this route.
    if (problem_similarity is not None and not technique_led
            and same_task_family and topical >= 0.46 and problem_similarity >= 0.46
            and (problem_similarity >= 0.58
                 or (problem_similarity >= 0.47 and topic_advantage is not None
                     and topic_advantage >= 0.48))):
        same_problem = True

    # The paper's technical role must be corroborated by the report, either
    # by a technique used there or by a specific uncited method + a strong
    # embedding connection. Domain/keyword overlap or citation-context score
    # alone does not confer methodological relevance.
    confirmed_method = (
        # A specific adopted method with a matching verified paper can be
        # reliable evidence even when generic sentence embeddings score low.
        (method_evidence_strength == "strong" and verified and method_usage
         and method is not None and method >= 0.12)
        or (method_evidence_strength == "moderate" and verified and method_usage
            and method is not None and method >= 0.32)
        # Compatibility for other callers that don't supply evidence strength.
        or (method_evidence_strength == "none" and method_usage
            and method is not None and method >= 0.32)
        or (independent_method is not None and independent_method >= 0.46
            and method_anchor_count >= 1)
        or (adopted_named_method and independent_method is not None
            and independent_method >= 0.43)
    )
    if same_problem:
        return "directly_relevant", (
            "The paper's research-task wording aligns with the report's central "
            "research problem, rather than merely sharing a general algorithm."
        )
    if confirmed_method:
        return "methodologically_relevant", (
            "A specific technique or method is supported by the report's "
            "methodological evidence; topical similarity alone is not the basis."
        )
    # Technical papers with a modest methodological resemblance should not
    # become 'unrelated' simply because the report describes their use only in
    # one cited sentence. This is an *uncertain* connection, not proof of use.
    if (topical >= 0.36 or (independent_method is not None and independent_method >= 0.36)
            or (technique_led and method is not None and method >= 0.37)):
        return "partially_relevant", (
            "There is possible topical or methodological overlap, but evidence "
            "does not firmly establish the same research task or an adopted method."
        )
    if verified and has_abstract:
        return "possibly_unrelated", (
            "Available metadata does not establish the same research problem or "
            "a specific, adopted methodological contribution."
        )
    return "insufficient_evidence", (
        "Available metadata is too limited to judge the paper's research "
        "role confidently."
    )


def _identity_safe_for_semantics(verification) -> bool:
    """Only reliable identity matches may supply external metadata to Phase 3.

    `needs_review` is a *bibliographic review status*, not necessarily an
    established paper identity. A title-search candidate needs an actual
    author and title corroboration before we trust its title/abstract.
    """
    if verification is None:
        return False
    if verification.status == "verified":
        return True
    if verification.status != "needs_review":
        return False
    return (verification.author_match is True
            and (verification.title_match is True or
                 (verification.title_similarity is not None
                  and verification.title_similarity >= 0.72)))


def assess_relevance(
    extraction: ResearchExtraction,
    document: ParsedDocument,
    *,
    encoder=None,
    cross_encoder=None,
    paper_abstracts: Optional[Mapping[int, str]] = None,
) -> list[PaperRelevance]:
    """Produce one relevance record per bibliography entry, even if unverified.

    Inference models are loaded once per process, or injected by an orchestrator.
    No model weights are modified; the Summary Agent is not imported.
    `paper_abstracts` remains an optional test/override hook.
    """
    if not extraction.papers:
        return []

    evidence: ReportEvidence = prepare_relevance_context(document, extraction)
    verifications = extraction.verification_results
    abstracts = paper_abstracts or {}
    records = []
    for index, paper in enumerate(extraction.papers):
        verification = verifications[index] if index < len(verifications) else None
        status = verification.status if verification else "not_checked"
        accepted = _identity_safe_for_semantics(verification)
        best_title = (verification.verified_title if verification and accepted and verification.verified_title else paper.title or "")
        abstract = _clean_text(
            abstracts.get(index) or (getattr(verification, "verified_abstract", None) if accepted else None), 1100
        )
        # Corrupted, unverified titles can have arbitrary website text pasted in.
        if status in {"not_found", "metadata_mismatch"} and len(best_title.split()) > 35 and not abstract:
            best_title = ""
        best_title = _clean_text(best_title, 220)
        paper_texts = [best_title] if best_title else []
        if abstract:
            if best_title:
                paper_texts.append(_clean_text(best_title + ". Abstract: " + abstract, 850))
            else:
                paper_texts.append(_clean_text(abstract, 850))
        paper_texts = list(dict.fromkeys(x for x in paper_texts if x))
        contexts = evidence.citation_contexts.get(index, [])[:3]
        records.append((paper, status, best_title, abstract, paper_texts, contexts))

    if not evidence.topic_passages:
        return [PaperRelevance(i, rec[2] or None, rec[1], "insufficient_evidence", "low",
                               evidence_reliability=("verified_metadata" if rec[1] == "verified" else
                                                     "metadata_needs_review" if rec[1] == "needs_review" else
                                                     "unverified_reference"),
                               citation_lines=evidence.citation_lines.get(i, []),
                               explanation="No usable report topic was extracted.")
                for i, rec in enumerate(records)]

    # Embeddings are computed once for all unique, bounded passages. This keeps
    # methodology coverage broad without multiplying cross-encoder inference.
    report_problem = _problem_phrase(evidence.title)
    paper_problems = [_problem_phrase(record[2]) for record in records]
    inputs = list(evidence.topic_passages + evidence.methodology_passages
                  + evidence.independent_methodology_passages)
    if report_problem:
        inputs.append(report_problem)
    inputs.extend(paper_problems)
    if evidence.literature:
        inputs.append(evidence.literature)
    for _, _, _, _, paper_texts, contexts in records:
        inputs.extend(paper_texts)
        inputs.extend(contexts)
    unique_inputs = list(dict.fromkeys(x for x in inputs if x))
    embedder = encoder if encoder is not None else _load_embedder()
    vectors = np.asarray(embedder.encode(unique_inputs, batch_size=32, show_progress_bar=False), dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) != len(unique_inputs):
        raise ValueError("The embedding model returned an unexpected batch shape.")
    embeddings = dict(zip(unique_inputs, vectors))

    # Each axis gets its own closest report/paper passage pair. Literature and
    # citation context remain diagnostics, not independent grounds for relevance.
    candidates = {}
    pairs = []
    pair_keys = []
    for i, (_, _, _, _, paper_texts, contexts) in enumerate(records):
        if not paper_texts:
            continue
        axes = {
            "topic": evidence.topic_passages,
            "methodology": evidence.methodology_passages,
            "independent_methodology": evidence.independent_methodology_passages,
            "citation_context": contexts,
            "literature": [evidence.literature] if evidence.literature else [],
        }
        best = {name: _best_pair(passages, paper_texts, embeddings)
                for name, passages in axes.items()}
        topical = best["topic"][0]
        methodological = best["methodology"][0]
        independent_method = best["independent_methodology"][0]
        contextual = best["citation_context"][0]
        problem_score = (_cosine(embeddings[report_problem], embeddings[paper_problems[i]])
                         if report_problem and paper_problems[i] else None)
        candidates[i] = (topical, methodological, contextual, independent_method, problem_score)
        for name, (score, report_passage, paper_passage) in best.items():
            if report_passage and paper_passage:
                pairs.append((report_passage[:700], paper_passage[:850]))
                pair_keys.append((i, name))

    cross_scores: Dict[int, Dict[str, float]] = {}
    if pairs:
        reranker = cross_encoder if cross_encoder is not None else _load_reranker()
        predictions = np.asarray(reranker.predict(pairs, batch_size=16, show_progress_bar=False), dtype=float).reshape(-1)
        if len(predictions) != len(pairs):
            raise ValueError("The cross-encoder returned the wrong number of scores.")
        for (index, name), value in zip(pair_keys, predictions):
            if np.isfinite(value):
                cross_scores.setdefault(index, {})[name] = round(float(value), 4)

    results = []
    for index, (paper, status, title, abstract, paper_texts, contexts) in enumerate(records):
        lines = evidence.citation_lines.get(index, [])
        if not paper_texts:
            results.append(PaperRelevance(
                index, paper.title, status, "insufficient_evidence", "low",
                citation_lines=lines,
                evidence_reliability=("verified_metadata" if status == "verified" else
                                      "metadata_needs_review" if status == "needs_review" else
                                      "unverified_reference"),
                explanation="No usable paper title or abstract was available for comparison.",
                notes=["Phase 1 or Phase 2 must supply paper text before semantic assessment."],
            ))
            continue
        topical, method, context, independent_method, problem_score = candidates[index]
        crosses = cross_scores.get(index, {})
        sections = evidence.citation_sections.get(index, [])
        overlap = _domain_overlap(evidence.title, title)
        anchors = _method_anchors(evidence.independent_methodology_passages, paper_texts)
        adopted = _cited_method_adoption(sections, evidence.citation_claims.get(index, []), paper_texts)
        method_strength, support_traces, support_terms = _specific_method_support(
            evidence.method_sentences, title, evidence.citation_claims.get(index, []),
            evidence.citation_aliases.get(index, []), evidence.topic_passages,
            evidence.independent_methodology_passages, sections, abstract,
            status if _identity_safe_for_semantics(verifications[index] if index < len(verifications) else None) else "not_found",
            method, independent_method,
        )
        # Keep the v1.3 detailed evidence field but distinguish observed exact
        # method matches from the *level* of corroboration.
        method_usage, usage_evidence = _method_adoption_evidence(
            evidence.method_sentences, title,
            evidence.citation_claims.get(index, []), sections, independent_method,
        )
        # The older helper can identify *possible* use via a citation without
        # a trustworthy identity/alias link. Keep it as a diagnostic, but do
        # not feed it into the classifier unless our evidence checks pass.
        method_usage = method_strength != "none"
        if method_usage:
            usage_evidence = list(dict.fromkeys([*usage_evidence, *support_terms]))
        else:
            usage_evidence = []
        specific_overlap = len(_specific_subject_terms(evidence.title) & _specific_subject_terms(title))
        technique_led = _is_technique_led(title)
        accepted = _identity_safe_for_semantics(verifications[index] if index < len(verifications) else None)
        shared_task = bool(_task_families(_problem_phrase(evidence.title))
                           & _task_families(_problem_phrase(title)))
        label, explanation = _classification(
            topical, method, context, domain_overlap=overlap,
            section_names=sections, context_snippets=contexts,
            crosses=crosses, has_abstract=bool(abstract), verified=accepted,
            paper_title=title, independent_method=independent_method,
            method_anchor_count=len(anchors),
            adopted_named_method=adopted and method_strength != "none",
            problem_similarity=problem_score, subject_overlap=specific_overlap,
            technique_led=technique_led, method_usage=method_usage,
            method_evidence_strength=method_strength, same_task_family=shared_task,
        )

        if not accepted and not abstract and label in {"directly_relevant", "methodologically_relevant"}:
            label = "partially_relevant"
            explanation = "The unverified title suggests a possible relationship, but no trustworthy paper metadata confirms it."

        sources = ["report_topic", "paper_title"]
        if evidence.literature:
            sources.append("literature_review")
        if evidence.methodology_passages:
            sources.append("report_methodology")
        if evidence.independent_methodology_passages:
            sources.append("uncited_report_methodology")
        if contexts:
            sources.append("citation_context")
        if abstract:
            sources.append("paper_abstract")
        if accepted:
            sources.append("verified_metadata")
        if sections:
            sources.append("citation_section")

        if not accepted:
            confidence = "low"
        elif status == "needs_review":
            # Unresolved bibliographic consistency caps confidence even if the
            # abstract and neural scores are compelling.
            confidence = "medium" if abstract and label not in {"insufficient_evidence", "possibly_unrelated"} else "low"
        elif (abstract and status == "verified" and
              ((label == "directly_relevant" and topical >= 0.62 and overlap >= 1) or
               (label == "methodologically_relevant" and independent_method is not None
                and independent_method >= 0.58 and len(anchors) >= 2))):
            confidence = "high"
        elif label in {"directly_relevant", "methodologically_relevant", "partially_relevant"} and abstract:
            confidence = "medium"
        elif label == "methodologically_relevant" and method_strength == "strong" and status == "verified":
            confidence = "medium"  # exact technique + verified identity, title-only
        else:
            confidence = "low"
        notes = ["Similarity and cross-encoder scores are uncalibrated, not probabilities or proof of claim support."]
        if not abstract:
            notes.append("No verified abstract was available; title-based relevance is tentative.")
        if not accepted:
            notes.append("Paper identity is unresolved; do not treat relevance as confirmed.")
        if not contexts:
            notes.append("No in-text citation context was linked to this bibliography entry.")
        if method is None:
            notes.append("No usable methodology text was extracted from the report.")
        if method is not None and independent_method is None:
            notes.append("No citation-independent methods passage was found; method relevance cannot be confirmed from citations alone.")
        if problem_score is not None:
            notes.append(f"Research-task clause similarity: {problem_score:.4f} (uncalibrated).")
        if technique_led:
            notes.append("Reference title emphasizes an algorithm or architecture; avoid treating broad task overlap as direct research relevance.")
        if usage_evidence:
            notes.append("Specific method-use evidence: " + "; ".join(usage_evidence))
        notes.extend(support_traces)
        if method_strength == "moderate" and support_traces:
            notes.append("Review the paper's full text before treating claimed adoption as proven.")
        if adopted and method_strength == "none":
            # A citation can *sound* like method adoption without proving that
            # this particular reference supplied a technique actually used.
            # Keep the useful diagnostic, but do not contradict the structured
            # `method_evidence_strength` / `method_evidence_traces` fields.
            notes.append(
                "A citation-level method-use cue was detected, but no "
                "reference-specific method adoption was corroborated; "
                "this cue was not counted as method-use evidence."
            )
        elif adopted:
            notes.append(
                "A citation-level method-use cue was detected and has "
                "corroborating method evidence; claim support by the cited "
                "paper is not independently verified."
            )
        if anchors:
            notes.append("Shared non-generic method anchors in uncited report text: " + ", ".join(sorted(anchors)[:8]))
        if context is not None and context >= 0.5 and label in {"possibly_unrelated", "insufficient_evidence", "partially_relevant"}:
            notes.append("Citation context sounds related, but the paper's independent evidence does not confirm that role; review possible citation misuse.")
        if sections:
            notes.append("Citation sections: " + ", ".join(dict.fromkeys(sections)))

        results.append(PaperRelevance(
            reference_index=index, paper_title=title or None,
            verification_status=status, classification=label, confidence=confidence,
            topical_similarity=round(topical, 4) if topical is not None else None,
            methodological_similarity=round(method, 4) if method is not None else None,
            citation_context_similarity=round(context, 4) if context is not None else None,
            independent_methodological_similarity=round(independent_method, 4) if independent_method is not None else None,
            research_problem_similarity=round(problem_score, 4) if problem_score is not None else None,
            research_problem_overlap=specific_overlap,
            technique_led=technique_led,
            method_use_evidence=usage_evidence,
            method_evidence_strength=method_strength,
            method_evidence_traces=support_traces,
            same_research_task_family=shared_task,
            evidence_reliability=("verified_metadata" if status == "verified" else
                                  "metadata_needs_review" if status == "needs_review" else
                                  "unverified_reference"),
            cross_encoder_scores=crosses,
            citation_lines=lines, evidence_sources=sources,
            explanation=explanation, notes=notes,
        ))
    return results
