---
name: python-testing
description: Write pytest tests named after behaviour, with fixtures for setup, parametrized tables for decision logic
  and mocks only at the edge (network, clock, randomness). Use it whenever you write or fix Python tests, add a
  fixture, are about to mock, test async code, or face a slow or flaky suite.
license: MIT
metadata:
  sources: wshobson/plugins/python-development/skills/python-testing-patterns
---

# Python testing

A good test fails for one reason, says in its name what broke, and survives a refactor that keeps the behaviour.
Write tests against what the code does, not how it does it.

## 1. Layout and names

```
tests/
  conftest.py          shared fixtures
  orders/
    test_pricing.py    mirrors src/shop/orders/pricing.py
  integration/
    test_order_api.py  marked as integration
```

Name each test after the behaviour, as a sentence: `test_discount_needs_a_minimum_total`, not `test_discount_2`.
Inside, keep the three parts visible: arrange, act, assert, with a blank line between them.

## 2. Tables for decisions

Logic with cases (prices, permissions, parsing, state transitions) gets one test with a table. The table shows at a
glance which case is missing.

```python
import pytest

from shop.orders.pricing import price_with_discount


@pytest.mark.parametrize(
    ("total", "code", "expected"),
    [
        (100, None, 100),
        (100, "TEN", 90),
        (40, "TEN", 40),
        (100, "UNKNOWN", 100),
    ],
    ids=["no-code", "ten-percent", "below-minimum", "unknown-code"],
)
def test_discount_applies_only_with_a_valid_code_above_the_minimum(
    total: int, code: str | None, expected: int
) -> None:
    assert price_with_discount(total, code) == expected
```

Give the rows `ids`, so a failure names the case instead of a number.

## 3. Fixtures for setup

A fixture builds what a test needs and cleans up after it. Put a fixture in the test file when one file uses it, in
`conftest.py` when several do.

```python
from collections.abc import Iterator
from pathlib import Path

import pytest

from shop.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store(tmp_path / "shop.db")
    yield store
    store.close()
```

- Use the built-in fixtures: `tmp_path` for files, `monkeypatch` for environment variables and attributes, `caplog`
  for logs, `capsys` for output.
- Keep the default function scope. A wider scope (`module`, `session`) is for expensive, read-only resources only;
  shared mutable state makes tests depend on their order.
- A factory fixture (one that returns a function) is clearer than a fixture with many parameters.

## 4. Mock only at the edge

Replace what is outside your control or slow: the network, a payment provider, the clock, randomness, email. Do not
mock your own classes and functions; a test full of mocks of your own code tests the mocks and breaks on every
refactor.

Prefer a small fake with real behaviour to a mock with configured return values:

```python
class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, subject: str) -> None:
        self.sent.append((to, subject))


def test_paid_order_sends_a_receipt(store: Store) -> None:
    store.add_order(Order(id=1, email="customer@example.com", total=100))
    mailer = FakeMailer()
    service = OrderService(store, mailer)

    service.pay(order_id=1)

    assert mailer.sent == [("customer@example.com", "Your receipt")]
```

This works because `OrderService` takes the mailer as a parameter. Code that is hard to test without patching usually
wants its dependency passed in. When you must patch, patch where the name is used (`shop.orders.service.send_mail`),
not where it is defined.

## 5. Time and randomness

Never sleep in a test and never depend on the real clock. Pass a clock or a `now` value into the code, or use the
project's time-freezing helper. Seed or inject randomness. A test that passes at 10:00 and fails at 23:59 is a bug.

## 6. Async code

Use the project's async plugin (pytest-asyncio or anyio) and mark async tests as it requires. Await everything; a
forgotten `await` makes a test pass without running the code. Test a timeout with a short, explicit timeout, not by
waiting.

## 7. Errors

Test the failure paths as carefully as the happy path:

```python
def test_unknown_order_is_refused(store: Store) -> None:
    with pytest.raises(OrderNotFound, match="order 999"):
        OrderService(store, FakeMailer()).pay(order_id=999)
```

## 8. Speed and markers

Unit tests run in milliseconds. Mark slower tests (`@pytest.mark.integration`) and register the markers in the
pytest configuration, so the fast set can run on every change. Coverage shows untested code; it does not show that the
tests check anything, so do not chase a number.

## Run and report

Run the tests with `uv run pytest` (or the project's test command), and for one file or test with its path or `-k`.
Read the output before saying they pass.

```
Tests: <added or changed, by name>
Command: <what you ran>
Result: <passed / failed, with the failure>
Not covered: <cases left out and why>
```
