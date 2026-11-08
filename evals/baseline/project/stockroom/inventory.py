"""The inventory: items per SKU, their stock movements and reservations.

Every change of a quantity goes through one of the public methods, which check it and record a Movement, so the
audit trail always explains the current stock. Reservations hold units for a customer for a limited time; expired
reservations are released by `expire_reservations`, which the CLI and the order flow call before they look at
availability.

Quantities, in short:

- `quantity` is what is on the shelf; only receiving, shipping, fulfilling and adjusting change it.
- `reserved` is the part of it promised to open reservations; reserving and releasing change only this.
- `available` is the difference, and is what can be sold, reserved or shipped without a reservation.

The clock is injected so tests and the order flow can move time; it must return timezone-aware datetimes.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from stockroom.models import Item, Movement, MovementKind, Reservation, utc_now

DEFAULT_RESERVATION_HOURS = 48
MAX_RESERVATION_HOURS = 24 * 14


class InventoryError(Exception):
    """A change the inventory refuses; the message is meant for the person who asked for it."""


class Inventory:
    def __init__(self, items: Iterable[Item] = (), clock: Callable[[], datetime] = utc_now) -> None:
        self._items: dict[str, Item] = {}
        self._movements: list[Movement] = []
        self._reservations: dict[str, Reservation] = {}
        self._reservation_counter = 0
        self._clock = clock
        for item in items:
            self.add_item(item)

    # --- Items ------------------------------------------------------------------------------------------------

    def add_item(self, item: Item) -> None:
        if item.sku in self._items:
            raise InventoryError(f"An item with SKU {item.sku} exists already.")
        if item.quantity < 0:
            raise InventoryError(f"{item.sku}: the quantity cannot be negative.")
        if item.unit_cost_cents < 0:
            raise InventoryError(f"{item.sku}: the unit cost cannot be negative.")
        self._items[item.sku] = item

    def remove_item(self, sku: str) -> Item:
        item = self._require(sku)
        if item.reserved:
            raise InventoryError(f"{sku} has {item.reserved} reserved units; release them first.")
        del self._items[sku]
        return item

    def get(self, sku: str) -> Item | None:
        return self._items.get(sku)

    def items(self) -> list[Item]:
        """Every item, by SKU."""
        return [self._items[sku] for sku in sorted(self._items)]

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, sku: object) -> bool:
        return sku in self._items

    # --- Movements --------------------------------------------------------------------------------------------

    def receive(self, sku: str, quantity: int, note: str = "") -> Item:
        """Units that arrived from a supplier."""
        item = self._require(sku)
        self._check_positive(quantity)
        item.quantity += quantity
        self._record(sku, MovementKind.RECEIVED, quantity, note)
        return item

    def ship(self, sku: str, quantity: int, note: str = "") -> Item:
        """Units that left the stockroom without a reservation."""
        item = self._require(sku)
        self._check_positive(quantity)
        if quantity > item.available:
            raise InventoryError(f"{sku}: only {item.available} available, cannot ship {quantity}.")
        item.quantity -= quantity
        self._record(sku, MovementKind.SHIPPED, -quantity, note)
        return item

    def adjust(self, sku: str, counted: int, reason: str) -> Item:
        """Set the quantity to what a stock count found. The difference is recorded with the reason."""
        item = self._require(sku)
        if counted < 0:
            raise InventoryError(f"{sku}: a count cannot be negative.")
        if counted < item.reserved:
            raise InventoryError(f"{sku}: the count {counted} is below the {item.reserved} reserved units.")
        if not reason.strip():
            raise InventoryError("An adjustment needs a reason.")
        difference = counted - item.quantity
        item.quantity = counted
        if difference:
            self._record(sku, MovementKind.ADJUSTED, difference, reason.strip())
        return item

    def move(self, sku: str, location: str) -> Item:
        """Put the item at another location; the quantity does not change."""
        item = self._require(sku)
        location = location.strip().upper()
        if not location:
            raise InventoryError("A location cannot be empty.")
        if location == item.location:
            return item
        note = f"{item.location} -> {location}"
        item.location = location
        self._record(sku, MovementKind.MOVED, 0, note)
        return item

    def movements(self, sku: str | None = None) -> list[Movement]:
        """The audit trail, oldest first, for one SKU or for all."""
        return [movement for movement in self._movements if sku is None or movement.sku == sku]

    def legacy_bulk_adjust(self, counts: dict[str, int], reason: str = "bulk count") -> int:
        """Apply a whole stock count at once and return how many items changed. Kept from the spreadsheet import."""
        changed = 0
        for sku, counted in sorted(counts.items()):
            before = self._require(sku).quantity
            self.adjust(sku, counted, reason)
            changed += before != counted
        return changed

    # --- Reservations -----------------------------------------------------------------------------------------

    def reserve(
        self, sku: str, quantity: int, customer: str = "", hours: int = DEFAULT_RESERVATION_HOURS
    ) -> Reservation:
        """Hold units for a customer. The units stay on the shelf but are no longer available."""
        item = self._require(sku)
        self._check_positive(quantity)
        if not 1 <= hours <= MAX_RESERVATION_HOURS:
            raise InventoryError(f"A reservation lasts between 1 and {MAX_RESERVATION_HOURS} hours.")
        self.expire_reservations()
        if quantity > item.available:
            raise InventoryError(f"{sku}: only {item.available} available, cannot reserve {quantity}.")
        self._reservation_counter += 1
        reservation = Reservation(
            id=f"R-{self._reservation_counter:04d}",
            sku=sku,
            quantity=quantity,
            expires_at=self._clock() + timedelta(hours=hours),
            customer=customer,
        )
        item.reserved += quantity
        self._reservations[reservation.id] = reservation
        return reservation

    def reservation(self, reservation_id: str) -> Reservation:
        found = self._reservations.get(reservation_id)
        if found is None:
            raise InventoryError(f"There is no reservation {reservation_id}.")
        return found

    def reservations(self, sku: str | None = None, include_released: bool = False) -> list[Reservation]:
        return [
            reservation
            for reservation in self._reservations.values()
            if (sku is None or reservation.sku == sku) and (include_released or not reservation.released)
        ]

    def release(self, reservation_id: str) -> Reservation:
        """Give the units of a reservation back; they become available again."""
        reservation = self.reservation(reservation_id)
        if reservation.released:
            return reservation
        item = self._require(reservation.sku)
        item.reserved -= reservation.quantity
        reservation.released = True
        return reservation

    def fulfil(self, reservation_id: str, note: str = "") -> Item:
        """Ship the units of a reservation: they leave the shelf and the reservation ends."""
        reservation = self.reservation(reservation_id)
        if reservation.released:
            raise InventoryError(f"Reservation {reservation_id} was released and cannot be shipped.")
        if reservation.is_expired(self._clock()):
            raise InventoryError(f"Reservation {reservation_id} expired at {reservation.expires_at:%Y-%m-%d %H:%M}.")
        item = self._require(reservation.sku)
        item.reserved -= reservation.quantity
        item.quantity -= reservation.quantity
        reservation.released = True
        self._record(item.sku, MovementKind.SHIPPED, -reservation.quantity, note or f"reservation {reservation.id}")
        return item

    def extend(self, reservation_id: str, hours: int) -> Reservation:
        """Keep a reservation longer, up to the maximum counted from now."""
        reservation = self.reservation(reservation_id)
        if reservation.released:
            raise InventoryError(f"Reservation {reservation_id} was released.")
        latest = self._clock() + timedelta(hours=MAX_RESERVATION_HOURS)
        reservation.expires_at = min(reservation.expires_at + timedelta(hours=hours), latest)
        return reservation

    def expire_reservations(self) -> list[Reservation]:
        """Release every reservation whose time is up and return them. Their units become available again; the
        quantity on the shelf does not change, so no movement is recorded."""
        now = self._clock()
        expired = [reservation for reservation in self._reservations.values() if reservation.is_expired(now)]
        for reservation in expired:
            self.release(reservation.id)
        return expired

    # --- Questions --------------------------------------------------------------------------------------------

    def search(self, text: str) -> list[Item]:
        """Items whose SKU, name or category contains the text, ignoring case."""
        needle = text.strip().casefold()
        if not needle:
            return []
        return [
            item
            for item in self.items()
            if needle in item.sku.casefold() or needle in item.name.casefold() or needle in item.category.casefold()
        ]

    def by_category(self) -> dict[str, list[Item]]:
        grouped: dict[str, list[Item]] = defaultdict(list)
        for item in self.items():
            grouped[item.category].append(item)
        return dict(sorted(grouped.items()))

    def by_location(self) -> dict[str, list[Item]]:
        grouped: dict[str, list[Item]] = defaultdict(list)
        for item in self.items():
            grouped[item.location].append(item)
        return dict(sorted(grouped.items()))

    def total_value_cents(self) -> int:
        return sum(item.value_cents for item in self._items.values())

    def value_by_category(self) -> dict[str, int]:
        return {category: sum(item.value_cents for item in items) for category, items in self.by_category().items()}

    def needs_reorder(self) -> list[Item]:
        """Items whose available units are at or below their reorder level, by SKU. Items without a reorder level
        are never reordered."""
        return [item for item in self.items() if item.reorder_level > 0 and item.available <= item.reorder_level]

    def restock_plan(self) -> list[tuple[Item, int]]:
        """What to order per item that needs it: its reorder quantity, or enough to reach twice the reorder level
        when no reorder quantity is set."""
        plan = []
        for item in self.needs_reorder():
            quantity = item.reorder_quantity or max(item.reorder_level * 2 - item.available, 1)
            plan.append((item, quantity))
        return plan

    def movements_between(
        self, start: datetime, end: datetime | None = None, kind: MovementKind | None = None, sku: str | None = None
    ) -> list[Movement]:
        """Movements from `start` up to (not including) `end`, optionally of one kind and one SKU, oldest first."""
        return [
            movement
            for movement in self.movements(sku)
            if movement.at >= start
            and (end is None or movement.at < end)
            and (kind is None or movement.kind is kind)
        ]

    def shipped_since(self, since: datetime, sku: str | None = None) -> int:
        """Units shipped from `since` on, for one SKU or all."""
        return -sum(movement.quantity for movement in self.movements_between(since, kind=MovementKind.SHIPPED, sku=sku))

    def slow_movers(self, days: int) -> list[Item]:
        """Items with stock that shipped nothing in the last `days` days."""
        since = self._clock() - timedelta(days=days)
        return [item for item in self.items() if item.quantity > 0 and self.shipped_since(since, item.sku) == 0]

    def turnover(self, sku: str, days: int) -> float:
        """Units shipped in the last `days` days divided by the current quantity; 0.0 for an empty shelf."""
        item = self._require(sku)
        if item.quantity == 0:
            return 0.0
        since = self._clock() - timedelta(days=days)
        return round(self.shipped_since(since, sku) / item.quantity, 2)

    def summary(self) -> dict[str, int]:
        """Counts for a dashboard: items, units, reserved units, items to reorder and the total value in cents."""
        return {
            "items": len(self._items),
            "units": sum(item.quantity for item in self._items.values()),
            "reserved": sum(item.reserved for item in self._items.values()),
            "to_reorder": len(self.needs_reorder()),
            "value_cents": self.total_value_cents(),
        }

    # --- Stock counts -----------------------------------------------------------------------------------------

    def start_count(self, location: str) -> "StockCount":
        """A count of every item at one location. Nothing changes until the count is applied."""
        location = location.strip().upper()
        expected = {item.sku: item.quantity for item in self.items() if item.location == location}
        if not expected:
            raise InventoryError(f"There are no items at location {location}.")
        return StockCount(location, expected, started_at=self._clock())

    def apply_count(self, count: "StockCount", reason: str = "") -> list[tuple[str, int]]:
        """Adjust every counted item to what was counted. Returns (sku, difference) for each item that changed.
        Items of the location that were not counted keep their quantity."""
        if count.applied:
            raise InventoryError(f"The count of {count.location} was applied already.")
        missing = [sku for sku in count.counted if sku not in self._items]
        if missing:
            raise InventoryError(f"Unknown SKUs in the count: {', '.join(sorted(missing))}.")
        changes = []
        for sku, counted in sorted(count.counted.items()):
            before = self._items[sku].quantity
            self.adjust(sku, counted, reason or f"count of {count.location}")
            if counted != before:
                changes.append((sku, counted - before))
        count.applied = True
        return changes

    # --- Kits -------------------------------------------------------------------------------------------------

    def kit_available(self, kit: dict[str, int]) -> int:
        """How many complete kits can be put together from available units. A kit maps SKUs to units per kit."""
        if not kit:
            raise InventoryError("A kit needs at least one component.")
        counts = []
        for sku, per_kit in kit.items():
            self._check_positive(per_kit)
            counts.append(self._require(sku).available // per_kit)
        return min(counts)

    def reserve_kit(self, kit: dict[str, int], kits: int, customer: str = "") -> list[Reservation]:
        """Reserve the components of `kits` kits at once: all of them or none."""
        self._check_positive(kits)
        self.expire_reservations()
        if self.kit_available(kit) < kits:
            raise InventoryError(f"Only {self.kit_available(kit)} complete kits are available.")
        made: list[Reservation] = []
        try:
            for sku, per_kit in sorted(kit.items()):
                made.append(self.reserve(sku, per_kit * kits, customer))
        except InventoryError:
            for reservation in made:
                self.release(reservation.id)
            raise
        return made

    # --- Internals --------------------------------------------------------------------------------------------

    def _require(self, sku: str) -> Item:
        item = self._items.get(sku)
        if item is None:
            raise InventoryError(f"There is no item with SKU {sku}.")
        return item

    @staticmethod
    def _check_positive(quantity: int) -> None:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InventoryError("The quantity must be a whole number above zero.")

    def _record(self, sku: str, kind: MovementKind, quantity: int, note: str) -> None:
        self._movements.append(Movement(sku, kind, quantity, self._clock(), note))

    def load_state(self, movements: Iterable[Movement], reservations: Iterable[Reservation]) -> None:
        """Restore movements and reservations read from storage. The items must be added first."""
        self._movements = list(movements)
        self._reservations = {}
        highest = 0
        for reservation in reservations:
            self._require(reservation.sku)
            self._reservations[reservation.id] = reservation
            number = reservation.id.removeprefix("R-")
            if number.isdigit():
                highest = max(highest, int(number))
        self._reservation_counter = highest

    def state(self) -> tuple[list[Movement], list[Reservation]]:
        """Movements and every reservation, for storage."""
        return list(self._movements), list(self._reservations.values())


class StockCount:
    """The counted quantities of one location, compared with what the inventory expected when the count started."""

    def __init__(self, location: str, expected: dict[str, int], started_at: datetime) -> None:
        self.location = location
        self.expected = expected
        self.started_at = started_at
        self.counted: dict[str, int] = {}
        self.applied = False

    def record(self, sku: str, counted: int) -> None:
        if counted < 0:
            raise InventoryError(f"{sku}: a count cannot be negative.")
        self.counted[sku] = counted

    def uncounted(self) -> list[str]:
        """SKUs of the location that have no count yet."""
        return sorted(sku for sku in self.expected if sku not in self.counted)

    def variances(self) -> list[tuple[str, int, int]]:
        """(sku, expected, counted) for each counted SKU whose count differs, by SKU. A SKU that was not expected at
        this location counts as expected 0."""
        return [
            (sku, self.expected.get(sku, 0), counted)
            for sku, counted in sorted(self.counted.items())
            if counted != self.expected.get(sku, 0)
        ]
