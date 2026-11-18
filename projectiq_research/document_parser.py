"""Load supported report formats while retaining hyperlinks and heading hints."""

from pathlib import Path
import re
from typing import List, Tuple

from .models import Hyperlink, ParsedDocument
from .url_utils import extend_wrapped_url

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\]\[(){}]+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?\"'`"


def _clean_url(url: str) -> str:
    return url.rstrip(_TRAILING_URL_PUNCTUATION)


def _is_explicit_heading(line: str) -> bool:
    value = line.strip()
    if not value:
        return False
    if re.match(r"^#{1,6}\s+\S", value):
        return True
    if re.match(r"^\s*\d+(?:\.\d+)*[.)]?\s+[A-Z][^.!?]{0,100}$", value):
        return True
    # All-caps standalone headings are common in PDF exports.
    letters = [ch for ch in value if ch.isalpha()]
    return bool(letters) and len(value) <= 90 and len(value.split()) <= 10 and all(ch.isupper() for ch in letters)


def _links_in_text(text: str, line_offset: int = 0) -> List[Hyperlink]:
    found: List[Hyperlink] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        for match in _MARKDOWN_LINK_RE.finditer(line):
            found.append(Hyperlink(match.group(1).strip(), _clean_url(match.group(2)),
                                   line_offset + line_number, "markdown"))
        # URLs may also be pasted without Markdown link syntax.
        for match in _URL_RE.finditer(line):
            raw_url = match.group(0)
            if match.end() == len(line.rstrip()):
                raw_url = extend_wrapped_url(raw_url, lines, line_number - 1)
            url = _clean_url(raw_url)
            if not any(item.url == url and item.line_number == line_offset + line_number for item in found):
                found.append(Hyperlink(url, url, line_offset + line_number, "text"))
    return found


def _deduplicate_links(links: List[Hyperlink]) -> List[Hyperlink]:
    seen = set()
    result = []
    for link in links:
        key = (link.url.rstrip("/"), link.line_number)
        if key not in seen:
            seen.add(key)
            result.append(link)
    return result


def _load_text_file(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8", errors="replace")
    heading_lines = {i for i, line in enumerate(text.splitlines(), start=1) if _is_explicit_heading(line)}
    return ParsedDocument(str(path), text, _links_in_text(text), heading_lines)


def _load_docx(path: Path) -> ParsedDocument:
    try:
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise ImportError("Reading .docx files requires python-docx: pip install python-docx") from exc

    document = Document(str(path))
    lines: List[str] = []
    heading_lines = set()
    hyperlinks: List[Hyperlink] = []

    # Walk body paragraphs in document order; this includes paragraphs inside tables.
    for element in document.element.body.iter(qn("w:p")):
        paragraph = Paragraph(element, document)
        line_number = len(lines) + 1
        paragraph_text = "".join(node.text or "" for node in element.xpath(".//w:t"))
        lines.append(paragraph_text)

        style_name = (paragraph.style.name or "") if paragraph.style is not None else ""
        if style_name.lower().startswith(("heading", "title", "subtitle")):
            heading_lines.add(line_number)
        elif _is_explicit_heading(paragraph_text):
            heading_lines.add(line_number)

        for link_element in element.xpath(".//w:hyperlink"):
            relationship_id = link_element.get(qn("r:id"))
            if not relationship_id:
                continue
            relationship = document.part.rels.get(relationship_id)
            if relationship is None:
                continue
            label = "".join(node.text or "" for node in link_element.xpath(".//w:t"))
            target = str(relationship.target_ref)
            if target:
                hyperlinks.append(Hyperlink(label or target, target, line_number, "docx"))

    text = "\n".join(lines)
    hyperlinks.extend(_links_in_text(text))
    return ParsedDocument(str(path), text, _deduplicate_links(hyperlinks), heading_lines)


def _load_pdf(path: Path) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise ImportError("Reading PDF files requires pypdf: pip install pypdf") from exc

    reader = PdfReader(str(path))
    page_texts: List[str] = []
    heading_lines = set()
    hyperlinks: List[Hyperlink] = []
    current_line = 1

    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        page_texts.append(page_text)
        for offset, line in enumerate(page_text.splitlines()):
            if _is_explicit_heading(line):
                heading_lines.add(current_line + offset)

        # pypdf exposes clickable PDF annotations through /Annots and /A /URI.
        for annotation_ref in page.get("/Annots", []) or []:
            try:
                annotation = annotation_ref.get_object()
                action = annotation.get("/A")
                url = action.get("/URI") if action else None
                if url:
                    hyperlinks.append(Hyperlink(str(url), str(url), current_line, f"pdf-page-{page_number}"))
            except (AttributeError, KeyError, TypeError):
                continue
        current_line += len(page_text.splitlines()) + 1

    text = "\n\n".join(page_texts)
    hyperlinks.extend(_links_in_text(text))
    return ParsedDocument(str(path), text, _deduplicate_links(hyperlinks), heading_lines)


def load_document(path: str | Path) -> ParsedDocument:
    """Load a TXT, Markdown, PDF or DOCX report.

    Text extraction is best-effort: scanned PDFs without an OCR text layer may
    produce little or no text. OCR is deliberately outside Phase 1.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    extension = file_path.suffix.lower()
    if extension in {".txt", ".md", ""}:
        return _load_text_file(file_path)
    if extension == ".docx":
        return _load_docx(file_path)
    if extension == ".pdf":
        return _load_pdf(file_path)
    raise ValueError(f"Unsupported file type '{extension}'. Use .txt, .md, .pdf or .docx")
