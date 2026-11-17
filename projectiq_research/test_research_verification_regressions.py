"""Focused offline regression cases from real bibliography misclassifications."""
from projectiq_research.models import PaperReference
from projectiq_research.paper_resolver import AcademicPaperResolver
from projectiq_research.verification import verify_papers
from projectiq_research.verification_models import AcademicPaperMetadata


def ref(title, authors="C. Garcia and Z. Saidane", year=2007, doi=None):
    return PaperReference("raw", title, authors, year, doi, [], 1, 1)


def md(title, authors=None, year=2007, doi=None, provider="openalex"):
    return AcademicPaperMetadata(provider, "W1", title, authors or ["Christophe Garcia", "Zohra Saidane"], year, doi, "https://example.org")


class Resolver:
    def __init__(self, result): self.result = result
    def resolve(self, paper): return self.result


def test_unrelated_openalex_candidate_not_a_mismatch():
    p = ref("Implementasi Optical Character RecognitionBerbasis Backpropagation untuk Text to Speech Perangkat Android", "K. Apriyanti and T. W. Widodo", 2016)
    m = md("Text recognition on images using pre-trained CNN", ["Afgani Fajar Rizky"], 2023)
    assert verify_papers([p], Resolver(m))[0].status == "not_found"


def test_contaminated_long_title_is_a_review_not_false_mismatch():
    good = "Automatic Scene Text Recognition using a Convolutional Neural Network"
    p = ref(good + " Metric Learning and Siamese Neural Networks View project " + good)
    result = verify_papers([p], Resolver(md(good)))[0]
    assert result.status == "needs_review"
    assert any("contaminated" in n for n in result.notes)


def test_matching_paper_with_different_year_is_review():
    p = ref("Densely Connected Convolutional Networks", "G. Huang and Z. Liu", 2016)
    m = md("Densely Connected Convolutional Networks", ["Gao Ming Huang", "Zhuang Liu"], 2017)
    result = verify_papers([p], Resolver(m))[0]
    assert result.status == "needs_review" and result.title_match and not result.year_match
    assert any("versions" in n for n in result.notes)


def test_no_reliable_openalex_search_result_returns_none(monkeypatch):
    r = AcademicPaperResolver()
    monkeypatch.setattr(r, "_openalex_candidates", lambda q: [md("Deep learning for video classification and captioning", ["Zuxuan Wu"], 2017)])
    p = ref("Automatic Scene Text Recognition using a Convolutional Neural Network")
    assert r.resolve(p) is None


def test_openalex_search_uses_authors_and_year_to_rank(monkeypatch):
    r = AcademicPaperResolver()
    title = "Scene Text Recognition using Higher Order Language Priors"
    unrelated = md(title, ["Another Person"], 2025)
    correct = md(title, ["Anand Mishra"], 2012)
    monkeypatch.setattr(r, "_openalex_candidates", lambda q: [unrelated, correct])
    p = ref(title, "A. Mishra", 2012)
    assert r.resolve(p).year == 2012


def test_long_contaminated_title_tries_prefix_lookup(monkeypatch):
    r = AcademicPaperResolver()
    good = "Automatic Scene Text Recognition using a Convolutional Neural Network"
    long_title = good + " Metric Learning and Siamese Neural Networks View project PhD Thesis Towards unsupervised activity monitoring and analysis of videos"
    calls = []
    def fake(q):
        calls.append(q)
        return [md(good)] if len(calls) == 2 else [md("Video classification with neural networks", ["Different Author"], 2021)]
    monkeypatch.setattr(r, "_openalex_candidates", fake)
    p = ref(long_title)
    assert r.resolve(p).title == good and len(calls) == 2
