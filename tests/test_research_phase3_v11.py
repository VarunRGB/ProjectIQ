"""Regression tests for Phase 3 v1.1. All HTTP and transformer calls are mocked."""
from dataclasses import asdict

import pytest

from projectiq_research.paper_resolver import AcademicPaperResolver, _openalex_abstract
from projectiq_research.verification import verify_papers
from projectiq_research.verification_models import AcademicPaperMetadata, PaperVerification
from projectiq_research.relevance_scorer import _classification, assess_relevance
from projectiq_research.relevance_context import prepare_relevance_context
from projectiq_research.models import ParsedDocument, Section

from test_research_phase3 import fixture, FakeEncoder, FakeCrossEncoder


class MockResolver:
    def __init__(self, record):
        self.record = record
    def resolve(self, paper):
        return self.record


def test_openalex_abstract_inverse_index_decoding():
    idx = {"Scene": [0], "text": [1, 6], "recognition": [2], "using": [3],
           "CNN": [4], "models": [5]}
    assert _openalex_abstract(idx) == "Scene text recognition using CNN models text"
    assert _openalex_abstract({"garbage": [999999, -3, "a"]}) is None
    assert _openalex_abstract(None) is None


def test_openalex_title_search_keeps_api_key_and_retrieves_abstract(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "test_key")
    params_seen = []
    resolver = AcademicPaperResolver()
    def fake_get_json(url, params=None):
        params_seen.append(params)
        return {"results": [{"id": "https://openalex.org/W1", "title": "Scene Text Recognition With CNN",
                             "publication_year": 2023, "authorships": [], "primary_location": {},
                             "abstract_inverted_index": {"Scene": [0], "text": [1], "recognition": [2]}}]}
    monkeypatch.setattr(resolver, "_get_json", fake_get_json)
    found = resolver._openalex_candidates("scene text")
    assert params_seen[0]["api_key"] == "test_key"
    assert "abstract_inverted_index" in params_seen[0]["select"]
    assert found[0].abstract == "Scene text recognition"


def test_crossref_abstract_strips_xml_markup_and_html_entities():
    item = {"title": ["Scene Text Recognition"], "abstract":
            "<jats:p>Methods &amp; results on <jats:italic>scene text</jats:italic>.</jats:p>"}
    out = AcademicPaperResolver._crossref_metadata(item)
    assert out.abstract == "Methods & results on scene text ."


def test_verification_retains_abstract_only_for_credible_identity():
    doc, ex = fixture()
    paper = ex.papers[0]
    metadata = AcademicPaperMetadata("openalex", "W1", paper.title, ["A Smith"], 2022,
                                     abstract="A CNN study for recognizing text in natural images.")
    result = verify_papers([paper], MockResolver(metadata))[0]
    assert result.status == "verified"
    assert result.verified_abstract == metadata.abstract
    assert asdict(result)["verified_abstract"] == metadata.abstract

    wrong = AcademicPaperMetadata("crossref", "W9", "Unrelated Planetary Dynamics", ["Other Author"], 2022,
                                  abstract="Abstract about astronomy.")
    rejected = verify_papers([paper], MockResolver(wrong))[0]
    assert rejected.status in {"metadata_mismatch", "not_found"}
    assert rejected.verified_abstract is None


def test_phase3_automatically_uses_verified_abstract_and_does_not_mutate_phase2():
    document, ex = fixture()
    ex.verification_results[0].verified_abstract = "Recognizing natural scene text with convolutional neural networks."
    before = ex.verification_results[0].verified_abstract
    out = assess_relevance(ex, document, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert "paper_abstract" in out[0].evidence_sources
    assert "paper_abstract" not in out[1].evidence_sources
    assert ex.verification_results[0].verified_abstract == before


def test_mismatch_does_not_automatically_trust_external_abstract():
    document, ex = fixture()
    ex.verification_results[0].status = "metadata_mismatch"
    ex.verification_results[0].verified_abstract = "Potentially unrelated external metadata."
    out = assess_relevance(ex, document, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert "paper_abstract" not in out[0].evidence_sources
    assert out[0].confidence == "low"


def test_methods_passages_cover_later_subsections_and_provenance():
    text = """Scene Text Recognition With CNN
ABSTRACT
We study scene text recognition from images.
1. INTRODUCTION
Scene text recognition is the subject of this report [1].
2. METHOD
""" + "\n".join(["Augmentation and pre-processing are applied to images for training." for _ in range(38)]) + """
2.6. VGG network
We use the VGG-16 CNN architecture, including small 3x3 filters [2].
3. RESULTS
We report accuracy.
REFERENCES
[1] A. Researcher, Scene text recognition advances, 2020.
[2] B. Researcher, Very Deep Convolutional Networks, 2015.
"""
    doc = ParsedDocument("report.txt", text)
    lines = text.splitlines()
    r = Section("references", True, start_line=lines.index("REFERENCES")+1, end_line=len(lines))
    ex = fixture()[1]
    ex.references = r
    ex.citation_reference_matches = [
        {"line_number": 5, "matched_paper_indices": [0]},
        {"line_number": lines.index("We use the VGG-16 CNN architecture, including small 3x3 filters [2].")+1,
         "matched_paper_indices": [1]},
    ]
    ctx = prepare_relevance_context(doc, ex)
    assert any("VGG-16" in chunk for chunk in ctx.methodology_passages)
    assert all(len(chunk) <= 620 for chunk in ctx.methodology_passages)
    assert ctx.citation_sections[0] == ["introduction"]
    assert ctx.citation_sections[1] == ["method"]
    assert "A. Researcher" not in " ".join(ctx.methodology_passages)


def test_direct_topical_context_not_misclassified_as_method():
    result, _ = _classification(
        topical=0.49, method=0.62, context=0.72,
        domain_overlap=2, section_names=["introduction"],
        context_snippets=["Scene text detection and recognition is an active research topic."],
        crosses={"topic": -1.0, "methodology": -0.5},
        has_abstract=False, verified=True)
    assert result == "directly_relevant"


def test_citation_section_alone_no_longer_confirms_method_relevance():
    result, _ = _classification(
        topical=0.43, method=0.31, context=0.43,
        domain_overlap=1, section_names=["method"],
        context_snippets=["The VGG architecture is used in this study [16]."],
        crosses={"topic": -3.0, "methodology": -1.0},
        has_abstract=False, verified=True)
    # Phase 3 v1.2: section and citation similarity alone are not evidence.
    assert result == "partially_relevant"


def test_abstract_plus_unrelated_text_does_not_automatically_confirm_relevance():
    label, _ = _classification(
        topical=0.08, method=0.12, context=0.13, domain_overlap=0,
        section_names=["introduction"], context_snippets=["Cited casually."],
        crosses={}, has_abstract=True, verified=True)
    assert label == "possibly_unrelated"


def test_crossencoder_can_resolve_borderline_intro_paper():
    label, _ = _classification(
        topical=0.45, method=0.50, context=0.48, domain_overlap=2,
        section_names=["introduction"], context_snippets=["Scene text is recognized."],
        crosses={"topic": 2.0, "methodology": 0.0},
        has_abstract=True, verified=True)
    assert label == "directly_relevant"


def test_topic_related_paper_not_downgraded_by_a_method_word_in_citation():
    label, _ = _classification(
        topical=0.51, method=0.42, context=0.65, domain_overlap=2,
        section_names=["introduction"],
        context_snippets=["Transfer learning helps train on small datasets [6]."],
        crosses={"topic": -1.0, "methodology": -1.2},
        has_abstract=False, verified=True,
        paper_title="Knowledge Transfer for Handwritten Text Recognition")
    assert label == "directly_relevant"
