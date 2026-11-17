"""AI-style check, evidence builder, confidence, the agent entry point and the validation
script. Offline: stand-ins replace the language models (see fake_models.py)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import fake_models  # noqa: E402
from fake_models import AI_LIKE, HUMAN_LIKE  # noqa: E402
from projectiq_integrity import run_integrity  # noqa: E402
from projectiq_integrity.ai_indicators import ai_signals  # noqa: E402
from projectiq_integrity.contracts import Evidence  # noqa: E402
from projectiq_integrity.evidence import Signal, build_findings, estimate_confidence  # noqa: E402

RESEARCH = FIX / "research"


def text(name):
    return (FIX / "ai_text" / f"{name}.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# AI-style text: both checks must agree
# --------------------------------------------------------------------------- #
def test_ai_flag_needs_both_checks():
    flagged, _ = ai_signals(text("ai_style_writing"), scorer=AI_LIKE)
    assert len(flagged) == 1
    f = build_findings(flagged)[0]
    assert f.confidence <= 0.7 and any("stock phrases" in e.location for e in f.evidence)
    assert ai_signals(text("ai_style_writing"), scorer=HUMAN_LIKE)[0] == []     # model disagrees
    assert ai_signals(text("plain_student_writing"), scorer=AI_LIKE)[0] == []   # style disagrees


def test_ai_vetoes_and_missing_model(tmp_path, monkeypatch):
    signals, notes = ai_signals(text("typo_heavy_writing"), scorer=AI_LIKE)
    assert signals == [] and "human pattern" in notes[-1]
    signals, notes = ai_signals("Too short. " * 5, scorer=AI_LIKE)
    assert signals == [] and "too short" in notes[-1]
    import projectiq_integrity.ai_indicators as ai
    monkeypatch.setattr(ai.LocalLM.__init__, "__defaults__", ("distilgpt2", tmp_path))
    signals, notes = ai_signals(text("ai_style_writing"))
    assert signals == [] and "never enough" in notes[-1]


# --------------------------------------------------------------------------- #
# Evidence builder and confidence
# --------------------------------------------------------------------------- #
def test_builder_drops_unquoted_signals_and_duplicates():
    ev = Evidence(source="report", location="p.1", quote="Same words here.")
    sigs = [Signal("similar_report_text", "no quote", [Evidence(source="report", location="p.2", quote="  ")]),
            Signal("similar_report_text", "dup", [ev, ev, Evidence(source="report", location="p.1",
                                                                   quote="same   WORDS here.")])]
    findings = build_findings(sigs)
    assert len(findings) == 1 and len(findings[0].evidence) == 1


def test_confidence_bounds():
    strong = Signal("copied_chunk", "", [], {"near_verbatim": 6, "share_of_report": 0.3})
    weak = Signal("similar_report_text", "", [], {"paraphrase": 1, "share_of_report": 0.02,
                                                   "lexical_backend": 1.0})
    ai = Signal("ai_style_text", "", [], {"style_margin": 5, "perplexity_margin": 5})
    assert estimate_confidence(strong) <= 0.95 and estimate_confidence(weak) < estimate_confidence(strong)
    assert estimate_confidence(ai) == 0.7


# --------------------------------------------------------------------------- #
# The agent entry point the Rubric Agent calls
# --------------------------------------------------------------------------- #
def test_run_integrity_returns_agent_result(monkeypatch):
    fake_models.install(monkeypatch)
    result = run_integrity(RESEARCH / "S201_report.txt", RESEARCH / "references", submission_id="S201")
    assert result.agent == "integrity" and result.submission_id == "S201"
    assert {f.kind for f in result.findings} >= {"copied_chunk", "similar_report_text"}
    assert any("never assigns" in n for n in result.notes)
    clean = run_integrity(RESEARCH / "S202_original.txt", RESEARCH / "references")
    assert clean.findings == []


def test_ai_check_ignores_copied_sentences(monkeypatch):
    """Copied sentences are someone else's writing: only the student's own text is checked."""
    fake_models.install(monkeypatch)
    seen = []

    def spy(sentences):
        seen.extend(sentences)
        return HUMAN_LIKE(sentences)

    from projectiq_integrity.document_check import check_document
    check_document(RESEARCH / "S201_report.txt", RESEARCH / "references", scorer=spy)
    assert seen and not any("Single-stage detectors predict bounding boxes" in s for s in seen)


# --------------------------------------------------------------------------- #
# Validation script
# --------------------------------------------------------------------------- #
def test_validation_metrics():
    from validate import agreement, quadratic_weighted_kappa, spearman

    assert quadratic_weighted_kappa([1, 2, 3], [1, 2, 3], 0, 5) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    m = agreement([5, 6, 7, 8], [6, 6, 7, 9], 10)
    assert m["mean_abs_error"] == pytest.approx(0.5) and m["mean_signed_error"] == pytest.approx(0.5)
    assert m["within_1"] == 1.0


def test_validation_controls(tmp_path, monkeypatch):
    from validate import controls_report, read_csv

    fake_models.install(monkeypatch)
    out = tmp_path / "integrity"
    out.mkdir()
    for sid, doc in [("S201", "S201_report.txt"), ("S202", "S202_original.txt")]:
        result = run_integrity(RESEARCH / doc, RESEARCH / "references", submission_id=sid)
        (out / f"{sid}.json").write_text(result.model_dump_json())
    rep = controls_report(read_csv(FIX / "validation" / "controls_example.csv"), out)
    assert rep["copied_chunk"]["detection_rate"] == 1.0 and rep["copied_chunk"]["false_flag_rate"] == 0.0
    assert rep["similar_report_text"]["detection_rate"] == 1.0
    assert rep["ai_style_text"]["fp"] == 0
