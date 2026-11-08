"""The command line: `python3 -m stockroom.cli <command> --data <file>`.

Commands: report, valuation, restock, export, search, receive and ship. Commands that change the stock save the
data file afterwards.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from stockroom import report, storage
from stockroom.inventory import Inventory, InventoryError

DEFAULT_DATA = Path("stockroom/data/sample.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stockroom", description="Keep track of the stockroom.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="the data file (default: %(default)s)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("report", help="the stock report per category")
    commands.add_parser("valuation", help="the stock value per category")
    commands.add_parser("restock", help="what to reorder")
    export = commands.add_parser("export", help="all items as CSV")
    export.add_argument("--output", type=Path, help="write to this file instead of the screen")
    search = commands.add_parser("search", help="find items by SKU, name or category")
    search.add_argument("text")
    for name in ("receive", "ship"):
        change = commands.add_parser(name, help=f"{name} units of an item")
        change.add_argument("sku")
        change.add_argument("quantity", type=int)
        change.add_argument("--note", default="")
    return parser


def run(arguments: argparse.Namespace, inventory: Inventory) -> tuple[str, bool]:
    """The command's output and whether it changed the inventory."""
    if arguments.command == "report":
        return report.stock_report(inventory), False
    if arguments.command == "valuation":
        return report.valuation_report(inventory), False
    if arguments.command == "restock":
        return report.restock_report(inventory), False
    if arguments.command == "export":
        text = report.csv_export(inventory)
        if arguments.output:
            arguments.output.write_text(text, encoding="utf-8")
            return f"Wrote {len(inventory)} items to {arguments.output}.\n", False
        return text, False
    if arguments.command == "search":
        found = inventory.search(arguments.text)
        if not found:
            return "No items found.\n", False
        return "\n".join(report.item_row(item) for item in found) + "\n", False
    if arguments.command == "receive":
        item = inventory.receive(arguments.sku, arguments.quantity, arguments.note)
        return f"{item.sku}: {item.quantity} on the shelf.\n", True
    item = inventory.ship(arguments.sku, arguments.quantity, arguments.note)
    return f"{item.sku}: {item.quantity} on the shelf.\n", True


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        inventory = storage.load(arguments.data)
        inventory.expire_reservations()
        output, changed = run(arguments, inventory)
        if changed:
            storage.save(inventory, arguments.data)
    except (InventoryError, storage.StorageError) as error:
        print(f"stockroom: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
