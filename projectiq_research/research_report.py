"""Concise, evidence-aware Research Quality Report from Phases 1 through 4A.

The report is a descriptive research-review aid, not a pass/fail grade. The
complete structured results remain available via ResearchExtraction.to_dict().
No external discovery or additional model is run here.
"""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
import re

from .models import ResearchExtraction
from .recency_models import ResearchQualityReport, RecencySummary


def _percent(value):
    return "N/A (no verified sample)" if value is None else f"{value:.1f}%"


def _human(value: str | None) -> str:
    return (value or "unknown").replace("_", " ").capitalize()


def _short_title(value: str, limit: int = 88) -> str:
    text = " ".join((value or "Untitled reference").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _safe_md(value: str) -> str:
    """Escape untrusted paper titles and labels embedded in Markdown."""
    raw = str(value).replace("\n", " ")
    raw = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", raw)


def _source_filename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def build_research_report(extraction: ResearchExtraction, summary: RecencySummary) -> ResearchQualityReport:
    refs = extraction.papers
    verifications = extraction.verification_results
    relevances = extraction.relevance_results
    verification_counts = Counter(getattr(p, "status", "not_checked") for p in verifications)
    relevance_counts = Counter(getattr(p, "classification", "not_assessed") for p in relevances)
    has_lr = extraction.literature_review.found

    # Keep key findings as evidence-aware interpretation, not duplicated console summaries.
    findings = []
    if summary.verified_direct_count:
        findings.append(
            f"{summary.verified_direct_recent_count} of {summary.verified_direct_count} distinct, "
            "verified directly relevant papers were published within the last five years. "
            "This describes the supplied references, not the availability of newer research."
        )
    else:
        findings.append("No distinct verified direct-relevance papers with usable publication years were available for a topic-recency estimate.")
    if has_lr:
        if summary.literature_review_reliable_count:
            findings.append(
                f"The Literature Review cites {summary.literature_review_recent_reliable_count} recent papers "
                f"among {summary.literature_review_reliable_count} distinct papers with confirmed years "
                f"({_percent(summary.literature_review_recent_percent)})."
            )
        else:
            findings.append("The Literature Review was detected, but no linked paper had a confirmed publication year.")
    else:
        findings.append("No dedicated Literature Review was detected; its citation coverage could not be assessed.")
    if summary.duplicate_reference_count:
        findings.append(
            f"{summary.duplicate_reference_count} repeated bibliography entries were excluded from "
            f"distinct-paper statistics ({len(summary.duplicate_groups)} confirmed duplicate groups)."
        )
    if summary.possible_version_groups:
        findings.append("Some publications have matching resolved titles but different publication records; potential versions were left separate for review.")

    if getattr(summary,"unresolved_citation_mention_count",0) and not refs:
        findings.append("In-text citations were detected without extracted bibliography entries; citation identities require review.")
    if summary.distinct_references:
        findings.append(f"Confirmed publication-year coverage: {summary.reliable_year_count} of {summary.distinct_references} distinct references.")
    attention = []
    reference_overview = []
    for i, paper in enumerate(refs):
        v = verifications[i] if i < len(verifications) else None
        r = relevances[i] if i < len(relevances) else None
        status = getattr(v, "status", "not_checked")
        classification = getattr(r, "classification", "not_assessed")
        recency = extraction.recency_results[i] if i < len(extraction.recency_results) else None
        reference_overview.append({
            "reference_number": i + 1,
            "title": paper.title or "Untitled reference",
            "verification_status": status,
            "relevance_classification": classification,
            "confidence": getattr(r, "confidence", "unknown"),
            "publication_year": getattr(recency, "publication_year", None),
            "year_source": getattr(recency, "year_source", "unknown"),
            "age_band": getattr(recency, "age_band", "unknown"),
            "cited_in_literature_review": getattr(recency, "literature_review_cited", False),
            "citation_context_linked": bool(getattr(r,"citation_lines",[])),
            "supporting_engineering_connection": bool(getattr(r,"explanation","").startswith("Verified source and citation-independent report methodology share")),
        })
        reasons = []
        if status == "metadata_mismatch":
            reasons.append("DOI/metadata points to a different paper; verify this citation")
        elif status == "not_found":
            reasons.append("Academic identity could not be confirmed")
        elif status == "lookup_error":
            reasons.append("Academic lookup failed; identity remains unconfirmed")
        elif status == "needs_review":
            reasons.append("Academic metadata needs manual review")
        if classification == "possibly_unrelated":
            reasons.append("May not substantively support the report topic or methods")
        elif classification == "insufficient_evidence":
            reasons.append("Relevance remains uncertain; inspect independent source metadata and citation context")
        if recency and recency.year_conflict:
            reasons.append(
                f"Submitted publication year {recency.submitted_year} conflicts with resolved year "
                f"{getattr(v, 'verified_year', None)}"
            )
        if recency and recency.age_band == "future_dated":
            reasons.append("Publication date is after the assessment year")
        if reasons:
            priority = (
                "high" if status in {"metadata_mismatch", "not_found", "lookup_error"}
                or (recency and recency.age_band == "future_dated")
                else "medium" if status == "needs_review" or classification == "possibly_unrelated"
                else "low"
            )
            attention.append({
                "reference_number": i + 1,
                "title": paper.title or "Untitled reference",
                "verification_status": status,
                "relevance": classification,
                "priority": priority,
                "reasons": reasons,
            })
    rank = {"high": 0, "medium": 1, "low": 2}
    attention.sort(key=lambda item: (rank[item["priority"]], item["reference_number"]))

    recommendations = []
    if any(x["verification_status"] in {"not_found", "metadata_mismatch", "lookup_error"} for x in attention):
        recommendations.append("Verify unresolved or conflicting bibliographic records against the original publication before treating them as established sources.")
    if summary.verified_direct_count and not summary.verified_direct_recent_count:
        recommendations.append("Check whether the Literature Review should discuss newer directly relevant research; the provided confirmed direct references are all more than five years old.")
    elif not summary.verified_direct_count:
        recommendations.append("Manually confirm the topic relevance of the available references before drawing recency conclusions.")
    if any(x["relevance"] == "possibly_unrelated" for x in attention):
        recommendations.append("Review potentially unrelated papers in their precise citation contexts and remove or justify unsupported references.")
    if summary.duplicate_reference_count:
        recommendations.append("Consolidate confirmed duplicates: " + "; ".join(
            ", ".join(f"[{n}]" for n in item["references"]) for item in summary.duplicate_groups
        ) + ".")
    if summary.possible_version_groups:
        recommendations.append("Inspect matching titles with different publication versions without merging them automatically: " + "; ".join(
            ", ".join(f"[{n}]" for n in item["references"]) for item in summary.possible_version_groups
        ) + ".")
    if summary.unresolved_citation_mention_count or summary.ambiguous_citation_mention_count:
        recommendations.append("Manually review unresolved or ambiguous in-text citation markers; some bracketed numbers may not be citations.")
    if not has_lr:
        recommendations.append("Confirm whether the report requires a dedicated Literature Review; none was detected.")
    if not recommendations:
        recommendations.append("Review low-confidence classifications and verify citation-to-claim support before final submission.")

    headline = (
        "Research evidence requires review"
        if attention or summary.recency_assessment in {"older_direct_literature", "insufficient_evidence", "limited_topic_evidence"}
        else "Research evidence summary"
    )
    recent_clause = (
        f"{summary.verified_direct_recent_count} of {summary.verified_direct_count} distinct verified "
        "directly relevant papers are from the last five years"
        if summary.verified_direct_count
        else "There are too few confirmed directly relevant papers to assess topic-specific recency"
    )
    overview = (
        f"The bibliography contains {len(refs)} entries ({summary.distinct_references} distinct works by "
        f"conservative deduplication). {verification_counts.get('verified', 0)} entries have verified identities. "
        f"The relevance model identifies {relevance_counts.get('directly_relevant', 0)} directly relevant and "
        f"{relevance_counts.get('methodologically_relevant', 0)} methodologically relevant entries. "
        f"{recent_clause}. These are descriptive indicators, not a pass/fail finding."
    )
    return ResearchQualityReport(
        schema_version="1.1", source=extraction.source, as_of_date=summary.as_of_date,
        headline=headline, overview=overview,
        phase_1={"literature_review_found": has_lr, "references_found": extraction.references.found,
                 "reference_entries": len(refs), "document_citation_mentions": len(extraction.document_citations),
                 "literature_review_citation_mentions": len(extraction.literature_citations),
                 "reference_match_records": len(extraction.citation_reference_matches),
                 "matched_reference_entries": summary.document_cited_reference_count,
                 "hyperlinks_found": len(extraction.hyperlinks)},
        phase_2={"status_counts": dict(verification_counts), "checked_references": len(verifications)},
        phase_3={"classification_counts": dict(relevance_counts), "assessed_references": len(relevances),
                 "interpretation": "Relevance classes are heuristic; they do not establish factual claim support."},
        phase_4={"recency_assessment": summary.recency_assessment,
                 "distinct_references": summary.distinct_references,
                 "duplicate_reference_count": summary.duplicate_reference_count,
                 "duplicate_groups": summary.duplicate_groups,
                 "possible_version_groups": summary.possible_version_groups,
                 "reliable_publication_year_count": summary.reliable_year_count,
                 "median_reliable_publication_year": summary.median_reliable_year,
                 "recent_reliable_count": summary.recent_reliable_count,
                 "recent_reliable_percent": summary.recent_reliable_percent,
                 "verified_direct_count": summary.verified_direct_count,
                 "verified_direct_recent_count": summary.verified_direct_recent_count,
                 "verified_direct_recent_percent": summary.verified_direct_recent_percent,
                 "literature_review_reliable_count": summary.literature_review_reliable_count,
                 "literature_review_recent_reliable_count": summary.literature_review_recent_reliable_count,
                 "literature_review_recent_percent": summary.literature_review_recent_percent,
                 "age_distribution": summary.age_distribution},
        key_findings=findings, attention_items=attention,
        reference_overview=reference_overview,
        methodological_notes=list(dict.fromkeys([
            "Recency denominators use distinct papers with confirmed years, not all bibliography entries.",
            "Publication years labeled disputed or unverified are visible but excluded from confirmed recency statistics.",
            "Older methodological foundations are not automatically obsolete.",
            "Relevance categories and confidence values are estimates; paper identity and semantic relevance are separate assessments.",
            "No search for newer or missing papers, full-text claim verification, or comprehensive literature review was performed.",
            *summary.limitations,
        ])),
        recommended_checks=recommendations,
    )


def render_research_report_markdown(report: ResearchQualityReport, *, max_attention: int | None = None) -> str:
    """Render actual CommonMark, not escaped Markdown pasted by chat interfaces.

    By default *all* high and medium priority issues are included. An optional
    row limit will truncate only after recording the omitted count by priority.
    """
    p1, p2, p3, p4 = report.phase_1, report.phase_2, report.phase_3, report.phase_4
    s2, s3 = p2["status_counts"], p3["classification_counts"]
    lines = [
        "# ProjectIQ — Research Quality Report", "",
        f"**Report:** `{_source_filename(report.source).replace('`', chr(39))}`  ",
        f"**Assessed:** {report.as_of_date}  ",
        "**Scope:** Supplied report and its extracted bibliography only", "",
        f"## Executive summary — {report.headline}", "",
        report.overview, "",
        "### At a glance", "",
        "| Measure | Result |", "|---|---:|",
        f"| Bibliography entries | {p1['reference_entries']} |",
        f"| Distinct works (conservative matching) | {p4['distinct_references']} |",
        f"| Verified identities (entries) | {s2.get('verified', 0)} |",
        f"| Direct / methodological relevance (entries) | {s3.get('directly_relevant', 0)} / {s3.get('methodologically_relevant', 0)} |",
        f"| Recent directly relevant papers (distinct, confirmed) | {p4['verified_direct_recent_count']}/{p4['verified_direct_count']} ({_percent(p4['verified_direct_recent_percent'])}) |",
        f"| Recent Literature Review papers (distinct, confirmed) | {p4['literature_review_recent_reliable_count']}/{p4['literature_review_reliable_count']} ({_percent(p4['literature_review_recent_percent'])}) |",
        "", "### Priority findings", "",
    ]
    lines.extend(f"- {item}" for item in report.key_findings)
    lines.extend(["", "## Results by phase", "",
                  "### Phase 1 — Document extraction and citations", "",
                  f"- Literature Review detected: **{'Yes' if p1['literature_review_found'] else 'No'}**; bibliography detected: **{'Yes' if p1['references_found'] else 'No'}**.",
                  f"- **{p1['reference_entries']}** bibliography entries; **{p1['document_citation_mentions']}** citation-like mentions in the report, **{p1['literature_review_citation_mentions']}** within the Literature Review.",
                  f"- **{p1['matched_reference_entries']}** distinct referenced works linked to text; **{p1['hyperlinks_found']}** hyperlinks extracted. Linkage can include false-positive citation markers.",
                  "", "### Phase 2 — Academic identity verification", "",
                  f"- **{s2.get('verified', 0)}** verified; **{s2.get('needs_review', 0)}** need review; **{s2.get('not_found', 0)}** not found; **{s2.get('metadata_mismatch', 0)}** metadata mismatches; **{s2.get('lookup_error', 0)}** lookup errors.",
                  "- A missing database match does not prove that a paper is fabricated.",
                  *(["- **Verification skipped or incomplete:** no verified-year conclusions should be drawn from unchecked entries."]
                    if p2.get("checked_references", 0) != p1["reference_entries"] and p1["reference_entries"] else []),
                  "", "### Phase 3 — Research relevance", "",
                  f"- **{s3.get('directly_relevant', 0)}** direct; **{s3.get('methodologically_relevant', 0)}** methodological; **{s3.get('partially_relevant', 0)}** partial; **{s3.get('possibly_unrelated', 0)}** possibly unrelated; **{s3.get('insufficient_evidence', 0)}** insufficient evidence.",
                  "- These are semantic estimates, not verification that a paper supports the specific cited claim.",
                  "", "### Phase 4A — Publication recency", "",
                  f"- Assessment: **{_human(p4['recency_assessment'])}**; **{p4['distinct_references']}** distinct works after excluding **{p4['duplicate_reference_count']}** repeated entries.",
                  f"- **{p4['reliable_publication_year_count']}** distinct papers have confirmed usable years; median verified year: **{p4['median_reliable_publication_year'] if p4['median_reliable_publication_year'] is not None else 'N/A'}**.",
                  f"- **{p4['recent_reliable_count']}/{p4['reliable_publication_year_count']}** ({_percent(p4['recent_reliable_percent'])}) of confirmed-year distinct papers are within five years.",
                  f"- Directly relevant: **{p4['verified_direct_recent_count']}/{p4['verified_direct_count']}** ({_percent(p4['verified_direct_recent_percent'])}) recent, based only on confirmed years and distinct works.",
                  f"- Literature Review: **{p4['literature_review_recent_reliable_count']}/{p4['literature_review_reliable_count']}** ({_percent(p4['literature_review_recent_percent'])}) recent among distinct, confirmed-year papers linked in that section.",
                  "- Unconfirmed dates appear in the appendix but **do not** improve confirmed recency coverage.", "",
                  "## References requiring manual attention", "",
                  ])
    attention = report.attention_items
    visible = attention if max_attention is None else attention[:max(0, max_attention)]
    if not attention:
        lines.append("No specific reference-level flags were generated. This does not establish that every citation is valid.")
    else:
        for priority, description in [
            ("high", "High priority — identity or lookup problems"),
            ("medium", "Review recommended — uncertain metadata or relevance"),
            ("low", "Additional checks"),
        ]:
            group = [item for item in visible if item.get("priority", "medium") == priority]
            if not group:
                continue
            lines.extend([f"### {description}", ""])
            for item in group:
                title = _safe_md(_short_title(item["title"]))
                reasons = "; ".join(_safe_md(reason) for reason in item["reasons"])
                lines.append(f"- **[{item['reference_number']}]** {title} — {reasons}.")
            lines.append("")
        omitted = len(attention) - len(visible)
        if omitted:
            lines.append(f"**{omitted} additional flagged references** are available under `research_report.attention_items` in the JSON.\n")

    duplicate_groups = p4.get("duplicate_groups", [])
    versions = p4.get("possible_version_groups", [])
    lines.extend(["## Duplicate and publication-version checks", ""])
    if duplicate_groups:
        lines.append(f"**{p4['duplicate_reference_count']} repeated entries in {len(duplicate_groups)} distinct duplicate groups:**")
        lines.extend("- " + ", ".join(f"[{n}]" for n in group["references"]) + f" — {_safe_md(group['basis'])}." for group in duplicate_groups)
    else:
        lines.append("No reliable duplicate groups identified.")
    if versions:
        lines.extend(["", "**Possible publication versions (not automatically merged):**"])
        lines.extend("- " + ", ".join(f"[{n}]" for n in group["references"]) + " — same resolved title, different publication records." for group in versions)
    lines.extend(["", "## Recommended review actions", ""])
    lines.extend(f"{n}. {item}" for n, item in enumerate(report.recommended_checks, 1))
    lines.extend(["", "## Appendix — Reference index", "",
                  "*Full paper titles, model diagnostics, verified metadata, and evidence are preserved in the JSON.*", "",
                  "| Ref. | Paper (short title) | Identity | Relevance (confidence) | Publication year | Age |",
                  "|---:|---|---|---|---|---|"])
    for item in report.reference_overview:
        title = _safe_md(_short_title(item["title"], 61))
        pub_year = item["publication_year"]
        source = item["year_source"]
        if pub_year is None:
            year_text = "Unknown"
        elif source == "verified_metadata":
            year_text = f"{pub_year} (verified)"
        elif source == "disputed":
            year_text = f"{pub_year} (disputed)"
        else:
            year_text = f"{pub_year} (unconfirmed)"
        age_text = _human(item["age_band"])
        if source != "verified_metadata" and item["publication_year"] is not None:
            age_text += " (unconfirmed)"
        lines.append(
            f"| {item['reference_number']} | {title} | {_human(item['verification_status'])} | "
            f"{_human(item['relevance_classification'])} ({_human(item['confidence']).lower()}) | "
            f"{year_text} | {age_text} |"
        )
    lines.extend(["", "## Scope and limitations", ""])
    notes = list(dict.fromkeys(report.methodological_notes))
    # Keep this section short; the detailed caveats are already present above.
    for note in notes:
        if not any(note.lower().startswith(prefix) for prefix in
                   ["no search for newer", "no newer-paper search"]):
            lines.append(f"- {note}")
    lines.append("- No newer-paper search was conducted; limited recent coverage in the bibliography is not proof that newer relevant papers exist or were omitted.")
    lines.append("")
    return "\n".join(lines)
