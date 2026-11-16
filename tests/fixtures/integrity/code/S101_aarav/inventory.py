
from common import load_rows, main_menu


class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, name, qty, price):
        if qty <= 0:
            raise ValueError("quantity must be positive")
        entry = self.items.setdefault(name, {"qty": 0, "price": price})
        entry["qty"] += qty
        entry["price"] = price

    def total_value(self):
        total = 0.0
        for name, entry in self.items.items():
            total += entry["qty"] * entry["price"]
        return round(total, 2)

    def low_stock(self, threshold=5):
        result = []
        for name, entry in sorted(self.items.items()):
            if entry["qty"] < threshold:
                result.append((name, entry["qty"]))
        return result

    def load(self, path):
        for row in load_rows(path):
            self.add(row["name"], int(row["qty"]), float(row["price"]))


def run():
    inv = Inventory()
    while True:
        choice = main_menu()
        if choice == "1":
            inv.add(input("name: "), int(input("qty: ")), float(input("price: ")))
        elif choice == "2":
            for name, entry in inv.items.items():
                print(name, entry["qty"], entry["price"])
        elif choice == "3":
            print("Total:", inv.total_value())
            print("Low stock:", inv.low_stock())
        else:
            break
