# Freight

Shipping quotes for the parcels of one web shop: the zone of the destination, the chargeable weight, the base rate,
the surcharges, the discount for the account, VAT and the delivery time.

## Layout

| Path | What it holds |
|---|---|
| `freight/rates.py` | Everything that prices a parcel, in the order of the tariff rules |
| `docs/rules.md` | The tariff rules the code follows |
| `docs/carrier-notice.md` | The carrier's announcement of its next surcharges |
| `tests/` | The tests, one file per section of the rules |

## Conventions

- Money is whole euro cents (`int`); only `format_cents` turns it into euros.
- Every percentage is rounded per line, half a cent up (`percent_of`).
- A change of a rule changes `docs/rules.md`, the code and the tests together.

## Running

Python 3.10 or later and pytest:

```sh
python3 -m pytest
```
