
"""ProjectIQ experiment runner with selectable files and stages."""

import argparse
import csv
import sys
import time
from pathlib import Path

from projectiq_summarizer.__main__ import safe_console
from projectiq_summarizer.config import Settings
from projectiq_summarizer.llm import LLMError, OllamaClient
from projectiq_summarizer.loaders import load_text
from projectiq_summarizer.pipeline import evaluate, _judge_input
from projectiq_summarizer.report import LABEL, TYPE_LABEL, save, to_console
from projectiq_summarizer.rubric import load_rubric
from projectiq_summarizer.signals import compute_signals
from projectiq_summarizer.summariser import summarise
from projectiq_summarizer.judge import judge_once

HERE = Path(__file__).parent

ALIASES = {
    "small": ["small"],
    "medium": ["medium"],
    "large": ["large"],
    "poorly_written": ["poorly_written", "poorly-written"],
    "highly_technical": ["highly_technical", "highly-technical"],
    "ai_written": ["ai_written", "ai-written", "3_ai_written"],
}

EXPECTED = {
    "1_poorly_written.txt": (
        {"REVISE_AND_RESUBMIT"}, "poorly_written"
    ),
    "2_highly_technical.txt": (
        {"ACCEPT", "ACCEPT_WITH_MINOR_REVISIONS"}, "highly_technical"
    ),
    "3_ai_written.txt": (
        {"FLAG_FOR_REVIEW"}, "likely_ai_generated"
    ),
}


def find_test_files():
    """Find the six supported test types in common local test directories."""
    roots = [
        HERE / "test_cases",
        HERE / "testcases",
        HERE / "test_cases" / "scalability",
        HERE / "testcases" / "scalability",
        HERE / "test_cases" / "content_quality",
        HERE / "testcases" / "content_quality",
    ]

    found = {}
    for root in roots:
        if root.exists():
            for path in root.rglob("*.txt"):
                found.setdefault(path.name.lower(), path)

    selected = {}
    for alias, patterns in ALIASES.items():
        for filename, path in found.items():
            if any(pattern in filename for pattern in patterns):
                selected.setdefault(alias, path)

    # Preserve support for the three original repository cases.
    for filename in EXPECTED:
        for path in found.values():
            if path.name.lower() == filename.lower():
                selected.setdefault(
                    "ai_written" if "ai_written" in filename
                    else "poorly_written" if "poorly_written" in filename
                    else "highly_technical",
                    path,
                )

    return selected


def resolve_files(requested, discovered):
    if not requested:
        return list(dict.fromkeys(discovered.values()))

    chosen = []
    missing = []

    for item in requested:
        key = item.lower().replace("-", "_")
        path = None

        if key in discovered:
            path = discovered[key]
        else:
            for candidate in discovered.values():
                if candidate.name.lower() == item.lower():
                    path = candidate
                    break

        if path is None:
            missing.append(item)
        elif path not in chosen:
            chosen.append(path)

    if missing:
        available = ", ".join(
            sorted(set(discovered) | {p.name for p in discovered.values()})
        )
        raise ValueError(
            f"Could not find: {', '.join(missing)}. "
            f"Available names: {available}"
        )

    return chosen


def run_selected_agents(text, filename, agents, llm, settings, rubric):
    """Run standalone stages. 'all' uses the existing complete pipeline."""
    if "all" in agents:
        started = time.perf_counter()
        report = evaluate(
            text, filename, llm, settings, rubric,
            progress=lambda message: print(
                f"  ... {message}", flush=True
            ),
        )
        elapsed = time.perf_counter() - started
        print(to_console(report))
        save(report, HERE / "outputs")
        return {
            "file": filename,
            "agent": "all",
            "runtime_seconds": round(elapsed, 3),
            "result": report.recommendation.value,
        }

    timings = []

    if "signals" in agents:
        started = time.perf_counter()
        signals = compute_signals(text)
        elapsed = time.perf_counter() - started
        print(f"\nSignals for {filename}:")
        print(f"  Profile: {signals.profile}")
        print(f"  Words: {signals.words}")
        print(f"  AI-style score: "
              f"{signals.profile_scores['likely_ai_generated']:.3f}")
        print(f"  Runtime: {elapsed:.3f}s")
        timings.append((filename, "signals", elapsed))

    summary_result = None

    if "summary" in agents or "rubric" in agents:
        started = time.perf_counter()
        summary_result, chunks = summarise(text, llm, settings)
        elapsed = time.perf_counter() - started
        print(f"\nSummary for {filename}:")
        print(summary_result.summary)
        for point in summary_result.key_points:
            print(f"  - {point}")
        print(f"  Chunks: {chunks}")
        print(f"  Runtime: {elapsed:.3f}s")
        timings.append((filename, "summary", elapsed))

    if "rubric" in agents:
        started = time.perf_counter()
        judge_text = _judge_input(text, summary_result.summary, settings)
        judgments = []

        for i in range(max(1, settings.judge_samples)):
            temperature = (
                0.0 if i == 0 else settings.sample_temperature
            )
            judgment = judge_once(
                judge_text, rubric, llm, temperature, settings.seed + i
            )
            judgments.append(judgment)

        elapsed = time.perf_counter() - started
        print(f"\nRubric judge for {filename}:")
        for i, judgment in enumerate(judgments, start=1):
            print(f"\n  Run {i}:")
            print(judgment.model_dump_json(indent=2))
        print(f"  Runtime for rubric judging: {elapsed:.3f}s")
        timings.append((filename, "rubric", elapsed))

    return [
        {
            "file": filename,
            "agent": agent,
            "runtime_seconds": round(seconds, 3),
            "result": "",
        }
        for _, agent, seconds in timings
    ]


def main():
    safe_console()

    defaults = Settings()
    parser = argparse.ArgumentParser(
        description="Run selected ProjectIQ test files and stages."
    )
    parser.add_argument(
        "--files",
        nargs="+",
        help="Names/aliases: small medium large poorly_written "
             "highly_technical ai_written. Omit to run all discovered files.",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=["summary", "signals", "rubric", "all"],
        default=["all"],
        help="Stages to run. 'all' runs the complete evaluation pipeline.",
    )
    parser.add_argument("--model", default=defaults.model)
    parser.add_argument("--samples", type=int, default=defaults.judge_samples)
    parser.add_argument("--url", default=defaults.ollama_url)
    parser.add_argument("--out", default=str(HERE / "outputs"))
    args = parser.parse_args()

    if args.samples < 1:
        parser.error("--samples must be at least 1")

    if "all" in args.agents and len(args.agents) > 1:
        parser.error("Use 'all' by itself; do not combine it with other agents.")

    settings = Settings(
        ollama_url=args.url,
        model=args.model,
        judge_samples=args.samples,
    )

    discovered = find_test_files()
    if not discovered:
        print(
            "ERROR: No supported test files found. Check your test_cases "
            "or testcases folders.",
            file=sys.stderr,
        )
        return 2

    try:
        files = resolve_files(args.files, discovered)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    needs_model = any(a in args.agents for a in ("summary", "rubric", "all"))
    llm = None
    rubric = load_rubric()

    if needs_model:
        llm = OllamaClient(settings)
        try:
            llm.check()
        except LLMError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2

    output_rows = []
    suite_started = time.perf_counter()

    for path in files:
        print(f"\n### {path.name}")
        try:
            text = load_text(path)
            result = run_selected_agents(
                text, path.name, args.agents, llm, settings, rubric
            )
            if isinstance(result, dict):
                output_rows.append(result)
            else:
                output_rows.extend(result)
        except Exception as error:
            print(f"ERROR processing {path.name}: {error}", file=sys.stderr)
            output_rows.append({
                "file": path.name,
                "agent": ",".join(args.agents),
                "runtime_seconds": "",
                "result": f"ERROR: {error}",
            })

    total_elapsed = time.perf_counter() - suite_started
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "agent_runtime.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["file", "agent", "runtime_seconds", "result"],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nCompleted {len(files)} file(s).")
    print(f"Total suite runtime: {total_elapsed:.3f}s")
    print(f"Runtime log: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
