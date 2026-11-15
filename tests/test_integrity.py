"""Offline tests for the Integrity Agent. No model downloads; JPlag itself runs only
when Java 21+ and tools/jplag-6.1.0.jar are present (otherwise that test is skipped)."""
import json
import math
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "integrity"
sys.path.insert(0, str(ROOT / "scripts"))

from projectiq_integrity import jplag_runner as jr  # noqa: E402
from projectiq_integrity.agent import IntegrityAgent  # noqa: E402
from projectiq_integrity.ai_indicators import ai_signals  # noqa: E402
from projectiq_integrity.config import JPLAG_JAR  # noqa: E402
from projectiq_integrity.contracts import Evidence  # noqa: E402
from projectiq_integrity.evidence import Signal, build_findings, estimate_confidence  # noqa: E402
from projectiq_integrity.report_similarity import load_folder, report_signals, template_sentences  # noqa: E402
from projectiq_integrity.ria.config import DEFAULT_SETTINGS as RIA  # noqa: E402

# Lexical-only run: the real MiniLM is not needed for these tests.
RIA_TEST = RIA.with_overrides(allow_model_fallback=True, use_embeddings=False)


# --------------------------------------------------------------------------- #
# Fake perplexity scorers (sentence -> summed NLL, tokens)
# --------------------------------------------------------------------------- #
def fake_scorer(level: float, spread: float):
    def score(sentences):
        out = []
        for i, s in enumerate(sentences):
            n = len(s.split()) + 2
            out.append((s, math.log(level * (1 + spread * ((i % 3) - 1))) * n, n))
        return out
    return score


AI_LIKE = fake_scorer(18, 0.1)      # predictable and even
HUMAN_LIKE = fake_scorer(70, 0.6)   # less predictable, uneven


def report(sid):
    return (FIX / "reports" / f"{sid}.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# JPlag
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("output,expected", [
    ('openjdk version "21.0.12" 2026-08-18', 21),
    ('java version "17.0.12" 2024-07-16 LTS', 17),
    ('java version "1.8.0_392"', 8),
    ('openjdk version "25" 2025-09-16', 25),
    ("nothing useful", -1),
])
def test_java_version_parsing(monkeypatch, output, expected):
    monkeypatch.setattr(jr.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", output))
    assert jr.java_major_version() == expected


def test_old_java_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(jr, "java_major_version", lambda java="java": 17)
    with pytest.raises(jr.JPlagError, match="needs Java 21"):
        jr.check_environment(tmp_path / "jplag.jar")


def test_notebook_converts_and_comments_magics(tmp_path):
    nb = {"cells": [{"cell_type": "markdown", "source": ["# title"]},
                    {"cell_type": "code", "source": ["%matplotlib inline\n", "x = 1\n"]},
                    {"cell_type": "code", "source": "!pip install foo\nprint(x)"}]}
    p = tmp_path / "a.ipynb"
    p.write_text(json.dumps(nb))
    code = jr.notebook_to_script(p)
    assert "# %matplotlib inline" in code and "x = 1" in code and "# !pip install foo" in code
    assert "title" not in code
    compile(code, "a.py", "exec")


def test_prepare_keeps_files_separate_and_blocks_unsafe_zip(tmp_path):
    src = tmp_path / "subs"
    shutil.copytree(FIX / "code" / "S101_aarav", src / "S101_aarav")
    (src / "S101_aarav" / "__pycache__").mkdir()
    (src / "S101_aarav" / "__pycache__" / "x.pyc").write_bytes(b"0")
    with zipfile.ZipFile(src / "S200_zip.zip", "w") as zf:
        zf.writestr("proj/main.py", "print('hi')\n")
        zf.writestr("../../evil.py", "boom\n")
    nb = {"cells": [{"cell_type": "code", "source": "y = 2"}]}
    (src / "S300_nb.ipynb").write_text(json.dumps(nb))
    counts = jr.prepare_submissions(src, tmp_path / "work")
    assert counts == {"S101_aarav": 3, "S200_zip": 1, "S300_nb": 1}
    assert (tmp_path / "work" / "S101_aarav" / "inventory.py").exists()       # not merged
    assert not (tmp_path / "work" / "S101_aarav" / "__pycache__").exists()
    assert (tmp_path / "work" / "S200_zip" / "main.py").exists()
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "work" / "evil.py").exists()


def test_parse_result_lines_and_starter_exclusion():
    rep = jr.parse_result(FIX / "sample_results.jplag")
    assert rep.version == "6.1.0" and len(rep.submissions) == 5
    pair = next(p for p in rep.pairs if {p.id1, p.id2} == {"S101_aarav", "S102_bhavna"})
    assert pair.max_similarity > 0.85
    m = pair.matches[0]
    assert m.file1.endswith(("inventory.py", "stock.py")) and m.lines1[1] > m.lines1[0]
    # starter code is matched to the base code, never to another student
    assert all("common.py" not in (mm.file1 + mm.file2) for p in rep.pairs for mm in p.matches)
    assert all(v > 0 for v in rep.base_code_tokens.values())


def test_code_signals_quote_both_sides_and_respect_threshold():
    rep = jr.parse_result(FIX / "sample_results.jplag")
    findings = build_findings(jr.code_signals(rep, "S101_aarav"))
    assert len(findings) == 1 and findings[0].kind == "similar_code"
    locs = [e.location for e in findings[0].evidence]
    assert locs[0].startswith("S101_aarav/inventory.py:") and locs[1].startswith("S102_bhavna/stock.py:")
    assert "class Inventory" in findings[0].evidence[0].quote
    assert build_findings(jr.code_signals(rep, "S105_eshan")) == []
    partial = build_findings(jr.code_signals(rep, "S103_chirag"))
    assert partial and partial[0].confidence < findings[0].confidence   # short match, lower confidence


@pytest.mark.skipif(not JPLAG_JAR.exists() or jr.java_major_version() < 21,
                    reason="needs Java 21+ and tools/jplag-6.1.0.jar")
def test_jplag_runs_end_to_end(tmp_path):
    jr.prepare_submissions(FIX / "code", tmp_path / "code")
    result = jr.run_jplag(tmp_path / "code", tmp_path / "out" / "r", base_code=FIX / "starter")
    rep = jr.parse_result(result)
    top = max(rep.pairs, key=lambda p: p.max_similarity)
    assert {top.id1, top.id2} == {"S101_aarav", "S102_bhavna"}


# --------------------------------------------------------------------------- #
# Report similarity
# --------------------------------------------------------------------------- #
def test_report_similarity_finds_copies_and_ignores_template():
    cohort, refs = load_folder(FIX / "reports"), load_folder(FIX / "references")
    shared = template_sentences(cohort)
    assert any("bonafide record" in s for s in shared)          # certificate text found
    f101 = build_findings(report_signals("S101_aarav", cohort, refs, ria_settings=RIA_TEST, shared=shared))
    assert f101 and "S102_bhavna" in f101[0].summary
    assert all("bonafide" not in e.quote for f in f101 for e in f.evidence)
    f104 = build_findings(report_signals("S104_diya", cohort, refs, ria_settings=RIA_TEST, shared=shared))
    assert any("edit_distance_notes" in f.summary for f in f104)
    assert build_findings(report_signals("S105_eshan", cohort, refs, ria_settings=RIA_TEST, shared=shared)) == []


# --------------------------------------------------------------------------- #
# AI-style text: both checks must agree
# --------------------------------------------------------------------------- #
def test_ai_flag_needs_both_checks():
    text = report("S103_chirag")
    flagged, _ = ai_signals(text, scorer=AI_LIKE)
    assert len(flagged) == 1
    f = build_findings(flagged)[0]
    assert f.confidence <= 0.7 and any("stock phrases" in e.location for e in f.evidence)
    assert ai_signals(text, scorer=HUMAN_LIKE)[0] == []        # model disagrees
    assert ai_signals(report("S101_aarav"), scorer=AI_LIKE)[0] == []  # style disagrees


def test_ai_vetoes_and_missing_model(tmp_path, monkeypatch):
    signals, notes = ai_signals(report("S105_eshan"), scorer=AI_LIKE)
    assert signals == [] and "human pattern" in notes[-1]
    signals, notes = ai_signals("Too short. " * 5, scorer=AI_LIKE)
    assert signals == [] and "too short" in notes[-1]
    import projectiq_integrity.ai_indicators as ai
    monkeypatch.setattr(ai, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ai.LocalLM.__init__, "__defaults__", ("distilgpt2", tmp_path))
    signals, notes = ai_signals(report("S103_chirag"))
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
    strong = Signal("similar_code", "", [], {"max_similarity": 1.0, "matched_tokens": 500})
    weak = Signal("similar_code", "", [], {"max_similarity": 0.5, "matched_tokens": 20,
                                            "smaller_submission_tokens": 60})
    ai = Signal("ai_style_text", "", [], {"style_margin": 5, "perplexity_margin": 5})
    assert estimate_confidence(strong) <= 0.95 and estimate_confidence(weak) < estimate_confidence(strong)
    assert estimate_confidence(ai) == 0.7


# --------------------------------------------------------------------------- #
# Agent end to end (reports only, so no Java needed)
# --------------------------------------------------------------------------- #
def test_agent_reports_only(tmp_path):
    agent = IntegrityAgent(tmp_path, reports_dir=FIX / "reports", references_dir=FIX / "references",
                           ria_settings=RIA_TEST, scorer=AI_LIKE).prepare()
    res = agent.analyze("S103_chirag")
    assert res.agent == "integrity" and [f.kind for f in res.findings] == ["ai_style_text"]
    assert any("never assigns" in n for n in res.notes)
    assert agent.analyze("S105_eshan").findings == []


# --------------------------------------------------------------------------- #
# Validation metrics
# --------------------------------------------------------------------------- #
def test_validation_metrics():
    from validate import agreement, controls_report, quadratic_weighted_kappa, read_csv, spearman

    assert quadratic_weighted_kappa([1, 2, 3], [1, 2, 3], 0, 5) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    m = agreement([5, 6, 7, 8], [6, 6, 7, 9], 10)
    assert m["mean_abs_error"] == pytest.approx(0.5) and m["mean_signed_error"] == pytest.approx(0.5)
    assert m["within_1"] == 1.0


def test_validation_controls(tmp_path):
    from validate import controls_report, read_csv

    out = tmp_path / "integrity"
    out.mkdir()
    agent = IntegrityAgent(tmp_path / "w", reports_dir=FIX / "reports", references_dir=FIX / "references",
                           ria_settings=RIA_TEST, scorer=AI_LIKE).prepare()
    for sid in agent.submission_ids():
        (out / f"{sid}.json").write_text(agent.analyze(sid).model_dump_json())
    rep = controls_report(read_csv(FIX / "validation" / "controls_example.csv"), out)
    assert rep["similar_report_text"]["detection_rate"] == 1.0
    assert rep["similar_report_text"]["false_flag_rate"] == 0.0
    assert rep["ai_style_text"]["tp"] == 1 and rep["ai_style_text"]["fp"] == 0
