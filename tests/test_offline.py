"""Offline tests: no model needed.   Run with:  python -m pytest -q"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fake_ollama import FakeOllama  # noqa: E402
from projectiq_summarizer.__main__ import main as cli_main  # noqa: E402
from projectiq_summarizer.chunking import chunk_text  # noqa: E402
from projectiq_summarizer.confidence import combine, quote_found  # noqa: E402
from projectiq_summarizer.config import Settings  # noqa: E402
from projectiq_summarizer.judge import (ai_flag, apply_evidence_cap, decide,  # noqa: E402
                                        majority, weighted_score)
from projectiq_summarizer.llm import LLMError, OllamaClient, parse_model  # noqa: E402
from projectiq_summarizer.pipeline import evaluate  # noqa: E402
from projectiq_summarizer.rubric import load_rubric  # noqa: E402
from projectiq_summarizer.schemas import FinalSummary, Recommendation  # noqa: E402
from projectiq_summarizer.signals import compute_signals  # noqa: E402

CASES = ROOT / "test_cases"


def read(name):
    return (CASES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Text signals: the non-LLM second opinion must classify the three cases
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fname,expected", [
    ("1_poorly_written.txt", "poorly_written"),
    ("2_highly_technical.txt", "highly_technical"),
    ("3_ai_written.txt", "likely_ai_generated"),
])
def test_signals_classify_the_three_cases(fname, expected):
    s = compute_signals(read(fname))
    assert s.profile == expected
    assert s.profile_margin >= 0.3


def test_signals_leave_ordinary_writing_as_standard():
    t = ("Our team built a web app that helps the college canteen take orders in "
         "advance. Students pick their items on a phone, pay at the counter, and "
         "collect the food when a token number is called. We chose Flask because two "
         "of us had used it before. During testing the order screen froze when more "
         "than twenty people ordered at once, and we traced it to a missing index.")
    assert compute_signals(t).profile == "standard"


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
def test_short_text_is_one_chunk():
    assert len(chunk_text("hello world. " * 10, 6000)) == 1


def test_long_text_is_chunked_within_limit_and_keeps_content():
    paras = [f"Paragraph {i} " + "word " * 180 for i in range(40)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, 3000, 200)
    assert len(chunks) > 5
    assert all(len(c) <= 3000 + 200 + 2 for c in chunks)
    for i in (0, 17, 39):
        assert any(f"Paragraph {i} " in c for c in chunks)


# --------------------------------------------------------------------------
# Policy and confidence maths
# --------------------------------------------------------------------------
def test_weighted_score_uses_weights():
    r = load_rubric()
    scores = {c.id: 10 for c in r.criteria}
    scores["specificity"] = 0          # weight 1.5 of total 6.0
    assert weighted_score(scores, r) == 7.5


@pytest.mark.parametrize("w,scores,flagged,expected", [
    (8.0, {"clarity": 8}, True, Recommendation.FLAG_FOR_REVIEW),
    (8.0, {"clarity": 8}, False, Recommendation.ACCEPT),
    (4.2, {"clarity": 5}, False, Recommendation.REVISE_AND_RESUBMIT),
    (6.5, {"clarity": 3}, False, Recommendation.REVISE_AND_RESUBMIT),
    (6.5, {"clarity": 6}, False, Recommendation.ACCEPT_WITH_MINOR_REVISIONS),
])
def test_policy(w, scores, flagged, expected):
    assert decide(w, scores, flagged)[0] == expected


@pytest.mark.parametrize("judge_ai,judge_type,ai_sig,poor_sig,expected", [
    ("low", "highly_technical", 0.97, 0.0, True),     # signals alone are enough
    ("high", "standard", 0.40, 0.0, True),            # judge + supporting signals
    ("high", "poorly_written", 0.09, 0.80, False),    # typos: human writing
    ("high", "standard", 0.20, 0.0, False),           # judge alone is not enough
    ("medium", "likely_ai_generated", 0.40, 0.0, True),
    ("low", "standard", 0.30, 0.0, False),
])
def test_ai_flag_needs_agreeing_evidence(judge_ai, judge_type, ai_sig, poor_sig, expected):
    assert ai_flag(judge_ai, judge_type, ai_sig, poor_sig) is expected


def test_specificity_cap():
    scores = {"clarity": 8, "specificity": 7}
    capped, applied = apply_evidence_cap(scores, verified_details=1)
    assert applied and capped["specificity"] == 3 and capped["clarity"] == 8
    same, applied = apply_evidence_cap(scores, verified_details=4)
    assert not applied and same["specificity"] == 7


def test_majority_breaks_ties_towards_caution():
    assert majority(["low", "high"], ["low", "medium", "high"]) == "high"
    assert majority(["low", "low", "high"], ["low", "medium", "high"]) == "low"


def test_quote_grounding():
    text = "We swept M over {8, 16, 32} and measured recall@10 on 5,000 queries."
    assert quote_found("we swept M over {8, 16, 32}", text) == 1.0
    assert quote_found("We swept M over 8 16 32 and measured recall", text) == 1.0
    assert quote_found("The system uses a blockchain ledger for audit", text) == 0.0
    assert quote_found("", text) == 0.0


def test_confidence_bands():
    assert combine(1.0, 1.0, 1.0, []).band == "High"
    low = combine(0.3, 0.15, 0.0, [])
    assert low.band == "Low" and any("human review" in n for n in low.notes)
    single = combine(None, 1.0, 1.0, [])
    assert single.score == 100 and any("--samples" in n for n in single.notes)


# --------------------------------------------------------------------------
# LLM client parsing
# --------------------------------------------------------------------------
def test_parse_model_handles_think_tags_and_fences():
    raw = '<think>hmm</think>\n```json\n{"summary": "s", "key_points": ["a"]}\n```'
    assert parse_model(raw, FinalSummary).summary == "s"
    with pytest.raises(ValueError):
        parse_model("no json here", FinalSummary)


# --------------------------------------------------------------------------
# End to end against the fake Ollama server
# --------------------------------------------------------------------------
EXPECTED = {
    "1_poorly_written.txt": (Recommendation.REVISE_AND_RESUBMIT, "poorly_written"),
    "2_highly_technical.txt": (Recommendation.ACCEPT, "highly_technical"),
    "3_ai_written.txt": (Recommendation.FLAG_FOR_REVIEW, "likely_ai_generated"),
}


@pytest.mark.parametrize("fname", list(EXPECTED))
def test_pipeline_end_to_end(fname):
    with FakeOllama() as fake:
        s = Settings(ollama_url=fake.url, judge_samples=3)
        llm = OllamaClient(s)
        llm.check()
        r = evaluate(read(fname), fname, llm, s)
        # 1 summary call + 3 judge calls
        assert len(fake.state.requests) == 4
        judge_calls = [p for p in fake.state.requests
                       if p["format"].get("title") == "Judgement"]
        assert [p["options"]["temperature"] for p in judge_calls] == [0.0, 0.6, 0.6]
        assert [p["options"]["seed"] for p in judge_calls] == [42, 43, 44]
    rec, ctype = EXPECTED[fname]
    assert r.recommendation == rec
    assert r.content_type == ctype
    assert r.summary.summary
    assert len(r.criteria) == 5
    assert all(c.evidence_found_in_text for c in r.criteria)
    # all three checks agree, so confidence must be high
    assert r.confidence.band == "High" and r.confidence.score >= 90
    assert 0 <= r.agent_result.confidence <= 1
    assert r.agent_result.agent == "summariser_rubric_evaluator"


def test_disagreeing_runs_lower_confidence():
    with FakeOllama() as fake:
        fake.state.drift["ai"] = [("likely_ai_generated", "high"),
                                  ("standard", "low"), ("standard", "low")]
        s = Settings(ollama_url=fake.url, judge_samples=3)
        r = evaluate(read("3_ai_written.txt"), "x", OllamaClient(s), s)
    # the flag comes from strong text signals, but 2 of 3 runs saw nothing:
    # the recommendation stands, confidence drops out of the High band
    assert r.recommendation == Recommendation.FLAG_FOR_REVIEW
    assert r.confidence.score < 75
    assert any("text signals say" in n for n in r.confidence.notes)


def test_retry_after_invalid_json():
    with FakeOllama() as fake:
        fake.state.bad_json_once = True
        s = Settings(ollama_url=fake.url, judge_samples=1)
        r = evaluate(read("2_highly_technical.txt"), "x", OllamaClient(s), s)
    assert r.recommendation == Recommendation.ACCEPT
    assert r.confidence.self_consistency is None


def test_missing_model_gives_pull_instruction():
    with FakeOllama() as fake:
        fake.state.models = ["some-other:7b"]
        s = Settings(ollama_url=fake.url, model="llama3.1:8b")
        with pytest.raises(LLMError, match="ollama pull llama3.1:8b"):
            OllamaClient(s).check()


def test_unreachable_ollama_is_explained():
    s = Settings(ollama_url="http://127.0.0.1:9")
    with pytest.raises(LLMError, match="Cannot reach Ollama"):
        OllamaClient(s).check()


def test_qwen3_gets_think_false():
    with FakeOllama() as fake:
        fake.state.models = ["qwen3:8b"]
        s = Settings(ollama_url=fake.url, model="qwen3:8b", judge_samples=1)
        evaluate(read("1_poorly_written.txt"), "x", OllamaClient(s), s)
        assert all(p.get("think") is False for p in fake.state.requests)


def test_cli_writes_reports(tmp_path, capsys):
    with FakeOllama() as fake:
        code = cli_main([str(CASES / "3_ai_written.txt"), "--url", fake.url,
                         "--samples", "2", "--out", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Flag for human review" in out and "Confidence" in out
    data = json.loads((tmp_path / "3_ai_written.json").read_text(encoding="utf-8"))
    assert data["recommendation"] == "FLAG_FOR_REVIEW"
    assert (tmp_path / "3_ai_written.md").exists()


# --------------------------------------------------------------------------
# Regression: replay what llama3.1:8b actually did on the first real run
# --------------------------------------------------------------------------
def test_regression_poorly_written_not_flagged_as_ai():
    with FakeOllama() as fake:
        fake.state.replay_real_failures = True
        s = Settings(ollama_url=fake.url, judge_samples=3)
        r = evaluate(read("1_poorly_written.txt"), "x", OllamaClient(s), s)
    assert r.recommendation == Recommendation.REVISE_AND_RESUBMIT
    assert r.content_type == "poorly_written"
    assert any("do not support it" in n for n in r.confidence.notes)


def test_regression_ai_text_flagged_even_when_model_misses_it():
    with FakeOllama() as fake:
        fake.state.replay_real_failures = True
        s = Settings(ollama_url=fake.url, judge_samples=3)
        r = evaluate(read("3_ai_written.txt"), "x", OllamaClient(s), s)
    assert r.recommendation == Recommendation.FLAG_FOR_REVIEW
    assert r.content_type == "likely_ai_generated"
    assert r.judge_content_type == "highly_technical"
    # the model and the text signals disagreed, so confidence must not be High
    assert r.confidence.band != "High"
    assert any("text signals say" in n for n in r.confidence.notes)
    # specificity capped: "facial recognition technology" is the only "detail"
    spec = next(c for c in r.criteria if c.criterion_id == "specificity")
    assert spec.score == 3
