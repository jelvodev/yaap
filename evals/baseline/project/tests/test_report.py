import unittest

from stockroom import report
from stockroom.inventory import Inventory
from tests.helpers import make_inventory


class Reports(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = make_inventory()

    def test_the_csv_export_has_a_header_and_one_line_per_item(self) -> None:
        lines = report.csv_export(self.inventory).splitlines()
        self.assertEqual(lines[0], "sku,name,category,location,quantity,reserved,unit_cost,value")
        self.assertEqual(lines[1], "SR-1,Claw hammer,tools,A1,10,0,12.50,125.00")
        self.assertEqual(len(lines), 5)

    def test_the_stock_report_groups_by_category_and_flags_reorders(self) -> None:
        text = report.stock_report(self.inventory)
        self.assertIn("Food (1 items)", text)
        self.assertTrue(text.splitlines()[-1].endswith("Stock value:          EUR 419.50"))
        self.assertTrue(report.item_row(self.inventory.get("SR-3")).endswith("reorder"))

    def test_long_names_are_shortened(self) -> None:
        self.assertEqual(report.short_name("x" * 40, 10), "x" * 9 + "…")

    def test_valuation_and_restock(self) -> None:
        self.assertIn("fasteners", report.valuation_report(self.inventory).splitlines()[3])
        self.assertIn("SR-3", report.restock_report(self.inventory))
        self.assertEqual(report.restock_report(Inventory()), "Nothing to reorder.\n")


if __name__ == "__main__":
    unittest.main()
