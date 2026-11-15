
def format_report(rows):
    lines = ["Name | Qty | Value"]
    for name, qty, price in rows:
        lines.append(f"{name} | {qty} | {qty * price:.2f}")
    return "\n".join(lines)
