import fitz
import pytest
from projectiq_ieee.checker import analyze_pdf, to_agent_result
from projectiq_ieee.chat import answer_question


def sample_pdf(two_column=True, include_keywords=True, include_figures=False):
    doc = fitz.open()
    p = doc.new_page(width=595.28, height=841.89)
    p.insert_text((90, 62), "Analysis of Research Paper Formatting", fontsize=19, fontname="times-roman")
    p.insert_text((230, 89), "A. Author, B. Author", fontsize=10, fontname="times-roman")
    p.insert_text((50, 126), "Abstract—This paper describes an automated method for paper layout evaluation.", fontsize=9, fontname="times-roman")
    if include_keywords:
        p.insert_text((50, 142), "Index Terms—Document understanding, layout, research evaluation.", fontsize=9, fontname="times-roman")
    p.insert_text((98, 168), "I. INTRODUCTION", fontsize=10, fontname="times-bold")
    for i in range(13):
        p.insert_text((50, 190 + i * 14), "A study compares PDF formatting against a template [1].", fontsize=10, fontname="times-roman")
        if two_column:
            p.insert_text((320, 190 + i * 14), "We measure text sizes and page layout heuristics.", fontsize=10, fontname="times-roman")
    p.insert_text((98, 407), "II. METHODS", fontsize=10, fontname="times-bold")
    p.insert_text((98, 432), "III. RESULTS", fontsize=10, fontname="times-bold")
    if include_figures:
        p.insert_text((50, 460), "As illustrated in Fig. 1, formatting can be checked.", fontsize=10, fontname="times-roman")
        p.insert_text((50, 485), "Fig. 1. Example document structure.", fontsize=9, fontname="times-roman")
    p.insert_text((98, 520), "REFERENCES", fontsize=10, fontname="times-bold")
    p.insert_text((50, 544), "[1] A. Researcher, 'Formatting analysis,' 2025.", fontsize=9, fontname="times-roman")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_score_report_structure():
    report = analyze_pdf(sample_pdf(), filename="correct.pdf")
    assert 0 <= report["score"] <= 100
    assert report["pages"] == 1
    assert report["filename"] == "correct.pdf"
    assert report["evaluated_weight"] == 90
    assert any(c["status"] == "not_applicable" for c in report["checks"])
    assert set(to_agent_result(report)) == {"agent", "score", "justification", "evidence", "confidence"}


def test_two_columns_score_higher():
    good = analyze_pdf(sample_pdf(two_column=True))
    bad = analyze_pdf(sample_pdf(two_column=False))
    assert good["score"] > bad["score"]
    left = next(c for c in good["checks"] if c["criterion"] == "Two-column body layout")
    right = next(c for c in bad["checks"] if c["criterion"] == "Two-column body layout")
    assert left["earned"] > right["earned"]
    assert good["layout_verdict"] == "Two-column layout detected"
    assert bad["layout_verdict"] == "Two-column layout NOT detected"


def test_missing_keywords_penalised():
    good = analyze_pdf(sample_pdf())
    bad = analyze_pdf(sample_pdf(include_keywords=False))
    assert good["score"] > bad["score"]


def test_figures_are_scored_when_present():
    report = analyze_pdf(sample_pdf(include_figures=True))
    category = next(c for c in report["categories"] if c["category"] == "Figures and tables")
    assert category["possible"] == 10
    assert report["evaluated_weight"] == 100


def test_scanned_like_pdf_refused():
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    with pytest.raises(ValueError, match="extractable text"):
        analyze_pdf(data)


def test_bot_grounds_answers_in_result():
    report = analyze_pdf(sample_pdf(include_keywords=False))
    reply = answer_question("What about keywords?", report)
    assert "Keywords" in reply or "Index Terms" in reply
    assert "not found" in reply
    assert str(report["score"]) in answer_question("What score did I get?", report)
