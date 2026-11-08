import unittest

from stockroom.inventory import Inventory, InventoryError
from stockroom.models import Item, MovementKind
from tests.helpers import Clock, make_inventory


class ItemsAndMovements(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.inventory = make_inventory(self.clock)

    def test_items_are_listed_by_sku_and_duplicates_are_refused(self) -> None:
        self.assertEqual([item.sku for item in self.inventory.items()], ["SR-1", "SR-2", "SR-3", "SR-4"])
        self.assertEqual(len(self.inventory), 4)
        self.assertIn("SR-2", self.inventory)
        with self.assertRaises(InventoryError):
            self.inventory.add_item(Item("SR-1", "Again", "tools", "A1", 1, 100))
        with self.assertRaises(InventoryError):
            self.inventory.add_item(Item("SR-9", "Negative", "tools", "A1", -1, 100))

    def test_receiving_and_shipping_change_the_quantity_and_are_recorded(self) -> None:
        self.inventory.receive("SR-1", 5, "supplier delivery")
        self.inventory.ship("SR-1", 3)
        self.assertEqual(self.inventory.get("SR-1").quantity, 12)
        kinds = [movement.kind for movement in self.inventory.movements("SR-1")]
        self.assertEqual(kinds, [MovementKind.RECEIVED, MovementKind.SHIPPED])
        with self.assertRaises(InventoryError):
            self.inventory.ship("SR-1", 100)
        with self.assertRaises(InventoryError):
            self.inventory.receive("SR-1", 0)

    def test_an_adjustment_needs_a_reason_and_records_the_difference(self) -> None:
        with self.assertRaises(InventoryError):
            self.inventory.adjust("SR-2", 38, " ")
        self.inventory.adjust("SR-2", 38, "two boxes damaged")
        self.assertEqual(self.inventory.movements("SR-2")[-1].quantity, -2)

    def test_moving_changes_the_location_only(self) -> None:
        self.inventory.move("SR-1", " b2 ")
        item = self.inventory.get("SR-1")
        self.assertEqual((item.location, item.quantity), ("B2", 10))
        self.assertEqual(self.inventory.movements("SR-1")[-1].note, "A1 -> B2")

    def test_an_item_with_reservations_cannot_be_removed(self) -> None:
        self.inventory.reserve("SR-1", 2)
        with self.assertRaises(InventoryError):
            self.inventory.remove_item("SR-1")
        self.assertEqual(self.inventory.remove_item("SR-4").sku, "SR-4")


class Reservations(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.inventory = make_inventory(self.clock)

    def test_a_reservation_holds_units_until_it_is_released(self) -> None:
        reservation = self.inventory.reserve("SR-1", 4, "Jansen")
        self.assertEqual(reservation.id, "R-0001")
        self.assertEqual(self.inventory.get("SR-1").available, 6)
        self.assertEqual(self.inventory.reservation("R-0001").customer, "Jansen")
        self.inventory.release(reservation.id)
        self.assertEqual(self.inventory.get("SR-1").available, 10)
        self.assertEqual(self.inventory.reservations(), [])
        self.assertEqual(len(self.inventory.reservations(include_released=True)), 1)

    def test_expired_reservations_give_their_units_back(self) -> None:
        self.inventory.reserve("SR-1", 4, hours=2)
        self.clock.advance(hours=3)
        expired = self.inventory.expire_reservations()
        self.assertEqual([reservation.id for reservation in expired], ["R-0001"])
        self.assertEqual(self.inventory.get("SR-1").available, 10)

    def test_fulfilling_ships_the_reserved_units(self) -> None:
        reservation = self.inventory.reserve("SR-3", 2)
        self.inventory.fulfil(reservation.id)
        item = self.inventory.get("SR-3")
        self.assertEqual((item.quantity, item.reserved), (3, 0))
        with self.assertRaises(InventoryError):
            self.inventory.fulfil(reservation.id)

    def test_an_expired_reservation_cannot_be_fulfilled_but_can_be_extended_before(self) -> None:
        reservation = self.inventory.reserve("SR-2", 5, hours=1)
        self.inventory.extend(reservation.id, 5)
        self.clock.advance(hours=4)
        self.assertEqual(self.inventory.fulfil(reservation.id).quantity, 35)
        late = self.inventory.reserve("SR-2", 5, hours=1)
        self.clock.advance(hours=2)
        with self.assertRaises(InventoryError):
            self.inventory.fulfil(late.id)

    def test_reservations_cannot_exceed_what_is_available(self) -> None:
        self.inventory.reserve("SR-4", 2)
        with self.assertRaises(InventoryError):
            self.inventory.reserve("SR-4", 1)
        with self.assertRaises(InventoryError):
            self.inventory.reserve("SR-1", 1, hours=1000)


class Questions(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.inventory = make_inventory(self.clock)

    def test_search_matches_sku_name_and_category(self) -> None:
        self.assertEqual([item.sku for item in self.inventory.search("hammer")], ["SR-1"])
        self.assertEqual([item.sku for item in self.inventory.search("FOOD")], ["SR-3"])
        self.assertEqual(self.inventory.search("  "), [])

    def test_grouping_and_values(self) -> None:
        self.assertEqual(list(self.inventory.by_category()), ["books", "fasteners", "food", "tools"])
        self.assertEqual([item.sku for item in self.inventory.by_location()["A1"]], ["SR-1", "SR-4"])
        self.assertEqual(self.inventory.total_value_cents(), 12500 + 19960 + 5500 + 3990)
        self.assertEqual(self.inventory.value_by_category()["tools"], 12500)

    def test_reorder_uses_available_units_and_the_reorder_quantity(self) -> None:
        self.inventory.reserve("SR-1", 7)
        self.assertEqual([item.sku for item in self.inventory.needs_reorder()], ["SR-1", "SR-3"])
        plan = {item.sku: quantity for item, quantity in self.inventory.restock_plan()}
        self.assertEqual(plan, {"SR-1": 10, "SR-3": 12})

    def test_slow_movers_and_turnover_look_at_recent_shipments(self) -> None:
        self.inventory.ship("SR-2", 10)
        self.clock.advance(days=40)
        self.inventory.ship("SR-1", 5)
        self.assertEqual(self.inventory.shipped_since(self.clock.now), 5)
        self.assertEqual([item.sku for item in self.inventory.slow_movers(30)], ["SR-2", "SR-3", "SR-4"])
        self.assertEqual(self.inventory.turnover("SR-1", 30), 1.0)
        self.assertEqual(self.inventory.turnover("SR-2", 30), 0.0)

    def test_the_summary_counts_everything(self) -> None:
        self.inventory.reserve("SR-2", 3)
        summary = self.inventory.summary()
        self.assertEqual(summary["items"], 4)
        self.assertEqual(summary["units"], 57)
        self.assertEqual(summary["reserved"], 3)
        self.assertEqual(summary["to_reorder"], 1)


class CountsAndKits(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = make_inventory()

    def test_a_count_shows_variances_and_applies_them(self) -> None:
        count = self.inventory.start_count("a1")
        self.assertEqual(count.uncounted(), ["SR-1", "SR-4"])
        count.record("SR-1", 9)
        count.record("SR-4", 2)
        self.assertEqual(count.variances(), [("SR-1", 10, 9)])
        self.assertEqual(self.inventory.apply_count(count), [("SR-1", -1)])
        with self.assertRaises(InventoryError):
            self.inventory.apply_count(count)
        with self.assertRaises(InventoryError):
            self.inventory.start_count("Z9")

    def test_kits_are_reserved_all_or_nothing(self) -> None:
        kit = {"SR-1": 1, "SR-2": 4}
        self.assertEqual(self.inventory.kit_available(kit), 10)
        reservations = self.inventory.reserve_kit(kit, 3, "Workshop")
        self.assertEqual([reservation.quantity for reservation in reservations], [3, 12])
        with self.assertRaises(InventoryError):
            self.inventory.reserve_kit({"SR-1": 1, "SR-4": 1}, 3)
        self.assertEqual(self.inventory.get("SR-1").reserved, 3)


class State(unittest.TestCase):
    def test_state_can_be_restored_into_a_new_inventory(self) -> None:
        inventory = make_inventory()
        inventory.receive("SR-1", 1)
        inventory.reserve("SR-2", 2)
        movements, reservations = inventory.state()
        restored = Inventory([Item("SR-1", "x", "tools", "A1", 11, 1), Item("SR-2", "y", "tools", "A1", 40, 1)])
        restored.load_state(movements, reservations)
        self.assertEqual(len(restored.movements()), 1)
        self.assertEqual(restored.reserve("SR-2", 1).id, "R-0002")


if __name__ == "__main__":
    unittest.main()
