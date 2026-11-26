
from pathlib import Path


def run_ieee_check(file_path: str | Path) -> dict:
    try:
        from projectiq_ieee import analyze_document

        report = analyze_document(Path(file_path))

        layout = next(
            (
                c for c in report["checks"]
                if c["criterion"] == "Two-column body layout"
            ),
            None
        )

        if report["assessment_scope"] == "structure_only":
            format_verdict = "CANNOT_VERIFY_LAYOUT"

        elif layout and layout["status"] == "fail":
            format_verdict = "NOT_IEEE_LIKE"

        elif report["assessment_scope"] == "style_and_structure":
            format_verdict = "NEEDS_PDF_VERIFICATION"

        elif (
            report["score"] is not None
            and report["score"] >= 85
            and layout
            and layout["status"] == "pass"
        ):
            format_verdict = "LIKELY_IEEE_STYLE"

        else:
            format_verdict = "NEEDS_REVIEW"

        return {
            "status": "COMPLETED" if report["score"] is not None else "REVIEW_REQUIRED",
            "score": report["score"],
            "max_score": report["max_score"],
            "rating": report["rating"],
            "format_verdict": format_verdict,
            "assessment_scope": report["assessment_scope"],
            "layout_verdict": report["layout_verdict"],
            "categories": report["categories"],
            "checks": report["checks"],
            "notes": report["notes"],
            "disclaimer": report["disclaimer"],
        }

    except Exception as exc:
        return {
            "status": "FAILED",
            "score": None,
            "max_score": 100,
            "format_verdict": "NOT_EVALUATED",
            "error_code": type(exc).__name__,
        }
