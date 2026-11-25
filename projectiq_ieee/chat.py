"""Simple rule-based Q&A over an existing IEEE formatting report.

No PDF text is sent to an external LLM. Answers only use scored evidence.
"""
from __future__ import annotations


def answer_question(question: str, report: dict) -> str:
    q = question.strip().lower()
    if not q:
        return "Ask me about the score, failed checks, or how to improve the paper's IEEE-style formatting."

    checks = report["checks"]
    problems = [c for c in checks if c["status"] in ("fail", "warning")]

    if any(word in q for word in ("score", "mark", "grade", "rating", "result", "pass")):
        score_type = "text-structure" if report.get("assessment_scope") == "structure_only" else "IEEE-style formatting"
        summary = (
            f"This paper's **{score_type} score is {report['score']}/100**: **{report['rating']}**. "
            f"The assessment used **{report['evaluated_weight']}/100** measurable rubric points "
            "before normalization. This is a capstone heuristic, not IEEE certification."
        )
        if report.get("assessment_scope") == "structure_only":
            summary += " **Important:** Plain text has no fonts, column layout, page size, or margins; this is NOT an IEEE layout-compliance score."
        elif report.get("assessment_scope") == "style_and_structure":
            summary += " DOCX Word properties were checked, but actual rendered page layout is unverified."
        if problems:
            worst = sorted(problems, key=lambda c: c["possible"] - c["earned"], reverse=True)[:3]
            summary += "\n\nThe biggest detected deductions:\n" + "\n".join(
                f"- {c['criterion']}: {c['detail']}" for c in worst
            )
        if report["layout_verdict"] == "Two-column layout NOT detected":
            summary += "\n\n**Important:** A required two-column layout was not detected."
        return summary

    keyword_groups = {
        "font": ("font", "text size", "typeface", "typograph"),
        "column": ("column", "margin", "page size", "layout", "a4", "letter"),
        "abstract": ("abstract", "keyword", "index term"),
        "reference": ("reference", "citation", "bibliograph"),
        "title": ("title", "author", "affiliation"),
        "figure": ("figure", "table", "caption"),
        "section": ("section", "introduction", "heading"),
    }
    categories = {
        "font": "Typography", "column": "Page layout", "abstract": "Abstract and keywords",
        "reference": "References and citations", "title": "Title and authors",
        "figure": "Figures and tables", "section": "Section structure",
    }
    selected = next((k for k, variants in keyword_groups.items() if any(term in q for term in variants)), None)
    if selected:
        matches = [c for c in checks if c["category"] == categories[selected]]
        if matches:
            details = []
            for c in matches:
                verdict = ("Not applicable" if c["status"] == "not_applicable" else
                           f"{c['earned']:g}/{c['possible']} points")
                text = f"- **{c['criterion']}** ({verdict}): {c['detail']}"
                if c["status"] in ("fail", "warning") and c["suggestion"]:
                    text += f" Fix: {c['suggestion']}"
                details.append(text)
            return "\n".join(details)

    if any(word in q for word in ("fix", "improve", "wrong", "issue", "problem", "change", "missing", "fail")):
        if not problems:
            if report.get("assessment_scope") == "structure_only":
                return "No problems were detected among the measurable text-structure checks, but columns, margins and fonts cannot be verified from a TXT/MD file. Upload PDF or DOCX for a formatting review."
            return "No obvious formatting problems were detected by this limited rule set. Verify against the venue's official template."
        return "Most useful improvements:\n" + "\n".join(
            f"- **{c['criterion']}**: {c['suggestion'] or c['detail']}" for c in
            sorted(problems, key=lambda c: c["possible"] - c["earned"], reverse=True)[:6]
        )

    return (
        "I can answer questions about this uploaded paper's **score, formatting issues, fonts, "
        "columns, abstract, figures, section headings, and references**. "
        "Try: *'How can I improve the paper?'*"
    )
