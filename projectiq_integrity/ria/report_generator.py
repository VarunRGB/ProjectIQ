"""Turn analysis results into review flags and write JSON / CSV / Markdown / HTML reports.

Flag types:
  unsupported claim, contradictory evidence, textual overlap,
  numeric inconsistency, uncited claim, unresolved citation

Every report carries the disclaimer: these are flags for human review, not
findings of fraud or plagiarism.
"""

from __future__ import annotations

import csv
import html
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DISCLAIMER
from .pdf_extractor import clean_heading

logger = logging.getLogger(__name__)

FLAG_TYPES = (
    "contradictory evidence",
    "unsupported claim",
    "textual overlap",
    "numeric inconsistency",
    "uncited claim",
    "unresolved citation",
)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
REPORT_FIELDS = (
    "flag_id", "type", "severity", "text", "section", "page", "citation", "evidence",
    "evidence_source", "scores", "nli_label", "reason", "recommendation",
)


@dataclass
class Flag:
    """One item for human review."""

    flag_id: str
    type: str
    severity: str
    text: str
    section: str
    page: int | str
    citation: str
    evidence: str
    evidence_source: str
    scores: dict[str, Any]
    nli_label: str
    reason: str
    recommendation: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Building flags
# --------------------------------------------------------------------------- #
BACKGROUND_SECTIONS = {"Introduction", "Related Work", "Discussion"}


def build_flags(result: Any) -> list[Flag]:
    """Create review flags from an :class:`~src.pipeline.AnalysisResult`."""
    from .citation_analyzer import UNCITED_CAVEAT
    from .evidence_analyzer import CONTRADICTS, INSUFFICIENT
    from .overlap_analyzer import NEAR_VERBATIM, PARAPHRASE

    flags: list[Flag] = []
    claims = {c.claim_id: c for c in result.claims}

    def heading(idx: int, fallback: str) -> str:
        """The paper's own heading (e.g. "Literature Survey") instead of the internal label."""
        return result.document.heading_of(idx) or fallback

    # Evidence-based flags
    for ev in result.evidence:
        c = claims.get(ev.claim_id)
        if c is None or ev.best_passage is None:
            continue
        bp = ev.best_passage
        scores = {"tfidf": bp.tfidf_score, "embedding": bp.embedding_score, **{f"p_{k}": v for k, v in bp.nli_probs.items()},
                  "nli_backend": ev.nli_backend, "rule_baseline": ev.rule_verdict}
        src = f"{bp.source_file}" + (f" [ref {bp.ref_number}]" if bp.ref_number else "")
        cite = ", ".join(c.citation_markers) or "none"
        if ev.verdict == CONTRADICTS:
            cited_it = bp.ref_number in ev.cited_refs_with_pdf
            flags.append(Flag("", "contradictory evidence", "high" if cited_it else "medium", c.text, heading(c.sentence_index, c.section), c.page,
                              cite, bp.text, src, scores, ev.nli_label, ev.reason,
                              "Read the cited passage in full and check whether it really says the opposite, "
                              "or whether conditions/populations differ. Correct or qualify the claim if needed.",
                              {"claim_id": c.claim_id, "claim_type": c.claim_type, "hedged": c.hedged}))
        elif ev.verdict == INSUFFICIENT and ev.cited_refs_with_pdf:
            flags.append(Flag("", "unsupported claim", "low" if c.hedged else "medium", c.text, heading(c.sentence_index, c.section), c.page,
                              cite, bp.text, src, scores, ev.nli_label, ev.reason,
                              "Locate where in the cited source this claim is made (it may be in a table, figure "
                              "or a part the parser missed). If it is not there, cite a better source.",
                              {"claim_id": c.claim_id, "claim_type": c.claim_type, "hedged": c.hedged}))

    # Uncited claims
    for c in result.claims:
        if c.citation_markers:
            continue
        if c.section not in BACKGROUND_SECTIONS:
            continue  # the authors' own results, design and conclusions do not need citations
        background = True
        flags.append(Flag("", "uncited claim", "medium" if background else "low", c.text, heading(c.sentence_index, c.section), c.page,
                          "none", "", "", {"claim_confidence": c.confidence}, "",
                          "Uncited claim - review. " + UNCITED_CAVEAT
                          + ("" if background else " In this section it is most likely the authors' own result."),
                          "If this states someone else's finding, add a citation. If it is the authors' own "
                          "result or common knowledge, no action is needed.",
                          {"claim_id": c.claim_id, "claim_type": c.claim_type}))

    # Unresolved citation markers
    for u in result.citations.unresolved:
        flags.append(Flag("", "unresolved citation", "low", u["sentence"], heading(u["sentence_index"], u["section"]), u["page"], u["marker"],
                          "", "", {}, "", f"Citation '{u['missing']}' does not match any entry in the parsed "
                          "reference list. This may be a missing reference or a parsing limitation.",
                          "Check the reference list for the missing entry.", {}))

    # Textual overlap
    for o in result.overlap:
        if o.category not in (NEAR_VERBATIM, PARAPHRASE):
            continue
        from .citation_analyzer import find_citations, resolve_marker

        markers = [resolve_marker(m, result.document.references) for m in find_citations(o.paper_text)]
        cited_refs = {n for m in markers for n in m.resolved}
        attributed = o.reference_number is not None and o.reference_number in cited_refs
        if o.category == NEAR_VERBATIM:
            sev = "medium" if attributed else "high"
            rec = ("Text closely matches the source. Even with a citation, near-verbatim reuse usually needs "
                   "quotation marks or rewording." if attributed else
                   "Text closely matches a reference passage without a matching citation. Check attribution "
                   "and quote or rephrase.")
        else:
            sev = "low" if attributed else "medium"
            rec = ("Paraphrase of a cited source; usually fine. Confirm the meaning is preserved." if attributed else
                   "Wording is close in meaning to a reference passage that is not cited here. Consider citing it.")
        flags.append(Flag("", "textual overlap", sev, o.paper_text, heading(o.sentence_index, o.section), o.page,
                          ", ".join(m.raw for m in markers) or "none", o.reference_text,
                          o.reference_file + (f" [ref {o.reference_number}]" if o.reference_number else ""),
                          {"ngram_jaccard": o.ngram_jaccard, "ngram_containment": o.ngram_containment,
                           "tfidf_cosine": o.tfidf_cosine, "embedding_cosine": o.embedding_cosine,
                           "embedding_backend": o.embedding_backend, "lexical_only_view": o.lexical_only_category},
                          "", f"Textual overlap flag for review: {o.category}"
                          + (" (the matching reference is cited in this sentence)." if attributed else "."),
                          rec, {"overlap_id": o.flag_id, "category": o.category}))

    # Numeric
    for n in result.numeric.flags:
        ctx = " || ".join(dict.fromkeys(n.contexts))
        flags.append(Flag("", "numeric inconsistency", n.severity, ctx, " vs ".join(dict.fromkeys(n.sections)),
                          ", ".join(str(p) for p in dict.fromkeys(n.pages)), "", "", "",
                          {"values": n.values, "difference": n.difference, "method": n.method}, "", n.reason,
                          "Check which value is correct and make all sections consistent, or state clearly "
                          "that the numbers refer to different settings.", {"kind": n.kind, "metric": n.metric}))

    flags.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), FLAG_TYPES.index(f.type) if f.type in FLAG_TYPES else 99))
    for i, f in enumerate(flags, 1):
        f.flag_id = f"F{i:03d}"
    return flags


def summarize(flags: list[Flag]) -> dict[str, Any]:
    """Counts per flag type and severity."""
    return {
        "total_flags": len(flags),
        "by_type": {t: sum(f.type == t for f in flags) for t in FLAG_TYPES},
        "by_severity": {s: sum(f.severity == s for f in flags) for s in SEVERITY_ORDER},
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def build_report_dict(result: Any, flags: list[Flag]) -> dict[str, Any]:
    """Full JSON-serialisable report."""
    doc = result.document
    return {
        "disclaimer": DISCLAIMER,
        "meta": {
            "paper": doc.source, "title": doc.title, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pages": len(doc.pages), "sections": [clean_heading(s.heading) if s.heading else s.name for s in doc.sections], "sentences": len(doc.sentences),
            "references_parsed": len(doc.references), "reference_style": doc.reference_style,
            "reference_pdfs": result.corpus.file_to_ref if result.corpus else {},
            "backends": result.backends, "extraction_warnings": doc.warnings,
            "settings": {k: str(v) for k, v in asdict(result.settings).items() if k != "extra"},
        },
        "summary": summarize(flags),
        "flags": [f.to_dict() for f in flags],
        "claims": [c.to_dict() for c in result.claims],
        "evidence": [e.to_dict() for e in result.evidence],
        "citations": {"summary": result.citations.summary(), "markers": result.citations.to_rows(),
                      "unresolved": result.citations.unresolved},
        "overlap": [o.to_dict() for o in result.overlap],
        "numbers": {
            "mentions": [asdict(m) for m in result.numeric.mentions],
            "section_aware_flags": [f.to_dict() for f in result.numeric.section_aware],
            "rule_based_flags": [f.to_dict() for f in result.numeric.rule_based],
            "arithmetic_flags": [f.to_dict() for f in result.numeric.arithmetic],
        },
    }


def _fmt_scores(scores: dict[str, Any]) -> str:
    parts = []
    for k, v in scores.items():
        parts.append(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}")
    return "; ".join(parts)


def write_json(report: dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def write_csv(flags: list[Flag], path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(REPORT_FIELDS))
        w.writeheader()
        for f in flags:
            row = {k: getattr(f, k) for k in REPORT_FIELDS}
            row["scores"] = _fmt_scores(f.scores)
            w.writerow(row)
        w.writerow({k: "" for k in REPORT_FIELDS} | {"flag_id": "NOTE", "type": "disclaimer", "text": DISCLAIMER})
    return path


def render_markdown(report: dict[str, Any]) -> str:
    m, s = report["meta"], report["summary"]
    lines = [f"# Research Document Integrity Report", "", f"> **{report['disclaimer']}**", "",
             f"- **Paper:** {m['paper']}", f"- **Title:** {m['title']}", f"- **Generated:** {m['generated_at']}",
             f"- **Pages / sentences:** {m['pages']} / {m['sentences']}",
             f"- **Sections detected:** {', '.join(m['sections'])}",
             f"- **Reference list:** {m['references_parsed']} entries ({m['reference_style']})",
             f"- **Model backends:** " + ", ".join(f"{k}: {v}" for k, v in m["backends"].items()), ""]
    if any("fallback" in str(v) for v in m["backends"].values()):
        lines += ["> **Warning:** one or more models ran in fallback mode (see backends). Semantic/NLI results "
                  "in this report are NOT from the transformer models.", ""]
    lines += ["## Summary", "", "| Flag type | Count |", "|---|---|"]
    lines += [f"| {t} | {c} |" for t, c in s["by_type"].items()]
    lines += ["", f"Severity: high {s['by_severity']['high']}, medium {s['by_severity']['medium']}, "
              f"low {s['by_severity']['low']}. Total {s['total_flags']}.", "", "## Flags for human review", ""]
    for f in report["flags"]:
        lines += [f"### {f['flag_id']} - {f['type']} ({f['severity']})", "",
                  f"- **Location:** {f['section']}, page {f['page']}", f"- **Text:** {f['text']}"]
        if f["citation"]:
            lines.append(f"- **Citation:** {f['citation']}")
        if f["evidence"]:
            lines.append(f"- **Evidence ({f['evidence_source']}):** {f['evidence']}")
        if f["scores"]:
            lines.append(f"- **Scores:** {_fmt_scores(f['scores'])}")
        if f["nli_label"]:
            lines.append(f"- **NLI label:** {f['nli_label']}")
        lines += [f"- **Reason:** {f['reason']}", f"- **Recommendation:** {f['recommendation']}", ""]
    if m["extraction_warnings"]:
        lines += ["## Extraction warnings", ""] + [f"- {w}" for w in m["extraction_warnings"]] + [""]
    lines += ["---", report["disclaimer"]]
    return "\n".join(lines)


def write_markdown(report: dict[str, Any], path: Path) -> Path:
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


_HTML_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--high:#c62828;--med:#b26a00;--low:#2e7d32;--accent:#3949ab}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececee;--muted:#a0a0a8;--line:#333338;--high:#ef5350;--med:#ffa726;--low:#66bb6a;--accent:#8c9eff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}
.disc{border-left:4px solid var(--med);background:var(--card);padding:12px 14px;border-radius:6px;margin:16px 0}
.meta{color:var(--muted);font-size:13px}.warn{border-left-color:var(--high)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}.card b{font-size:22px;display:block}
.card span{color:var(--muted);font-size:12px}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}input,select{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{cursor:pointer;user-select:none;white-space:nowrap;position:sticky;top:0;background:var(--card)}th:after{content:" \\2195";color:var(--muted);font-size:11px}
.sev{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;font-size:12px;font-weight:600}
.sev-high{background:var(--high)}.sev-medium{background:var(--med)}.sev-low{background:var(--low)}
tr.row-high td:first-child{border-left:4px solid var(--high)}tr.row-medium td:first-child{border-left:4px solid var(--med)}tr.row-low td:first-child{border-left:4px solid var(--low)}
details summary{cursor:pointer;color:var(--accent)}details div{margin-top:6px;padding:8px;background:var(--bg);border-radius:6px}
.k{color:var(--muted)}
"""

_HTML_JS = """
document.querySelectorAll('th').forEach((th,i)=>th.addEventListener('click',()=>{
 const tb=th.closest('table').tBodies[0];const rows=[...tb.rows];const asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
 rows.sort((a,b)=>{const x=a.cells[i].dataset.sort||a.cells[i].innerText,y=b.cells[i].dataset.sort||b.cells[i].innerText;
 const nx=parseFloat(x),ny=parseFloat(y);const r=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?r:-r});
 rows.forEach(r=>tb.appendChild(r));}));
function filt(){const q=document.getElementById('q').value.toLowerCase(),t=document.getElementById('t').value,s=document.getElementById('s').value;
 document.querySelectorAll('#flags tbody tr').forEach(r=>{const ok=(!q||r.innerText.toLowerCase().includes(q))&&(!t||r.dataset.type===t)&&(!s||r.dataset.sev===s);r.style.display=ok?'':'none'})}
['q','t','s'].forEach(id=>document.getElementById(id).addEventListener('input',filt));
"""


def render_html(report: dict[str, Any]) -> str:
    """Self-contained HTML report (no external assets)."""
    e = html.escape
    m, s = report["meta"], report["summary"]
    fallback = any("fallback" in str(v) for v in m["backends"].values())
    cards = "".join(f'<div class="card"><b>{c}</b><span>{e(t)}</span></div>' for t, c in s["by_type"].items())
    rows = []
    for f in report["flags"]:
        sev = f["severity"]
        detail = []
        if f["evidence"]:
            detail.append(f'<p><span class="k">Evidence ({e(f["evidence_source"])}):</span> {e(f["evidence"])}</p>')
        if f["scores"]:
            detail.append(f'<p><span class="k">Scores:</span> {e(_fmt_scores(f["scores"]))}</p>')
        if f["nli_label"]:
            detail.append(f'<p><span class="k">NLI label:</span> {e(f["nli_label"])}</p>')
        detail.append(f'<p><span class="k">Reason:</span> {e(f["reason"])}</p>')
        detail.append(f'<p><span class="k">Recommendation:</span> {e(f["recommendation"])}</p>')
        rows.append(
            f'<tr class="row-{sev}" data-type="{e(f["type"])}" data-sev="{sev}">'
            f'<td>{e(f["flag_id"])}</td><td>{e(f["type"])}</td>'
            f'<td data-sort="{SEVERITY_ORDER.get(sev, 9)}"><span class="sev sev-{sev}">{sev}</span></td>'
            f'<td>{e(str(f["section"]))}</td><td>{e(str(f["page"]))}</td><td>{e(f["citation"])}</td>'
            f'<td>{e(f["text"])}<details><summary>Evidence &amp; reasoning</summary><div>{"".join(detail)}</div></details></td></tr>'
        )
    type_opts = "".join(f'<option value="{e(t)}">{e(t)}</option>' for t in FLAG_TYPES)
    backends = ", ".join(f"{e(k)}: {e(str(v))}" for k, v in m["backends"].items())
    warn = ('<div class="disc warn"><b>Fallback mode:</b> one or more models were unavailable, so semantic '
            'and NLI results in this report come from fallback methods, not transformer models.</div>') if fallback else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Integrity Report</title><style>{_HTML_CSS}</style></head><body><main>
<h1>Research Document Integrity Report</h1>
<div class="meta">{e(m['paper'])} &middot; {e(m['title'])} &middot; generated {e(m['generated_at'])}</div>
<div class="disc"><b>Disclaimer.</b> {e(report['disclaimer'])}</div>{warn}
<div class="meta">Pages: {m['pages']} &middot; Sentences: {m['sentences']} &middot; Sections: {e(', '.join(m['sections']))} &middot;
References parsed: {m['references_parsed']} ({e(m['reference_style'])}) &middot; Backends: {backends}</div>
<h2>Summary ({s['total_flags']} flags: {s['by_severity']['high']} high, {s['by_severity']['medium']} medium, {s['by_severity']['low']} low)</h2>
<div class="cards">{cards}</div>
<h2>Flags for human review</h2>
<div class="controls"><input id="q" placeholder="Search text..."><select id="t"><option value="">All types</option>{type_opts}</select>
<select id="s"><option value="">All severities</option><option>high</option><option>medium</option><option>low</option></select></div>
<div class="tablewrap"><table id="flags"><thead><tr><th>ID</th><th>Type</th><th>Severity</th><th>Section</th><th>Page</th><th>Citation</th><th>Text</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="meta">{e(report['disclaimer'])}</p></main><script>{_HTML_JS}</script></body></html>"""


def write_html(report: dict[str, Any], path: Path) -> Path:
    path.write_text(render_html(report), encoding="utf-8")
    return path


def write_reports(result: Any, out_dir: Path) -> dict[str, Path]:
    """Write report.json/.csv/.md/.html to ``out_dir``. Returns the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    flags = result.flags
    report = build_report_dict(result, flags)
    paths = {
        "json": write_json(report, out_dir / "report.json"),
        "csv": write_csv(flags, out_dir / "report.csv"),
        "md": write_markdown(report, out_dir / "report.md"),
        "html": write_html(report, out_dir / "report.html"),
    }
    logger.info("Reports written to %s", out_dir)
    return paths
