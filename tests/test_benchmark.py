"""The benchmark paper: every case decided by words (not meaning) must match the answer
key even with stand-in models. Paraphrase cases need the real MiniLM and DeBERTa, so
they are checked on a laptop with python -m projectiq_integrity.benchmark."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import fake_models  # noqa: E402
from projectiq_integrity.benchmark import BENCH, evaluate  # noqa: E402
from projectiq_integrity.document_check import check_document  # noqa: E402

WORD_CASES = {"V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "V10", "V11",
              "N1", "N2", "N3", "N4", "H1", "H2", "H4"}


def test_benchmark_word_based_cases(monkeypatch):
    fake_models.install(monkeypatch)
    report = check_document(BENCH / "student_paper.pdf", BENCH / "references", check_ai=False)
    res = evaluate(report, BENCH / "answer_key.csv", BENCH / "expected_chunks.csv")
    wrong = [f"{c.case}: expected {c.expected}, got {c.got}" for c in res.cases
             if c.case in WORD_CASES and not c.ok]
    assert not wrong, wrong
    assert not [c for c in res.cases if c.case == "own" and c.got not in ("none", "not checked (quoted)")]
    assert res.chunks_missing == [] and len(res.chunks_found) == 2
    assert not res.missing_sentences
    assert "crop_yield_prediction" not in report.percent_by_reference   # unrelated paper: no match
