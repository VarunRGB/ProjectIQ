"""Heuristic, explainable IEEE conference-paper formatting assessment.

This is a capstone demo rubric, not IEEE certification. It inspects PDF layout,
not just extracted text. It intentionally refrains from scoring venue-specific
rules (page limit, exact margins, copyright notices) without a venue profile.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Optional, Union
import re

import pymupdf as fitz


CATEGORY_WEIGHTS = {
    "Page layout": 25,
    "Typography": 20,
    "Title and authors": 10,
    "Abstract and keywords": 15,
    "Section structure": 10,
    "Figures and tables": 10,
    "References and citations": 10,
}

_SECTION_RE = re.compile(r"^\s*(?:(?:[IVX]{1,6}|\d+)\s*[.\-]\s+)[A-Za-z][A-Za-z\s\-]{2,80}$", re.I)
_REF_ENTRY_RE = re.compile(r"^\s*\[\d{1,3}\]\s*\S+")
_CITATION_RE = re.compile(r"\[(?:\d{1,3})(?:\s*[-,]\s*\d{1,3})*\]")
_ABSTRACT_RE = re.compile(r"^\s*abstract\b", re.I)
_KEYWORDS_RE = re.compile(r"^\s*(?:index\s+terms|key\s*words)\b", re.I)
_FIG_CAPTION_RE = re.compile(r"^\s*fig(?:ure)?\.?\s*\d+\s*[.:-]\s*\S+", re.I)
_TAB_CAPTION_RE = re.compile(r"^\s*table\s+(?:[IVX]+|\d+)\b", re.I)
_FIG_MENTION_RE = re.compile(r"\bfig(?:ure)?\.?\s*\d+\b", re.I)
_TAB_MENTION_RE = re.compile(r"\btable\s+(?:[IVX]+|\d+)\b", re.I)


@dataclass
class Check:
    category: str
    criterion: str
    earned: float
    possible: int
    detail: str
    suggestion: str = ""
    page: Optional[int] = None
    applicable: bool = True

    @property
    def status(self) -> str:
        if not self.applicable:
            return "not_applicable"
        if self.earned >= self.possible:
            return "pass"
        if self.earned > 0:
            return "warning"
        return "fail"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status
        return result


@dataclass
class TextLine:
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    char_count: int


def _read_lines(document: fitz.Document) -> list[TextLine]:
    lines: list[TextLine] = []
    for page_no, page in enumerate(document, start=1):
        data = page.get_text("dict", sort=True)
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for item in block.get("lines", []):
                spans = [span for span in item.get("spans", []) if span.get("text", "").strip()]
                if not spans:
                    continue
                text = "".join(span["text"] for span in spans).strip()
                if not text:
                    continue
                n = sum(len(s["text"].strip()) for s in spans)
                dominant = max(spans, key=lambda s: len(s["text"].strip()))
                lines.append(TextLine(
                    page=page_no, text=text,
                    x0=min(float(s["bbox"][0]) for s in spans),
                    y0=min(float(s["bbox"][1]) for s in spans),
                    x1=max(float(s["bbox"][2]) for s in spans),
                    y1=max(float(s["bbox"][3]) for s in spans),
                    size=float(dominant["size"]),
                    font=str(dominant["font"]),
                    char_count=n,
                ))
    return lines


def _looks_serif(font_name: str) -> bool:
    f = re.sub(r"^[A-Z]{6}\+", "", font_name).lower().replace(" ", "").replace("-", "")
    return any(token in f for token in (
        "times", "roman", "term", "nimbusrom", "liberationserif",
        "cmr", "centuryschlbk", "palatino", "texgyre", "stix", "serif"
    ))


def _main_text_lines(lines: list[TextLine], document: fitz.Document) -> list[TextLine]:
    body = []
    for item in lines:
        page = document[item.page - 1]
        if (item.char_count >= 25 and 8 <= item.size <= 12.5
                and item.y0 > (105 if item.page == 1 else 32)
                and item.y1 < page.rect.height - 30):
            body.append(item)
    return body


def _score_page_size(document: fitz.Document) -> tuple[float, str]:
    sizes = []
    for page in document:
        w, h = sorted((page.rect.width, page.rect.height))
        a4 = abs(w - 595.28) < 5 and abs(h - 841.89) < 5
        letter = abs(w - 612.0) < 5 and abs(h - 792.0) < 5
        sizes.append((a4 or letter) and page.rect.width < page.rect.height)
    count = sum(sizes)
    if count == len(document):
        return 8, f"All {len(document)} pages use A4 or US Letter dimensions."
    if count:
        return 4, f"Only {count} of {len(document)} pages have A4/Letter dimensions."
    return 0, "Page dimensions do not match A4 or US Letter."


def _score_columns(body: list[TextLine], document: fitz.Document) -> tuple[float, str, Optional[int]]:
    if len(body) < 8:
        return 5, "Too little body text for a reliable two-column assessment.", None
    count_good = 0
    inspected = 0
    example_page = None
    for page_number in sorted({line.page for line in body}):
        sample = [line for line in body if line.page == page_number]
        if len(sample) < 8:
            continue
        inspected += 1
        mid = document[page_number - 1].rect.width / 2
        left = sum(1 for line in sample if line.x1 < mid - 5)
        right = sum(1 for line in sample if line.x0 > mid + 5)
        if left >= 3 and right >= 3:
            count_good += 1
            if example_page is None:
                example_page = page_number
    if count_good:
        return 14, f"Two-column body layout detected on {count_good} page(s).", example_page
    if inspected == 0:
        return 5, "Not enough body paragraphs for reliable column detection.", None
    return 0, "Could not detect two text columns in the body; this may be single-column.", 1


def _score_margins(body: list[TextLine], document: fitz.Document) -> tuple[float, str]:
    left_edges = []
    right_edges = []
    for item in body:
        w = document[item.page - 1].rect.width
        mid = w / 2
        if item.x1 < mid - 5:
            left_edges.append(item.x0)
        if item.x0 > mid + 5:
            right_edges.append(w - item.x1)
    if len(left_edges) < 3 or len(right_edges) < 3:
        return 1, "Outer margins could not be measured reliably from both columns."
    left = sorted(left_edges)[max(0, int(0.10 * (len(left_edges) - 1)))]
    right = sorted(right_edges)[max(0, int(0.10 * (len(right_edges) - 1)))]
    if 25 <= left <= 90 and 25 <= right <= 90 and abs(left - right) < 35:
        return 3, f"Approximate outer margins: left {left:.0f} pt, right {right:.0f} pt."
    return 0, f"Outer margins appear inconsistent: left {left:.0f} pt, right {right:.0f} pt."


def _font_distribution(lines: list[TextLine], doc: fitz.Document) -> tuple[float, str, str]:
    body = _main_text_lines(lines, doc)
    if not body:
        return 0.0, "unknown", "No body text detected."
    size_weights = Counter()
    family_weights = Counter()
    for line in body:
        size_weights[round(line.size, 1)] += line.char_count
        family_weights[line.font] += line.char_count
    main_size = size_weights.most_common(1)[0][0]
    main_font = family_weights.most_common(1)[0][0]
    return main_size, main_font, f"Predominant body font: {main_font}, {main_size:g} pt."


def _report_from_checks(
    name: str,
    doc: Optional[fitz.Document],
    checks: list[Check],
    notes: list[str],
    *,
    pages: Optional[int] = None,
    layout_verdict: Optional[str] = None,
    source_format: str = "PDF",
    analysis_method: str = "PDF visual-layout inspection",
    assessment_scope: str = "layout_and_structure",
) -> dict:
    denominator = sum(c.possible for c in checks if c.applicable)
    numerator = sum(c.earned for c in checks if c.applicable)
    score = round(100 * numerator / denominator, 1) if denominator else None
    by_category = []
    for name_cat, original_weight in CATEGORY_WEIGHTS.items():
        relevant = [c for c in checks if c.category == name_cat]
        possible = sum(c.possible for c in relevant if c.applicable)
        earned = sum(c.earned for c in relevant if c.applicable)
        by_category.append({
            "category": name_cat,
            "rubric_weight": original_weight,
            "earned": round(earned, 2) if possible else None,
            "possible": possible,
            "status": "not_applicable" if not possible else ("pass" if earned == possible else "needs_attention"),
        })
    if score is None:
        band = "Unable to evaluate"
    elif score >= 85:
        band = "Strong formatting match"
    elif score >= 70:
        band = "Minor formatting changes"
    elif score >= 50:
        band = "Major formatting changes"
    else:
        band = "Extensive formatting changes"
    if assessment_scope == "structure_only":
        band = {
            "Strong formatting match": "Strong text-structure match",
            "Minor formatting changes": "Minor text-structure issues",
            "Major formatting changes": "Major text-structure issues",
            "Extensive formatting changes": "Extensive text-structure issues",
        }.get(band, band)
    elif assessment_scope == "style_and_structure":
        band = {
            "Strong formatting match": "Strong Word style/structure match",
            "Minor formatting changes": "Minor Word style/structure issues",
            "Major formatting changes": "Major Word style/structure issues",
            "Extensive formatting changes": "Extensive Word style/structure issues",
        }.get(band, band)
    if layout_verdict is None:
        column_check = next((c for c in checks if c.criterion == "Two-column body layout"), None)
        if column_check and not column_check.applicable:
            layout_verdict = "Column layout unavailable for this format"
        elif column_check and column_check.earned == column_check.possible:
            layout_verdict = "Two-column layout detected"
        elif column_check and column_check.earned == 0:
            layout_verdict = "Two-column layout NOT detected"
        else:
            layout_verdict = "Two-column layout uncertain"
    payload = {
        "module": "projectiq_ieee",
        "layout_verdict": layout_verdict,
        "template_profile": "Generic IEEE two-column conference paper (heuristic)",
        "filename": name,
        "pages": len(doc) if doc is not None else pages,
        "source_format": source_format,
        "analysis_method": analysis_method,
        "assessment_scope": assessment_scope,
        "score": score,
        "max_score": 100,
        "evaluated_weight": denominator,
        "rubric_weight": sum(CATEGORY_WEIGHTS.values()),
        "rating": band,
        "categories": by_category,
        "checks": [c.as_dict() for c in checks],
        "notes": notes,
        "disclaimer": (
            "This is a teaching/demo formatting score, not an official IEEE score, "
            "publication decision or replacement for a venue's template and IEEE PDF Checker."
        ),
    }
    return payload


def analyze_pdf(source: Union[str, Path, bytes, bytearray], filename: Optional[str] = None) -> dict[str, Any]:
    """Inspect a path or in-memory PDF and return a JSON-serializable report."""
    if isinstance(source, (bytes, bytearray)):
        name = filename or "uploaded.pdf"
        document = fitz.open(stream=bytes(source), filetype="pdf")
    else:
        path = Path(source)
        name = filename or path.name
        if path.suffix.lower() != ".pdf":
            raise ValueError("Please provide a PDF file (other formats are not supported yet).")
        document = fitz.open(str(path))

    with document as doc:
        if doc.needs_pass:
            raise ValueError("The PDF is password-protected. Please provide an unlocked PDF.")
        if not len(doc):
            raise ValueError("PDF has no pages.")
        lines = _read_lines(doc)
        plain_text = "\n".join(line.text for line in lines)
        if len(re.sub(r"\s", "", plain_text)) < 120:
            raise ValueError(
                "The PDF contains too little extractable text. It might be scanned; "
                "OCR/manual review is required before an IEEE formatting score is possible."
            )
        checks: list[Check] = []
        notes = [
            "For a real submission, choose the exact conference/journal template and verify against its call for papers.",
            "Figures, table caption placement, exact spacing and IEEE Xplore PDF compliance still require manual or dedicated validation.",
        ]

        def add(category, label, earned, possible, detail, suggestion="", page=None, applicable=True):
            checks.append(Check(category, label, float(earned), possible, detail, suggestion, page, applicable))

        good_size, size_detail = _score_page_size(doc)
        add("Page layout", "A4 or US Letter page size", good_size, 8, size_detail,
            "Use the paper size specified by the target IEEE template.")

        body = _main_text_lines(lines, doc)
        good_columns, columns_detail, columns_page = _score_columns(body, doc)
        add("Page layout", "Two-column body layout", good_columns, 14, columns_detail,
            "Start from the IEEE two-column conference template. The title and author area may span both columns.", columns_page)

        good_margins, margins_detail = _score_margins(body, doc)
        add("Page layout", "Plausible and balanced outer margins", good_margins, 3, margins_detail,
            "Compare the PDF visually with the selected IEEE template; avoid manually changing its margins.")

        main_size, main_font, main_detail = _font_distribution(lines, doc)
        if 9.5 <= main_size <= 10.5:
            font_size_points = 12
        elif 9 <= main_size <= 11:
            font_size_points = 8
        else:
            font_size_points = 0
        add("Typography", "Body text near 10 pt", font_size_points, 12, main_detail,
            "Use the body style provided by the chosen IEEE conference template.")
        add("Typography", "Serif/Times-like body font", 8 if _looks_serif(main_font) else 0, 8,
            f"Dominant body font: {main_font}.",
            "Use the template font; do not replace it with a generic sans-serif font.")

        first = [line for line in lines if line.page == 1]
        abstract_line = next((line for line in first if _ABSTRACT_RE.match(line.text)), None)
        keyword_line = next((line for line in first if _KEYWORDS_RE.match(line.text)), None)
        first_height = doc[0].rect.height
        title_candidates = [
            line for line in first if line.y0 < first_height * 0.38 and line.size >= 13.5
            and line.char_count > 8 and not _ABSTRACT_RE.match(line.text)
        ]
        title = max(title_candidates, key=lambda line: (line.size, line.char_count), default=None)
        add("Title and authors", "Prominent first-page title", 6 if title else 0, 6,
            f"Large title text on page 1: {title.text[:100]}" if title else "No clearly sized title detected on page 1.",
            "Use the IEEE template's title style and position.", 1)
        if title:
            cutoff = abstract_line.y0 if abstract_line else min(first_height * .47, title.y1 + 115)
            author_text = [l for l in first if l.y0 > title.y1 + 2 and l.y1 < cutoff - 1 and l.char_count >= 3]
            authors_present = bool(author_text)
            author_detail = ("Text identified between title and abstract; author/affiliation block is plausible."
                             if authors_present else "No author/affiliation text detected beneath the title.")
        else:
            authors_present = False
            author_detail = "Unable to locate author block because no title was identified."
        add("Title and authors", "Author / affiliation block", 4 if authors_present else 0, 4,
            author_detail,
            "Confirm names, affiliations and email placement against the chosen template.", 1)

        add("Abstract and keywords", "Abstract label", 8 if abstract_line else 0, 8,
            "Abstract label found on first page." if abstract_line else "Abstract label not detected on first page.",
            "Include an 'Abstract—' paragraph in the first-page layout.", 1)
        add("Abstract and keywords", "Index Terms / Keywords label", 7 if keyword_line else 0, 7,
            "Keywords/Index Terms found on first page." if keyword_line else "Index Terms or Keywords not found on first page.",
            "Add an 'Index Terms—' line with relevant terms.", 1)

        headings = [l for l in lines if _SECTION_RE.match(l.text)]
        intro = next((l for l in headings if "introduction" in l.text.lower()), None)
        add("Section structure", "Numbered main sections", 6 if len(headings) >= 3 else (3 if len(headings) >= 2 else 0), 6,
            f"{len(headings)} numbered section heading(s) detected.",
            "Use consistent IEEE-style section headings, such as 'I. INTRODUCTION'.",
            headings[0].page if headings else None)
        add("Section structure", "Introduction section", 4 if intro else 0, 4,
            "Introduction heading found." if intro else "Introduction heading not detected.",
            "Add a numbered Introduction section if appropriate.", intro.page if intro else None)

        references_line = next((l for l in lines if re.match(r"^\s*(?:[IVX]+\.\s*)?references\s*$", l.text, re.I)), None)
        body_excluding_references = [l for l in lines if not references_line or
                                     (l.page < references_line.page or
                                      (l.page == references_line.page and l.y0 < references_line.y0))]
        captions_fig = [l for l in body_excluding_references if _FIG_CAPTION_RE.match(l.text)]
        captions_tab = [l for l in body_excluding_references if _TAB_CAPTION_RE.match(l.text)]
        mentions_fig = [l for l in body_excluding_references if _FIG_MENTION_RE.search(l.text)]
        mentions_tab = [l for l in body_excluding_references if _TAB_MENTION_RE.search(l.text)]
        any_figure_or_table = bool(mentions_fig or mentions_tab)
        if any_figure_or_table:
            caption_pts = (5 if captions_fig or not mentions_fig else 0) + (5 if captions_tab or not mentions_tab else 0)
            if mentions_fig and not mentions_tab:
                caption_pts = 10 if captions_fig else 0
            if mentions_tab and not mentions_fig:
                caption_pts = 10 if captions_tab else 0
            detail = (f"Found {len(captions_fig)} figure caption(s), {len(captions_tab)} table caption(s); "
                      f"{len(mentions_fig)} figure mention(s), {len(mentions_tab)} table mention(s).")
            add("Figures and tables", "Figure and table captions", caption_pts, 10, detail,
                "Give every figure/table a numbered caption; manually check correct placement.")
        else:
            add("Figures and tables", "Figure and table captions", 0, 10,
                "No figure or table mentions were detected; this category was excluded from the score.",
                applicable=False)

        add("References and citations", "References heading", 4 if references_line else 0, 4,
            f"References heading found on page {references_line.page}." if references_line else "References heading not found.",
            "Include a References section at the end of the paper.",
            references_line.page if references_line else None)
        ref_entries = [l for l in lines if _REF_ENTRY_RE.match(l.text) and
                       (not references_line or l.page >= references_line.page)]
        add("References and citations", "Numbered bibliography entries", 4 if ref_entries else 0, 4,
            f"{len(ref_entries)} numbered reference entry/entries found.",
            "Format bibliography items as [1], [2], etc., following the selected IEEE reference style.",
            ref_entries[0].page if ref_entries else None)
        intext_citations = [l for l in body_excluding_references if _CITATION_RE.search(l.text)]
        add("References and citations", "Bracketed in-text citations", 2 if intext_citations else 0, 2,
            f"{len(intext_citations)} line(s) containing bracketed citations found.",
            "Use IEEE-style bracketed in-text citations and cross-check each number with the References list.",
            intext_citations[0].page if intext_citations else None)

        try:
            unembedded_fonts = set()
            for page in doc:
                for font in page.get_fonts(full=True):
                    xref, basefont = font[0], font[3]
                    if not xref:
                        continue
                    font_data = doc.extract_font(xref)
                    if not font_data[3]:
                        unembedded_fonts.add(basefont)
            if unembedded_fonts:
                notes.append("Possible non-embedded PDF fonts: " + ", ".join(sorted(unembedded_fonts)) + ". Validate using IEEE PDF Checker.")
        except Exception:
            notes.append("PDF font embedding was not determined; validate with IEEE PDF Checker.")

        return _report_from_checks(name, doc, checks, notes)


def to_agent_result(report: dict[str, Any]) -> dict[str, Any]:
    """JSON object matching ProjectIQ's AgentResult fields.

    ProjectIQ summaries commonly use a 0-10 rubric. We return score / 10.
    'confidence' is only a coverage-derived heuristic, NOT calibrated accuracy.
    """
    failed = [c for c in report["checks"] if c["status"] in ("fail", "warning")]
    confidence = min(0.8, 0.5 + 0.3 * (report["evaluated_weight"] / 100))
    if report.get("assessment_scope") == "structure_only":
        confidence = min(confidence, 0.5)
    return {
        "agent": "ieee_format_checker",
        "score": round((report["score"] or 0) / 10, 2),
        "justification": f"{report['rating']}: {report['score']}/100 (demo {'text structure only' if report.get('assessment_scope') == 'structure_only' else 'formatting'} rubric; {report['evaluated_weight']}/100 points measurable).",
        "evidence": [f"{c['criterion']}: {c['detail']}" for c in failed[:8]],
        "confidence": round(confidence, 2),
    }
