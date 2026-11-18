"""Offline tests: no Hugging Face downloads and no Crossref/OpenAlex calls."""
from collections import Counter
import hashlib
from pathlib import Path
import re

import numpy as np
import pytest

from projectiq_research.agent import analyze_research_document
from projectiq_research.models import (
    CitationMention, PaperReference, ParsedDocument, ResearchExtraction, Section
)
from projectiq_research.relevance_context import prepare_relevance_context
from projectiq_research.relevance_scorer import assess_relevance
from projectiq_research.verification_models import PaperVerification


class FakeEncoder:
    def __init__(self):
        self.calls = 0

    def encode(self, texts, **kwargs):
        self.calls += 1
        vectors = []
        for text in texts:
            v = np.zeros(128)
            for word in re.findall(r"[a-z]+", text.casefold()):
                slot = int(hashlib.sha1(word.encode()).hexdigest(), 16) % len(v)
                v[slot] += 1
            vectors.append(v)
        return np.stack(vectors)


class FakeCrossEncoder:
    def __init__(self):
        self.calls = 0

    def predict(self, pairs, **kwargs):
        self.calls += 1
        return np.asarray([len(set(a.lower().split()) & set(b.lower().split())) / 10
                           for a, b in pairs])


def fixture():
    text = """Scene Text Recognition using CNN
ABSTRACT
This project studies scene text recognition and convolutional neural networks.
1. INTRODUCTION
Scene text recognition uses image classification algorithms [1].
A skin lesion CNN paper provides a classification example [2].
2. METHOD
We train CNN models using transfer learning and augmentation.
REFERENCES
[1] A. Smith, "Scene text recognition with CNN models," 2022.
[2] B. Chen, "Skin lesion classification with CNN networks," 2021.
"""
    document = ParsedDocument("test.txt", text)
    papers = [
        PaperReference('[1] A. Smith, "Scene text recognition with CNN models," 2022.',
                       "Scene text recognition with CNN models", "A. Smith", 2022, None, [], 10, 10),
        PaperReference('[2] B. Chen, "Skin lesion classification with CNN networks," 2021.',
                       "Skin lesion classification with CNN networks", "B. Chen", 2021, None, [], 11, 11),
    ]
    result = ResearchExtraction(
        source="test.txt", literature_review=Section("literature_review", False),
        references=Section("references", True, "REFERENCES", text.split("REFERENCES\n", 1)[1], 9, 11),
        literature_citations=[], papers=papers, literature_links=[],
        reference_links=[], hyperlinks=[],
        document_citations=[CitationMention("[1]", "numeric", 5), CitationMention("[2]", "numeric", 6)],
        citation_reference_matches=[
            {"line_number": 5, "matched_paper_indices": [0], "status": "matched"},
            {"line_number": 6, "matched_paper_indices": [1], "status": "matched"},
        ],
        verification_results=[
            PaperVerification("Scene text recognition with CNN models", "A. Smith", 2022, None, "verified",
                              verified_title="Scene text recognition with CNN models"),
            PaperVerification("Skin lesion classification with CNN networks", "B. Chen", 2021, None, "verified",
                              verified_title="Skin lesion classification with CNN networks"),
        ],
    )
    return document, result


def test_context_excludes_bibliography_and_preserves_citation_lines():
    doc, extraction = fixture()
    ctx = prepare_relevance_context(doc, extraction)
    assert "scene text recognition" in ctx.topic.lower()
    assert "transfer learning" in ctx.methodology.lower()
    assert "[1]" in ctx.citation_contexts[0][0]
    assert ctx.citation_lines[0] == [5]
    assert "A. Smith" not in ctx.topic
    assert "A. Smith" not in ctx.methodology


def test_all_references_receive_results_even_without_literature_review():
    document, extraction = fixture()
    encoder, reranker = FakeEncoder(), FakeCrossEncoder()
    results = assess_relevance(extraction, document, encoder=encoder, cross_encoder=reranker)
    assert len(results) == 2
    assert [r.reference_index for r in results] == [0, 1]
    assert all(r.topical_similarity is not None for r in results)
    assert all(r.cross_encoder_scores for r in results)
    assert results[0].citation_lines == [5]
    assert encoder.calls == 1
    assert reranker.calls == 1


def test_relevance_is_not_a_verified_claim_support_result():
    doc, extraction = fixture()
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert all("uncalibrated" in r.notes[0] for r in results)
    assert all(r.classification in {"directly_relevant", "methodologically_relevant", "partially_relevant",
                                    "possibly_unrelated", "insufficient_evidence"} for r in results)


def test_abstract_provided_independently_is_in_evidence_sources():
    doc, extraction = fixture()
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder(),
                               paper_abstracts={0: "We recognize scene text using deep CNN classifiers."})
    assert "paper_abstract" in results[0].evidence_sources
    assert "paper_abstract" not in results[1].evidence_sources


def test_unverified_paper_never_receives_high_confidence():
    doc, extraction = fixture()
    extraction.verification_results[0].status = "not_found"
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder(),
                               paper_abstracts={0: "Scene text recognition with CNN models."})
    assert results[0].confidence == "low"
    assert any("identity" in x for x in results[0].notes)


def test_no_references_returns_without_loading_models():
    doc, extraction = fixture()
    extraction.papers = []
    assert assess_relevance(extraction, doc) == []


def test_missing_paper_title_does_not_hallucinate():
    doc, extraction = fixture()
    extraction.papers[0].title = None
    extraction.verification_results = []
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert results[0].classification == "insufficient_evidence"
    assert results[0].topical_similarity is None


def test_json_serialization_keeps_prior_phases():
    doc, extraction = fixture()
    extraction.relevance_results = assess_relevance(
        extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    payload = extraction.to_dict()
    assert len(payload["papers"]) == 2
    assert payload["literature_citations"] == []
    assert len(payload["document_citations"]) == 2
    assert len(payload["verification_results"]) == 2
    assert len(payload["relevance_results"]) == 2
    assert payload["relevance_results"][0]["reference_index"] == 0


def test_full_entry_point_calls_one_parser_and_no_summary_agent(tmp_path, monkeypatch):
    path = tmp_path / "simple.txt"
    path.write_text("""Scene text recognition with CNN
ABSTRACT
We recognize scene text with CNN.
INTRODUCTION
We cite CNN studies [1].
REFERENCES
[1] A. Smith, "Scene text recognition with CNN", 2020.
""")
    # No external API calls, no transformers downloads, no summarizer imports.
    from projectiq_research import agent
    calls = []
    real_loader = agent.load_document
    def tracked_loader(file):
        calls.append(file)
        return real_loader(file)
    monkeypatch.setattr(agent, "load_document", tracked_loader)
    extraction = analyze_research_document(path, verify=False,
                                           encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert len(calls) == 1
    assert len(extraction.papers) == 1
    assert len(extraction.relevance_results) == 1
    assert extraction.verification_results == []


def test_missing_report_topic_is_insufficient():
    doc, extraction = fixture()
    doc.text = "REFERENCES\n[1] A. Smith, Example, 2020."
    extraction.references.start_line = 1
    results = assess_relevance(extraction, doc)
    assert all(r.classification == "insufficient_evidence" for r in results)


def test_pdf_real_fixture_if_present():
    path = Path('/mnt/data/2302.05105v1.pdf')
    if not path.exists():
        pytest.skip("Real PDF not available on this machine")
    result = analyze_research_document(path, verify=False,
                                       encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert len(result.papers) == 25
    assert len(result.relevance_results) == 25
    assert len(result.document_citations) == 25
    assert result.literature_review.found is False
    assert all(0 <= r.reference_index < 25 for r in result.relevance_results)


def test_conflicting_external_metadata_is_not_used_as_relevant_title():
    doc, extraction = fixture()
    extraction.verification_results[0].status = "metadata_mismatch"
    extraction.verification_results[0].verified_title = "Completely unrelated astronomical research"
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert results[0].paper_title == "Scene text recognition with CNN models"
    assert results[0].confidence == "low"


def test_contaminated_unresolved_title_yields_insufficient_evidence():
    doc, extraction = fixture()
    extraction.verification_results[0].status = "not_found"
    extraction.papers[0].title = "Automatic scene text recognition " + "View project marketing metadata " * 15
    results = assess_relevance(extraction, doc, encoder=FakeEncoder(), cross_encoder=FakeCrossEncoder())
    assert results[0].classification == "insufficient_evidence"
