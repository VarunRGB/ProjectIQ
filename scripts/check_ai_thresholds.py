"""Check the AI percentage on a folder of labelled paragraphs, and suggest a threshold.

    python scripts/check_ai_thresholds.py --folder tests/fixtures/ai_calibration_real
    python scripts/check_ai_thresholds.py --folder tests/fixtures/ai_calibration_real --threshold 0.85

The folder holds .txt files and labels.csv (file, expected, what_it_tests), where
expected is ai, human or mixed. Each file goes through the same passage split and
scoring as a real report (projectiq_integrity/ai_detection.py): the Desklib classifier
gives each passage an AI probability, and HIGHER means more machine-like.

A file counts as flagged when its AI % reaches ai_report_percent (config.py, 20%).
At the end the script suggests the lowest threshold that flags NO human passage, and
how many AI passages that would catch. Put the value you choose in
projectiq_integrity/config.py (CLASSIFIER_THRESHOLD).

Needs models/ai-text-detector-v1.01 (python scripts/setup_offline.py).
"""
import argparse
import csv
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from projectiq_integrity import ai_detection  # noqa: E402
from projectiq_integrity.config import DEFAULT, ai_detector_info  # noqa: E402
from projectiq_integrity.ria.preprocessing import split_sentences  # noqa: E402
from projectiq_summarizer.signals import compute_signals  # noqa: E402

DEFAULT_FOLDER = ROOT / "tests" / "fixtures" / "ai_calibration_real"


def load_labels(folder: Path) -> dict:
    path = folder / "labels.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {row["file"]: row for row in csv.DictReader(f)}


def as_sentences(text: str) -> list:
    """(index, location, text) per sentence, one location per paragraph, like a real report."""
    out, i = [], 0
    for n, para in enumerate([p for p in text.split("\n\n") if p.strip()], 1):
        for s in split_sentences(para):
            out.append((i, f"para {n}", " ".join(s.split())))
            i += 1
    return out


def main(argv=None, scorer=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    ap.add_argument("--threshold", type=float, help="try this threshold instead of the one in config.py")
    args = ap.parse_args(argv)

    settings = replace(DEFAULT, classifier_ai_min=args.threshold)
    info = ai_detector_info(settings)
    threshold = info["threshold"]
    if scorer is None:
        scorer = ai_detection.make_detector(settings)
        if not scorer.available:
            print("Model not found: " + ", ".join(scorer.missing()) + "\nRun: python scripts/setup_offline.py")
            return 1

    files = sorted(args.folder.glob("*.txt"))
    if not files:
        print(f"No .txt files in {args.folder}")
        return 1
    labels = load_labels(args.folder)

    print(f"{info['label']}\nThreshold: passage flagged at AI probability >= {threshold}; "
          f"file flagged at AI % >= {settings.ai_report_percent}; passages of "
          f"{settings.ai_passage_min_words} to {settings.ai_passage_max_words} words\n")
    head = f"{'file':38} {'expect':7} {'words':>5} {'style':>6} {'AI probability (per passage)':28} {'AI %':>6}  {'result':12} ok?"
    print(head)
    print("-" * len(head))

    right = wrong = 0
    false_alarms, misses = [], []
    passage_scores = {"ai": [], "human": []}
    started = time.time()
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        det, passages = ai_detection.estimate(as_sentences(text), scorer=scorer, settings=settings)
        expected = labels.get(f.name, {}).get("expected", "?")
        scores = [p.score for p in passages if p.score is not None]
        if expected in passage_scores:
            passage_scores[expected] += scores
        pct = det["estimated_percent"]
        flagged = pct is not None and pct >= settings.ai_report_percent
        result = det["status"].lower() if pct is None else ("FLAGGED" if flagged else "not flagged")
        ok = None
        if pct is not None and expected == "ai":
            ok = flagged
        elif pct is not None and expected == "human":
            ok = not flagged
        if ok is True:
            right += 1
        elif ok is False:
            wrong += 1
            (false_alarms if expected == "human" else misses).append(f.name)
        style = compute_signals(text).profile_scores["likely_ai_generated"]
        score_txt = ", ".join(f"{s:.3f}" for s in scores) or "-"
        pct_txt = "-" if pct is None else f"{pct:.0f}"
        print(f"{f.name[:38]:38} {expected:7} {sum(p.words for p in passages):>5} {style:>6.2f} "
              f"{score_txt[:28]:28} {pct_txt:>6}  {result:12} {'-' if ok is None else ('yes' if ok else 'NO')}")

    print(f"\nCorrect: {right}   Wrong: {wrong}   ({time.time() - started:.0f} s for {len(files)} files)")
    if false_alarms:
        print("False alarms (human text flagged): " + ", ".join(false_alarms))
    if misses:
        print("Missed (AI text not flagged): " + ", ".join(misses))

    ai_s, hu_s = sorted(passage_scores["ai"]), sorted(passage_scores["human"])
    if ai_s and hu_s:
        print(f"\nAI passages scored {ai_s[0]:.3f} to {ai_s[-1]:.3f}; human passages {hu_s[0]:.3f} to {hu_s[-1]:.3f}.")
        safe = min(hu_s[-1] + 0.005, 1.0)            # just above the most AI-like human passage
        caught = sum(s >= safe for s in ai_s)
        print(f"Safest threshold with no human passage flagged: about {safe:.3f} "
              f"(would catch {caught} of {len(ai_s)} AI passages). "
              f"To use it: CLASSIFIER_THRESHOLD = {safe:.3f} in config.py")
        if ai_s[0] > hu_s[-1]:
            print("On this set AI and human passages do not overlap.")
        else:
            print("On this set the ranges overlap, so some AI text will be missed at a safe threshold.")
        print("Choose with care: a handful of paragraphs is not enough; add real student paragraphs.")
    print("\nstyle = stock-phrase score from projectiq_summarizer/signals.py (shown as context only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
