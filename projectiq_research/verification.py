"""Evaluate evidence for paper identity separately from bibliographic consistency."""
import re
import unicodedata
from typing import List, Optional

from requests import RequestException

from .models import PaperReference
from .paper_resolver import AcademicPaperResolver, normalise_doi, title_similarity, normalise_title
from .verification_models import AcademicPaperMetadata, PaperVerification

VERIFIED_TITLE_THRESHOLD = 0.90
REVIEW_TITLE_THRESHOLD = 0.72


def _author_match(submitted: Optional[str], verified: List[str]) -> Optional[bool]:
    if not submitted or not verified:
        return None
    def norm(s):
        s = unicodedata.normalize("NFKD", s.casefold())
        return "".join(c for c in s if not unicodedata.combining(c))
    submitted_parts = re.split(r",|\band\b|&", norm(submitted))
    submitted_names = {p.strip().split()[-1] for p in submitted_parts if p.strip()}
    verified_names = {norm(x).strip().split()[-1] for x in verified if x.strip()}
    # This is a signal, not conclusive author identity proof.
    return bool(submitted_names & verified_names) if submitted_names and verified_names else None


def _title_contained(submitted: Optional[str], found: Optional[str]) -> bool:
    a, b = normalise_title(submitted), normalise_title(found)
    return bool(len(b.split()) >= 8 and len(b) >= 45 and b in a)


def _build_result(paper: PaperReference, metadata: Optional[AcademicPaperMetadata]) -> PaperVerification:
    submitted_doi = normalise_doi(paper.doi)
    base = dict(submitted_title=paper.title, submitted_authors=paper.authors,
                submitted_year=paper.year, submitted_doi=submitted_doi)
    if metadata is None:
        return PaperVerification(**base, status="not_found",
                                 notes=["No sufficiently reliable academic metadata match was found."])

    score = title_similarity(paper.title, metadata.title)
    year_match = paper.year == metadata.year if paper.year is not None and metadata.year is not None else None
    author_match = _author_match(paper.authors, metadata.authors)
    doi_match = bool(submitted_doi and metadata.doi and submitted_doi.casefold() == metadata.doi.casefold())
    title_match = score >= VERIFIED_TITLE_THRESHOLD if paper.title and metadata.title else None
    contaminated_title = bool(_title_contained(paper.title, metadata.title) and author_match is True)

    notes = []
    if contaminated_title and not title_match:
        notes.append("Matched a long title embedded in an apparently contaminated extracted reference title.")
    elif title_match is False:
        notes.append("Academic metadata title differs from the extracted reference title.")
    if year_match is False:
        notes.append(f"Publication year differs: submitted {paper.year}, database {metadata.year}.")
        if (title_match or contaminated_title) and author_match is True:
            notes.append("Paper identity appears strong; check preprint, conference, online-first and journal publication versions.")
    if author_match is False:
        notes.append("Extracted author surnames do not clearly match database authors.")
    if year_match is None:
        notes.append("Publication year could not be compared because one value is missing.")
    if author_match is None:
        notes.append("Author match could not be determined confidently.")

    if doi_match and title_match is False and not contaminated_title:
        # A direct identifier pointing at conflicting metadata warrants investigation.
        status = "metadata_mismatch"
    elif title_match is True and year_match is not False and author_match is not False:
        status = "verified"
    elif (title_match or contaminated_title) and year_match is False:
        status = "needs_review"  # Keep existing public API for version-year differences.
    elif contaminated_title:
        status = "needs_review"
    elif score >= REVIEW_TITLE_THRESHOLD:
        status = "needs_review"
    elif doi_match and title_match is None:
        status = "needs_review"
    else:
        # A low-scoring title-search hit is not proof of bad bibliography metadata.
        status = "not_found" if metadata.provider != "crossref" else "metadata_mismatch"
        notes.append("The returned search candidate is not reliable enough to confirm this citation.")

    return PaperVerification(
        **base, status=status, provider=metadata.provider, verified_title=metadata.title,
        verified_authors=metadata.authors, verified_year=metadata.year,
        verified_doi=metadata.doi, verified_url=metadata.landing_page_url,
        title_similarity=round(score, 4), title_match=title_match,
        year_match=year_match, author_match=author_match, notes=notes,
    )


def verify_papers(papers: List[PaperReference], resolver: Optional[AcademicPaperResolver] = None) -> List[PaperVerification]:
    """Verify academic metadata per reference; isolate request failures."""
    resolver = resolver or AcademicPaperResolver()
    results = []
    for paper in papers:
        if not paper.title and not paper.doi:
            results.append(PaperVerification(
                submitted_title=paper.title, submitted_authors=paper.authors,
                submitted_year=paper.year, submitted_doi=normalise_doi(paper.doi),
                status="insufficient_metadata", notes=["Neither a usable title nor DOI was extracted."],
            ))
            continue
        try:
            results.append(_build_result(paper, resolver.resolve(paper)))
        except RequestException as exc:
            results.append(PaperVerification(
                submitted_title=paper.title, submitted_authors=paper.authors,
                submitted_year=paper.year, submitted_doi=normalise_doi(paper.doi),
                status="lookup_error", notes=[f"Academic metadata lookup failed: {exc.__class__.__name__}."],
            ))
    return results
