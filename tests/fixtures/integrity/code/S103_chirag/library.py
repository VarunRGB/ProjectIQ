
from common import main_menu
import json


def read_store(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []


def save_store(path, records):
    with open(path, "w") as fh:
        json.dump(records, fh, indent=2)


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


def fine_for(record, today, per_day=2):
    if not record.get("due") or record["due"] >= today:
        return 0
    days = (today - record["due"])
    return min(days * per_day, 100)
