"""Extra IEEE checker regression cases. Copy into ProjectIQ/tests/.

Run from repository root:
    python -m pytest -v -rs tests/test_ieee_extended.py

The tests deliberately create malformed and non-IEEE documents in memory.  They
check *which* rule lost marks rather than only asserting that some score exists.
"""
from __future__ import annotations

from io import BytesIO
import json

import pymupdf as fitz
import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from projectiq_ieee import analyze_document


def make_pdf(
    *, two_columns=True, fontname="times-roman", body_size=10,
    abstract=True, keywords=True, sections=True, intro=True,
    refs=True, cite=True, title=True, authors=True,
    figure_mention=False, figure_caption=True, page_width=595.28,
    page_height=841.89,
):
    """A predictable synthetic one-page PDF; not an official IEEE template."""
    doc = fitz.open()
    p = doc.new_page(width=page_width, height=page_height)
    if title:
        p.insert_text((80, 60), "A Study of Automated Research Paper Analysis", fontsize=18, fontname="times-roman")
    if authors:
        p.insert_text((145, 88), "Alice Author and Bob Author - University", fontsize=10, fontname="times-roman")
    if abstract:
        p.insert_text((50, 122), "Abstract - A study of automated assessment for research paper formatting.", fontsize=9, fontname="times-roman")
    if keywords:
        p.insert_text((50, 138), "Index Terms - Layout, extraction, validation and testing.", fontsize=9, fontname="times-roman")
    if sections:
        if intro:
            p.insert_text((80, 160), "I. INTRODUCTION", fontsize=10, fontname="times-bold")
        else:
            p.insert_text((80, 160), "I. BACKGROUND", fontsize=10, fontname="times-bold")
    for i in range(12):
        p.insert_text((50, 183 + 14 * i),
                      "Research analysis checks templates and citations" + (" [1]." if cite else "."),
                      fontsize=body_size, fontname=fontname)
        if two_columns:
            p.insert_text((320, 183 + 14 * i),
                          "Text analysis identifies layout differences in papers.",
                          fontsize=body_size, fontname=fontname)
    if sections:
        p.insert_text((80, 390), "II. METHODS", fontsize=10, fontname="times-bold")
        p.insert_text((80, 412), "III. RESULTS", fontsize=10, fontname="times-bold")
    if figure_mention:
        p.insert_text((50, 452), "As shown in Fig. 1, the layout is evaluated.", fontsize=10, fontname="times-roman")
        if figure_caption:
            p.insert_text((50, 470), "Fig. 1. Paper layout example.", fontsize=9, fontname="times-roman")
    if refs:
        p.insert_text((80, 510), "REFERENCES", fontsize=10, fontname="times-bold")
        p.insert_text((50, 531), "[1] A. Author, Formatting research papers, 2025.", fontsize=9, fontname="times-roman")
    result = doc.tobytes()
    doc.close()
    return result


def make_docx(*, two_columns=True, keywords=True, abstract=True,
              fontname="Times New Roman", font_size=10, refs=True):
    d = Document()
    d.styles["Normal"].font.name = fontname
    d.styles["Normal"].font.size = Pt(font_size)
    d.add_paragraph("A Study of Automated Research Paper Analysis", style="Title")
    d.add_paragraph("Alice Author and Bob Author - University")
    if abstract:
        d.add_paragraph("Abstract - A study of automated analysis and formatting verification.")
    if keywords:
        d.add_paragraph("Index Terms - Layout, extraction, validation and testing.")
    for section in ("I. INTRODUCTION", "II. METHODS", "III. RESULTS"):
        d.add_paragraph(section)
        d.add_paragraph("Research analysis checks formatting and citations [1]. " * 4)
    if refs:
        d.add_paragraph("REFERENCES")
        d.add_paragraph("[1] A. Author, Formatting research papers, 2025.")
    if two_columns:
        cols = d.sections[0]._sectPr.find(qn("w:cols"))
        if cols is None:
            cols = OxmlElement("w:cols")
            d.sections[0]._sectPr.append(cols)
        cols.set(qn("w:num"), "2")
    out = BytesIO()
    d.save(out)
    return out.getvalue()


def check(report, criterion):
    return next(c for c in report["checks"] if c["criterion"] == criterion)


@pytest.fixture
def baseline_pdf():
    return analyze_document(make_pdf(), filename="baseline.pdf")


def test_good_pdf_high_score_and_valid_json(baseline_pdf):
    assert baseline_pdf["source_format"] == "PDF"
    assert baseline_pdf["assessment_scope"] == "layout_and_structure"
    assert baseline_pdf["score"] >= 85
    assert check(baseline_pdf, "Two-column body layout")["status"] == "pass"
    json.dumps(baseline_pdf, allow_nan=False)


def test_non_ieee_single_column_loses_layout_marks(baseline_pdf):
    bad = analyze_document(make_pdf(two_columns=False), filename="bad.pdf")
    assert bad["score"] < baseline_pdf["score"]
    assert check(bad, "Two-column body layout")["earned"] == 0
    assert bad["layout_verdict"] == "Two-column layout NOT detected"


@pytest.mark.parametrize("field,criterion", [
    ("abstract", "Abstract label"),
    ("keywords", "Index Terms / Keywords label"),
    ("refs", "References heading"),
    ("cite", "Bracketed in-text citations"),
    ("intro", "Introduction section"),
    ("sections", "Numbered main sections"),
    ("title", "Prominent first-page title"),
    ("authors", "Author / affiliation block"),
])
def test_missing_pdf_feature_penalized(field, criterion, baseline_pdf):
    bad = analyze_document(make_pdf(**{field: False}), filename="missing.pdf")
    assert check(bad, criterion)["earned"] < check(baseline_pdf, criterion)["earned"]
    assert bad["score"] < baseline_pdf["score"]


@pytest.mark.parametrize("variant,criterion", [
    ({"fontname": "helv"}, "Serif/Times-like body font"),
    ({"body_size": 12}, "Body text near 10 pt"),
    ({"page_width": 500, "page_height": 700}, "A4 or US Letter page size"),
])
def test_pdf_bad_style_penalized(variant, criterion, baseline_pdf):
    bad = analyze_document(make_pdf(**variant), filename="bad-style.pdf")
    assert check(bad, criterion)["earned"] < check(baseline_pdf, criterion)["earned"]


def test_figure_mention_without_caption_penalized():
    good = analyze_document(make_pdf(figure_mention=True, figure_caption=True), filename="figure-good.pdf")
    bad = analyze_document(make_pdf(figure_mention=True, figure_caption=False), filename="figure-bad.pdf")
    assert check(good, "Figure and table captions")["earned"] == 10
    assert check(bad, "Figure and table captions")["earned"] == 0
    assert bad["score"] < good["score"]


def test_very_bad_pdf_has_substantially_lower_score(baseline_pdf):
    poor = analyze_document(make_pdf(two_columns=False, fontname="helv", body_size=12,
                        abstract=False, keywords=False, sections=False, refs=False,
                        title=False, authors=False, cite=False,
                        page_width=500, page_height=700), filename="very-bad.pdf")
    assert poor["score"] <= 50
    assert baseline_pdf["score"] > poor["score"]


def test_docx_two_column_vs_one_column_and_no_keywords():
    good = analyze_document(make_docx(), filename="good.docx")
    bad = analyze_document(make_docx(two_columns=False, keywords=False), filename="bad.docx")
    assert good["source_format"] == "DOCX"
    assert good["assessment_scope"] == "style_and_structure"
    assert good["score"] > bad["score"]
    assert check(bad, "Two-column body layout")["status"] == "fail"
    assert check(bad, "Index Terms / Keywords label")["status"] == "fail"


@pytest.mark.parametrize("variant,criterion", [
    ({"fontname": "Arial"}, "Serif/Times-like body font"),
    ({"font_size": 12}, "Body text near 10 pt"),
    ({"abstract": False}, "Abstract label"),
    ({"refs": False}, "References heading"),
])
def test_docx_bad_feature_is_detected(variant, criterion):
    good = analyze_document(make_docx(), filename="good.docx")
    bad = analyze_document(make_docx(**variant), filename="bad.docx")
    assert check(bad, criterion)["earned"] < check(good, criterion)["earned"]


def test_txt_uses_structure_only_not_layout_score():
    text = ("Paper Title\nAlice Author, Example University\n"
            "Abstract - This study considers validation of an IEEE-like research report.\n"
            "Index Terms - assessment, research, layout\n"
            "I. INTRODUCTION\nResearch approaches compare structured papers with known formats [1].\n"
            "II. METHODS\nA number of formatting checks are evaluated [1].\n"
            "III. RESULTS\nThe tests produced useful explanations.\n"
            "REFERENCES\n[1] A. Author, Research testing, 2025.\n")
    report = analyze_document(text.encode("utf-8"), filename="test.TXT")
    assert report["assessment_scope"] == "structure_only"
    assert check(report, "Two-column body layout")["status"] == "not_applicable"
    assert report["evaluated_weight"] <= 45


def test_txt_without_research_structure_loses_points():
    bad = ("Generic project memo\nA brief internal note on an unrelated project.\n" +
           "We discussed this project during our team meeting and agreed " * 12)
    report = analyze_document(bad.encode(), filename="memo.txt")
    assert report["score"] < 60
    assert check(report, "Abstract label")["earned"] == 0
    assert check(report, "References heading")["earned"] == 0


def test_markdown_and_utf16_are_accepted():
    text = ("# Research Title\nAuthor Name, University\nAbstract - An analysis of a research paper.\n"
            "Index Terms - validation, format\n## I. INTRODUCTION\n" +
            "We test the system against several requirements [1].\n" * 8 +
            "## II. METHODS\n## III. RESULTS\nREFERENCES\n[1] Author, 2025.\n")
    md = analyze_document(text.encode(), filename="demo.MD")
    txt = analyze_document(text.encode("utf-16"), filename="demo.txt")
    assert md["assessment_scope"] == "structure_only"
    assert txt["assessment_scope"] == "structure_only"


def test_blank_pdf_needs_extractable_text():
    doc = fitz.open()
    doc.new_page()
    empty = doc.tobytes()
    doc.close()
    with pytest.raises(ValueError, match="extractable text"):
        analyze_document(empty, filename="blank.pdf")


def test_encrypted_pdf_rejected():
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 72), "This is a password-protected example.")
    protected = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                            owner_pw="owner", user_pw="secret")
    doc.close()
    with pytest.raises(ValueError, match="password-protected"):
        analyze_document(protected, filename="protected.pdf")


def test_invalid_pdf_is_rejected():
    with pytest.raises(Exception):
        analyze_document(b"not a PDF", filename="invalid.pdf")


def test_invalid_docx_is_rejected_with_error():
    with pytest.raises(ValueError, match="valid, unlocked Word"):
        analyze_document(b"not a Word ZIP", filename="bad.docx")


@pytest.mark.parametrize("filename", ["bad.exe", "bad.jpg", "bad.zip"])
def test_unsupported_extension_is_rejected(filename):
    with pytest.raises(ValueError, match="Unsupported document format"):
        analyze_document(b"Not a document", filename=filename)


def test_empty_txt_rejected():
    with pytest.raises(ValueError, match="empty"):
        analyze_document(b"", filename="empty.txt")


def test_short_txt_rejected():
    with pytest.raises(ValueError, match="too little extractable text"):
        analyze_document(b"Small note", filename="short.txt")


def test_missing_file_path_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        analyze_document(tmp_path / "missing.pdf")


def test_oversize_upload_rejected():
    with pytest.raises(ValueError, match="maximum 30 MB"):
        analyze_document(b"x" * (30 * 1024 * 1024 + 1), filename="oversized.pdf")


def test_pdf_deterministic_report_for_same_content():
    pdf = make_pdf(keywords=False)
    a = analyze_document(pdf, filename="same.pdf")
    b = analyze_document(pdf, filename="same.pdf")
    assert a["score"] == b["score"]
    assert a["checks"] == b["checks"]
