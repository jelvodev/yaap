import unittest

from stockroom.pricing import breakdown, format_cents, parse_cents, sale_price_cents, vat_cents, vat_rate_for


class Pricing(unittest.TestCase):
    def test_vat_rates_follow_the_category(self) -> None:
        self.assertEqual(vat_rate_for("tools"), 21)
        self.assertEqual(vat_rate_for("Food"), 9)
        self.assertEqual(vat_rate_for("books", export=True), 0)

    def test_vat_and_the_breakdown(self) -> None:
        self.assertEqual(vat_cents(1000, 21), 210)
        self.assertEqual(vat_cents(1999, 9), 180)
        self.assertEqual(breakdown(1000, "food").gross_cents, 1090)
        with self.assertRaises(ValueError):
            vat_cents(-1, 21)

    def test_sale_prices_use_a_margin_and_end_in_five_cents(self) -> None:
        self.assertEqual(sale_price_cents(600, 40), 1000)
        self.assertEqual(sale_price_cents(601, 40), 1005)
        with self.assertRaises(ValueError):
            sale_price_cents(100, 100)

    def test_formatting_and_parsing_amounts(self) -> None:
        self.assertEqual(format_cents(123456), "EUR 1,234.56")
        self.assertEqual(format_cents(-5), "-EUR 0.05")
        self.assertEqual(parse_cents("EUR 12,5"), 1250)
        self.assertEqual(parse_cents("-3"), -300)
        with self.assertRaises(ValueError):
            parse_cents("1.234,56")


if __name__ == "__main__":
    unittest.main()
