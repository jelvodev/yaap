"""The plain data of the stockroom: items, stock movements, reservations and orders.

Amounts of money are whole cents (int) everywhere, so sums never drift. Datetimes are timezone-aware (UTC).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MovementKind(str, Enum):
    RECEIVED = "received"
    SHIPPED = "shipped"
    ADJUSTED = "adjusted"
    MOVED = "moved"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PICKED = "picked"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


@dataclass
class Item:
    """One stock keeping unit (SKU) at one location."""

    sku: str
    name: str
    category: str
    location: str
    quantity: int
    unit_cost_cents: int
    reorder_level: int = 0
    reorder_quantity: int = 0
    # Units promised to open reservations; they are still on the shelf but not for sale.
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.quantity - self.reserved

    @property
    def value_cents(self) -> int:
        return self.quantity * self.unit_cost_cents


@dataclass(frozen=True)
class Movement:
    """A change of an item's quantity, kept for the audit trail."""

    sku: str
    kind: MovementKind
    quantity: int
    at: datetime
    note: str = ""


@dataclass
class Reservation:
    """Units held for a customer until they are picked or the reservation expires."""

    id: str
    sku: str
    quantity: int
    expires_at: datetime
    customer: str = ""
    released: bool = False

    def is_expired(self, now: datetime) -> bool:
        return not self.released and now >= self.expires_at


@dataclass
class OrderLine:
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Order:
    id: str
    customer: str
    lines: list[OrderLine] = field(default_factory=list)
    status: OrderStatus = OrderStatus.DRAFT
    discount_code: str = ""
    created_at: datetime = field(default_factory=utc_now)
    reservation_ids: list[str] = field(default_factory=list)

    @property
    def subtotal_cents(self) -> int:
        return sum(line.total_cents for line in self.lines)

    @property
    def unit_count(self) -> int:
        return sum(line.quantity for line in self.lines)
