"""Phase 3 explanation/structured-evidence consistency, fully offline.

These tests do not alter or calibrate relevance labels. They isolate whether a
weak citation-use hint is incorrectly described as a corroborated adopted method.
"""

from projectiq_research import relevance_scorer as scorer
from test_research_phase3 import FakeCrossEncoder, FakeEncoder, fixture


def test_uncorroborated_method_cue_is_not_presented_as_proven(monkeypatch):
    doc, extraction = fixture()
    # Reproduce the [8] Tesseract failure mode deterministically: the nearby
    # sentence sounds like adoption, but reference-specific checks fail.
    monkeypatch.setattr(scorer, "_cited_method_adoption", lambda *args: True)
    monkeypatch.setattr(scorer, "_specific_method_support",
                        lambda *args: ("none", [], []))

    extraction.relevance_results = scorer.assess_relevance(
        extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())

    for result in extraction.relevance_results:
        assert result.method_evidence_strength == "none"
        assert result.method_evidence_traces == []
        assert result.method_use_evidence == []
        assert any("cue was not counted" in note for note in result.notes)
        assert not any("explicitly adopted, named technique was found" in note.lower()
                       for note in result.notes)
    # The narrative and structured fields agree after JSON serialization too.
    payload = extraction.to_dict()["relevance_results"][0]
    assert payload["method_evidence_strength"] == "none"
    assert payload["method_evidence_traces"] == []
    assert any("cue was not counted" in note for note in payload["notes"])


def test_corroborated_cue_mentions_support_but_not_proven_claim_support(monkeypatch):
    doc, extraction = fixture()
    monkeypatch.setattr(scorer, "_cited_method_adoption", lambda *args: True)
    monkeypatch.setattr(scorer, "_specific_method_support",
                        lambda *args: ("moderate", ["Specific method corroboration"], ["kalman filter"]))

    results = scorer.assess_relevance(
        extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert results[0].method_evidence_strength == "moderate"
    assert results[0].method_evidence_traces == ["Specific method corroboration"]
    assert any("corroborating method evidence" in note for note in results[0].notes)
    assert any("not independently verified" in note for note in results[0].notes)
    assert not any("cue was not counted" in note for note in results[0].notes)


def test_no_method_cue_creates_no_cue_note(monkeypatch):
    doc, extraction = fixture()
    monkeypatch.setattr(scorer, "_cited_method_adoption", lambda *args: False)
    monkeypatch.setattr(scorer, "_specific_method_support",
                        lambda *args: ("none", [], []))

    results = scorer.assess_relevance(
        extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert not any("citation-level method-use cue" in note for note in results[0].notes)
