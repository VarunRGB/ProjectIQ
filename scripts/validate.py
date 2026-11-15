"""Validation: how closely ProjectIQ agrees with faculty, and whether the Integrity
Agent catches the planted controls.

1) Marks agreement (one row per submission and criterion):

    submission_id,criterion,faculty,system,max
    S101,implementation,7,6,10
    S101,report_clarity,8,8,10

   For each criterion, and for the total per submission:
   - Spearman rank correlation, with a 95% bootstrap interval (small samples have
     wide intervals; report them)
   - quadratic weighted kappa on whole marks (0..max)
   - mean absolute error and mean signed error (positive = system marks higher)
   - share of marks within 1 point

2) Integrity controls (one row per planted or clean case):

    submission_id,kind,planted
    S102,similar_code,1
    S105,similar_code,0

   compared with the Integrity Agent's <submission>.json files: detection rate,
   precision and false flags per kind.

    python scripts/validate.py --marks marks.csv
    python scripts/validate.py --controls controls.csv --integrity-dir outputs/integrity
    python scripts/validate.py --marks marks.csv --split test   # needs a "split" column

Tune on the dev split only; report numbers from the test split.
"""
import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- #
# Metrics (plain Python so the numbers are easy to check by hand)
# --------------------------------------------------------------------------- #
def _ranks(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x: List[float], y: List[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx and sy else float("nan")


def spearman(x: List[float], y: List[float]) -> float:
    """Pearson correlation of the ranks (ties get average ranks)."""
    if len(x) < 3:
        return float("nan")
    return pearson(_ranks(x), _ranks(y))


def bootstrap_ci(x: List[float], y: List[float], stat=spearman, n: int = 2000,
                 seed: int = 7) -> tuple:
    rng = random.Random(seed)
    idx = list(range(len(x)))
    values = []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        v = stat([x[i] for i in s], [y[i] for i in s])
        if not math.isnan(v):
            values.append(v)
    if len(values) < n * 0.5:
        return (float("nan"), float("nan"))
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def quadratic_weighted_kappa(a: List[int], b: List[int], lo: int, hi: int) -> float:
    """Cohen's kappa with quadratic weights: a 2-point disagreement costs four times
    a 1-point one. 1 = perfect agreement, 0 = no better than chance."""
    k = hi - lo + 1
    if k < 2 or not a:
        return float("nan")
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[x - lo][y - lo] += 1
    n = len(a)
    ra = [sum(row) for row in obs]
    rb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2)
            num += w * obs[i][j]
            den += w * ra[i] * rb[j] / n
    return 1 - num / den if den else float("nan")


def agreement(faculty: List[float], system: List[float], max_mark: Optional[float]) -> Dict:
    n = len(faculty)
    diffs = [s - f for f, s in zip(faculty, system)]
    fi, si = [round(v) for v in faculty], [round(v) for v in system]
    lo = 0 if max_mark is not None else min(fi + si)
    hi = int(max_mark) if max_mark is not None else max(fi + si)
    lo_ci, hi_ci = bootstrap_ci(faculty, system)
    return {
        "n": n,
        "spearman": spearman(faculty, system),
        "spearman_ci95": [lo_ci, hi_ci],
        "weighted_kappa": quadratic_weighted_kappa(fi, si, lo, hi),
        "mean_abs_error": sum(abs(d) for d in diffs) / n,
        "mean_signed_error": sum(diffs) / n,
        "within_1": sum(abs(d) <= 1 for d in diffs) / n,
    }


# --------------------------------------------------------------------------- #
# Marks
# --------------------------------------------------------------------------- #
def read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def marks_report(rows: List[Dict[str, str]], split: Optional[str]) -> Dict:
    if split:
        rows = [r for r in rows if r.get("split", "") == split]
    by_crit: Dict[str, list] = defaultdict(list)
    totals: Dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        f, s = float(r["faculty"]), float(r["system"])
        mx = float(r["max"]) if r.get("max") else None
        by_crit[r["criterion"]].append((f, s, mx))
        t = totals[r["submission_id"]]
        t[0] += f
        t[1] += s
        t[2] += mx or 0
    out = {}
    for crit, vals in sorted(by_crit.items()):
        mx = max((v[2] for v in vals if v[2] is not None), default=None)
        out[crit] = agreement([v[0] for v in vals], [v[1] for v in vals], mx)
    if len(by_crit) > 1 and totals:
        vals = list(totals.values())
        out["TOTAL"] = agreement([v[0] for v in vals], [v[1] for v in vals],
                                 max(v[2] for v in vals) or None)
    return out


# --------------------------------------------------------------------------- #
# Integrity controls
# --------------------------------------------------------------------------- #
def controls_report(rows: List[Dict[str, str]], integrity_dir: Path) -> Dict:
    flagged: Dict[str, set] = {}
    for path in integrity_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "submission_id" in data:
            flagged[data["submission_id"]] = {f["kind"] for f in data.get("findings", [])}
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    missing = []
    for r in rows:
        sid, kind, planted = r["submission_id"], r["kind"], r["planted"] in ("1", "yes", "true")
        if sid not in flagged:
            missing.append(sid)
            continue
        hit = kind in flagged[sid]
        key = ("tp" if hit else "fn") if planted else ("fp" if hit else "tn")
        stats[kind][key] += 1
    out = {}
    for kind, s in sorted(stats.items()):
        pos, neg = s["tp"] + s["fn"], s["fp"] + s["tn"]
        out[kind] = {**s,
                     "detection_rate": s["tp"] / pos if pos else float("nan"),
                     "precision": s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else float("nan"),
                     "false_flag_rate": s["fp"] / neg if neg else float("nan")}
    if missing:
        out["_missing_results"] = sorted(set(missing))
    return out


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _f(x, digits=2) -> str:
    if isinstance(x, list):
        return f"[{_f(x[0])}, {_f(x[1])}]"
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def to_markdown(marks: Optional[Dict], controls: Optional[Dict]) -> str:
    lines = []
    if marks:
        lines += ["## Agreement with faculty marks", "",
                  "| Criterion | n | Spearman | 95% CI | Weighted kappa | Mean abs. error | Mean signed error | Within 1 |",
                  "|---|---|---|---|---|---|---|---|"]
        for crit, m in marks.items():
            lines.append(f"| {crit} | {m['n']} | {_f(m['spearman'])} | {_f(m['spearman_ci95'])} | "
                         f"{_f(m['weighted_kappa'])} | {_f(m['mean_abs_error'])} | "
                         f"{_f(m['mean_signed_error'])} | {m['within_1']:.0%} |")
        lines += ["", "Positive signed error = ProjectIQ marks higher than faculty.", ""]
    if controls:
        lines += ["## Integrity Agent on planted controls", "",
                  "| Check | Caught | Missed | False flags | Clean | Detection rate | Precision | False-flag rate |",
                  "|---|---|---|---|---|---|---|---|"]
        for kind, s in controls.items():
            if kind.startswith("_"):
                continue
            lines.append(f"| {kind} | {s['tp']} | {s['fn']} | {s['fp']} | {s['tn']} | "
                         f"{_f(s['detection_rate'])} | {_f(s['precision'])} | {_f(s['false_flag_rate'])} |")
        if controls.get("_missing_results"):
            lines += ["", f"No integrity result for: {', '.join(controls['_missing_results'])}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--marks", type=Path, help="CSV: submission_id,criterion,faculty,system[,max][,split]")
    ap.add_argument("--split", help='only rows whose "split" column equals this (e.g. test)')
    ap.add_argument("--controls", type=Path, help="CSV: submission_id,kind,planted")
    ap.add_argument("--integrity-dir", type=Path, default=Path("outputs/integrity"))
    ap.add_argument("--out", type=Path, default=Path("outputs/validation"))
    args = ap.parse_args(argv)
    if not args.marks and not args.controls:
        ap.error("give --marks, --controls or both")
    marks = marks_report(read_csv(args.marks), args.split) if args.marks else None
    controls = controls_report(read_csv(args.controls), args.integrity_dir) if args.controls else None
    md = to_markdown(marks, controls)
    print(md)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "validation.md").write_text(md + "\n", encoding="utf-8")
    (args.out / "validation.json").write_text(json.dumps({"marks": marks, "controls": controls},
                                                         indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
