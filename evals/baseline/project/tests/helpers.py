"""Shared test helpers: a clock the tests move by hand, and a small inventory."""

from datetime import datetime, timedelta, timezone

from stockroom.inventory import Inventory
from stockroom.models import Item

START = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def sample_items() -> list[Item]:
    return [
        Item("SR-1", "Claw hammer", "tools", "A1", 10, 1250, reorder_level=3, reorder_quantity=10),
        Item("SR-2", "Wood screws (200)", "fasteners", "B1", 40, 499, reorder_level=10),
        Item("SR-3", "Coffee beans 1 kg", "food", "C1", 5, 1100, reorder_level=6, reorder_quantity=12),
        Item("SR-4", "DIY handbook", "books", "A1", 2, 1995),
    ]


def make_inventory(clock: Clock | None = None) -> Inventory:
    return Inventory(sample_items(), clock=clock or Clock())
