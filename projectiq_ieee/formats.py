"""Multi-format entry point for ProjectIQ's explainable IEEE-style checker.

PDF: inspect rendered geometry with PyMuPDF.
DOCX: inspect Word section settings, effective font styles and document text.
TXT/MD: assess textual paper structure only; visual rules are not scoreable.
DOC/RTF/ODT: use optional, locally installed LibreOffice for PDF conversion.
No files are uploaded to external services.
"""

from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Optional, Union

from .checker import (
    Check, _report_from_checks, analyze_pdf, _looks_serif, CATEGORY_WEIGHTS,
    _SECTION_RE, _REF_ENTRY_RE, _CITATION_RE, _ABSTRACT_RE, _KEYWORDS_RE,
    _FIG_CAPTION_RE, _TAB_CAPTION_RE, _FIG_MENTION_RE, _TAB_MENTION_RE,
)

SUPPORTED_EXTENSIONS = ("pdf", "docx", "doc", "txt", "md", "rtf", "odt")
_OFFICE_CONVERSION_EXTENSIONS = {".doc", ".rtf", ".odt"}
_TEXT_EXTENSIONS = {".txt", ".md"}
_MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _add(checks, category, criterion, earned, possible, detail, suggestion="", applicable=True):
    checks.append(Check(category, criterion, earned, possible, detail, suggestion, None, applicable))


def _clean_text_line(line: str) -> str:
    # Supports markdown headings as well as plain headings.
    return re.sub(r"^\s{0,3}#{1,6}\s*", "", line).strip().strip("* ")


def _check_paper_structure(text_lines: list[str], checks: list[Check], *, title_mode: str):
    """Score parseable structure without pretending to have PDF coordinates."""
    raw = [l.strip() for l in text_lines if l.strip()]
    lines = [_clean_text_line(l) for l in raw]
    if len(re.sub(r"\s", "", "\n".join(lines))) < 120:
        raise ValueError("The document contains too little extractable text (at least 120 non-space characters are required).")

    first_abstract = next((i for i, line in enumerate(lines) if _ABSTRACT_RE.match(line)), None)
    first_keywords = next((i for i, line in enumerate(lines) if _KEYWORDS_RE.match(line)), None)
    heading_indices = [i for i, line in enumerate(lines) if _SECTION_RE.match(line)]
    intro = next((i for i in heading_indices if "introduction" in lines[i].lower()), None)
    references = next((i for i, line in enumerate(lines)
                       if re.match(r"^\s*(?:[IVX]+\.\s*)?references\s*$", line, re.I)), None)

    first = lines[0] if lines else ""
    first_is_title = bool(first and not _ABSTRACT_RE.match(first)
                          and not _KEYWORDS_RE.match(first)
                          and not _SECTION_RE.match(first))
    if title_mode == "plain":
        _add(checks, "Title and authors", "Title text on first line", 6 if first_is_title else 0, 6,
             f"Potential title: {first[:100]}" if first_is_title else "No plausible title line found.",
             "Place the research-paper title at the start of the document.")
    # DOCX title is checked separately from the Word paragraph formatting.
    if title_mode == "plain":
        author_lines = lines[1:first_abstract] if first_abstract is not None else lines[1:4]
        author_present = first_is_title and any(
            len(l) >= 5 and not _SECTION_RE.match(l) and not _KEYWORDS_RE.match(l) for l in author_lines
        )
        _add(checks, "Title and authors", "Author / affiliation text", 4 if author_present else 0, 4,
             "Text found between the title and abstract." if author_present else "No likely author/affiliation line identified.",
             "Add author names and affiliations below the title.")

    _add(checks, "Abstract and keywords", "Abstract label", 8 if first_abstract is not None else 0, 8,
         "Abstract label found." if first_abstract is not None else "Abstract label not found.",
         "Include an Abstract paragraph following the chosen IEEE template.")
    _add(checks, "Abstract and keywords", "Index Terms / Keywords label", 7 if first_keywords is not None else 0, 7,
         "Index Terms/Keywords label found." if first_keywords is not None else "Index Terms/Keywords label not found.",
         "Include an 'Index Terms—' line beneath the abstract.")
    _add(checks, "Section structure", "Numbered main sections",
         6 if len(heading_indices) >= 3 else 3 if len(heading_indices) >= 2 else 0, 6,
         f"{len(heading_indices)} numbered main section(s) detected.",
         "Use headings such as 'I. INTRODUCTION', 'II. METHODS', 'III. RESULTS'.")
    _add(checks, "Section structure", "Introduction section", 4 if intro is not None else 0, 4,
         "Introduction section found." if intro is not None else "Introduction heading not found.",
         "Add an Introduction section using a consistent section-heading style.")

    before_refs = lines if references is None else lines[:references]
    fig_mentions = [line for line in before_refs if _FIG_MENTION_RE.search(line)]
    table_mentions = [line for line in before_refs if _TAB_MENTION_RE.search(line)]
    fig_captions = [line for line in before_refs if _FIG_CAPTION_RE.match(line)]
    table_captions = [line for line in before_refs if _TAB_CAPTION_RE.match(line)]
    if fig_mentions or table_mentions:
        if fig_mentions and table_mentions:
            caption_points = (5 if fig_captions else 0) + (5 if table_captions else 0)
        elif fig_mentions:
            caption_points = 10 if fig_captions else 0
        else:
            caption_points = 10 if table_captions else 0
        _add(checks, "Figures and tables", "Figure and table captions", caption_points, 10,
             f"Found {len(fig_captions)} figure and {len(table_captions)} table caption(s).",
             "Provide numbered captions for figures and tables. Check their physical placement manually.")
    else:
        _add(checks, "Figures and tables", "Figure and table captions", 0, 10,
             "No figure/table mentions detected; category excluded.", applicable=False)

    entries = [line for line in lines[references + 1:] if _REF_ENTRY_RE.match(line)] if references is not None else []
    cites = [line for line in before_refs if _CITATION_RE.search(line)]
    _add(checks, "References and citations", "References heading", 4 if references is not None else 0, 4,
         "References heading found." if references is not None else "References heading not found.",
         "Add a References section at the end of the paper.")
    _add(checks, "References and citations", "Numbered bibliography entries", 4 if entries else 0, 4,
         f"{len(entries)} numbered reference entry/entries found.",
         "Use IEEE-style numbered reference entries such as [1] and [2].")
    _add(checks, "References and citations", "Bracketed in-text citations", 2 if cites else 0, 2,
         f"{len(cites)} line(s) contain bracketed citations before the reference list.",
         "Cite your sources in the main text using [1], [2], etc.")


def _text_decode(data: bytes) -> str:
    if not data:
        raise ValueError("The uploaded text file is empty.")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise ValueError("This does not look like a plain text file. Save as UTF-8 TXT or DOCX instead.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Could not decode the text file. Save it as UTF-8 TXT.")


def _analyze_text(data: bytes, name: str):
    lines = _text_decode(data).splitlines()
    checks = []
    for category, criterion, possible in (
        ("Page layout", "A4 or US Letter page size", 8),
        ("Page layout", "Two-column body layout", 14),
        ("Page layout", "Plausible and balanced outer margins", 3),
        ("Typography", "Body text near 10 pt", 12),
        ("Typography", "Serif/Times-like body font", 8),
    ):
        _add(checks, category, criterion, 0, possible,
             "Plain text has no page geometry, columns, margins, or font formatting.", applicable=False)
    _check_paper_structure(lines, checks, title_mode="plain")
    notes = [
        "This is a TEXT STRUCTURE score, not an IEEE layout/format-compliance score. Plain text does not store page dimensions, columns, margins, or fonts.",
        "Export a formatted DOCX or PDF for a fuller formatting assessment.",
        "For submission, validate the specific conference/journal template and run official PDF compliance checks separately.",
    ]
    return _report_from_checks(
        name, None, checks, notes,
        layout_verdict="Column layout unavailable for this format",
        source_format=Path(name).suffix.lstrip(".").upper(),
        analysis_method="Plain-text structure parsing",
        assessment_scope="structure_only",
    )


def _get_effective_style_attr(style, attribute):
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        result = getattr(style.font, attribute)
        if result is not None:
            return result
        style = style.base_style
    return None


def _font_value(paragraph, run, attribute, normal_style):
    if getattr(run.font, attribute) is not None:
        return getattr(run.font, attribute)
    if run.style is not None:
        value = _get_effective_style_attr(run.style, attribute)
        if value is not None:
            return value
    value = _get_effective_style_attr(paragraph.style, attribute)
    return value if value is not None else _get_effective_style_attr(normal_style, attribute)


def _section_column_count(section) -> int:
    from docx.oxml.ns import qn
    columns = section._sectPr.find(qn("w:cols"))
    if columns is None:
        return 1
    try:
        return max(1, int(columns.get(qn("w:num"), "1")))
    except ValueError:
        return 1


def _analyze_docx(data: bytes, name: str):
    try:
        from docx import Document
        document = Document(BytesIO(data))
    except Exception as exc:
        raise ValueError("Could not read DOCX. Verify that it is a valid, unlocked Word .docx file.") from exc
    paragraphs = [p for p in document.paragraphs if p.text.strip()]
    # Include text inside table cells for textual structure checks.
    structured = [p.text for p in paragraphs]
    for table in document.tables:
        for row in table.rows:
            structured.extend(cell.text for cell in row.cells if cell.text.strip())
    if len(re.sub(r"\s", "", "\n".join(structured))) < 120:
        raise ValueError("The Word document contains too little extractable text (at least 120 non-space characters are required).")
    checks: list[Check] = []
    notes = [
        "DOCX assessment reads Word section and paragraph settings; it does not render pages, so visual placement and actual page count are not verified.",
        "Two-column detection is based on Word section settings, not proof that body text visually occupies both columns.",
        "For best visual accuracy, export from Word to PDF and run the PDF checker as well.",
        "Exact conference/journal formatting depends on the venue-specific IEEE template.",
    ]

    sections = list(document.sections)
    page_sizes = []
    for sec in sections:
        w, h = sorted([sec.page_width.pt, sec.page_height.pt])
        valid = ((abs(w - 595.28) < 5 and abs(h - 841.89) < 5)
                 or (abs(w - 612) < 5 and abs(h - 792) < 5))
        page_sizes.append(valid and sec.page_width.pt < sec.page_height.pt)
    _add(checks, "Page layout", "A4 or US Letter page size",
         8 if page_sizes and all(page_sizes) else 4 if any(page_sizes) else 0, 8,
         f"{sum(page_sizes)}/{len(sections)} Word section(s) use A4/US Letter portrait settings.",
         "Set page size according to your target IEEE Word template.")
    counts = [_section_column_count(s) for s in sections]
    multicolumn = any(n >= 2 for n in counts)
    _add(checks, "Page layout", "Two-column body layout", 14 if multicolumn else 0, 14,
         f"Word section column settings: {counts}. " + (
             "At least one section specifies two or more columns; body placement is unverified."
             if multicolumn else "No Word section specifies multiple text columns."),
         "Use the two-column section settings in the IEEE Word conference template.")
    margins = [(s.left_margin.pt, s.right_margin.pt) for s in sections
               if s.left_margin is not None and s.right_margin is not None]
    if margins:
        acceptable = [25 <= l <= 90 and 25 <= r <= 90 and abs(l-r) < 35 for l, r in margins]
        pts = 3 if all(acceptable) else 1 if any(acceptable) else 0
        _add(checks, "Page layout", "Plausible and balanced outer margins", pts, 3,
             f"Word section left/right margins (pt): {[(round(l), round(r)) for l, r in margins]}.",
             "Use the margins from the official template; don't assume these heuristic ranges are official IEEE values.")
    else:
        _add(checks, "Page layout", "Plausible and balanced outer margins", 0, 3,
             "Margin values are not present in the document properties.", applicable=False)

    normal = document.styles["Normal"]
    # Exclude obvious title, labels, headings and bibliography from the body font sample.
    ref_start = next((i for i, p in enumerate(paragraphs) if re.match(r"^\s*(?:[IVX]+\.\s*)?references\s*$", p.text.strip(), re.I)), None)
    samples = []
    for i, paragraph in enumerate(paragraphs):
        text = paragraph.text.strip()
        if (i <= 1 or (ref_start is not None and i >= ref_start)
            or _SECTION_RE.match(text) or _ABSTRACT_RE.match(text)
            or _KEYWORDS_RE.match(text) or _FIG_CAPTION_RE.match(text)
            or _TAB_CAPTION_RE.match(text) or len(text) < 35):
            continue
        for run in paragraph.runs:
            n = len(run.text.strip())
            if not n:
                continue
            size = _font_value(paragraph, run, "size", normal)
            font = _font_value(paragraph, run, "name", normal)
            samples.append((n, round(size.pt, 1) if size else None, font))
    size_counts = Counter()
    font_counts = Counter()
    for n, size, font in samples:
        if size:
            size_counts[size] += n
        if font:
            font_counts[font] += n
    if size_counts:
        size = size_counts.most_common(1)[0][0]
        _add(checks, "Typography", "Body text near 10 pt",
             12 if 9.5 <= size <= 10.5 else 8 if 9 <= size <= 11 else 0, 12,
             f"Predominant explicit/inherited Word body font size: {size:g} pt.",
             "Use the body text style in the IEEE conference Word template.")
    else:
        _add(checks, "Typography", "Body text near 10 pt", 0, 12,
             "No resolved body font sizes found in Word styles or run formatting.", applicable=False)
    if font_counts:
        font_name = font_counts.most_common(1)[0][0]
        _add(checks, "Typography", "Serif/Times-like body font", 8 if _looks_serif(font_name) else 0, 8,
             f"Predominant resolved Word body font: {font_name}.",
             "Prefer the font defined in the official IEEE template.")
    else:
        _add(checks, "Typography", "Serif/Times-like body font", 0, 8,
             "No resolved body font name found in Word styles or run formatting.", applicable=False)

    title_para = next((p for p in paragraphs if not _ABSTRACT_RE.match(p.text.strip())
                       and not _KEYWORDS_RE.match(p.text.strip())
                       and not _SECTION_RE.match(p.text.strip())), None)
    if title_para:
        title_sizes = [v.pt for run in title_para.runs
                       if (v := _font_value(title_para, run, "size", normal)) is not None]
        large = (max(title_sizes) >= 13.5 if title_sizes else False)
        named_style = "title" in title_para.style.name.lower()
        points = 6 if large or named_style else 3
        title_detail = f"First likely title: {title_para.text[:100]}. " + (
            "Word title styling detected." if points == 6 else "Title prominence could not be confirmed.")
    else:
        points, title_detail = 0, "No likely title paragraph found."
    _add(checks, "Title and authors", "Prominent first-page title", points, 6,
         title_detail, "Use the IEEE Word title style and confirm prominence in exported PDF.")
    author_paras = []
    if title_para is not None:
        idx = paragraphs.index(title_para)
        for p in paragraphs[idx + 1:idx + 5]:
            if _ABSTRACT_RE.match(p.text.strip()) or _KEYWORDS_RE.match(p.text.strip()) or _SECTION_RE.match(p.text.strip()):
                break
            author_paras.append(p)
    _add(checks, "Title and authors", "Author / affiliation block", 4 if author_paras else 0, 4,
         "Text appears between the title and first paper section/abstract." if author_paras else "No likely author/affiliation text found beneath the title.",
         "Include all authors and affiliations below the title.")
    _check_paper_structure(structured, checks, title_mode="docx")
    return _report_from_checks(
        name, None, checks, notes,
        layout_verdict=("Two-column section configured (visual layout unverified)" if multicolumn
                        else "Two-column section NOT configured"),
        source_format="DOCX",
        analysis_method="Native DOCX structure and formatting properties",
        assessment_scope="style_and_structure",
    )


def _convert_office_to_pdf(data: bytes, suffix: str) -> bytes:
    program = shutil.which("soffice") or shutil.which("libreoffice")
    if not program:
        raise RuntimeError(
            f"Reading {suffix.upper()} files requires LibreOffice installed locally and available on PATH. "
            "Alternatively open the file in Microsoft Word and save as DOCX or export as PDF."
        )
    with tempfile.TemporaryDirectory(prefix="projectiq-ieee-") as temp:
        root = Path(temp)
        src = root / ("input" + suffix)
        src.write_bytes(data)
        output = root / "converted"
        output.mkdir()
        profile = (root / "lo-profile").as_uri()
        try:
            result = subprocess.run(
                [program, f"-env:UserInstallation={profile}", "--headless", "--convert-to",
                 "pdf:writer_pdf_Export", "--outdir", str(output), str(src)],
                capture_output=True, text=True, timeout=75, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOffice conversion timed out. Try saving your file as DOCX/PDF first.") from exc
        pdf = output / "input.pdf"
        if result.returncode != 0 or not pdf.exists() or pdf.stat().st_size == 0:
            raise RuntimeError("LibreOffice could not convert this document. Try resaving it as DOCX or PDF.")
        return pdf.read_bytes()


def analyze_document(source: Union[str, Path, bytes, bytearray], filename: Optional[str] = None):
    """Analyze PDF, DOCX, DOC, RTF, ODT, TXT or MD by path or uploaded bytes.

    Byte input requires a filename (and extension) to determine the document format.
    """
    if isinstance(source, (bytes, bytearray)):
        name = filename or "uploaded.pdf"  # Backwards-friendly default for raw PDFs.
        data = bytes(source)
    else:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"No such document: {path}")
        name = filename or path.name
        if path.stat().st_size > _MAX_UPLOAD_BYTES:
            raise ValueError("Document is too large (maximum 30 MB).")
        data = path.read_bytes()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ValueError("Document is too large (maximum 30 MB).")
    ext = Path(name).suffix.lower()
    if ext.lstrip(".") not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported document format. Upload PDF, DOCX, DOC, TXT, MD, RTF or ODT.")
    if ext == ".pdf":
        return analyze_pdf(data, filename=name)
    if ext == ".docx":
        return _analyze_docx(data, name)
    if ext in _TEXT_EXTENSIONS:
        return _analyze_text(data, name)
    if ext in _OFFICE_CONVERSION_EXTENSIONS:
        pdf_data = _convert_office_to_pdf(data, ext)
        report = analyze_pdf(pdf_data, filename=name)
        report["source_format"] = ext.lstrip(".").upper()
        report["analysis_method"] = "LibreOffice-converted PDF layout inspection"
        report["notes"].insert(0, f"This {ext.upper()} file was temporarily rendered to PDF by LibreOffice. PDF layout can differ slightly from Microsoft Word.")
        return report
    raise ValueError(f"Unsupported format: {ext}")
