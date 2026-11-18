"""Regression tests for real ACM/IEEE colon-separated references and sections."""
from pathlib import Path

from projectiq_research.document_parser import load_document
from projectiq_research.models import ParsedDocument
from projectiq_research.reference_extractor import extract_references
from projectiq_research.section_detector import detect_sections


def test_colon_reference_title_and_author():
    entry = ('[8] R. Smith: An overview of the Tesseract OCR Engine,\n'
             'Proc. 9th IEEE Intl. Conf. on Document Analysis and\n'
             'Recognition (ICDAR), pp. 629-633, 2007.')
    paper, = extract_references(entry)
    assert paper.title == 'An overview of the Tesseract OCR Engine'
    assert paper.authors == 'R. Smith'
    assert paper.year == 2007
    assert paper.raw == entry


def test_multiple_authors_colon_and_ligatures():
    entry = ('[2] S. Chaudhury, R. Sheth: Trainable Script\n'
             'Identiﬁcation Strategies for Indian Languages, Proc.\n'
             '5th IEEE Intl. Conf. on Document Analysis and\n'
             'Recognition (ICDAR), pp. 657-680, 1999.')
    paper, = extract_references(entry)
    assert paper.title == 'Trainable Script Identification Strategies for Indian Languages'
    assert paper.authors == 'S. Chaudhury, R. Sheth'
    assert paper.year == 1999
    assert 'ﬁ' in paper.raw  # source provenance must be unchanged


def test_colons_not_automatically_assumed_to_be_authors():
    source = '[1] Research question: A survey of computational approaches, 2021.'
    paper, = extract_references(source)
    assert paper.title is None
    assert paper.authors is None


def test_web_references_remain_resources_not_fabricated_papers():
    entries = ('[1] Tesseract open source OCR engine.\n'
               'http://code.google.com/p/tesseract-ocr.\n'
               '[2] Leptonica image processing and analysis library.\n'
               'http://www.leptonica.com.')
    papers = extract_references(entries)
    assert len(papers) == 2
    assert all(p.title is None for p in papers)
    assert all(any('web or software resource' in n for n in p.extraction_notes) for p in papers)


def test_related_work_ends_before_unrecognized_numbered_section():
    doc = ParsedDocument('fixture.txt', '''1. INTRODUCTION
Intro.
1.1 Related work
Old research [1].
2. APPROACH
Method description [1].
3. TRAINING
Training method.
8. REFERENCES
[1] R. Smith: An overview of the Tesseract OCR Engine, Proc. IEEE, 2007.''')
    lr, refs, _ = detect_sections(doc)
    assert lr.heading == '1.1 Related work'
    assert 'Old research' in lr.content
    assert 'Method description' not in lr.content
    assert 'Training method' not in lr.content
    assert refs.found
    assert '[1] R. Smith' in refs.content


def test_numbered_bibliography_entry_is_not_new_section():
    doc = ParsedDocument('fixture.txt', '''1. INTRODUCTION
Intro.
2. REFERENCES
[1] R. Smith: A paper title, Proc. IEEE, 2007.
2. R. Smith: Another title, Proc. IEEE, 2008.
[3] T. Jones: A third title, Proc. IEEE, 2009.''')
    _, refs, _ = detect_sections(doc)
    assert refs.found
    assert 'Another title' in refs.content
    assert 'third title' in refs.content


def test_actual_tesseract_pdf_when_available():
    pdf = Path('/mnt/data/1577802.1577809.pdf')
    if not pdf.exists():
        import pytest
        pytest.skip('External real-PDF fixture unavailable')
    doc = load_document(pdf)
    lr, refs, _ = detect_sections(doc)
    assert lr.found and refs.found
    assert '2. APPROACH' not in lr.content
    papers = extract_references(refs.content)
    assert len(papers) == 11
    assert sum(p.title is not None for p in papers) == 9
    assert papers[7].authors == 'R. Smith'
    assert 'overview' in papers[7].title
