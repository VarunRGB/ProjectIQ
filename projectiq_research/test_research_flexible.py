"""Regression tests for common research-report bibliography layouts."""

import os
from pathlib import Path

import pytest

from projectiq_research.agent import analyze_document
from projectiq_research.models import Hyperlink, ParsedDocument
from projectiq_research.reference_extractor import extract_references
from projectiq_research.section_detector import detect_sections


def test_uppercase_paper_title_inside_references_is_not_a_heading():
    text = '''INTRODUCTION
A paper uses methods.
REFERENCES
[1] A. Smith, “A STUDY OF DEEP
NEURAL NETWORKS,” 2020, doi: 10.1234/example.2020.
[2] B. Brown, “Another Paper,” 2021.
BIOGRAPHIES OF AUTHORS
Author description.
'''
    _, references, _ = detect_sections(ParsedDocument("p.pdf", text, heading_lines={1, 3, 5, 8}))
    assert references.found
    assert "NEURAL NETWORKS" in references.content
    assert "[2] B. Brown" in references.content
    assert "Author description" not in references.content


def test_page_header_inside_reference_is_not_a_new_entry():
    text = '''[1] A. Smith, “Paper One,” 2020, pp. 441–

10
446. doi: 10.1109/DAS.2018.75.
[2] B. Brown, “Paper Two,” 2021.'''
    refs = extract_references(text)
    assert len(refs) == 2
    assert refs[0].doi == '10.1109/DAS.2018.75'
    assert '446.' in refs[0].raw
    assert '\n10\n' not in refs[0].raw
    assert refs[0].end_line == 4
    assert refs[1].start_line == 5


def test_wrapped_doi_is_joined_and_quoted_title_is_joined():
    text = '''[1] X. Liu, G. Meng, and C. Pan, “Scene text detection and recognition with advances
in deep learning: a survey,” Journal, 2019, doi: 10.1007/s10032-
019-00320-5.'''
    ref = extract_references(text)[0]
    assert ref.title == 'Scene text detection and recognition with advances in deep learning: a survey'
    assert ref.doi == '10.1007/s10032-019-00320-5'
    assert ref.authors == 'X. Liu, G. Meng, and C. Pan'
    assert 'https://doi.org/10.1007/s10032-019-00320-5' in ref.urls


def test_apa_entries_without_blank_separators():
    text = '''Smith, J., & Lee, A. (2023). Deepfake detection using vision transformers. Journal of AI.
Brown, P. (2025). Robust audiovisual manipulation detection. Proceedings of Conf.
García, L. (2021). Reconocimiento de texto en imágenes. Revista de IA.'''
    refs = extract_references(text)
    assert len(refs) == 3
    assert refs[0].title == 'Deepfake detection using vision transformers'
    assert refs[0].authors.startswith('Smith, J.')
    assert refs[1].year == 2025
    assert refs[2].title == 'Reconocimiento de texto en imágenes'


def test_numbered_apa_entries_and_heading_after_them():
    text = '''3. Literature Review
Many studies report progress.
4. References
1. Smith, J. (2023). Neural network methods. Journal.
2. Brown, A. (2024). Model reliability. Conference.
5. Results
Unrelated material.'''
    lit, refs, _ = detect_sections(ParsedDocument('sample.txt', text))
    assert lit.found and 'progress' in lit.content
    assert refs.found and 'Model reliability' in refs.content
    assert 'Unrelated material' not in refs.content
    entries = extract_references(refs.content, section_start_line=refs.start_line)
    assert [e.title for e in entries] == ['Neural network methods', 'Model reliability']


def test_mla_chicago_style_with_quote_keeps_title_and_authors():
    text = '''Smith, John. “A Study of Vision Models.” Journal of Computing, 2021.
Brown, Alice. “Learning Robust Features.” Computing Review, 2023.'''
    refs = extract_references(text)
    assert len(refs) == 2
    assert refs[0].title == 'A Study of Vision Models'
    assert refs[1].title == 'Learning Robust Features'


def test_institutional_author_with_apa_year():
    text = '''World Health Organization. (2023). Global report on health systems. WHO.
National Research Council. (2021). Review of technical methods. NRC.'''
    refs = extract_references(text)
    assert len(refs) == 2
    assert refs[0].title == 'Global report on health systems'
    assert refs[0].year == 2023


def test_doi_only_does_not_become_a_paper_title():
    ref = extract_references('''[1] Author unknown. doi: 10.1234/some.2021.100''')[0]
    assert ref.title is None
    assert ref.doi == '10.1234/some.2021.100'
    assert ref.year is None


def test_preserve_line_offsets_when_references_start_after_blank_lines():
    text = 'Literature Review\nOverview.\nReferences\n\n[1] A. Author, “A Review Paper,” 2020.\n\nAppendix\nStuff.'
    _, section, _ = detect_sections(ParsedDocument('report.txt', text))
    assert section.start_line == 3
    assert section.content.startswith('\n')
    links = [Hyperlink('paper', 'https://example.org/paper', 5)]
    refs = extract_references(section.content, links, section.start_line)
    assert len(refs) == 1
    assert refs[0].start_line == 5
    assert 'https://example.org/paper' in refs[0].urls


def test_unknown_bibliography_is_preserved():
    text = 'Unusual author list with missing citation markers\ncontains a multiline continuation.'
    refs = extract_references(text)
    assert len(refs) == 1
    assert refs[0].title is None
    assert 'multiline continuation' in refs[0].raw


def test_chapter_and_roman_numeral_heading_variants():
    text = '''CHAPTER II: LITERATURE SURVEY
Previous works described.
III. METHODOLOGY
Methods.
IV. BIBLIOGRAPHY
[1] J. Doe, “Example research paper,” 2022.'''
    lit, refs, _ = detect_sections(ParsedDocument('report.txt', text))
    assert lit.found and 'Previous works' in lit.content
    assert 'Methods' not in lit.content
    assert refs.found and 'Example research' in refs.content


def test_unknown_heading_in_docx_is_still_respected_when_isolated():
    text = 'REFERENCES\n[1] A. Smith, “Paper Title,” 2022.\n\nFunding Disclosures\n\nGrant text.'
    _, refs, _ = detect_sections(ParsedDocument('report.docx', text, heading_lines={1, 4}))
    assert refs.found and 'Paper Title' in refs.content
    assert 'Grant text' not in refs.content


def test_urls_with_parentheses_and_punctuation_trim_safely():
    text = '[1] A. Author, “On computer science,” 2021. (https://example.com/item(2021)).'
    ref = extract_references(text)[0]
    assert 'https://example.com/item(2021)' in ref.urls


def test_optional_real_pdf():
    """Set PROJECTIQ_REAL_PDF to the supplied 2302.05105v1.pdf for integration."""
    file_path = os.environ.get('PROJECTIQ_REAL_PDF')
    if not file_path:
        pytest.skip('Set PROJECTIQ_REAL_PDF to run the published-paper regression')
    result = analyze_document(Path(file_path))
    assert result.references.found
    assert len(result.papers) == 25
    assert all(result.papers[i].raw.startswith(f'[{i + 1}]') for i in range(25))
    assert result.papers[0].title == 'Scene text detection and recognition: recent advances and future trends'
    assert result.papers[1].doi == '10.1007/s10032-019-00320-5'
    assert result.papers[5].doi == '10.1109/DAS.2018.75'
    assert all(ref.title for ref in result.papers)
