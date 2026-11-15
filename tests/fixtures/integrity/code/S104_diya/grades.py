
from common import main_menu, load_rows
import statistics


def grade_stats(marks):
    if not marks:
        return {}
    return {"mean": statistics.mean(marks), "median": statistics.median(marks),
            "max": max(marks), "min": min(marks)}


def search(records, term):
    term = term.lower()
    return [r for r in records if term in r["title"].lower() or term in r["author"].lower()]


def overdue(records, today):
    late = []
    for r in records:
        if r.get("due") and r["due"] < today and not r.get("returned"):
            late.append(r)
    late.sort(key=lambda r: r["due"])
    return late


def letter(mark):
    for cut, grade in ((90, "A"), (75, "B"), (60, "C"), (40, "D")):
        if mark >= cut:
            return grade
    return "F"
