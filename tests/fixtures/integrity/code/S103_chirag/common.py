
import csv
import sys


def load_rows(path):
    """Starter code given to every student: read a CSV into a list of dicts."""
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main_menu():
    print("1. Add item")
    print("2. List items")
    print("3. Report")
    print("4. Quit")
    return input("Choose: ").strip()
