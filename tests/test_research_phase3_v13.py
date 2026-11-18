"""Phase 3 v1.3 boundary tests, without downloading/fine-tuning models.

These exercise general decision rules with independently specified similarity
signals. They intentionally cover several *unrelated* report domains. They do
not establish end-to-end accuracy on pretrained semantic models.
"""
import pytest
from projectiq_research.relevance_scorer import (
    _classification, _problem_phrase, _specific_subject_terms,
    _is_technique_led, _method_adoption_evidence, _relevant_method_ngrams,
    _method_anchors,
    assess_relevance,
)
from projectiq_research.relevance_context import prepare_relevance_context
from projectiq_research.models import ParsedDocument
from test_research_phase3 import fixture, FakeEncoder, FakeCrossEncoder


def judge(*, topical=0.50, method=0.52, problem_similarity=0.71,
          specific=0, technique_led=False, method_usage=False, independent=0.48,
          context=0.8, crosses=None, verified=True, has_abstract=True):
    return _classification(
        topical=topical, method=method, context=context,
        domain_overlap=2, section_names=["methodology"],
        context_snippets=["Method cited here."],
        crosses=crosses or {}, has_abstract=has_abstract, verified=verified,
        problem_similarity=problem_similarity, subject_overlap=specific,
        technique_led=technique_led, method_usage=method_usage,
        independent_method=independent, method_anchor_count=0,
    )[0]


@pytest.mark.parametrize("report_topic,paper_task", [
    ("Character Recognition in Natural Images using CNNs", "Scene Text Recognition with Language Models"),
    ("Structural Vibration Prediction for Suspension Bridges", "Prediction of Vibrations in Civil Bridges"),
    ("Water Quality Forecasting for Urban Lakes", "Forecasting Water Quality in Freshwater Reservoirs"),
    ("Malware Detection on Computer Networks", "Detecting Malicious Network Traffic"),
])
def test_task_clause_extraction_is_domain_independent(report_topic, paper_task):
    assert _problem_phrase(report_topic)
    assert _problem_phrase(paper_task)
    assert not _is_technique_led(paper_task)


@pytest.mark.parametrize("method_title", [
    "Deep Residual Learning for Image Recognition",
    "Convolutional Neural Network with Transfer Learning for Classification of Food Types",
    "Very Deep Convolutional Networks for Large-Scale Image Recognition",
])
def test_technique_first_titles_are_not_direct_by_generic_task_overlap(method_title):
    assert _is_technique_led(method_title)
    assert judge(technique_led=True, specific=0, problem_similarity=0.71) != "directly_relevant"
    assert judge(technique_led=True, specific=0, problem_similarity=0.71, method_usage=True) == "methodologically_relevant"


def test_direct_problem_remains_direct_despite_strong_method_scores():
    assert judge(problem_similarity=0.68, topical=0.44, method=0.92,
                 independent=0.80, specific=0, technique_led=False) == "directly_relevant"


def test_weak_problem_similarity_and_generic_shared_words_do_not_imply_direct():
    assert judge(problem_similarity=0.48, topical=0.59, method=0.59,
                 specific=0, technique_led=False) == "partially_relevant"


def test_crossencoder_does_not_overrule_a_task_mismatch():
    assert judge(problem_similarity=0.33, topical=0.25,
                 method=0.22, independent=0.18, specific=0,
                 crosses={"topic": 8.0, "methodology": -3.0}) == "possibly_unrelated"


def test_citation_context_cannot_turn_pottery_into_method():
    assert judge(problem_similarity=0.24, topical=0.22,
                 method=0.20, independent=0.15, context=0.99,
                 specific=0, technique_led=False) == "possibly_unrelated"


def test_report_used_gradient_descent_can_support_methodology():
    assert _relevant_method_ngrams("An overview of gradient descent optimization algorithms")
    method, evidence = _method_adoption_evidence(
        ["Optimization uses cross-entropy loss and gradient descent, with early layers frozen [12, 13]."],
        "An overview of gradient descent optimization algorithms", [], ["methodology"], None,
    )
    assert method
    assert any("gradient descent" in item for item in evidence)
    assert judge(problem_similarity=0.18, topical=0.19, method=0.38,
                 independent=0.14, technique_led=True, method_usage=method) == "methodologically_relevant"


def test_domain_names_are_not_specific_method_ngrams():
    assert not ("ancient chinese" in _relevant_method_ngrams(
        "Deep Learning to Identify Ancient Chinese Ceramics"))
    method, evidence = _method_adoption_evidence(
        ["The report cites archaeological ceramic work as inspiration for its image-processing strategy."],
        "Deep Learning to Identify Ancient Chinese Ceramics", [], ["methodology"], 0.56,
    )
    assert not method
    assert not evidence


def test_survey_of_technical_methods_for_a_problem_is_not_automatically_method_first():
    title = "Deep learning approaches to earthquake prediction: a comprehensive review"
    assert _problem_phrase(title).casefold().startswith("earthquake prediction")
    assert not _is_technique_led(title)


def test_implementation_requires_concrete_citation_evidence_for_alias_fallback():
    method, evidence = _method_adoption_evidence(
        ["We compare feature encoders using the same evaluation metrics."],
        "Very Deep Convolutional Networks for Large-Scale Image Recognition",
        ["For VGG-16 [4], the final layers are adapted for character classes."],
        ["methodology"], 0.49,
    )
    assert method and evidence
    method, _ = _method_adoption_evidence(
        ["We train generic convolutional networks on images."],
        "Deep Convolutional Networks for Bird Species Classification",
        ["We cite CNN-based bird classification as motivation [4]."],
        ["methodology"], 0.66,
    )
    assert not method


def test_real_pipeline_has_additive_task_diagnostics_and_keeps_existing_interface():
    doc, extraction = fixture()
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert len(results) == 2
    for result in results:
        assert result.research_problem_similarity is not None
        assert 0 <= result.research_problem_similarity <= 1
        assert isinstance(result.research_problem_overlap, int)
        assert isinstance(result.method_use_evidence, list)
        assert result.classification in {
            "directly_relevant", "methodologically_relevant", "partially_relevant",
            "possibly_unrelated", "insufficient_evidence",
        }


def test_pdf_line_breaks_do_not_break_independent_method_sentence():
    doc, extraction = fixture()
    text = doc.text.replace("We train CNN models using transfer learning and augmentation.",
                            "Optimization uses gradient descent and regularization.\n"
                            "Training results from bird species studies [2] are cited as motivation.")
    source = ParsedDocument(doc.source, text)
    ctx = prepare_relevance_context(source, extraction)
    assert any("gradient descent" in x for x in ctx.independent_methodology_passages)
    assert all("bird species studies" not in x for x in ctx.independent_methodology_passages)


def test_method_first_character_level_network_not_confused_with_task_of_text_ocr():
    title = "Character-level convolutional networks for text classification"
    assert _is_technique_led(title)
    assert _problem_phrase(title).casefold() == "text classification"
    assert judge(technique_led=True, problem_similarity=0.70, specific=0) != "directly_relevant"


def test_unusual_domain_terms_are_not_counted_as_technique_anchors():
    assert not _method_anchors(
        ["We cite coral reef classification papers as an analogy."],
        ["Convolutional classification of coral reefs"],
    )
    assert not _method_anchors(
        ["Ceramic archaeology is discussed for inspiration."],
        ["Analysis of archaeological ceramics using machine learning"],
    )
    assert "kalman" in _method_anchors(
        ["We implement a Kalman filter to predict bridge vibration."],
        ["A Kalman filter for structural dynamic estimation"],
    )
