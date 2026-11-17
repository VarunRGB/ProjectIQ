"""Compare a document check with an answer key (scripts/make_benchmark.py writes one).

    python -m projectiq_integrity.benchmark            # runs the bundled benchmark

For every planted sentence: what the key expects, what the tool said, and whether it
matched. Then the totals that matter: copied sentences caught, right type, right
source, false alarms, copied chunks found, and the overall percentage.
"""
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .document_check import COPY_CATEGORIES, NEAR, PARA, SIMILAR, VERBATIM, DocumentReport, check_document, copy_form

BENCH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "benchmark"
SHORT = {VERBATIM: "verbatim", NEAR: "near-verbatim", PARA: "paraphrase", SIMILAR: "similar", None: "none"}


@dataclass
class CaseResult:
    case: str
    expected: str
    got: str
    ok: bool
    source_expected: str
    source_got: str
    note: str
    sentence: str


@dataclass
class BenchmarkResult:
    cases: List[CaseResult]
    copied_total: int            # planted sentences that should count as copying
    copied_caught: int           # ... that the tool counted as copying (any type)
    right_type: int              # planted cases whose answer matched the key exactly
    right_source: int            # caught copies traced to the right reference
    false_alarms: List[CaseResult]   # counted as copying, but the key says it is not
    chunks_expected: List[str]
    chunks_found: List[str]
    chunks_missing: List[str]
    expected_percent: float
    tool_percent: float
    missing_sentences: List[str] = field(default_factory=list)   # key rows the tool never checked

    @property
    def planted(self) -> List[CaseResult]:
        return [c for c in self.cases if c.case != "own"]


def _key(text: str) -> str:
    return copy_form(text)[:60]


def evaluate(report: DocumentReport, key_csv: Path, chunks_csv: Optional[Path] = None) -> BenchmarkResult:
    rows = list(csv.DictReader(open(key_csv, encoding="utf-8")))
    by_key: Dict[str, object] = {}
    for m in report.matches:
        by_key.setdefault(_key(m.text), m)
    cases, missing = [], []
    words_expected = 0
    for r in rows:
        m = by_key.get(_key(r["sentence"]))
        allowed = r["expected"].split("|")
        if m is None:
            # The tool did not check it at all: right for fully quoted text, else a reading problem.
            if "none" in allowed:
                cases.append(CaseResult(case=r["case"], expected=r["expected"], got="not checked (quoted)",
                                        ok=True, source_expected=r["source"], source_got="", note=r["note"],
                                        sentence=r["sentence"]))
            else:
                missing.append(r["case"] + ": " + r["sentence"][:60])
            continue
        got = SHORT[m.category]
        if any(a in ("verbatim", "near-verbatim", "paraphrase") for a in allowed[:1]):
            words_expected += m.words
        cases.append(CaseResult(
            case=r["case"], expected=r["expected"], got=got, ok=got in allowed,
            source_expected=r["source"], source_got=m.reference if m.category in COPY_CATEGORIES else "",
            note=r["note"], sentence=r["sentence"]))

    copy_types = {"verbatim", "near-verbatim", "paraphrase"}
    should_copy = [c for c in cases if c.expected.split("|")[0] in copy_types]
    caught = [c for c in should_copy if c.got in copy_types]
    false_alarms = [c for c in cases if c.got in copy_types and not (set(c.expected.split("|")) & copy_types)]

    # Chunks: a key chunk is found if a tool chunk from that source covers its first and last sentence.
    found, missing_chunks, expected_chunks = [], [], []
    if chunks_csv and chunks_csv.exists():
        index_of = {_key(m.text): m.index for m in report.matches}
        sent_of = {r["case"]: r["sentence"] for r in rows}
        for ch in csv.DictReader(open(chunks_csv, encoding="utf-8")):
            label = f"{ch['first_case']} to {ch['last_case']} from {ch['source']}"
            expected_chunks.append(label)
            a = index_of.get(_key(sent_of.get(ch["first_case"], "")))
            b = index_of.get(_key(sent_of.get(ch["last_case"], "")))
            hit = a is not None and b is not None and any(
                c.reference == ch["source"] and c.first <= a and c.last >= b for c in report.chunks)
            (found if hit else missing_chunks).append(label)

    return BenchmarkResult(
        cases=cases, copied_total=len(should_copy), copied_caught=len(caught),
        right_type=sum(c.ok for c in cases if c.case != "own"),
        right_source=sum(c.source_got == c.source_expected for c in caught),
        false_alarms=false_alarms, chunks_expected=expected_chunks, chunks_found=found,
        chunks_missing=missing_chunks,
        expected_percent=round(100.0 * words_expected / report.words_checked, 1) if report.words_checked else 0.0,
        tool_percent=report.overall_percent, missing_sentences=missing)


def to_markdown(res: BenchmarkResult) -> str:
    planted = res.planted
    lines = [
        "## Benchmark result", "",
        f"- Copied sentences counted as copying: **{res.copied_caught} of {res.copied_total}**",
        f"- Planted cases with exactly the expected answer: **{res.right_type} of {len(planted)}**",
        f"- Caught copies traced to the right reference: **{res.right_source} of {res.copied_caught}**",
        f"- False alarms (counted as copying but should not be): **{len(res.false_alarms)}**"
        + (f" ({', '.join(c.case for c in res.false_alarms)})" if res.false_alarms else ""),
        f"- Copied chunks found: **{len(res.chunks_found)} of {len(res.chunks_expected)}**"
        + (f" (missing: {'; '.join(res.chunks_missing)})" if res.chunks_missing else ""),
        f"- Overall copying: tool **{res.tool_percent}%**, answer key **{res.expected_percent}%**", "",
        "| Case | Expected | Got | OK | Source | What it tests |", "|---|---|---|---|---|---|",
    ]
    for c in planted:
        src = c.source_got or "-"
        if c.source_got and c.source_got != c.source_expected:
            src += f" (expected {c.source_expected})"
        lines.append(f"| {c.case} | {c.expected} | {c.got} | {'yes' if c.ok else '**NO**'} | {src} | {c.note} |")
    own_flagged = [c for c in res.cases if c.case == "own" and c.got not in ("none", "not checked (quoted)")]
    lines.append("")
    lines.append(f"Student's own sentences: {sum(c.case == 'own' for c in res.cases) - len(own_flagged)} of "
                 f"{sum(c.case == 'own' for c in res.cases)} correctly left alone.")
    for c in own_flagged:
        lines.append(f"- marked {c.got}: {c.sentence}")
    if res.missing_sentences:
        lines += ["", "Key sentences the tool did not check (PDF reading problem?):"]
        lines += [f"- {s}" for s in res.missing_sentences]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--document", type=Path, default=BENCH / "student_paper.pdf")
    ap.add_argument("--references", type=Path, default=BENCH / "references")
    ap.add_argument("--key", type=Path, default=BENCH / "answer_key.csv")
    ap.add_argument("--chunks", type=Path, default=BENCH / "expected_chunks.csv")
    ap.add_argument("--out", type=Path, help="also save the table as a Markdown file")
    args = ap.parse_args(argv)
    report = check_document(args.document, args.references, check_ai=False)
    md = to_markdown(evaluate(report, args.key, args.chunks))
    md += f"\n\nModels used: embeddings {report.backends.get('embeddings')}; NLI {report.backends.get('nli')}."
    print(md)
    if args.out:
        args.out.write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
