"""Command line:  python -m projectiq_summarizer report.pdf [more files...]"""
import argparse
import sys
from pathlib import Path

from .config import Settings
from .llm import LLMError, OllamaClient
from .loaders import load_text
from .pipeline import evaluate
from .report import save, to_console
from .rubric import load_rubric


def build_parser() -> argparse.ArgumentParser:
    s = Settings()
    p = argparse.ArgumentParser(
        prog="python -m projectiq_summarizer",
        description="Summarise a project report, score it against a rubric, and give "
                    "a recommendation with a confidence score. Runs on a local Ollama model.")
    p.add_argument("files", nargs="+", help=".txt, .md, .pdf or .docx files")
    p.add_argument("--model", default=s.model, help=f"Ollama model (default {s.model})")
    p.add_argument("--samples", type=int, default=s.judge_samples,
                   help=f"judge runs per file; more runs = better confidence (default {s.judge_samples})")
    p.add_argument("--rubric", default=None, help="path to a rubric JSON file")
    p.add_argument("--out", default="outputs", help="folder for JSON and Markdown reports")
    p.add_argument("--url", default=s.ollama_url, help=f"Ollama URL (default {s.ollama_url})")
    return p


def safe_console() -> None:
    """Windows consoles can choke on characters the model emits (dashes,
    curly quotes). Replace them instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    safe_console()
    args = build_parser().parse_args(argv)
    settings = Settings(ollama_url=args.url, model=args.model, judge_samples=args.samples)
    llm = OllamaClient(settings)
    try:
        llm.check()
    except LLMError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    rubric = load_rubric(args.rubric)

    status = 0
    for f in args.files:
        name = Path(f).name
        try:
            text = load_text(f)
            report = evaluate(text, name, llm, settings, rubric,
                              progress=lambda m: print(f"[{name}] {m}...", flush=True))
        except (LLMError, ValueError, ImportError, OSError) as e:
            print(f"ERROR on {name}: {e}", file=sys.stderr)
            status = 1
            continue
        print(to_console(report))
        print(f"  Saved: {save(report, args.out)}\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
