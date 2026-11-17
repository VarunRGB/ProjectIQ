"""Resolve academic references through Crossref DOI and ranked OpenAlex searches.

Unrelated search hits are never treated as evidence that a citation is false.
"""
import os
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from .models import PaperReference
from .verification_models import AcademicPaperMetadata

CROSSREF_WORKS = "https://api.crossref.org/works"
OPENALEX_WORKS = "https://api.openalex.org/works"
REQUEST_TIMEOUT = 12
MIN_SEARCH_TITLE_SCORE = 0.72


def normalise_doi(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    doi = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", value.strip())
    doi = re.sub(r"(?i)^doi\s*:\s*", "", doi)
    # DOI line wrapping: a newline after a hyphen is part of the DOI, not a space.
    doi = re.sub(r"\s*\n\s*", "", doi)
    return doi.strip().rstrip(".,;)") or None


def normalise_title(value: Optional[str]) -> str:
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def title_similarity(left: Optional[str], right: Optional[str]) -> float:
    a, b = normalise_title(left), normalise_title(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _surname_tokens(value: str) -> set[str]:
    # Name matching remains conservative: a matching surname is corroboration,
    # but a missing surname match is not by itself grounds to reject a paper.
    result = set()
    for part in re.split(r",|\band\b|&", value, flags=re.I):
        words = normalise_title(part).split()
        if words:
            result.add(words[-1])
            if len(words) > 1 and len(words[0]) > 2:
                result.add(words[0])  # Lastname, Firstname citation layouts
    return result


def _candidate_author_match(paper: PaperReference, candidate: AcademicPaperMetadata) -> bool:
    if not paper.authors or not candidate.authors:
        return False
    submitted = _surname_tokens(paper.authors)
    candidates = set().union(*(_surname_tokens(n) for n in candidate.authors))
    return bool(submitted & candidates)


def _title_containment_match(query: str, candidate_title: Optional[str]) -> bool:
    """Require a long, contiguous title match before tolerating contaminated titles."""
    q, c = normalise_title(query), normalise_title(candidate_title)
    return len(c.split()) >= 8 and len(c) >= 45 and c in q


def _search_rank(paper: PaperReference, candidate: AcademicPaperMetadata) -> tuple:
    score = title_similarity(paper.title, candidate.title)
    author_agrees = _candidate_author_match(paper, candidate)
    containment = bool(paper.title and _title_containment_match(paper.title, candidate.title) and author_agrees)
    plausible = score >= MIN_SEARCH_TITLE_SCORE or containment
    year_agrees = paper.year is not None and candidate.year == paper.year
    # Prefer good title + author + year, not just the provider's search ranking.
    quality = score + (0.12 if author_agrees else 0) + (0.04 if year_agrees else 0)
    if containment:
        quality = max(quality, 0.86)
    return (plausible, quality, score, author_agrees)


class AcademicPaperResolver:
    """Resolve a DOI first; otherwise search OpenAlex, rejecting weak candidates."""

    def __init__(self, timeout: int = REQUEST_TIMEOUT, mailto: Optional[str] = None):
        self.timeout = timeout
        self.mailto = mailto or os.getenv("CROSSREF_MAILTO")
        self.session = requests
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY")

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(
            url,
            params=params,
            headers={"User-Agent": "ProjectIQ-ResearchAgent/0.3 (academic metadata verification)"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _crossref_metadata(item: Dict[str, Any]) -> AcademicPaperMetadata:
        titles = item.get("title") or []
        authors = []
        for author in item.get("author") or []:
            name = " ".join(x for x in (author.get("given"), author.get("family")) if x).strip()
            if name:
                authors.append(name)
        date_parts = (item.get("published-print", {}).get("date-parts")
                      or item.get("published-online", {}).get("date-parts")
                      or item.get("created", {}).get("date-parts") or [])
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        doi = normalise_doi(item.get("DOI"))
        return AcademicPaperMetadata(
            provider="crossref", provider_id=doi, title=titles[0] if titles else None,
            authors=authors, year=year, doi=doi,
            landing_page_url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
            abstract=item.get("abstract"),
        )

    @staticmethod
    def _openalex_metadata(item: Dict[str, Any]) -> AcademicPaperMetadata:
        authors = []
        for authorship in item.get("authorships") or []:
            name = (authorship.get("author") or {}).get("display_name")
            if name:
                authors.append(name)
        doi = normalise_doi(item.get("doi"))
        primary = item.get("primary_location") or {}
        return AcademicPaperMetadata(
            provider="openalex", provider_id=item.get("id"), title=item.get("title"),
            authors=authors, year=item.get("publication_year"), doi=doi,
            landing_page_url=primary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
        )

    def _crossref_by_doi(self, doi: str) -> Optional[AcademicPaperMetadata]:
        params = {"mailto": self.mailto} if self.mailto else None
        payload = self._get_json(f"{CROSSREF_WORKS}/{quote(doi, safe='/')}", params=params)
        item = payload.get("message")
        return self._crossref_metadata(item) if isinstance(item, dict) else None

    def _openalex_candidates(self, query: str) -> List[AcademicPaperMetadata]:
        params = {
            "search": query,
            "per-page": 10,
            "select": "id,doi,title,publication_year,authorships,primary_location",
        }

        # Add API key if configured
        if self.openalex_api_key:
            params["api_key"] = self.openalex_api_key

        payload = self._get_json(OPENALEX_WORKS, params=params)

        return [
            self._openalex_metadata(x)
            for x in payload.get("results") or []
            if isinstance(x, dict)
        ]

    def _openalex_by_title(self, title: str, paper: Optional[PaperReference] = None) -> Optional[AcademicPaperMetadata]:
        if not title or len(title.strip()) < 8:
            return None
        # The optional paper argument preserves the previous title-only API.
        reference = paper or PaperReference("", title, None, None, None, [], 0, 0)
        candidates = self._openalex_candidates(title)
        ranked = sorted(candidates, key=lambda x: _search_rank(reference, x), reverse=True)
        if ranked and _search_rank(reference, ranked[0])[0]:
            return ranked[0]

        # Very long pasted/contaminated titles can ruin a title search. A bounded
        # prefix query retrieves candidates; the full reference is still checked.
        words = normalise_title(title).split()
        if len(words) >= 18:
            shortened = " ".join(words[:10])
            alternate = self._openalex_candidates(shortened)
            ranked = sorted(alternate, key=lambda x: _search_rank(reference, x), reverse=True)
            if ranked and _search_rank(reference, ranked[0])[0]:
                return ranked[0]
        return None

    def resolve(self, paper: PaperReference) -> Optional[AcademicPaperMetadata]:
        doi = normalise_doi(paper.doi)
        if doi:
            try:
                record = self._crossref_by_doi(doi)
                if record:
                    return record
            except requests.RequestException:
                # Some DOI registrations are outside Crossref; try title search.
                pass
            except (ValueError, KeyError, TypeError):
                pass
        return self._openalex_by_title(paper.title or "", paper=paper)
