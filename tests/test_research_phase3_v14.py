"""Phase 3 v1.4: evidence provenance and domain-held-out boundary tests.

No API keys, pretrained weights, or network access. These are deterministic
rule tests; separate real-model evaluation is still necessary.
"""
from pathlib import Path
import json

import pytest

from projectiq_research.models import ParsedDocument, PaperReference, ResearchExtraction, Section
from projectiq_research.document_parser import load_document
from projectiq_research.agent import _analyse_parsed_document
from projectiq_research.relevance_context import prepare_relevance_context
from projectiq_research.relevance_scorer import (
    _classification, _corroborated_alias, _general_method_paper,
    _is_technique_led, _problem_phrase, _specific_method_support, _task_families,
    assess_relevance,
)
from test_research_phase3 import FakeEncoder, FakeCrossEncoder


@pytest.mark.parametrize('title,expected', [
    ('Very Deep Convolutional Networks for Large-Scale Image Recognition', True),
    ('Deep Residual Learning for Image Recognition', True),
    ('Densely Connected Convolutional Networks', True),
    ('Deep Convolutional Neural Network for Automated Bird Species Classification', False),
    ('Machine learning for ancient clay pottery', False),
    ('Kalman filtering for bridge vibration prediction', False),
])
def test_general_method_paper_is_not_any_application_with_a_cnn(title, expected):
    assert _general_method_paper(title) is expected


def test_method_adoption_ignores_weak_embedding_for_specific_exact_method():
    level, traces, terms = _specific_method_support(
        ['We implement gradient descent to optimize the objective function.'],
        'An overview of gradient descent optimization algorithms',
        [], [], ['Prediction of structural vibrations in bridges'], [],
        ['methodology'], '', 'verified', 0.17, 0.13)
    assert level == 'strong'
    assert any('gradient descent' in t for t in terms)
    # Explicit match + verified identity can beat low method embedding similarity.
    label, _ = _classification(
        topical=0.12, method=0.17, context=0.1, domain_overlap=0,
        section_names=['methodology'], context_snippets=[], crosses={},
        has_abstract=False, verified=True, independent_method=0.13,
        problem_similarity=0.15, technique_led=True,
        method_usage=True, method_evidence_strength=level)
    assert label == 'methodologically_relevant'


def test_named_architecture_alias_needs_verified_paper_and_separate_report_mention():
    kwargs = dict(
        report_methods=['For ResNet-18 [3], the classifier head is adapted to bridge sensors.'],
        title='Deep Residual Learning for Image Recognition',
        citation_claims=['For ResNet-18 [3], the classifier head is adapted to bridge sensors.'],
        citation_aliases=['ResNet-18'],
        topic_passages=['We compare a baseline against ResNet-18 for bridge vibration prediction.'],
        uncited_methods=['The sensor readings are normalized in each experiment.'],
        section_names=['methodology'], paper_abstract='',
        methodological=0.53, independent_method=0.23,
    )
    assert _specific_method_support(verification_status='verified', **kwargs)[0] == 'moderate'
    assert _specific_method_support(verification_status='not_found', **kwargs)[0] == 'none'
    assert _specific_method_support(verification_status='verified', **{
        **kwargs, 'topic_passages': ['We predict bridge vibrations using accelerometers.']
    })[0] == 'none'
    assert _specific_method_support(verification_status='verified', **{
        **kwargs, 'citation_claims': ['ResNet-18 [3] is cited as motivation.']
    })[0] == 'none'
    assert _specific_method_support(verification_status='verified', **{
        **kwargs, 'title': 'Deep Convolutional Neural Network for Automated Bird Species Classification'
    })[0] == 'none'


def test_task_family_comparison_is_domain_neutral():
    for left, right in [
        ('Forecasting river discharge', 'River flow prediction'),
        ('Detecting unauthorized network traffic', 'Network intrusion detection'),
        ('Recognizing Latin letters from photographs', 'Visual character recognition'),
        ('Classifying crop diseases using satellite imagery', 'Crop disease classification'),
    ]:
        assert _task_families(left) & _task_families(right), (left, right)
    assert not (_task_families('Recognizing handwritten text') &
                _task_families('Bird species classification'))


def test_rephrased_same_task_can_be_direct_but_generic_architecture_cannot():
    args = dict(topical=0.49, method=0.52, context=0.90,
                domain_overlap=0, section_names=['literature review'],
                context_snippets=['A citation with suggestive wording.'],
                crosses={'topic': -3.9, 'methodology': -4.8},
                has_abstract=True, verified=True, independent_method=0.23,
                problem_similarity=0.48, subject_overlap=0,
                same_task_family=True)
    assert _classification(technique_led=False, **args)[0] == 'directly_relevant'
    assert _classification(technique_led=True, **args)[0] != 'directly_relevant'
    assert _classification(technique_led=False, **{
        **args, 'same_task_family': False
    })[0] != 'directly_relevant'


def test_context_alone_cannot_grant_method_status_from_unrelated_pottery():
    label, _ = _classification(
        topical=0.04, method=0.20, context=0.98,
        domain_overlap=0, section_names=['methodology'],
        context_snippets=['We implement morphology-aware segmentation using pottery [8].'],
        crosses={'citation_context': 9.0}, has_abstract=True, verified=True,
        independent_method=0.10, problem_similarity=0.03,
        method_usage=False, method_evidence_strength='none',
    )
    assert label == 'possibly_unrelated'


def test_contradictory_identity_caps_positive_classification_and_confidence():
    # No approved paper metadata; name matching alone must not confirm a method.
    from test_research_phase3 import fixture
    doc, ex = fixture()
    ex.verification_results[0].status = 'metadata_mismatch'
    ex.verification_results[0].verified_abstract = None
    predictions = assess_relevance(ex, doc, encoder=FakeEncoder(),
                                   cross_encoder=FakeCrossEncoder())
    assert predictions[0].confidence == 'low'
    assert predictions[0].evidence_reliability == 'unverified_reference'
    assert predictions[0].classification not in {'methodologically_relevant','directly_relevant'}


def test_pdf_wrapped_method_claim_and_aliases_are_reconstructed():
    pdf = Path('/mnt/data/projectiq_phase3_comprehensive_stress_test.pdf')
    if not pdf.exists():
        pytest.skip('PDF fixture not available')
    doc = load_document(pdf)
    ex = _analyse_parsed_document(doc, verify=False)
    ev = prepare_relevance_context(doc, ex)
    assert len(ex.papers) == 45
    assert 'VGG-16' in ev.citation_aliases.get(15, [])
    assert 'ResNet-18' in ev.citation_aliases.get(16, [])
    assert 'DenseNet-121' in ev.citation_aliases.get(17, [])
    assert any('final layers are adapted' in x for x in ev.citation_claims[15])
    assert any('cross-entropy loss and gradient descent' in x for x in ev.citation_claims[12])
    # Fabricated contextual analogy must never become a named model link.
    assert not ev.citation_aliases.get(26)
    # Banners and 'Page N' must not become methodological evidence.
    assert not any('SYNTHETIC VALIDATION FIXTURE' in x for x in ev.method_sentences)


def test_unseen_water_quality_report_alias_and_citation_attack():
    """Completely different report domain with a citation attack in Methods."""
    txt = '''Urban Lake Water Quality Forecasting with Sensor Networks
Abstract
We forecast nitrate levels in urban lakes from continuous sensor measurements.
Introduction
Forecasting water quality is important for city reservoirs.
Methodology
We implement an extended Kalman filter to estimate nitrate concentrations.
Our sensor measurements use physically calibrated probes.
Studies on medieval pottery [2] are cited as inspiration for filtering observations.
Results
We plan to measure error over repeated sensor readings.
References
[1] R. Example, "Extended Kalman Filter for Water Quality Forecasting", 2020.
[2] E. Example, "Medieval Pottery Ceremonies and Cultural Practices", 2020.
'''
    doc = ParsedDocument('water_quality.txt', txt)
    ex = _analyse_parsed_document(doc, verify=False)
    ev = prepare_relevance_context(doc, ex)
    assert len(ex.papers) == 2
    # Citation to pottery doesn't invent an adoption claim about pottery.
    level, traces, terms = _specific_method_support(
        ev.method_sentences, ex.papers[1].title,
        ev.citation_claims.get(1, []), ev.citation_aliases.get(1, []),
        ev.topic_passages, ev.independent_methodology_passages,
        ev.citation_sections.get(1, []), '', 'verified', 0.2, 0.2)
    assert level == 'none'
    level, _, terms = _specific_method_support(
        ev.method_sentences, ex.papers[0].title,
        ev.citation_claims.get(0, []), ev.citation_aliases.get(0, []),
        ev.topic_passages, ev.independent_methodology_passages,
        ev.citation_sections.get(0, []), '', 'verified', 0.3, 0.2)
    assert level == 'strong' and any('kalman filter' in t for t in terms)
