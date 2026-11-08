"""Orders: from a draft to shipped, with reservations so confirmed orders keep their stock.

A draft takes lines freely. Confirming reserves every line's units (all or nothing); picking checks the reservations
are still there; shipping fulfils them. Cancelling releases what was reserved. Discount codes apply to the subtotal.
"""

from dataclasses import dataclass
from datetime import datetime

from stockroom.inventory import Inventory, InventoryError
from stockroom.models import Order, OrderLine, OrderStatus
from stockroom.pricing import breakdown

# code -> (percentage off, minimum subtotal in cents)
DISCOUNT_RULES: dict[str, tuple[int, int]] = {
    "WELCOME10": (10, 0),
    "BULK15": (15, 50_000),
    "STAFF25": (25, 0),
}
MAX_LINES = 50


class OrderError(Exception):
    """An order change that is not allowed in the order's current status."""


@dataclass(frozen=True)
class OrderTotals:
    subtotal_cents: int
    discount_cents: int
    vat_cents: int

    @property
    def net_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents

    @property
    def total_cents(self) -> int:
        return self.net_cents + self.vat_cents


def apply_discount(subtotal_cents: int, code: str) -> int:
    """The discount in cents for a code on a subtotal; 0 for an empty code. An unknown code or a subtotal below
    the code's minimum is an error, so the customer hears why."""
    if not code:
        return 0
    rule = DISCOUNT_RULES.get(code.strip().upper())
    if rule is None:
        raise OrderError(f"Unknown discount code {code!r}.")
    percent, minimum = rule
    if subtotal_cents < minimum:
        raise OrderError(f"{code.upper()} needs a subtotal of at least {minimum / 100:.2f}.")
    return subtotal_cents * percent // 100


class OrderBook:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self._orders: dict[str, Order] = {}
        self._counter = 0

    def create(self, customer: str, created_at: datetime | None = None) -> Order:
        if not customer.strip():
            raise OrderError("An order needs a customer.")
        self._counter += 1
        order = Order(id=f"O-{self._counter:05d}", customer=customer.strip())
        if created_at is not None:
            order.created_at = created_at
        self._orders[order.id] = order
        return order

    def get(self, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise OrderError(f"There is no order {order_id}.")
        return order

    def orders(self, status: OrderStatus | None = None) -> list[Order]:
        return [order for order in self._orders.values() if status is None or order.status is status]

    def add_line(self, order_id: str, sku: str, quantity: int, unit_price_cents: int) -> OrderLine:
        order = self._draft(order_id)
        if sku not in self.inventory:
            raise OrderError(f"There is no item with SKU {sku}.")
        if quantity <= 0 or unit_price_cents < 0:
            raise OrderError("A line needs a quantity above zero and a price of zero or more.")
        for line in order.lines:
            if line.sku == sku and line.unit_price_cents == unit_price_cents:
                line.quantity += quantity
                return line
        if len(order.lines) >= MAX_LINES:
            raise OrderError(f"An order has at most {MAX_LINES} lines.")
        line = OrderLine(sku, quantity, unit_price_cents)
        order.lines.append(line)
        return line

    def remove_line(self, order_id: str, sku: str) -> None:
        order = self._draft(order_id)
        order.lines = [line for line in order.lines if line.sku != sku]

    def set_discount(self, order_id: str, code: str) -> None:
        order = self._draft(order_id)
        apply_discount(order.subtotal_cents, code)
        order.discount_code = code.strip().upper()

    def totals(self, order_id: str) -> OrderTotals:
        """Subtotal, discount and VAT; VAT is computed per line on the discounted line amount."""
        order = self.get(order_id)
        subtotal = order.subtotal_cents
        discount = apply_discount(subtotal, order.discount_code)
        vat = 0
        for line in order.lines:
            share = line.total_cents - (line.total_cents * discount // subtotal if subtotal else 0)
            item = self.inventory.get(line.sku)
            category = item.category if item is not None else ""
            vat += breakdown(share, category).vat_cents
        return OrderTotals(subtotal, discount, vat)

    def confirm(self, order_id: str) -> Order:
        """Reserve every line; when one cannot be reserved, nothing stays reserved."""
        order = self._draft(order_id)
        if not order.lines:
            raise OrderError("An order without lines cannot be confirmed.")
        made = []
        try:
            for line in order.lines:
                made.append(self.inventory.reserve(line.sku, line.quantity, order.customer))
        except InventoryError as error:
            for reservation in made:
                self.inventory.release(reservation.id)
            raise OrderError(f"The order cannot be confirmed: {error}") from error
        order.reservation_ids = [reservation.id for reservation in made]
        order.status = OrderStatus.CONFIRMED
        return order

    def pick(self, order_id: str) -> Order:
        order = self._in_status(order_id, OrderStatus.CONFIRMED)
        self.inventory.expire_reservations()
        lost = [rid for rid in order.reservation_ids if self.inventory.reservation(rid).released]
        if lost:
            raise OrderError(f"Reservations expired before picking: {', '.join(lost)}. Confirm the order again.")
        order.status = OrderStatus.PICKED
        return order

    def ship(self, order_id: str) -> Order:
        order = self._in_status(order_id, OrderStatus.PICKED)
        for reservation_id in order.reservation_ids:
            self.inventory.fulfil(reservation_id, f"order {order.id}")
        order.status = OrderStatus.SHIPPED
        return order

    def cancel(self, order_id: str) -> Order:
        order = self.get(order_id)
        if order.status is OrderStatus.SHIPPED:
            raise OrderError("A shipped order cannot be cancelled.")
        for reservation_id in order.reservation_ids:
            self.inventory.release(reservation_id)
        order.reservation_ids = []
        order.status = OrderStatus.CANCELLED
        return order

    def reopen(self, order_id: str) -> Order:
        """Back to draft after the reservations expired, so the order can be confirmed again."""
        order = self._in_status(order_id, OrderStatus.CONFIRMED)
        for reservation_id in order.reservation_ids:
            self.inventory.release(reservation_id)
        order.reservation_ids = []
        order.status = OrderStatus.DRAFT
        return order

    def _draft(self, order_id: str) -> Order:
        return self._in_status(order_id, OrderStatus.DRAFT)

    def _in_status(self, order_id: str, status: OrderStatus) -> Order:
        order = self.get(order_id)
        if order.status is not status:
            raise OrderError(f"Order {order_id} is {order.status.value}, not {status.value}.")
        return order
