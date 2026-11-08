# Stockroom

A small inventory and order system for one shop: items per SKU, stock movements with an audit trail, reservations
that expire, orders from draft to shipped, reports for the bookkeeper, and the cart of the web shop.

## Layout

| Path | What it holds |
|---|---|
| `stockroom/models.py` | The plain data: items, movements, reservations, orders |
| `stockroom/inventory.py` | The inventory: movements, reservations, stock counts, kits and questions about the stock |
| `stockroom/orders.py` | Orders and discount codes |
| `stockroom/pricing.py` | VAT, sale prices and money formatting |
| `stockroom/storage.py` | Saving and loading the inventory as one JSON file |
| `stockroom/report.py` | The stock report, the valuation, the restock plan and the CSV export |
| `stockroom/cli.py` | The command line |
| `stockroom/data/sample.json` | Sample data: 72 items in seven categories |
| `web/src/` | The web shop's cart and formatting, in TypeScript |
| `tests/`, `web/test/` | The tests |

## Conventions

- Money is whole cents (`int` in Python, `number` holding an integer in TypeScript); only formatting turns it into
  euros.
- Datetimes are timezone-aware and in UTC.
- Every change of a quantity goes through `Inventory`, which records a movement, so the audit trail explains the
  stock.
- The cart in `web/src/cart.ts` follows the backend's rules for discounts and VAT; change both together.

## Running

Python 3.10 or later, standard library only:

```sh
python3 -m unittest discover -s tests -t .
python3 -m stockroom.cli report
python3 -m stockroom.cli --data stockroom/data/sample.json export --output stock.csv
```

The web part needs Node.js 22.18 or later, which runs TypeScript directly:

```sh
cd web && npm test
```

## Discount codes

| Code | Off | Minimum subtotal |
|---|---|---|
| WELCOME10 | 10% | none |
| BULK15 | 15% | EUR 500.00 |
| STAFF25 | 25% | none |
