"""Domain-independent heading and evidence-boundary regression tests.

Offline-only: no model downloads, OpenAlex or Crossref requests. These test
heading families, structural hierarchy, evidence provenance and safety checks.
"""
from pathlib import Path

import pytest

from projectiq_research.agent import _analyse_parsed_document
from projectiq_research.document_parser import load_document
from projectiq_research.models import ParsedDocument
from projectiq_research.relevance_context import (
    _heading, _parse_heading, _line_annotation, is_method_section,
    prepare_relevance_context,
)


@pytest.mark.parametrize('heading', [
    '2. APPROACH', 'II. Materials and Methods', '## Proposed Solution',
    '3. Research Design', '3.2 Empirical Strategy', '### Feature Extraction',
    '4. Classification', '4.2 Model Training', '3. Computational Methods',
    '5. Data Collection', '2. Experimental Setup', '3. Algorithm',
    '### Numerical Scheme', '2. Workflow', '2.1 Inference',
    'Chapter 3: Methodology', 'Section 2 — Approach', '## 2. Methods',
    '3.1 Pre-processing', 'Methods',
])
def test_varied_research_methodology_headings(heading):
    parsed = _parse_heading(heading)
    assert parsed and parsed.role == 'methodology', heading
    assert is_method_section(parsed.title)


@pytest.mark.parametrize('heading,expected_role', [
    ('1. INTRODUCTION', 'introduction'),
    ('1.1 Related work', 'literature'),
    ('2. Previous Studies', 'literature'),
    ('2. State of the Art', 'literature'),
    ('5. RESULTS', 'results'),
    ('5. Evaluation and Experimental Results', 'results'),
    ('### Ablation Study', 'results'),
    ('6. Conclusion', 'closing'),
    ('7. References', 'references'),
    ('KEYWORDS', 'front_matter'),
    ('GENERAL TERMS', 'front_matter'),
])
def test_non_method_headings_override_method_sections(heading, expected_role):
    assert _parse_heading(heading).role == expected_role


def test_hierarchical_subsections_inherit_role_and_keep_unknown_vocabulary():
    lines = '''Research Report on River Water Quality
Abstract
We forecast water quality from physical sensors.
1. Introduction
Sensor methods provide forecasting baselines.
2. Related Work
Earlier studies evaluated rivers [1].
3. Proposed Approach
We implement an extended Kalman filter for nitrate forecasting.
3.1 Candidate sensor primitives
We calibrate a fluorescence probe.
3.2 Novel anchor equation setup
We estimate nitrate concentrations from streaming measurements [2].
3.3 Hyperparameter optimization
We tune filter parameters.
4. Experimental Results
Prediction error is reduced by 10 percent.
5. Conclusions
We summarize the study.'''.splitlines()
    blocks, per_line = _line_annotation(lines)
    text = ' '.join(blocks['methodology'])
    assert 'extended Kalman filter' in text
    assert 'fluorescence probe' in text
    assert 'streaming measurements' in text
    assert 'prediction error is reduced' not in text.lower()
    assert 'Earlier studies' not in text
    idx = lines.index('We estimate nitrate concentrations from streaming measurements [2].')
    assert is_method_section(per_line[idx])
    assert per_line[lines.index('Earlier studies evaluated rivers [1].')] == 'related work'


def test_numbered_algorithm_steps_remain_method_content_not_headings():
    lines = '''Sensor Quality Forecasting
Abstract
We forecast sensor quality.
1. Introduction
We describe the problem.
4. Classification
1. Binarize the image and segment it into connected components
2. For each blob estimate a confidence score
3. Choose the orientation using those scores
4. Choose the script that has the highest count
5. Evaluation and Experimental Results
The error rate is 3 percent.'''.splitlines()
    blocks, per_line = _line_annotation(lines)
    text = ' '.join(blocks['methodology'])
    for fragment in ('Binarize the image', 'For each blob', 'Choose the orientation', 'Choose the script'):
        assert fragment in text
    assert 'The error rate' not in text
    assert per_line[lines.index('4. Choose the script that has the highest count')] == 'classification'


def test_section_boundaries_stop_at_unfamiliar_top_level_heading():
    lines = '''Sample Research Report
Abstract
We analyze sensor data.
3. Methodology
We calibrate sensors.
3.1 Sensor camera integration
We apply a robust filtering pipeline.
4. Ethical Considerations
We discuss privacy policies.
5. Experimental Results
The experiment achieved acceptable accuracy.'''.splitlines()
    blocks, per_line = _line_annotation(lines)
    assert 'We apply a robust filtering pipeline.' in ' '.join(blocks['methodology'])
    assert 'privacy policies' not in ' '.join(blocks['methodology'])
    assert 'accuracy' not in ' '.join(blocks['methodology'])
    assert per_line[lines.index('We discuss privacy policies.')] == 'ethical considerations'


def test_prose_and_front_matter_are_not_misread_as_methods():
    assert not _parse_heading('We describe a new method for estimating nitrate concentrations.')
    assert not _parse_heading('The results were better after preprocessing.')
    assert not _parse_heading('1600 Amphitheatre Pkwy, Mountain View, CA 94043')
    assert not _parse_heading('40 languages, and also distinguish them from earlier scripts')
    assert not _parse_heading('Algorithms, Languages')
    assert _heading('Keywords') == 'keywords'


def test_methods_in_markdown_and_social_science_headings():
    text = '''Impact of Family Income on Schooling
# Abstract
We estimate schooling outcomes using survey records.
## Related Work
Prior education economics studies are reviewed [1].
## Empirical Strategy
We use an instrumental variables regression design.
### Novel robustness assembly
We check household weights and sample strata.
## Discussion
This supports a cautious interpretation.
## References
[1] A. Researcher, "Education and Income", 2020.'''
    doc = ParsedDocument('economics.md', text)
    ex = _analyse_parsed_document(doc, False)
    ev = prepare_relevance_context(doc, ex)
    text_methods = ' '.join(ev.methodology_passages)
    assert 'instrumental variables regression' in text_methods
    assert 'household weights' in text_methods
    assert 'cautious interpretation' not in text_methods
    assert ev.literature.startswith('Prior education economics')


def test_tesseract_pdf_now_has_real_methods_and_correct_citation_provenance():
    pdf = Path('/mnt/data/1577802.1577809.pdf')
    if not pdf.exists():
        pytest.skip('Attached PDF not available on this machine')
    doc = load_document(pdf)
    ex = _analyse_parsed_document(doc, False)
    ev = prepare_relevance_context(doc, ex)
    assert len(ex.papers) == 11
    assert len(ev.methodology_passages) >= 5
    assert len(ev.independent_methodology_passages) >= 5
    assert 'Our proposed approach' in ' '.join(ev.methodology_passages)
    assert 'Binarize the image' in ' '.join(ev.methodology_passages)
    assert 'Choose the script' in ' '.join(ev.methodology_passages)
    assert ev.citation_sections[1] == ['related work']
    assert 'classification' in ev.citation_sections[10]  # Reference 11
    assert all(len(chunk) <= 620 for chunk in ev.methodology_passages)
    # Domain-specific thresholds and classifications are deliberately not fixed.


def test_numbered_steps_after_unnumbered_methods_are_not_new_chapters():
    lines = [
        'Generic Geophysical Survey', 'ABSTRACT', 'We measure magnetic fields.',
        'METHODS', 'We collect data at multiple stations.',
        '1. Extract magnetic intensity from each instrument',
        '2. Normalize the readings to a common reference',
        'RESULTS', 'Errors are below 4 percent.',
    ]
    blocks, labels = _line_annotation(lines)
    methods = ' '.join(blocks['methodology'])
    assert 'Extract magnetic intensity' in methods
    assert 'Normalize the readings' in methods
    assert 'Errors are below' not in methods
