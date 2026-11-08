"""Prices, VAT and money formatting. Amounts are whole cents; VAT rates are whole percentages.

The Dutch rates are the defaults: 21% for most goods, 9% for food and books, 0% for exports outside the EU.
"""

from dataclasses import dataclass

VAT_RATES = {"standard": 21, "reduced": 9, "zero": 0}
CATEGORY_VAT = {"food": "reduced", "books": "reduced"}


@dataclass(frozen=True)
class PriceBreakdown:
    net_cents: int
    vat_cents: int
    vat_rate: int

    @property
    def gross_cents(self) -> int:
        return self.net_cents + self.vat_cents


def vat_rate_for(category: str, export: bool = False) -> int:
    """The VAT percentage for a category of goods; exports outside the EU carry no VAT."""
    if export:
        return VAT_RATES["zero"]
    return VAT_RATES[CATEGORY_VAT.get(category.casefold(), "standard")]


def vat_cents(net_cents: int, rate_percent: int) -> int:
    """The VAT on a net amount, rounded to whole cents; half a cent rounds up, as the tax office requires."""
    if net_cents < 0:
        raise ValueError("A net amount cannot be negative.")
    return round(net_cents * rate_percent / 100)


def breakdown(net_cents: int, category: str, export: bool = False) -> PriceBreakdown:
    rate = vat_rate_for(category, export)
    return PriceBreakdown(net_cents, vat_cents(net_cents, rate), rate)


def sale_price_cents(unit_cost_cents: int, margin_percent: int) -> int:
    """The net sale price for a unit cost and a margin on the sale price (not a markup on the cost), rounded up to
    the next 5 cents so prices look tidy."""
    if not 0 <= margin_percent < 100:
        raise ValueError("A margin is at least 0% and below 100%.")
    exact = unit_cost_cents * 100 / (100 - margin_percent)
    whole = int(-(-exact // 1))
    return whole + (-whole) % 5


def format_cents(cents: int, symbol: str = "EUR") -> str:
    """1234 -> 'EUR 12.34'; negative amounts get a minus sign before the symbol."""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{symbol} {whole:,}.{rest:02d}"


def parse_cents(text: str) -> int:
    """'12.34', '12,34', 'EUR 12.34' or '12' -> cents. Thousands separators are not accepted."""
    cleaned = text.strip().upper().removeprefix("EUR").strip().replace(",", ".")
    if not cleaned:
        raise ValueError("An amount cannot be empty.")
    negative = cleaned.startswith("-")
    cleaned = cleaned.removeprefix("-")
    whole, _, fraction = cleaned.partition(".")
    if not whole.isdigit() or (fraction and (not fraction.isdigit() or len(fraction) > 2)):
        raise ValueError(f"Not an amount: {text!r}.")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0") or 0)
    return -cents if negative else cents


def cents_to_words(cents: int) -> str:
    """12.05 -> '12 euro and 5 cent', for printed invoices."""
    whole, rest = divmod(abs(cents), 100)
    text = f"{whole} euro" + (f" and {rest} cent" if rest else "")
    return f"minus {text}" if cents < 0 else text
