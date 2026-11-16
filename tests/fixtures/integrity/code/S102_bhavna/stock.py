
from common import load_rows, main_menu


class StockBook:
    def __init__(self):
        self.stock = {}

    def add(self, name, qty, price):
        if qty <= 0:
            raise ValueError("quantity must be positive")
        rec = self.stock.setdefault(name, {"qty": 0, "price": price})
        rec["qty"] += qty
        rec["price"] = price

    def worth(self):
        total = 0.0
        for name, rec in self.stock.items():
            total += rec["qty"] * rec["price"]
        return round(total, 2)

    def running_low(self, limit=5):
        out = []
        for name, rec in sorted(self.stock.items()):
            if rec["qty"] < limit:
                out.append((name, rec["qty"]))
        return out

    def load(self, path):
        for row in load_rows(path):
            self.add(row["name"], int(row["qty"]), float(row["price"]))


def run():
    book = StockBook()
    while True:
        choice = main_menu()
        if choice == "1":
            book.add(input("name: "), int(input("qty: ")), float(input("price: ")))
        elif choice == "2":
            for name, rec in book.items.items():
                print(name, rec["qty"], rec["price"])
        elif choice == "3":
            print("Total:", book.worth())
            print("Low stock:", book.running_low())
        else:
            break
