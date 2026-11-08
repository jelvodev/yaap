"""Reports on the inventory: a stock report as text, a valuation per category, and a CSV export for the bookkeeper.

The text reports are fixed-width so they read well in a terminal and in an email.
"""

from stockroom.inventory import Inventory
from stockroom.models import Item
from stockroom.pricing import format_cents

CSV_COLUMNS = ("sku", "name", "category", "location", "quantity", "reserved", "unit_cost", "value")
NAME_WIDTH = 34


def csv_export(inventory: Inventory) -> str:
    """Every item as one CSV line, with a header, amounts in euros with a dot."""
    lines = [",".join(CSV_COLUMNS)]
    for item in inventory.items():
        fields = [
            item.sku,
            item.name,
            item.category,
            item.location,
            str(item.quantity),
            str(item.reserved),
            f"{item.unit_cost_cents / 100:.2f}",
            f"{item.value_cents / 100:.2f}",
        ]
        lines.append(",".join(fields))
    return "\n".join(lines) + "\n"


def short_name(name: str, width: int = NAME_WIDTH) -> str:
    return name if len(name) <= width else name[: width - 1] + "…"


def item_row(item: Item) -> str:
    flag = "reorder" if item.reorder_level and item.available <= item.reorder_level else ""
    return (
        f"{item.sku:<9} {short_name(item.name):<{NAME_WIDTH}} {item.location:<6} "
        f"{item.quantity:>6} {item.reserved:>5} {item.available:>6} {format_cents(item.value_cents):>14}  {flag}"
    ).rstrip()


def stock_report(inventory: Inventory, title: str = "Stock report") -> str:
    """Every item per category with quantities and value, a subtotal per category and the totals."""
    header = (
        f"{'SKU':<9} {'Name':<{NAME_WIDTH}} {'Loc':<6} {'Qty':>6} {'Res':>5} {'Avail':>6} {'Value':>14}"
    )
    rule = "-" * len(header)
    lines = [title, "=" * len(title), ""]
    for category, items in inventory.by_category().items():
        lines += [f"{category.title()} ({len(items)} items)", header, rule]
        lines += [item_row(item) for item in items]
        subtotal = sum(item.value_cents for item in items)
        lines += [rule, f"{'Subtotal ' + category:<{len(header) - 15}} {format_cents(subtotal):>14}", ""]
    summary = inventory.summary()
    lines += [
        "Totals",
        "------",
        f"Items:            {summary['items']:>8}",
        f"Units on shelf:   {summary['units']:>8}",
        f"Units reserved:   {summary['reserved']:>8}",
        f"Items to reorder: {summary['to_reorder']:>8}",
        f"Stock value:      {format_cents(summary['value_cents']):>14}",
    ]
    return "\n".join(lines) + "\n"


def valuation_report(inventory: Inventory) -> str:
    """The stock value per category with its share of the total."""
    values = inventory.value_by_category()
    total = sum(values.values())
    lines = ["Valuation", "=========", ""]
    for category, value in sorted(values.items(), key=lambda pair: (-pair[1], pair[0])):
        share = value * 100 / total if total else 0.0
        lines.append(f"{category:<20} {format_cents(value):>14} {share:>6.1f}%")
    lines += ["", f"{'Total':<20} {format_cents(total):>14}"]
    return "\n".join(lines) + "\n"


def restock_report(inventory: Inventory) -> str:
    """What to order, with the cost at the current unit cost."""
    plan = inventory.restock_plan()
    if not plan:
        return "Nothing to reorder.\n"
    lines = ["Restock plan", "============", ""]
    total = 0
    for item, quantity in plan:
        cost = quantity * item.unit_cost_cents
        total += cost
        lines.append(f"{item.sku:<9} {short_name(item.name):<{NAME_WIDTH}} order {quantity:>5}  {format_cents(cost):>14}")
    lines += ["", f"Total cost: {format_cents(total)}"]
    return "\n".join(lines) + "\n"


def format_legacy_row(item: Item) -> str:
    """The semicolon format of the old spreadsheet, for a one-off migration."""
    return ";".join([item.sku, item.name, str(item.quantity), str(item.unit_cost_cents)])
