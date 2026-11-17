"""Regressions from the real 2024 cybersecurity paper and older OCR paper.

All tests are local/offline; no model downloads or API calls are required.
"""
from pathlib import Path

import pytest

from projectiq_research.citation_extractor import extract_citations
from projectiq_research.document_parser import _links_in_text, load_document
from projectiq_research.models import PaperReference, ParsedDocument
from projectiq_research.paper_resolver import AcademicPaperResolver, _search_rank
from projectiq_research.reference_extractor import extract_references
from projectiq_research.relevance_context import _parse_heading, prepare_relevance_context
from projectiq_research.section_detector import detect_sections
from projectiq_research.verification import _build_result
from projectiq_research.verification_models import AcademicPaperMetadata
from projectiq_research.agent import analyze_document


def paper(title, authors="A. Author", year=2023, doi=None):
    return PaperReference("[1] test", title, authors, year, doi, [], 10, 10)


def metadata(title, authors=None, year=2023, abstract="Specific paper abstract", doi=None):
    return AcademicPaperMetadata("openalex", "W123", title, authors or ["A. Author"], year, doi,
                                 "https://example.org", abstract)


@pytest.mark.parametrize("heading", [
    "VI. RELATED WORK", "VI. R ELA TED WORK", "VI. R ELATED W ORK",
    "VI. R E L A T E D W O R K", "6. RELATED WORK", "### Related Work",
    "II. BACKGROUND AND RELATED WORK", "VI. R ELA TED WORKS",
])
def test_related_work_heading_pdf_spacing(heading):
    text = f"I. I NTRODUCTION\nIntroduction [1].\n{heading}\nPrior literature [1]-[2].\nVII. L IMITATIONS &D ISCUSSIONS\nOur limitations.\nREFERENCES\n[1] A. Smith: A paper, Proceedings, 2020.\n[2] B. Smith: A paper, Proceedings, 2021."
    doc = ParsedDocument("cybersecurity.txt", text)
    lr, refs, notes = detect_sections(doc)
    assert lr.found and lr.heading == heading
    assert lr.content.strip() == "Prior literature [1]-[2]."
    assert refs.found
    assert not any("Literature Review section was not found" in n for n in notes)
    assert [c.text for c in extract_citations(lr.content, lr.start_line)] == ["[1]-[2]"]
    assert _parse_heading(heading).role == "literature"


def test_related_work_line_numbers_and_citation_linking(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("I. I NTRODUCTION\nIntro [1].\nVI. R ELA TED WORK\nWe use [1] and compare [2].\nVII. L IMITATIONS\nLimitations.\nREFERENCES\n[1] A. Smith: Intrusion detection, Proc. IEEE, 2020.\n[2] B. Jones: Intrusion prevention, Proc. IEEE, 2021.", encoding="utf8")
    extraction = analyze_document(source, verify=False)
    assert extraction.literature_review.found
    assert [c.line_number for c in extraction.literature_citations] == [4, 4]
    assert len(extraction.citation_reference_matches) == 3
    assert all(m["status"] == "matched" for m in extraction.citation_reference_matches)
    doc = load_document(source)
    context = prepare_relevance_context(doc, extraction)
    assert any("related work" in s.replace(" ", "") or "r ela ted work" in s for ss in context.citation_sections.values() for s in ss)


def test_body_prose_not_mistaken_for_literature_heading():
    doc = ParsedDocument("test.txt", "I. INTRODUCTION\nWe summarize related work in this paragraph.\nNot a separate review.\nREFERENCES\n[1] A. Author: A title, Proc. IEEE, 2022.")
    lr, refs, notes = detect_sections(doc)
    assert not lr.found and refs.found
    assert any("Literature Review section was not found" in n for n in notes)


def test_cybersecurity_false_openalex_candidate_rejected_and_abstract_never_exposed():
    original = paper("Generative intrusion detection and prevention on data stream",
                     "HyungBin Seo and MyungKeun Yoon", 2023)
    wrong = metadata("AI-Based Intrusion Detection and Prevention System",
                     ["Deepthi Repalle", "T. Bharathi"], 2025,
                     "Unrelated IoT smart home paper abstract")
    assert _search_rank(original, wrong)[0] is False
    result = _build_result(original, wrong)
    assert result.status == "not_found"
    assert result.verified_abstract is None
    assert result.verified_title is None
    assert result.verified_doi is None
    assert result.title_match is False and result.author_match is False and result.year_match is False
    assert any("rejected" in note.lower() for note in result.notes)


def test_resolver_does_not_accept_similar_topic_without_author_or_year(monkeypatch):
    original = paper("Generative intrusion detection and prevention on data stream",
                     "HyungBin Seo and MyungKeun Yoon", 2023)
    wrong = metadata("AI-Based Intrusion Detection and Prevention System",
                     ["Deepthi Repalle", "T. Bharathi"], 2025)
    resolver = AcademicPaperResolver()
    monkeypatch.setattr(resolver, "_openalex_candidates", lambda query: [wrong])
    assert resolver.resolve(original) is None


def test_strong_correct_openalex_identity_still_verified():
    original = paper("Kitsune: an ensemble of autoencoders for online network intrusion detection",
                     "Yisroel Mirsky, Tomer Doitshman, Yuval Elovici, and Asaf Shabtai", 2018)
    correct = metadata("Kitsune: An Ensemble of Autoencoders for Online Network Intrusion Detection",
                       ["Yisroel Mirsky", "Tomer Doitshman", "Yuval Elovici", "Asaf Shabtai"], 2018)
    assert _search_rank(original, correct)[0] is True
    result = _build_result(original, correct)
    assert result.status == "verified" and result.verified_abstract


def test_same_title_same_author_different_year_remains_review():
    original = paper("Introduction to Algorithms", "Thomas Cormen, Charles Leiserson", 2022)
    variant = metadata("Introduction to Algorithms", ["Thomas Cormen", "Charles Leiserson"], 2009)
    result = _build_result(original, variant)
    assert result.status == "needs_review"
    assert result.year_match is False and result.author_match is True


def test_exact_doi_conflict_stays_explicit_mismatch():
    original = paper("Network intrusion detection in kernel", "A. Author", 2022, "10.9999/example")
    candidate = metadata("An unrelated scientific title", ["Other Person"], 2020,
                         doi="10.9999/example")
    result = _build_result(original, candidate)
    assert result.status == "metadata_mismatch"
    assert result.verified_abstract is None


def test_recover_urls_wrapped_after_slash_or_dot():
    text = """[1] Amazon. Cloud security software. https://aws.amazon.com/marketplace/
solutions/security.
[2] Linux. Guide. https://www.kernel.org/doc/html/
v5.2/bpf/.
[3] Cilium. Reference guide. https://docs.cilium.
io/en/latest/bpf/.
[4] Benchmark. Site. https://www.unb.
ca/cic/research/applications.html.
[5] Tool. Documentation. https://man7.org/linux/man-pages/man1/pidstat.1.
html.
"""
    papers = extract_references(text)
    assert len(papers) == 5
    expected = [
        "https://aws.amazon.com/marketplace/solutions/security",
        "https://www.kernel.org/doc/html/v5.2/bpf/",
        "https://docs.cilium.io/en/latest/bpf/",
        "https://www.unb.ca/cic/research/applications.html",
        "https://man7.org/linux/man-pages/man1/pidstat.1.html",
    ]
    for paper_ref, expected_url in zip(papers, expected):
        assert expected_url in paper_ref.urls
    links = _links_in_text(text)
    for expected_url in expected:
        assert any(link.url == expected_url for link in links)


def test_url_does_not_swallow_unrelated_next_line():
    text = "See the project at https://example.com/\nThe research explores better algorithms.\n"
    links = _links_in_text(text)
    assert [link.url for link in links] == ["https://example.com/"]


def test_tesseract_pdf_regression_if_available():
    pdf = Path('/mnt/data/1577802.1577809.pdf')
    if not pdf.exists():
        pytest.skip('Real OCR paper fixture is not available')
    document = load_document(pdf)
    lr, refs, notes = detect_sections(document)
    assert lr.found and refs.found
    assert '2. APPROACH' not in lr.content
    assert len(extract_references(refs.content)) == 11


def test_review_status_without_identity_corroboration_cannot_supply_abstract():
    from projectiq_research.relevance_scorer import _identity_safe_for_semantics
    from projectiq_research.verification_models import PaperVerification

    weak = PaperVerification("Generative intrusion detection on data streams", "Seo and Yoon",
                             2023, None, "needs_review",
                             verified_title="AI-Based Intrusion Detection and Prevention System",
                             verified_abstract="Unrelated smart-home IoT article",
                             title_similarity=0.7455, title_match=False,
                             author_match=False, year_match=False)
    assert _identity_safe_for_semantics(weak) is False
    strong_with_year_discrepancy = PaperVerification(
        "Introduction to Algorithms", "Thomas Cormen", 2022, None, "needs_review",
        verified_title="Introduction to Algorithms", title_similarity=1.0,
        title_match=True, author_match=True, year_match=False)
    assert _identity_safe_for_semantics(strong_with_year_discrepancy) is True
