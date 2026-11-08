import unittest

from stockroom.models import OrderStatus
from stockroom.orders import OrderBook, OrderError, apply_discount
from tests.helpers import Clock, make_inventory


class Orders(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.inventory = make_inventory(self.clock)
        self.book = OrderBook(self.inventory)

    def test_lines_with_the_same_sku_and_price_are_merged(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-1", 1, 2000)
        self.book.add_line(order.id, "SR-1", 2, 2000)
        self.book.add_line(order.id, "SR-2", 3, 800)
        self.assertEqual([(line.sku, line.quantity) for line in order.lines], [("SR-1", 3), ("SR-2", 3)])
        self.book.remove_line(order.id, "SR-2")
        self.assertEqual(order.unit_count, 3)
        with self.assertRaises(OrderError):
            self.book.add_line(order.id, "NOPE", 1, 100)

    def test_discount_codes_have_a_minimum(self) -> None:
        self.assertEqual(apply_discount(10_000, "welcome10"), 1000)
        self.assertEqual(apply_discount(10_000, ""), 0)
        with self.assertRaises(OrderError):
            apply_discount(10_000, "BULK15")
        with self.assertRaises(OrderError):
            apply_discount(10_000, "FREE")

    def test_totals_apply_the_discount_before_vat_per_line(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-1", 2, 2000)
        self.book.add_line(order.id, "SR-3", 1, 1500)
        self.book.set_discount(order.id, "WELCOME10")
        totals = self.book.totals(order.id)
        self.assertEqual((totals.subtotal_cents, totals.discount_cents), (5500, 550))
        # 3600 at 21% and 1350 at 9% (food).
        self.assertEqual(totals.vat_cents, 756 + 122)
        self.assertEqual(totals.total_cents, 4950 + 878)

    def test_an_order_goes_from_draft_to_shipped(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-1", 4, 2000)
        self.book.confirm(order.id)
        self.assertEqual(self.inventory.get("SR-1").available, 6)
        self.book.pick(order.id)
        self.book.ship(order.id)
        self.assertEqual(order.status, OrderStatus.SHIPPED)
        self.assertEqual(self.inventory.get("SR-1").quantity, 6)
        self.assertEqual(self.book.orders(OrderStatus.SHIPPED), [order])
        with self.assertRaises(OrderError):
            self.book.cancel(order.id)

    def test_confirming_reserves_all_lines_or_none(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-1", 4, 2000)
        self.book.add_line(order.id, "SR-4", 5, 2500)
        with self.assertRaises(OrderError):
            self.book.confirm(order.id)
        self.assertEqual(self.inventory.get("SR-1").reserved, 0)
        self.assertEqual(order.status, OrderStatus.DRAFT)

    def test_expired_reservations_stop_picking_until_the_order_is_reopened(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-2", 5, 800)
        self.book.confirm(order.id)
        self.clock.advance(days=3)
        with self.assertRaises(OrderError):
            self.book.pick(order.id)
        self.book.reopen(order.id)
        self.book.confirm(order.id)
        self.assertEqual(self.book.pick(order.id).status, OrderStatus.PICKED)

    def test_cancelling_releases_the_reservations(self) -> None:
        order = self.book.create("Jansen")
        self.book.add_line(order.id, "SR-3", 2, 1500)
        self.book.confirm(order.id)
        self.book.cancel(order.id)
        self.assertEqual(self.inventory.get("SR-3").reserved, 0)
        with self.assertRaises(OrderError):
            self.book.get("O-99999")


if __name__ == "__main__":
    unittest.main()
