"""Report similarity: one report against the rest of the cohort and the reference corpus.

Uses Surabhi's overlap_analyzer unchanged. The "reference corpus" it normally
builds from cited PDFs is built here from the OTHER students' reports plus any
reference documents, so the same near-verbatim / paraphrase categories apply.

Template text (certificate, declaration, acknowledgement wording that the
department gives everyone) is found automatically: a sentence that appears in a
large share of the cohort's reports is ignored. A department template file can
also be given explicitly.
"""
import logging
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .config import DEFAULT, IntegritySettings
from .contracts import Evidence
from .evidence import Signal
from .ria.config import DEFAULT_SETTINGS as RIA_DEFAULTS, Settings as RiaSettings
from .ria.evidence_analyzer import Passage, ReferenceCorpus
from .ria.overlap_analyzer import NEAR_VERBATIM, PARAPHRASE, analyze_overlap
from .ria.pdf_extractor import Document, Page, Section, Sentence, extract_document
from .ria.preprocessing import split_sentences

logger = logging.getLogger(__name__)

REPORT_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
_REF_HEADING = re.compile(r"^\s*(?:\d+\.?\s*)?(references|bibliography|works cited|reference list)\s*:?\s*$",
                          re.IGNORECASE)
REPORT_LIMITATION = ("Overlapping wording can come from shared sources, a common brief or "
                     "properly attributed material the parser did not recognise. A human must "
                     "read both passages before drawing any conclusion.")


# --------------------------------------------------------------------------- #
# Loading reports
# --------------------------------------------------------------------------- #
def _text_document(text: str, name: str) -> Document:
    """A Document for .docx/.txt/.md, which have no pages. Paragraph numbers stand in
    for page numbers so every quote still has a location."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    sentences: List[Sentence] = []
    section = "Body"
    for para_no, para in enumerate(paragraphs, start=1):
        if _REF_HEADING.match(para):
            section = "References"   # the reference list is never compared
            continue
        for sent in split_sentences(" ".join(para.split())):
            sentences.append(Sentence(len(sentences), sent, section, para_no, section))
    return Document(source=name, title=paragraphs[0][:120] if paragraphs else name,
                    pages=[Page(1, text)], sections=[Section("Body", "", 1, text)],
                    sentences=sentences)


def load_report(path: Path) -> Document:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return extract_document(path, name=path.name)
    if path.suffix.lower() == ".docx":
        import docx  # python-docx

        text = "\n\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return _text_document(text, path.name)


def location_label(doc: Document, sentence: Sentence) -> str:
    unit = "p." if doc.source.lower().endswith(".pdf") else "para "
    return f"{unit}{sentence.page}"


def load_folder(folder: Optional[Path]) -> Dict[str, Document]:
    """{id: Document} for every report in a folder. The id is the file stem, or the
    sub-folder name when each student has a folder."""
    docs: Dict[str, Document] = {}
    if not folder or not Path(folder).exists():
        return docs
    for entry in sorted(Path(folder).iterdir()):
        files = [entry] if entry.is_file() else sorted(p for p in entry.rglob("*") if p.is_file())
        files = [f for f in files if f.suffix.lower() in REPORT_SUFFIXES]
        if not files:
            continue
        sid = entry.stem if entry.is_file() else entry.name
        try:
            docs[sid] = load_report(files[0])
        except Exception as exc:  # noqa: BLE001 - one unreadable report must not stop the cohort
            logger.error("Could not read report %s: %s", files[0], exc)
    return docs


# --------------------------------------------------------------------------- #
# Template text
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def template_sentences(cohort: Dict[str, Document], template: Optional[Document] = None,
                       settings: IntegritySettings = DEFAULT) -> Set[str]:
    """Normalised sentences shared by many reports (or present in the template)."""
    counts: Counter = Counter()
    for doc in cohort.values():
        counts.update({_norm(s.text) for s in doc.sentences})
    need = max(settings.template_min_reports, int(settings.template_share * len(cohort) + 0.999))
    shared = {s for s, c in counts.items() if c >= need and s}
    if template is not None:
        shared |= {_norm(s.text) for s in template.sentences}
    return shared


def _without(doc: Document, skip: Set[str]) -> Document:
    kept = [s for s in doc.sentences if _norm(s.text) not in skip]
    return replace(doc, sentences=[replace(s, index=i) for i, s in enumerate(kept)])


def source_sentences(doc: Document) -> List[Sentence]:
    """Sentences of a source document that can be copied from. Unlike the student's
    document, a source's title page and abstract count too; only its own reference
    list is left out."""
    return [s for s in doc.sentences if s.section != "References"]


def _corpus(sources: Dict[str, Document], skip: Set[str]) -> ReferenceCorpus:
    sentences = []
    for name, doc in sources.items():
        for s in source_sentences(doc):
            if _norm(s.text) not in skip:
                sentences.append(Passage(f"{name}#s{s.index}", name, None, s.section, s.page, s.text))
    # overlap_analyzer only needs sentence-level passages; reuse them as passages too.
    return ReferenceCorpus(passages=list(sentences), file_to_ref={n: None for n in sources},
                           titles={n: d.title for n, d in sources.items()}, sentences=sentences)


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def report_signals(sid: str, cohort: Dict[str, Document], references: Dict[str, Document],
                   template: Optional[Document] = None, settings: IntegritySettings = DEFAULT,
                   ria_settings: RiaSettings = RIA_DEFAULTS,
                   shared: Optional[Set[str]] = None) -> List[Signal]:
    """Signals for one report: one per other report or reference document it overlaps with."""
    if sid not in cohort:
        return []
    shared = shared if shared is not None else template_sentences(cohort, template, settings)
    target = _without(cohort[sid], shared)
    sources = {f"cohort:{k}": v for k, v in cohort.items() if k != sid}
    sources.update({f"corpus:{k}": v for k, v in references.items()})
    corpus = _corpus(sources, shared)
    if not corpus.sentences or not target.sentences:
        return []
    results = analyze_overlap(target, corpus, ria_settings)
    flagged = [r for r in results if r.category in (NEAR_VERBATIM, PARAPHRASE)]
    n_eligible = max(1, len(results))
    by_source: Dict[str, list] = {}
    for r in flagged:
        by_source.setdefault(r.reference_file, []).append(r)

    signals = []
    for source, hits in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
        if len(hits) < settings.min_flagged_sentences:
            continue
        kind, name = source.split(":", 1)
        src_doc = sources[source]
        near = sum(h.category == NEAR_VERBATIM for h in hits)
        para = len(hits) - near
        evidence = []
        hits.sort(key=lambda h: (h.category != NEAR_VERBATIM, -h.ngram_jaccard, -h.embedding_cosine))
        for h in hits[:settings.max_pairs_quoted]:
            mine = target.sentences[h.sentence_index] if h.sentence_index < len(target.sentences) else None
            loc = location_label(target, mine) if mine else f"sentence {h.sentence_index}"
            theirs = next((s for s in src_doc.sentences if s.text == h.reference_text), None)
            their_loc = location_label(src_doc, theirs) if theirs else "?"
            evidence.append(Evidence(source="report", location=f"{sid} report {loc} ({h.category})",
                                     quote=h.paper_text))
            evidence.append(Evidence(source=kind, location=f"{name} {their_loc}", quote=h.reference_text))
        what = "another student's report" if kind == "cohort" else "a reference document"
        signals.append(Signal(
            kind="similar_report_text",
            summary=(f"{len(hits)} sentence(s) overlap with {what} ({name}): {near} near-verbatim, "
                     f"{para} possible paraphrase; shared template text excluded."),
            evidence=evidence,
            scores={"near_verbatim": float(near), "paraphrase": float(para),
                    "share_of_report": len(hits) / n_eligible,
                    "lexical_backend": float(not any(h.embedding_backend == "sentence-transformers"
                                                     for h in hits))},
            limitations=REPORT_LIMITATION + (
                " Embeddings were not available, so paraphrase detection was lexical only."
                if not any(h.embedding_backend == "sentence-transformers" for h in hits) else ""),
        ))
    return signals
