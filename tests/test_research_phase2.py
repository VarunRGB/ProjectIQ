"""Offline tests for Phase 2. All HTTP calls are mocked."""
import json
import pytest
import requests
from projectiq_research.models import PaperReference
from projectiq_research.paper_resolver import AcademicPaperResolver, normalise_doi, normalise_title, title_similarity
from projectiq_research.verification import verify_papers
from projectiq_research.verification_models import AcademicPaperMetadata


def paper(title="A Study of Neural Network Image Classification", authors="J. Smith", year=2023, doi=None):
    return PaperReference("raw reference", title, authors, year, doi, [], 1, 1)


class FakeResponse:
    def __init__(self, payload, status_code=200): self.payload, self.status_code = payload, status_code
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(f"HTTP {self.status_code}")
    def json(self): return self.payload


class FakeResolver:
    def __init__(self, metadata=None, error=None): self.metadata, self.error, self.calls = metadata, error, 0
    def resolve(self, item):
        self.calls += 1
        if self.error: raise self.error
        return self.metadata


def metadata(provider="crossref", title="A Study of Neural Network Image Classification", year=2023, doi="10.1234/example"):
    return AcademicPaperMetadata(provider, doi, title, ["Jane Smith"], year, doi, "https://publisher.example/article")


def test_normalise_doi_prefix_and_url():
    assert normalise_doi(" DOI: 10.1234/ABC.1 ") == "10.1234/ABC.1"
    assert normalise_doi("https://doi.org/10.1234/ABC.1") == "10.1234/ABC.1"
    assert normalise_doi(None) is None


def test_normalise_title_case_and_punctuation():
    assert normalise_title("A Study: of Neural-Networks!") == "a study of neural networks"


def test_title_similarity_punctuation_variant():
    assert title_similarity("A Study of Neural Networks", "A Study: of Neural-Networks") > .95


def test_crossref_doi_payload_mapping(monkeypatch):
    payload = {"message": {"DOI":"10.1234/example", "title":["A Study of Neural Network Image Classification"],
        "author":[{"given":"Jane","family":"Smith"}], "published-print":{"date-parts":[[2023,4,1]]},
        "URL":"https://publisher.example/article"}}
    monkeypatch.setattr("projectiq_research.paper_resolver.requests.get", lambda *a, **k: FakeResponse(payload))
    item = AcademicPaperResolver()._crossref_by_doi("10.1234/example")
    assert (item.provider, item.year, item.authors, item.doi) == ("crossref", 2023, ["Jane Smith"], "10.1234/example")


def test_openalex_payload_mapping(monkeypatch):
    payload = {"results":[{"id":"W1","doi":"https://doi.org/10.1234/example", "title":"A Study of Neural Network Image Classification",
        "publication_year":2023,"authorships":[{"author":{"display_name":"Jane Smith"}}],
        "primary_location":{"landing_page_url":"https://publisher.example/article"}}]}
    monkeypatch.setattr("projectiq_research.paper_resolver.requests.get", lambda *a, **k: FakeResponse(payload))
    item = AcademicPaperResolver()._openalex_by_title("A Study of Neural Network Image Classification")
    assert item.provider == "openalex" and item.year == 2023 and item.authors == ["Jane Smith"]


def test_doi_and_title_match_verified():
    result = verify_papers([paper(doi="10.1234/example")], FakeResolver(metadata()))[0]
    assert result.status == "verified" and result.title_match and result.year_match
    assert result.verified_url.endswith("/article")


def test_title_only_openalex_match_verified():
    result = verify_papers([paper()], FakeResolver(metadata("openalex", doi=None)))[0]
    assert result.status == "verified" and result.provider == "openalex"


def test_same_doi_but_different_title_is_mismatch():
    result = verify_papers([paper(doi="10.1234/example")], FakeResolver(metadata(title="Marine Biology")))[0]
    assert result.status == "metadata_mismatch" and result.title_match is False


def test_year_mismatch_is_flagged_for_review_not_silently_accepted():
    result = verify_papers([paper(year=2023, doi="10.1234/example")], FakeResolver(metadata(year=2022)))[0]
    assert result.status == "needs_review" and result.year_match is False
    assert any("year differs" in note.lower() for note in result.notes)


def test_missing_record_returns_not_found():
    assert verify_papers([paper()], FakeResolver(None))[0].status == "not_found"


def test_missing_title_and_doi_is_insufficient_without_api_call():
    resolver = FakeResolver()
    result = verify_papers([paper(title=None, doi=None)], resolver)[0]
    assert result.status == "insufficient_metadata" and resolver.calls == 0


def test_network_error_is_reported_for_paper():
    result = verify_papers([paper()], FakeResolver(error=requests.Timeout()))[0]
    assert result.status == "lookup_error" and "Timeout" in result.notes[0]


def test_one_lookup_failure_does_not_abort_other_papers():
    class SequenceResolver:
        def __init__(self): self.n = 0
        def resolve(self, item):
            self.n += 1
            if self.n == 1: raise requests.ConnectionError()
            return metadata("openalex", title=item.title, year=item.year, doi=None)
    results = verify_papers([paper(), paper(title="Another Research Paper Title")], SequenceResolver())
    assert [r.status for r in results] == ["lookup_error", "verified"]


def test_verification_serialises_to_json():
    result = verify_papers([paper()], FakeResolver(metadata()))[0]
    assert json.loads(json.dumps(result.to_dict()))["status"] == "verified"


def test_crossref_404_falls_back_to_openalex(monkeypatch):
    calls = []
    def fake_get(url, **kwargs):
        calls.append(url)
        if "crossref" in url: return FakeResponse({}, 404)
        return FakeResponse({"results":[{"id":"W9", "title":"A Study of Neural Network Image Classification", "publication_year":2023,
            "authorships":[{"author":{"display_name":"Jane Smith"}}], "primary_location":{"landing_page_url":"https://example.org/paper"}}]})
    monkeypatch.setattr("projectiq_research.paper_resolver.requests.get", fake_get)
    found = AcademicPaperResolver().resolve(paper(doi="10.1234/not-crossref"))
    assert found.provider == "openalex" and len(calls) == 2


def test_timeout_and_user_agent_are_set(monkeypatch):
    seen = {}
    def fake_get(url, **kwargs): seen.update(kwargs); return FakeResponse({"results":[]})
    monkeypatch.setattr("projectiq_research.paper_resolver.requests.get", fake_get)
    AcademicPaperResolver(timeout=7)._openalex_by_title("A sufficiently long paper title")
    assert seen["timeout"] == 7 and "ProjectIQ-ResearchAgent" in seen["headers"]["User-Agent"]


def test_short_title_does_not_call_provider(monkeypatch):
    monkeypatch.setattr("projectiq_research.paper_resolver.requests.get", lambda *a, **k: pytest.fail("unexpected API call"))
    assert AcademicPaperResolver()._openalex_by_title("AI") is None
