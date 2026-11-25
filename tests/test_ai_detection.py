"""AI percentage (projectiq_integrity/ai_detection.py) with a stand-in scorer.

The stand-in gives a low (AI-like) Binoculars score to text containing stock AI
phrases and a high (human-like) score otherwise, so the logic can be tested without
the two Qwen models. Real thresholds are checked with scripts/check_ai_thresholds.py.
"""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from projectiq_integrity import ai_detection  # noqa: E402
from projectiq_integrity.config import DEFAULT  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
AI_TEXT = (FIX / "ai_calibration" / "01_ai_chatgpt_buzzwords.txt").read_text(encoding="utf-8")
NO_PHRASE_TEXT = (FIX / "ai_calibration" / "05_ai_imitating_typos.txt").read_text(encoding="utf-8")


def fake_scorer(text):
    return (0.70 if ai_detection.stock_phrases(text) else 1.10), 100


def as_sentences(*paragraphs, start=0):
    """(index, location, text) like check_document passes, one location per paragraph."""
    from projectiq_integrity.ria.preprocessing import split_sentences

    out, i = [], start
    for n, para in enumerate(paragraphs, 1):
        for s in split_sentences(para):
            out.append((i, f"para {n}", " ".join(s.split())))
            i += 1
    return out


def test_ai_paragraph_is_flagged_and_human_is_not():
    det, _ = ai_detection.estimate(as_sentences(AI_TEXT, NO_PHRASE_TEXT), scorer=fake_scorer)
    assert det["status"] == "COMPLETED"
    assert 30 < det["estimated_percent"] < 70           # about half the words are the AI paragraph
    assert det["flagged_passages"] and det["flagged_passages"][0]["location"].startswith("para 1")
    assert "leverag" in " ".join(det["flagged_passages"][0]["stock_phrases"])
    assert det["confidence"] in {"low", "medium"}        # never "high" while provisional


def test_all_human_gives_zero_percent():
    det, _ = ai_detection.estimate(as_sentences(NO_PHRASE_TEXT, NO_PHRASE_TEXT), scorer=fake_scorer)
    assert det["status"] == "COMPLETED"
    assert det["estimated_percent"] == 0.0
    assert det["flagged_passages"] == []


def test_short_text_is_inconclusive_not_zero():
    det, _ = ai_detection.estimate(as_sentences("This is a short note. It has two sentences only."),
                                   scorer=fake_scorer)
    assert det["status"] == "INCONCLUSIVE"
    assert det["estimated_percent"] is None


def test_missing_models_is_not_available(tmp_path):
    settings = replace(DEFAULT, ai_observer_model="no-such-model", ai_performer_model="no-such-model-2")
    det, _ = ai_detection.estimate(as_sentences(AI_TEXT), settings=settings)
    assert det["status"] == "NOT_AVAILABLE"
    assert det["estimated_percent"] is None
    assert "setup_offline" in det["limitations"][0]


def test_scorer_error_is_failed_not_crash():
    def broken(_):
        raise RuntimeError("out of memory")

    det, _ = ai_detection.estimate(as_sentences(AI_TEXT, NO_PHRASE_TEXT), scorer=broken)
    assert det["status"] == "FAILED"
    assert det["estimated_percent"] is None


def test_passages_break_at_gaps_and_respect_lengths():
    sents = as_sentences(AI_TEXT)
    gap_at = len(sents) // 2
    with_gap = sents[:gap_at] + [(i + 5, loc, t) for i, loc, t in sents[gap_at:]]   # copied sentences removed
    passages = ai_detection.build_passages(with_gap)
    assert len(passages) == 2
    long = ai_detection.build_passages(as_sentences(" ".join([AI_TEXT] * 3)))
    assert all(p.words <= DEFAULT.ai_passage_max_words + 60 for p in long)


@pytest.mark.parametrize("status", ["COMPLETED", "INCONCLUSIVE", "NOT_AVAILABLE", "FAILED"])
def test_output_matches_orchestrator_contract(status):
    """Same rules as projectiq_core AIAnalysis: COMPLETED needs a value and an
    interpretation; every other status must have no value."""
    texts = {"COMPLETED": (AI_TEXT, NO_PHRASE_TEXT), "INCONCLUSIVE": ("Too short. Really.",),
             "NOT_AVAILABLE": (AI_TEXT,), "FAILED": (AI_TEXT, NO_PHRASE_TEXT)}[status]
    kwargs = {"scorer": fake_scorer}
    if status == "NOT_AVAILABLE":
        kwargs = {"settings": replace(DEFAULT, ai_observer_model="x", ai_performer_model="y")}
    if status == "FAILED":
        kwargs = {"scorer": lambda _: 1 / 0}
    det, _ = ai_detection.estimate(as_sentences(*texts), **kwargs)
    assert det["status"] == status
    assert set(det) == {"status", "estimated_percent", "interpretation", "confidence",
                        "flagged_passages", "limitations"}
    if status == "COMPLETED":
        assert 0 <= det["estimated_percent"] <= 100 and det["interpretation"].strip()
    else:
        assert det["estimated_percent"] is None


def test_check_document_reports_ai_percentage(monkeypatch):
    import fake_models
    from projectiq_integrity.document_check import check_document, to_agent_result

    fake_models.install(monkeypatch)
    rep = check_document(FIX / "research" / "S201_report.txt", FIX / "research" / "references",
                         ai_scorer=fake_scorer)
    assert rep.ai_detection["status"] == "COMPLETED"
    assert rep.to_dict()["ai_detection"]["estimated_percent"] is not None
    result = to_agent_result(rep, "S201")
    assert any(rep.ai_detection["interpretation"] in n for n in result.notes)


def test_finding_only_from_report_percent():
    det = {"status": "COMPLETED", "estimated_percent": 15.0, "interpretation": "x", "confidence": "medium",
           "flagged_passages": [{"location": "para 1", "words": 60, "score": 0.8, "threshold": 0.9,
                                 "stock_phrases": [], "text": "some text"}], "limitations": []}
    assert ai_detection.to_signal(det, "doc") == []                    # under 20%: no finding
    det["estimated_percent"] = 45.0
    [sig] = ai_detection.to_signal(det, "doc")
    assert sig.kind == "ai_generated_share"
