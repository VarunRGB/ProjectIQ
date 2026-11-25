"""Reading a student's document and its reference papers.

PDF files go through Research Integrity Analyzer's extractor (sections, pages,
reference list). .docx, .txt and .md have no pages, so paragraph numbers are used
as locations, and a "References" or "Bibliography" heading starts the reference list.
"""
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from .ria.evidence_analyzer import Passage, ReferenceCorpus
from .ria.pdf_extractor import Document, Page, Section, Sentence, extract_document
from .ria.preprocessing import split_sentences
from projectiq_research.section_detector import REFERENCES_ALIASES  # read-only: same headings as the research agent

logger = logging.getLogger(__name__)

DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def is_references_heading(line: str) -> bool:
    """True for a References / Bibliography heading ("6. References", "VII. REFERENCES:").

    Uses the research agent's list of heading names, so both agents agree on where
    the reference list starts. The research agent's code is not changed."""
    text = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?\s+", "", line.strip(), flags=re.IGNORECASE)
    text = re.sub(r"[^a-z ]", " ", text.lower())
    return " ".join(text.split()) in REFERENCES_ALIASES


def _text_document(text: str, name: str) -> Document:
    """A Document for .docx/.txt/.md, which have no pages. Paragraph numbers stand in
    for page numbers so every quote still has a location."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    sentences: List[Sentence] = []
    section = "Body"
    for para_no, para in enumerate(paragraphs, start=1):
        if is_references_heading(para):
            section = "References"   # the reference list is never compared
            continue
        for sent in split_sentences(" ".join(para.split())):
            sentences.append(Sentence(len(sentences), sent, section, para_no, section))
    return Document(source=name, title=paragraphs[0][:120] if paragraphs else name,
                    pages=[Page(1, text)], sections=[Section("Body", "", 1, text)],
                    sentences=sentences)


def load_document(path: Path) -> Document:
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
    """{name: Document} for every document in a folder (the reference papers). The name
    is the file name without its extension; a sub-folder counts as one document."""
    docs: Dict[str, Document] = {}
    if not folder or not Path(folder).exists():
        return docs
    for entry in sorted(Path(folder).iterdir()):
        files = [entry] if entry.is_file() else sorted(p for p in entry.rglob("*") if p.is_file())
        files = [f for f in files if f.suffix.lower() in DOCUMENT_SUFFIXES]
        if not files:
            continue
        sid = entry.stem if entry.is_file() else entry.name
        try:
            docs[sid] = load_document(files[0])
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the check
            logger.error("Could not read document %s: %s", files[0], exc)
    return docs


def source_sentences(doc: Document) -> List[Sentence]:
    """Sentences of a source document that can be copied from. Unlike the student's
    document, a source's title page and abstract count too; only its own reference
    list is left out."""
    return [s for s in doc.sentences if s.section != "References"]


def build_corpus(sources: Dict[str, Document]) -> ReferenceCorpus:
    """All copyable sentences of the reference documents, in the form overlap_analyzer reads."""
    sentences = []
    for name, doc in sources.items():
        for s in source_sentences(doc):
            sentences.append(Passage(f"{name}#s{s.index}", name, None, s.section, s.page, s.text))
    # overlap_analyzer only needs sentence-level passages; reuse them as passages too.
    return ReferenceCorpus(passages=list(sentences), file_to_ref={n: None for n in sources},
                           titles={n: d.title for n, d in sources.items()}, sentences=sentences)
