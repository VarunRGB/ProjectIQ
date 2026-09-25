"""Run the three required test cases and check each result.

    python run_test_cases.py                 # default model, 3 judge runs each
    python run_test_cases.py --samples 1     # faster, confidence is less complete
    python run_test_cases.py --model qwen3:8b

A case PASSES when the recommendation is in the expected set. The content
type the model assigned is shown alongside, as the second check."""
import argparse
import sys
from pathlib import Path

from projectiq_summarizer.__main__ import safe_console
from projectiq_summarizer.config import Settings
from projectiq_summarizer.llm import LLMError, OllamaClient
from projectiq_summarizer.loaders import load_text
from projectiq_summarizer.pipeline import evaluate
from projectiq_summarizer.report import LABEL, TYPE_LABEL, save, to_console
from projectiq_summarizer.rubric import load_rubric

HERE = Path(__file__).parent
CASES = [
    ("1_poorly_written.txt", "Poorly written content",
     {"REVISE_AND_RESUBMIT"}, "poorly_written"),
    ("2_highly_technical.txt", "Highly technical content",
     {"ACCEPT", "ACCEPT_WITH_MINOR_REVISIONS"}, "highly_technical"),
    ("3_ai_written.txt", "Obviously AI-written content",
     {"FLAG_FOR_REVIEW"}, "likely_ai_generated"),
]


def main() -> int:
    safe_console()
    s = Settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=s.model)
    ap.add_argument("--samples", type=int, default=s.judge_samples)
    ap.add_argument("--url", default=s.ollama_url)
    ap.add_argument("--out", default=str(HERE / "outputs"))
    args = ap.parse_args()

    settings = Settings(ollama_url=args.url, model=args.model, judge_samples=args.samples)
    llm = OllamaClient(settings)
    try:
        llm.check()
    except LLMError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    rubric = load_rubric()

    rows = []
    for fname, title, expected, expected_type in CASES:
        print(f"\n### {title}  ({fname})")
        text = load_text(HERE / "test_cases" / fname)
        r = evaluate(text, fname, llm, settings, rubric,
                     progress=lambda m: print(f"  ... {m}", flush=True))
        print(to_console(r))
        save(r, args.out)
        ok = r.recommendation.value in expected
        rows.append((title, r, ok, r.content_type == expected_type))

    # ---- summary table -----------------------------------------------------
    line = "-" * 104
    print("\n" + line)
    print(f"{'Test case':<30} {'Recommendation':<30} {'Confidence':<16} "
          f"{'Content type':<22} Result")
    print(line)
    md = ["# ProjectIQ summariser: test case results", "",
          f"Model: `{settings.model}`  ·  judge runs per case: {settings.judge_samples}", "",
          "| Test case | Recommendation | Confidence | Content type (model) | "
          "Text signals | Result |",
          "|---|---|---|---|---|---|"]
    for title, r, ok, type_ok in rows:
        conf = f"{r.confidence.score:.0f} ({r.confidence.band})"
        ctype = r.content_type + ("" if type_ok else " (!)")
        verdict = "PASS" if ok else "FAIL"
        print(f"{title:<30} {LABEL[r.recommendation.value]:<30} {conf:<16} "
              f"{ctype:<22} {verdict}")
        md.append(f"| {title} | {LABEL[r.recommendation.value]} | {conf} | "
                  f"{TYPE_LABEL[r.content_type]} | {r.signals['profile']} | "
                  f"{'PASS' if ok else 'FAIL'} |")
    print(line)
    passed = sum(ok for _, _, ok, _ in rows)
    print(f"{passed}/{len(rows)} passed.  Full reports in: {args.out}")
    md += ["", f"**{passed}/{len(rows)} passed.**", "",
           "Per-case reports with the summary, rubric scores, evidence quotes and "
           "confidence breakdown are in the same folder."]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "test_case_summary.md").write_text("\n".join(md), encoding="utf-8")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
