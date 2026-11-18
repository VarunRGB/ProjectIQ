"""Phase 3 v1.2 generalization tests. No domain-specific trained thresholds.

These tests use unrelated report domains, counterfactual citation placements,
and synthetic scores. They don't fine-tune or download any neural model and are
not an accuracy benchmark. The real-model PDF test remains the user's next step.
"""
import numpy as np
from projectiq_research.models import ParsedDocument
from projectiq_research.relevance_context import prepare_relevance_context
from projectiq_research.relevance_scorer import (
    _best_pair, _cited_method_adoption, _classification, _method_anchors,
    assess_relevance,
)
from test_research_phase3 import fixture, FakeEncoder, FakeCrossEncoder


def test_direct_topical_match_ignores_literature_and_citation_position():
    common = dict(topical=0.53, method=0.60, domain_overlap=2,
                  crosses={"topic": 1.0, "methodology": 0.5},
                  has_abstract=True, verified=True)
    for section, context in [
        (["literature review"], ["A real paper [1] considers bridge vibration."]),
        (["methodology"], ["Cited in a section talking about an unrelated method [1]."]),
        ([], []),
    ]:
        label, _ = _classification(context=0.97 if context else None,
                                   section_names=section, context_snippets=context, **common)
        assert label == "directly_relevant"


def test_misleading_placement_never_creates_methodology_relevance():
    # Very high context similarity is the attack. Independent paper/method
    # similarity and specific technical evidence are deliberately absent.
    for report_domain, paper_domain in [
        ("water quality", "bird migration"),
        ("bridge vibrations", "ceramic archaeology"),
        ("character recognition", "coral conservation"),
        ("soil moisture mapping", "economic inequality"),
    ]:
        label, _ = _classification(
            topical=0.20, method=0.22, context=0.99, domain_overlap=0,
            section_names=["methodology"],
            context_snippets=[f"We cite {paper_domain} in a {report_domain} methods paragraph [27]."],
            crosses={"topic": -3.0, "methodology": 4.0,
                     "citation_context": 9.0},
            has_abstract=True, verified=True,
        )
        assert label == "possibly_unrelated"


def test_named_method_requires_independent_method_evidence_or_adoption():
    # A specific Kalman filter is different from generic AI/classification.
    anchors = _method_anchors(["We estimate vibration using an extended Kalman filter."],
                              ["Kalman filter for structural vibration detection"])
    assert "kalman" in anchors
    label, _ = _classification(
        topical=0.32, method=0.52, context=0.96, domain_overlap=0,
        section_names=["methodology"], context_snippets=["Paper cited."],
        crosses={}, has_abstract=True, verified=True,
        independent_method=0.50, method_anchor_count=len(anchors),
    )
    assert label == "methodologically_relevant"
    decoy = _method_anchors(["We train convolutional neural networks to classify images."],
                            ["CNN-based classification of coral reef photographs"])
    assert not decoy


def test_explicit_adoption_versus_misleading_citation_wording():
    papers = ["Kalman filters for structural vibration estimation"]
    assert _cited_method_adoption(
        ["methodology"], ["We applied Kalman filters [4] to estimate vibration."], papers)
    assert not _cited_method_adoption(
        ["methodology"], ["We cite Kalman filters [4] as inspiration for testing vibration."], papers)
    assert not _cited_method_adoption(
        ["literature review"], ["We applied Kalman filters [4] to estimate vibration."], papers)
    assert not _cited_method_adoption(
        ["methodology"], ["We used CNN models [4] to classify images."],
        ["Convolutional neural networks for image classification"])


def test_each_reranker_axis_chooses_its_own_paper_passage():
    # Regression: previously all axes used whichever paper passage matched TOPIC.
    embeddings = {
        "research objective": np.array([1.0, 0.0]),
        "we implement filter": np.array([0.0, 1.0]),
        "paper title": np.array([1.0, 0.0]),
        "paper abstract about filtering": np.array([0.0, 1.0]),
    }
    top = _best_pair(["research objective"],
                     ["paper title", "paper abstract about filtering"], embeddings)
    method = _best_pair(["we implement filter"],
                        ["paper title", "paper abstract about filtering"], embeddings)
    assert top[1:] == ("research objective", "paper title")
    assert method[1:] == ("we implement filter", "paper abstract about filtering")


def test_topic_profile_is_not_redefined_by_noisy_literature_review():
    doc, ex = fixture()
    stable = prepare_relevance_context(doc, ex).topic_passages
    noisy = doc.text.replace(
        "2. METHOD", "LITERATURE REVIEW\nA long passage about maritime archaeology "
        "and coral reefs is cited [1].\n2. METHOD")
    altered = prepare_relevance_context(ParsedDocument("test.txt", noisy), ex)
    assert altered.topic_passages == stable


def test_citation_context_does_not_promote_an_unrelated_paper():
    doc, ex = fixture()
    ex.papers[1].title = "Molecular composition of seabird feathers"
    ex.verification_results[1].verified_title = ex.papers[1].title
    ex.verification_results[1].verified_abstract = (
        "This study analyzes marine bird feathers and migratory patterns."
    )
    in_intro = assess_relevance(ex, doc, encoder=FakeEncoder(),
                                cross_encoder=FakeCrossEncoder())
    ex.citation_reference_matches[1]["line_number"] = 8  # Methods paragraph
    in_methods = assess_relevance(ex, doc, encoder=FakeEncoder(),
                                  cross_encoder=FakeCrossEncoder())
    assert in_intro[1].classification != "methodologically_relevant"
    assert in_methods[1].classification != "methodologically_relevant"


def test_title_skips_running_header_and_multiline_cover_title():
    doc, ex = fixture()
    doc.text = """PROJECT BUILD / EXPERIMENT REVIEW
VALIDATION DOCUMENT FOR TECHNICAL REPORT
Page 1
Vibration Prediction in Suspended Bridges
With Kalman Filtering
Abstract
We predict the vibration of bridges using time-series measurements.
Methodology
We filter sensor readings with a Kalman filter.
References
[1] A. Author, Paper title, 2024.
"""
    ex.references.start_line = 10
    context = prepare_relevance_context(doc, ex)
    assert context.title.startswith("Vibration Prediction in Suspended Bridges")
    assert "PROJECT BUILD" not in context.title
    assert "VALIDATION DOCUMENT" not in context.topic


def test_45_reference_pdf_remains_parseable_without_network():
    from pathlib import Path
    from projectiq_research.agent import analyze_research_document
    path = Path("/mnt/data/projectiq_phase3_comprehensive_stress_test.pdf")
    if not path.exists():
        import pytest
        pytest.skip("PDF fixture absent")
    result = analyze_research_document(
        path, verify=False, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert len(result.papers) == 45
    context = prepare_relevance_context(
        __import__("projectiq_research.document_parser", fromlist=["load_document"]).load_document(path), result)
    assert "Character Recognition in Natural Images" in context.title
    assert "Transfer-Learning Study" in context.title
    assert len(result.relevance_results) == 45
    assert all(x.reference_index == i for i, x in enumerate(result.relevance_results))
