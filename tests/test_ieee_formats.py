"""Regression tests for PDF, Word, and text entrypoints."""
from io import BytesIO
from pathlib import Path
import shutil

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from projectiq_ieee import analyze_document, analyze_pdf
from projectiq_ieee.chat import answer_question

SAMPLE = """A Tool for Research Paper IEEE Format Evaluation
Alice Author, Bob Author, Example University
Abstract—We develop a tool for checking research paper structure and providing clear feedback to authors.
Index Terms—IEEE, analysis, citations, formatting
I. INTRODUCTION
The importance of identifying formatting issues in draft research papers is high [1].
II. METHODOLOGY
The proposed checker extracts section headings and tests basic formatting rules [1].
III. RESULTS
We show that useful feedback can be generated from selected formatting signals.
REFERENCES
[1] A. Author, Writing Research Reports, 2025.
"""


def docx_bytes(two_columns=True, keywords=True):
    doc = Document()
    doc.styles["Normal"].font.name = "Times New Roman"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_paragraph("A Tool for Research Paper IEEE Format Evaluation", style="Title")
    doc.add_paragraph("Alice Author, Bob Author, Example University")
    doc.add_paragraph("Abstract—We develop a tool for checking research paper structure and providing clear feedback to authors.")
    if keywords:
        doc.add_paragraph("Index Terms—IEEE, analysis, citations, formatting")
    for head in ["I. INTRODUCTION", "II. METHODOLOGY", "III. RESULTS"]:
        doc.add_paragraph(head)
        doc.add_paragraph("The importance of checking draft research paper formatting is high " * 3 + "[1].")
    doc.add_paragraph("REFERENCES")
    doc.add_paragraph("[1] A. Author, Writing Research Reports, 2025.")
    if two_columns:
        section = doc.sections[0]
        cols = section._sectPr.find(qn("w:cols"))
        if cols is None:
            cols = OxmlElement("w:cols")
            section._sectPr.append(cols)
        cols.set(qn("w:num"), "2")
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def find(report, label):
    return next(check for check in report["checks"] if check["criterion"] == label)


def test_docx_upload_without_converter(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    report = analyze_document(docx_bytes(), filename="paper.DOCX")
    assert report["source_format"] == "DOCX"
    assert report["assessment_scope"] == "style_and_structure"
    assert report["pages"] is None
    assert report["evaluated_weight"] == 90
    assert report["layout_verdict"].startswith("Two-column section configured")
    assert find(report, "Body text near 10 pt")["status"] == "pass"
    assert 0 <= report["score"] <= 100


def test_docx_columns_and_keywords_affect_score():
    baseline = analyze_document(docx_bytes(), filename="sample.docx")
    no_columns = analyze_document(docx_bytes(two_columns=False), filename="sample.docx")
    no_keywords = analyze_document(docx_bytes(keywords=False), filename="sample.docx")
    assert baseline["score"] > no_columns["score"]
    assert baseline["score"] > no_keywords["score"]
    assert no_columns["layout_verdict"] == "Two-column section NOT configured"


def test_txt_structure_is_not_full_format_score():
    report = analyze_document(SAMPLE.encode("utf-8"), filename="paper.txt")
    assert report["assessment_scope"] == "structure_only"
    assert report["evaluated_weight"] == 45
    assert report["score"] == 100  # 100% of TEXT checks, NOT IEEE layout compliance
    assert report["pages"] is None
    for criterion in ("Two-column body layout", "Serif/Times-like body font", "A4 or US Letter page size"):
        assert find(report, criterion)["status"] == "not_applicable"
    assert "NOT an IEEE layout-compliance" in answer_question("What is my score?", report)


def test_text_missing_abstract_penalised():
    full = analyze_document(SAMPLE.encode(), filename="paper.txt")
    absent = analyze_document(SAMPLE.replace("Abstract—", "Background—").encode(), filename="paper.txt")
    assert absent["score"] < full["score"]


def test_markdown_and_utf16_text():
    md = SAMPLE.replace("I. INTRODUCTION", "## I. INTRODUCTION").replace("II. METHODOLOGY", "## II. METHODOLOGY")
    assert find(analyze_document(md.encode(), filename="paper.md"), "Introduction section")["earned"] == 4
    utf16 = SAMPLE.encode("utf-16")
    assert analyze_document(utf16, filename="paper.txt")["score"] > 0


def test_path_input_and_unsupported_extension(tmp_path: Path):
    paper = tmp_path / "paper.txt"
    paper.write_text(SAMPLE, encoding="utf-8")
    assert analyze_document(paper)["filename"] == "paper.txt"
    with pytest.raises(ValueError, match="Unsupported document format"):
        analyze_document(b"fake", filename="paper.exe")


def test_legacy_doc_requires_libreoffice(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="requires LibreOffice"):
        analyze_document(b"fake .doc file", filename="paper.doc")


def test_invalid_word_file_rejected():
    with pytest.raises(ValueError, match="valid, unlocked Word"):
        analyze_document(b"fake", filename="paper.docx")


def test_real_rtf_conversion_if_libreoffice_installed():
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice not installed")
    # RTF with enough extractable text to exercise the actual conversion path.
    rtf = b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}\\f0\\fs20 "
    text = SAMPLE.replace("\n", "\\par ").replace("—", "-")
    payload = rtf + text.encode("ascii", errors="ignore") + b"}"
    report = analyze_document(payload, filename="sample.rtf")
    assert report["source_format"] == "RTF"
    assert report["analysis_method"] == "LibreOffice-converted PDF layout inspection"
    assert report["pages"] >= 1
