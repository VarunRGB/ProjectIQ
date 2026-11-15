
def format_report(rows):
    out_lines = ["Name | Qty | Value"]
    for name, qty, price in rows:
        out_lines.append(f"{name} | {qty} | {qty * price:.2f}")
    return "\n".join(out_lines)
