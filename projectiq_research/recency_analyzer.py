"""Offline, relevance-aware recency analysis using only Phase 1–3 results.

Recency is descriptive, not a claim that a work is obsolete. Verified dates and
semantic relevance predictions have different reliability and are kept separate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from statistics import median
from typing import Optional
import re

from .models import ResearchExtraction
from .recency_models import PaperRecency, RecencySummary

RELIABLE_IDENTITY = {"verified"}
VALID_POSITIVE = {"directly_relevant", "methodologically_relevant"}
AGE_BANDS = ("very_recent", "recent", "older", "historical", "future_dated", "unknown")


def _parse_as_of(value: Optional[date | str]) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _age_band(age: Optional[int]) -> str:
    if age is None:
        return "unknown"
    if age < 0:
        return "future_dated"
    if age <= 3:
        return "very_recent"
    if age <= 5:
        return "recent"
    if age <= 10:
        return "older"
    return "historical"


def _normalise_doi(value: Optional[str]) -> Optional[str]:
    """Canonicalise DOI spelling without guessing an identity from a bare URL."""
    if not value:
        return None
    value = re.sub(r"(?i)^\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value.strip())
    value = value.rstrip(" .,/\t\n").casefold()
    return value if value.startswith("10.") and "/" in value else None


def _normalise_title(value: Optional[str]) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _canonical_identity(paper, verification, index):
    """Conservative canonical identity for bibliography-level deduplication.

    * Confirmed DOI matches are stable even when a cited year is incorrect.
    * ``needs_review`` may join an existing DOI group only when Phase 2
      explicitly confirmed the title match. Conflicting/mismatched identities
      must never be merged by a DOI supplied by the student.
    * For entries without a DOI, identical resolved titles AND confirmed years
      can identify the same work. Different publication versions stay separate.
    """
    status = getattr(verification, "status", "not_checked")
    if not verification or status not in {"verified", "needs_review"}:
        return ("reference_index", index)
    trusted_title = _normalise_title(getattr(verification, "verified_title", None))
    submitted_title = _normalise_title(getattr(paper, "title", None))
    title_match = getattr(verification, "title_match", None)
    # A disputed year is acceptable; a disputed *identity* is not.
    if status == "needs_review" and not (title_match is True and trusted_title):
        return ("reference_index", index)
    if status == "verified" and not trusted_title:
        return ("reference_index", index)
    doi = _normalise_doi(getattr(verification, "verified_doi", None))
    if doi:
        return ("doi", doi)
    year = getattr(verification, "verified_year", None)
    if trusted_title and isinstance(year, int) and 1000 <= year <= 9999:
        return ("title_year", trusted_title, year)
    return ("reference_index", index)


def _build_identity_groups(papers, verifications):
    """DOI and exact-title/year groups, with safe cross-key reconciliation.

    If one provider gives a DOI and another gives only a title/year, group
    them only when the title and confirmed year both match and no conflicting
    DOI is present. Different DOI-bearing versions stay distinct.
    """
    by_key = defaultdict(list)
    for i, paper in enumerate(papers):
        verification = verifications[i] if i < len(verifications) else None
        by_key[_canonical_identity(paper, verification, i)].append(i)

    # Merge a missing-DOI title/year group into one DOI-backed group only if
    # there's exactly one matching (verified-title, verified-year) target.
    doi_targets = defaultdict(set)
    for key, group in by_key.items():
        if key[0] != "doi":
            continue
        for i in group:
            v = verifications[i]
            title = _normalise_title(getattr(v, "verified_title", None))
            year = getattr(v, "verified_year", None)
            if title and isinstance(year, int):
                doi_targets[(title, year)].add(key)
    for key in list(by_key):
        if key[0] != "title_year":
            continue
        candidates = doi_targets.get((key[1], key[2]), set())
        if len(candidates) == 1:
            by_key[next(iter(candidates))].extend(by_key.pop(key))
    groups = [sorted(group) for group in by_key.values()]
    groups.sort(key=lambda group: group[0])
    duplicate_groups = [
        {"references": [i + 1 for i in group],
         "basis": "verified DOI" if _canonical_identity(
             papers[group[0]],
             verifications[group[0]] if group[0] < len(verifications) else None,
             group[0]
         )[0] == "doi" else "matching confirmed title and year"}
        for group in groups if len(group) > 1
    ]
    return groups, duplicate_groups


def analyze_recency(extraction: ResearchExtraction, *, as_of_date: Optional[date | str] = None):
    """Return (one PaperRecency per entry, document-level RecencySummary).

    Only *verified* Phase 2 paper identities with a verified year enter reliable
    coverage statistics. Needs-review years remain visible but uncertain.
    """
    as_of = _parse_as_of(as_of_date)
    year = as_of.year
    papers = extraction.papers
    verifications = extraction.verification_results
    relevances = extraction.relevance_results
    match_rows = extraction.citation_reference_matches
    lr_start = extraction.literature_review.start_line if extraction.literature_review.found else None
    lr_end = extraction.literature_review.end_line if extraction.literature_review.found else None

    cited_indices: set[int] = set()
    lr_indices: set[int] = set()
    unresolved = ambiguous = 0
    for match in match_rows:
        status = match.get("status")
        if status == "ambiguous":
            ambiguous += 1
        elif status == "unresolved":
            unresolved += 1
        for idx in match.get("matched_paper_indices", []):
            if isinstance(idx, int) and 0 <= idx < len(papers):
                cited_indices.add(idx)
                if lr_start is not None and lr_end is not None and lr_start <= match.get("line_number", -1) <= lr_end:
                    lr_indices.add(idx)

    rows: list[PaperRecency] = []
    for i, paper in enumerate(papers):
        verify = verifications[i] if i < len(verifications) else None
        relevance = relevances[i] if i < len(relevances) else None
        verify_status = verify.status if verify else "not_checked"
        relevance_label = relevance.classification if relevance else "not_assessed"
        submitted = paper.year if isinstance(paper.year, int) and 1000 <= paper.year <= 9999 else None
        official = getattr(verify, "verified_year", None)
        official = official if isinstance(official, int) and 1000 <= official <= 9999 else None
        conflict = bool(submitted is not None and official is not None and submitted != official)

        if verify_status == "verified" and official is not None:
            effective, source = official, "verified_metadata"
        elif official is not None and verify_status == "needs_review":
            effective, source = official, "disputed" if conflict else "metadata_needs_review"
        elif submitted is not None:
            effective, source = submitted, "submitted_unverified"
        else:
            effective, source = None, "unknown"
        age = (year - effective) if effective is not None else None
        band = _age_band(age)
        notes: list[str] = []
        if conflict:
            notes.append(f"Submitted year {submitted} differs from database year {official}; check publication versions.")
        if source != "verified_metadata":
            notes.append("Publication year is not independently confirmed for this reference.")
        if band == "future_dated":
            notes.append(f"Publication year {effective} is after assessment year {year}; exclude from recency coverage.")
        if relevance_label == "directly_relevant":
            role = "recent_direct_research" if band in {"very_recent", "recent"} else "older_direct_research_review"
        elif relevance_label == "methodologically_relevant":
            role = "foundational_method_candidate" if band in {"older", "historical"} else "methodological_support"
        elif relevance_label == "possibly_unrelated":
            role = "possible_irrelevant_reference"
        elif relevance_label == "insufficient_evidence":
            role = "relevance_uncertain"
        elif relevance_label == "partially_relevant":
            role = "partial_connection_review"
        else:
            role = "not_assessed"
        if band in {"older", "historical"} and relevance_label == "methodologically_relevant":
            notes.append("Older methodological work may be foundational; age alone does not imply obsolescence.")
        rows.append(PaperRecency(
            reference_index=i, paper_title=(getattr(verify, "verified_title", None) if verify_status in {"verified", "needs_review"} else None) or paper.title,
            submitted_year=submitted, publication_year=effective, year_source=source,
            year_conflict=conflict, age_years=age, age_band=band,
            relevance_classification=relevance_label, verification_status=verify_status,
            literature_review_cited=i in lr_indices, document_cited=i in cited_indices,
            role_assessment=role, notes=notes,
        ))

    # Aggregate over distinct *confident identities*, not repeated bibliography entries.
    unique_groups, duplicate_groups = _build_identity_groups(papers, verifications)

    # Same resolved title with differing verified years/identifiers may denote
    # editions, preprints or later publications. Show a manual-review prompt,
    # but never silently collapse them into a single research work.
    version_index = defaultdict(list)
    for group in unique_groups:
        for i in group:
            v = verifications[i] if i < len(verifications) else None
            if getattr(v, "status", None) == "verified":
                title = _normalise_title(getattr(v, "verified_title", None))
                if title:
                    version_index[title].append(group)
                break
    possible_versions = []
    for same_title_groups in version_index.values():
        distinct_group_ids = {group[0] for group in same_title_groups}
        if len(distinct_group_ids) > 1:
            possible_versions.append({"references": sorted(
                i + 1 for group in same_title_groups for i in group
            ), "note": "Same resolved title but distinct publication records; inspect editions/versions."})

    # A duplicate's multiple citation locations count only once in research coverage.
    def reliable_representative(group):
        candidates = [rows[i] for i in group if rows[i].year_source == "verified_metadata" and rows[i].age_band != "future_dated"]
        return candidates[0] if candidates else None

    reliable = [x for group in unique_groups if (x := reliable_representative(group)) is not None]
    reliable_years = [r.publication_year for r in reliable if r.publication_year is not None]
    distinct_known = [group for group in unique_groups if any(rows[i].publication_year is not None for i in group)]
    valid_years = [r for r in reliable if r.age_band in {"very_recent", "recent"}]
    lr_reliable_groups = [group for group in unique_groups if any(i in lr_indices for i in group) and reliable_representative(group) is not None]
    lr_recent_groups = [group for group in lr_reliable_groups if reliable_representative(group).age_band in {"very_recent", "recent"}]

    direct_groups = [group for group in unique_groups if any(
        i < len(relevances) and relevances[i].classification == "directly_relevant" for i in group
    ) and reliable_representative(group) is not None]
    direct_recent = [group for group in direct_groups if reliable_representative(group).age_band in {"very_recent", "recent"}]
    distribution = Counter(reliable_representative(group).age_band for group in unique_groups if reliable_representative(group))

    def pct(n, d):
        return round(100 * n / d, 1) if d else None

    summary = RecencySummary(
        as_of_date=as_of.isoformat(), as_of_year=year,
        total_references=len(papers), distinct_references=len(unique_groups),
        duplicate_reference_count=len(papers) - len(unique_groups),
        duplicate_groups=duplicate_groups,
        possible_version_groups=possible_versions,
        known_year_count=len(distinct_known), reliable_year_count=len(reliable),
        uncertain_year_count=len([g for g in unique_groups if any(rows[i].publication_year is not None for i in g) and reliable_representative(g) is None]),
        unknown_year_count=len(unique_groups) - len(distinct_known),
        future_dated_count=sum(any(rows[i].age_band == "future_dated" for i in g) for g in unique_groups),
        median_reliable_year=median(reliable_years) if reliable_years else None,
        newest_reliable_year=max(reliable_years, default=None),
        oldest_reliable_year=min(reliable_years, default=None),
        recent_reliable_count=len(valid_years), recent_reliable_percent=pct(len(valid_years), len(reliable)),
        verified_direct_count=len(direct_groups), verified_direct_recent_count=len(direct_recent),
        verified_direct_recent_percent=pct(len(direct_recent), len(direct_groups)),
        literature_review_reference_count=sum(any(i in lr_indices for i in g) for g in unique_groups),
        literature_review_reliable_count=len(lr_reliable_groups),
        literature_review_recent_reliable_count=len(lr_recent_groups),
        literature_review_recent_percent=pct(len(lr_recent_groups), len(lr_reliable_groups)),
        document_cited_reference_count=sum(any(i in cited_indices for i in g) for g in unique_groups),
        uncited_reference_count=sum(not any(i in cited_indices for i in g) for g in unique_groups),
        unresolved_citation_mention_count=unresolved, ambiguous_citation_mention_count=ambiguous,
        age_distribution={name: distribution.get(name, 0) for name in AGE_BANDS},
    )

    if not papers or not reliable:
        summary.recency_assessment = "insufficient_evidence"
        summary.findings.append("Verified publication years are insufficient for a reliable recency assessment.")
    elif not direct_groups:
        summary.recency_assessment = "limited_topic_evidence"
        summary.findings.append("No verified directly relevant research was identified; topic-specific recency cannot be assessed reliably.")
    elif not direct_recent:
        summary.recency_assessment = "older_direct_literature"
        summary.findings.append("No verified directly relevant papers from the last five years were identified among extracted references.")
    elif len(direct_recent) < len(direct_groups):
        summary.recency_assessment = "mixed_direct_literature"
        summary.findings.append(f"{len(direct_recent)}/{len(direct_groups)} verified directly relevant works are from the last five years.")
    else:
        summary.recency_assessment = "recent_direct_literature_present"
        summary.findings.append(f"All {len(direct_groups)} verified directly relevant works with known years are within five years.")

    if extraction.literature_review.found:
        if not lr_reliable_groups:
            summary.findings.append("Literature Review detected, but no cited references with confirmed years were linked.")
        else:
            summary.findings.append(f"Literature Review cites {len(lr_reliable_groups)} distinct references with confirmed years; {len(lr_recent_groups)} are at most five years old.")
    else:
        summary.findings.append("No Literature Review section detected; Literature Review recency coverage cannot be assessed.")
    if summary.duplicate_reference_count:
        summary.findings.append(
            f"{summary.duplicate_reference_count} repeated bibliography entries across "
            f"{len(duplicate_groups)} confirmed duplicate groups were excluded "
            "from distinct-paper recency statistics."
        )
    if summary.uncited_reference_count:
        summary.findings.append(f"{summary.uncited_reference_count} distinct bibliography entries were not linked to in-text citation markers.")
    if summary.future_dated_count:
        summary.findings.append(f"{summary.future_dated_count} distinct references appear future-dated relative to {as_of.isoformat()}.")
    if summary.uncertain_year_count or summary.unknown_year_count:
        summary.limitations.append("References with unverified or missing dates were excluded from confirmed-year percentages.")
    summary.limitations.extend([
        "Five years is a reporting window, not a universal definition of scientific obsolescence.",
        "Phase 3 relevance labels are heuristic and can contain errors; recency statistics are descriptive, not a quality score.",
        "No search for newer or missing papers was conducted; literature gaps cannot be established from references alone.",
        "Unlinked references may be uncited, or citation matching may have missed them.",
    ])
    return rows, summary
