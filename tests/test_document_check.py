"""Tests for the research document vs references check (stand-in models, offline)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "research"
sys.path.insert(0, str(ROOT / "tests"))

import fake_models  # noqa: E402
from projectiq_integrity import document_check as dc  # noqa: E402
from projectiq_integrity.__main__ import main as cli  # noqa: E402
from projectiq_integrity.report_similarity import load_report  # noqa: E402

DOC, REFS = FIX / "S201_report.txt", FIX / "references"


@pytest.fixture
def models(monkeypatch):
    fake_models.install(monkeypatch)


def test_categories_percentages_and_chunk(models):
    rep = dc.check_document(DOC, REFS)
    cats = {m.text[:30]: m.category for m in rep.matches if m.category}
    assert list(cats.values()).count(dc.VERBATIM) == 4 and list(cats.values()).count(dc.NEAR) == 0
    assert list(cats.values()).count(dc.PARA) == 2
    # paraphrases share almost no words with their source
    assert all(m.lexical < 0.1 for m in rep.matches if m.category == dc.PARA)
    assert rep.overall_percent == pytest.approx(sum(rep.percent_by_category.values()), abs=0.2)
    assert set(rep.percent_by_reference) == {"single_stage_detection", "food_recognition_survey"}
    assert len(rep.chunks) == 1 and rep.chunks[0].sentences == 4 and rep.chunks[0].cited
    assert rep.chunks[0].reference == "single_stage_detection"


def test_quoted_text_and_reference_list_are_not_counted(models):
    rep = dc.check_document(DOC, REFS)
    quoted = [m for m in rep.matches if "Non-maximum suppression" in m.text]
    assert quoted and quoted[0].category is None
    assert not any("Redmon J." in m.text or "Bossard" in m.text for m in rep.matches)


def test_paraphrase_needs_deberta_in_both_directions(models, monkeypatch):
    class OneWay(fake_models.FakeNLI):
        def predict(self, premises, hypotheses):
            # document sentence implies the source, but not the other way round
            out = super().predict(premises, hypotheses)
            return [dict(o, entailment=0.9 if "Recognising" in p or "dominated by Western" in p else 0.1)
                    for o, p in zip(out, premises)]

    from projectiq_integrity.ria import models as rm
    monkeypatch.setattr(rm, "get_nli", lambda settings=None: OneWay())
    rep = dc.check_document(DOC, REFS)
    assert not any(m.category == dc.PARA for m in rep.matches)
    assert sum(m.category == dc.SIMILAR for m in rep.matches) == 2
    assert rep.percent_by_category[dc.PARA] == 0.0


def test_without_deberta_nothing_is_counted_as_paraphrase(models, monkeypatch):
    class RuleNLI(fake_models.FakeNLI):
        backend = "rule-based"
        is_neural = False

    from projectiq_integrity.ria import models as rm
    monkeypatch.setattr(rm, "get_nli", lambda settings=None: RuleNLI())
    rep = dc.check_document(DOC, REFS)
    assert rep.percent_by_category[dc.PARA] == 0.0
    assert any("DeBERTa was not used" in n for n in rep.notes)


def test_chunks_break_on_other_reference_or_gap():
    def m(i, ref, pos, cat=dc.VERBATIM):
        return dc.SentenceMatch(index=i, location=f"p.{i}", text=f"s{i}", words=10, category=cat,
                                reference=ref, reference_position=pos)
    run = [m(1, "A", 1), m(2, "A", 2), m(3, "A", 3)]
    assert len(dc.find_chunks(run)) == 1
    assert dc.find_chunks([m(1, "A", 1), m(2, "B", 2), m(3, "A", 3)]) == []
    gap = [m(1, "A", 1), m(2, "A", 2), m(3, "", -1, None), m(4, "", -1, None), m(5, "A", 3)]
    assert dc.find_chunks(gap) == []            # two unmatched sentences break the run
    one_gap = [m(1, "A", 1), m(2, "A", 2), m(3, "", -1, None), m(4, "A", 3)]
    assert len(dc.find_chunks(one_gap)) == 1    # one is allowed
    far = [m(1, "A", 1), m(2, "A", 2), m(3, "A", 40)]
    assert dc.find_chunks(far) == []            # jumps far in the source: not one chunk


def test_reference_heading_excluded_in_text_documents():
    doc = load_report(DOC)
    refs = [s for s in doc.sentences if s.section == "References"]
    assert refs and any("Redmon" in s.text for s in refs)
    assert not any("Redmon" in s.text or "Bossard" in s.text for s in doc.body_sentences())


def test_headingless_document_is_still_checked(models, tmp_path):
    from dataclasses import replace

    from projectiq_integrity.ria.pdf_extractor import FRONT_MATTER
    doc = load_report(DOC)
    as_front = replace(doc, sentences=[replace(s, section=FRONT_MATTER) for s in doc.sentences])
    notes = []
    rescued = dc._rescue_front_matter(as_front, notes)
    assert notes and all(s.section == "Body" for s in rescued.sentences)


def test_findings_and_outputs(models, tmp_path):
    rep = dc.check_document(DOC, REFS)
    paths = dc.write_outputs(rep, tmp_path, "S201")
    result = json.loads(paths["agent_result"].read_text())
    kinds = [f["kind"] for f in result["findings"]]
    assert "copied_chunk" in kinds and kinds.count("similar_report_text") == 2
    assert all(f["evidence"] for f in result["findings"])
    html = paths["report_html"].read_text()
    assert "32.9%" in html or f"{rep.overall_percent}%" in html
    assert "cited, not quoted" in html


def test_cli_single_and_batch(models, tmp_path, capsys):
    assert cli(["--document", str(DOC), "--references", str(REFS), "--out", str(tmp_path / "one")]) == 0
    assert (tmp_path / "one" / "S201_report_similarity.html").exists()
    batch = tmp_path / "cohort" / "S201"
    (batch / "references").mkdir(parents=True)
    (batch / "report.txt").write_text(DOC.read_text())
    for f in REFS.iterdir():
        (batch / "references" / f.name).write_bytes(f.read_bytes())
    (tmp_path / "cohort" / "S202").mkdir()          # no document: skipped, not a crash
    assert cli(["--submissions", str(tmp_path / "cohort"), "--out", str(tmp_path / "many")]) == 0
    summary = (tmp_path / "many" / "summary.csv").read_text()
    assert "S201" in summary and "S202" not in summary
    assert "skipped" in capsys.readouterr().out


def test_runs_with_the_real_model_classes():
    """No stand-ins: the analyzer's own Embedder and NLIModel classes (in their
    fallback mode, since models are not installed in CI). Guards against calling
    their properties wrongly, which stand-ins cannot catch."""
    from projectiq_integrity.config import IntegritySettings
    from projectiq_integrity.ria.config import DEFAULT_SETTINGS

    # candidate threshold 0: every sentence goes to the NLI step, so its code path always runs
    rep = dc.check_document(DOC, REFS, settings=IntegritySettings(paraphrase_candidate_cosine=0.0),
                            ria_settings=DEFAULT_SETTINGS.with_overrides(allow_model_fallback=True))
    assert rep.sentences_checked > 0
    assert len(rep.chunks) == 1                       # word overlap alone finds the copied chunk
    if "not neural" in rep.backends["nli"]:           # models not installed: lexical fallback
        assert rep.percent_by_category[dc.PARA] == 0.0  # nothing may count as paraphrase
    else:                                             # real DeBERTa installed (a normal laptop)
        assert rep.backends["nli"] == "transformers-nli"


def test_hyphens_dashes_and_case_do_not_change_verbatim():
    words, chars = dc.copy_overlap("They run well in real time and are Two Stage.",
                                   "They run well in real-time and are two–stage.", 3)
    assert words == 1.0
    _, typo = dc.copy_overlap("Single stage detectors predict boundng boxes from full images.",
                              "Single-stage detectors predict bounding boxes from full images.", 3)
    assert typo >= 0.95                                # one-letter typo: still verbatim
    words, chars = dc.copy_overlap("The model is usually evaluated with mean average precision.",
                                   "We trained the network for 120 epochs on a single GPU.", 3)
    assert words < 0.45 and chars < 0.95               # unrelated: neither rule fires


def test_copied_paragraph_is_all_verbatim(models):
    rep = dc.check_document(DOC, REFS)
    chunk_sents = [m for m in rep.matches if m.reference == "single_stage_detection" and m.category]
    assert len(chunk_sents) == 4 and all(m.category == dc.VERBATIM for m in chunk_sents)
