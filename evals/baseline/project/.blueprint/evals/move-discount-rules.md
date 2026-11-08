---
summary: "Baseline: a refactor that moves code between modules and keeps behaviour"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 40 steps
  tokens: 1M tokens
  time: 15 min
checks:
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: file
    name: the rules live in pricing
    path: stockroom/pricing.py
    matches: ['^DISCOUNT_RULES\b', '^def apply_discount\(']
  - kind: file
    name: orders no longer defines them
    path: stockroom/orders.py
    not_matches: ['^DISCOUNT_RULES\b', '^def apply_discount\(']
  - kind: command
    name: orders still refuse a bad code
    run: >-
      python3 -c "import unittest;
      from stockroom.orders import OrderBook, OrderError;
      from tests.helpers import make_inventory;
      book = OrderBook(make_inventory()); order = book.create('Jansen');
      book.add_line(order.id, 'SR-1', 1, 2000); book.set_discount(order.id, 'welcome10');
      assert book.totals(order.id).discount_cents == 200;
      unittest.TestCase().assertRaises(OrderError, book.set_discount, order.id, 'FREE')"
    timeout: 1 min
---

The discount rules (`DISCOUNT_RULES` and `apply_discount`) live in `stockroom/orders.py`, but they are about prices.
Move them to `stockroom/pricing.py` (issue SR-14). Orders must behave as before: an order with a code that does not
apply still raises `OrderError`. Update the imports, and the tests where they import from the old place.
