"""Shipping quotes for parcels: zones, chargeable weight, base rates, surcharges, discounts, VAT and delivery.

Money is whole euro cents. The rules are in docs/rules.md; the sections below follow its numbering.
"""

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


class NotAccepted(ValueError):
    """The parcel or an option cannot be shipped to this destination; the message says why."""


@dataclass(frozen=True)
class Parcel:
    """One parcel: its actual weight, its sides and what it holds."""

    weight_kg: float
    length_cm: float
    width_cm: float
    height_cm: float
    declared_value_cents: int = 0
    dangerous_goods: bool = False

    def sides(self) -> tuple[float, float, float]:
        """The sides from long to short."""
        longest, middle, shortest = sorted((self.length_cm, self.width_cm, self.height_cm), reverse=True)
        return longest, middle, shortest


@dataclass(frozen=True)
class Destination:
    country: str
    postcode: str = ""

    def code(self) -> str:
        return self.country.strip().upper()

    def compact_postcode(self) -> str:
        return self.postcode.replace(" ", "").upper()


@dataclass(frozen=True)
class Options:
    saturday: bool = False
    signature: bool = False


@dataclass(frozen=True)
class Account:
    """Who ships: how many parcels a month and for how many years."""

    parcels_per_month: int = 0
    years: int = 0


@dataclass(frozen=True)
class Line:
    label: str
    cents: int


@dataclass(frozen=True)
class Quote:
    lines: tuple[Line, ...]
    net_cents: int
    vat_cents: int
    total_cents: int
    delivery_days: int
    zone: int
    chargeable_kg: float = 0.0
    notes: tuple[str, ...] = field(default=())


# 1. Zones

DOMESTIC = "NL"
ZONE_1 = frozenset({"BE", "LU", "DE"})
ZONE_2 = frozenset({"FR", "AT", "DK", "IT", "ES"})
EU = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
        "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    }
)  # fmt: skip


def zone_of(country: str) -> int:
    """The zone of a country code, in any case."""
    code = country.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise NotAccepted(f"{country!r} is not a country code.")
    if code == DOMESTIC:
        return 0
    if code in ZONE_1:
        return 1
    if code in ZONE_2:
        return 2
    if code in EU:
        return 3
    return 4


def is_export(zone: int) -> bool:
    """Zone 4 leaves the EU."""
    return zone == 4


# 2. Chargeable weight

VOLUMETRIC_DIVISOR = 5000
MINIMUM_KG = 0.5
MAXIMUM_KG = 31.5


def volumetric_weight(parcel: Parcel) -> float:
    """Length × width × height in cm divided by 5,000, in kg."""
    return parcel.length_cm * parcel.width_cm * parcel.height_cm / VOLUMETRIC_DIVISOR


def round_up_to_half_kg(kg: float) -> float:
    """Up to the next half kilogram; a whole or half kilogram stays as it is."""
    return math.ceil(round(kg * 2, 9)) / 2


def chargeable_weight(parcel: Parcel) -> float:
    """The larger of the actual and the volumetric weight, rounded up to half a kg, at least 0.5 kg."""
    if parcel.weight_kg <= 0:
        raise NotAccepted("A parcel has a weight above zero.")
    if min(parcel.length_cm, parcel.width_cm, parcel.height_cm) <= 0:
        raise NotAccepted("A parcel has three sides above zero.")
    weight = max(parcel.weight_kg, volumetric_weight(parcel))
    kg = max(round_up_to_half_kg(weight), MINIMUM_KG)
    if kg > MAXIMUM_KG:
        raise NotAccepted(f"{kg} kg is over the maximum of {MAXIMUM_KG} kg.")
    return kg


# 3. Base rates

WEIGHT_BANDS = (2.0, 5.0, 10.0, 20.0, 31.5)
BASE_RATES = {
    0: (695, 795, 995, 1450, 1995),
    1: (950, 1150, 1595, 2250, 2995),
    2: (1350, 1695, 2250, 3195, 4250),
    3: (1695, 2150, 2895, 4150, 5595),
    4: (2495, 3450, 4995, 7450, 9995),
}


def weight_band(kg: float) -> int:
    """The index of the band a chargeable weight falls in; a band includes its upper limit."""
    for index, limit in enumerate(WEIGHT_BANDS):
        if kg <= limit:
            return index
    raise NotAccepted(f"{kg} kg is over the maximum of {MAXIMUM_KG} kg.")


def base_rate(zone: int, kg: float) -> int:
    """The base rate in cents for a zone and a chargeable weight."""
    if zone not in BASE_RATES:
        raise NotAccepted(f"There is no zone {zone}.")
    return BASE_RATES[zone][weight_band(kg)]


# 4. Surcharges

FUEL_PERCENT = 12
REMOTE_AREA_CENTS = 450
REMOTE_PREFIXES = ("179", "8881", "8882", "8883", "8884", "8899", "9161", "9162", "9163", "9164")
OVERSIZE_CENTS = 1500
OVERSIZE_LONGEST_CM = 120
OVERSIZE_GIRTH_CM = 300
HEAVY_CENTS = 800
HEAVY_ABOVE_KG = 20.0
DANGEROUS_GOODS_CENTS = 2500
DANGEROUS_GOODS_ZONES = frozenset({0, 1, 2})
SATURDAY_CENTS = 600
SATURDAY_ZONES = frozenset({0})
SIGNATURE_CENTS = 150
INSURANCE_ABOVE_CENTS = 50000
INSURANCE_PERCENT = 1
INSURANCE_MINIMUM_CENTS = 200


def fuel_surcharge(discounted_base_cents: int) -> int:
    """12 % of the base rate after the discount."""
    return percent_of(discounted_base_cents, FUEL_PERCENT)


def is_remote(destination: Destination) -> bool:
    """A Dutch postcode on one of the Wadden islands."""
    if destination.code() != DOMESTIC:
        return False
    postcode = destination.compact_postcode()
    return any(postcode.startswith(prefix) for prefix in REMOTE_PREFIXES)


def remote_area_surcharge(destination: Destination) -> int:
    return REMOTE_AREA_CENTS if is_remote(destination) else 0


def girth(parcel: Parcel) -> float:
    """The longest side plus twice each of the other two."""
    longest, middle, shortest = parcel.sides()
    return longest + 2 * middle + 2 * shortest


def is_oversize(parcel: Parcel) -> bool:
    longest, _, _ = parcel.sides()
    return longest > OVERSIZE_LONGEST_CM or girth(parcel) > OVERSIZE_GIRTH_CM


def oversize_surcharge(parcel: Parcel) -> int:
    return OVERSIZE_CENTS if is_oversize(parcel) else 0


def heavy_surcharge(chargeable_kg: float) -> int:
    return HEAVY_CENTS if chargeable_kg > HEAVY_ABOVE_KG else 0


def dangerous_goods_surcharge(parcel: Parcel, zone: int) -> int:
    """Dangerous goods are accepted in zones 0 to 2 only."""
    if not parcel.dangerous_goods:
        return 0
    if zone not in DANGEROUS_GOODS_ZONES:
        raise NotAccepted(f"Dangerous goods are not accepted in zone {zone}.")
    return DANGEROUS_GOODS_CENTS


def saturday_surcharge(options: Options, zone: int) -> int:
    """Saturday delivery exists in zone 0 only."""
    if not options.saturday:
        return 0
    if zone not in SATURDAY_ZONES:
        raise NotAccepted(f"Saturday delivery is not offered in zone {zone}.")
    return SATURDAY_CENTS


def signature_surcharge(options: Options, parcel: Parcel) -> int:
    """A signature on delivery, when asked for."""
    return SIGNATURE_CENTS if options.signature else 0


def insurance_surcharge(declared_value_cents: int) -> int:
    """1 % of a declared value over 500.00, at least 2.00; nothing at or under 500.00."""
    if declared_value_cents < 0:
        raise NotAccepted("A declared value cannot be negative.")
    if declared_value_cents <= INSURANCE_ABOVE_CENTS:
        return 0
    return max(percent_of(declared_value_cents, INSURANCE_PERCENT), INSURANCE_MINIMUM_CENTS)


def surcharge_lines(
    parcel: Parcel, destination: Destination, options: Options, zone: int, kg: float, discounted_base_cents: int
) -> list[Line]:
    """The surcharges that apply, in the order of docs/rules.md, without the ones that are zero."""
    candidates = [
        Line("Fuel", fuel_surcharge(discounted_base_cents)),
        Line("Remote area", remote_area_surcharge(destination)),
        Line("Oversize", oversize_surcharge(parcel)),
        Line("Heavy", heavy_surcharge(kg)),
        Line("Dangerous goods", dangerous_goods_surcharge(parcel, zone)),
        Line("Saturday delivery", saturday_surcharge(options, zone)),
        Line("Signature", signature_surcharge(options, parcel)),
        Line("Insurance", insurance_surcharge(parcel.declared_value_cents)),
    ]
    return [line for line in candidates if line.cents]


# 5. Discounts

VOLUME_DISCOUNTS = ((1000, 15), (500, 10), (100, 5))
LOYALTY_YEARS = 3
LOYALTY_POINTS = 2
MAXIMUM_DISCOUNT_PERCENT = 15


def volume_discount_percent(parcels_per_month: int) -> int:
    for threshold, percent in VOLUME_DISCOUNTS:
        if parcels_per_month >= threshold:
            return percent
    return 0


def discount_percent(account: Account) -> int:
    """The volume discount plus the loyalty points, at most 15."""
    percent = volume_discount_percent(account.parcels_per_month)
    if account.years >= LOYALTY_YEARS:
        percent += LOYALTY_POINTS
    return min(percent, MAXIMUM_DISCOUNT_PERCENT)


def discount_cents(base_cents: int, account: Account) -> int:
    """The discount on the base rate in cents, as a positive number."""
    return percent_of(base_cents, discount_percent(account))


# 6. Rounding


def percent_of(cents: int, percent: int) -> int:
    """A percentage of an amount in whole cents, half a cent rounding up."""
    exact = Decimal(cents) * Decimal(percent) / Decimal(100)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# 7. VAT

VAT_PERCENT = 21


def vat_percent(zone: int) -> int:
    return 0 if is_export(zone) else VAT_PERCENT


def vat_cents(net_cents: int, zone: int) -> int:
    return percent_of(net_cents, vat_percent(zone))


# 8. Delivery

DELIVERY_DAYS = {0: 1, 1: 2, 2: 3, 3: 5, 4: 8}


def delivery_days(zone: int) -> int:
    return DELIVERY_DAYS[zone]


# 9. Quotes


def quote(
    parcel: Parcel, destination: Destination, options: Options | None = None, account: Account | None = None
) -> Quote:
    """The full price of one parcel, line by line."""
    options = options or Options()
    account = account or Account()
    zone = zone_of(destination.country)
    kg = chargeable_weight(parcel)
    base = base_rate(zone, kg)
    discount = discount_cents(base, account)
    lines = [Line("Base rate", base)]
    if discount:
        lines.append(Line(f"Discount {discount_percent(account)} %", -discount))
    lines += surcharge_lines(parcel, destination, options, zone, kg, base - discount)
    net = sum(line.cents for line in lines)
    vat = vat_cents(net, zone)
    notes = ("Export: no VAT.",) if is_export(zone) else ()
    return Quote(tuple(lines), net, vat, net + vat, delivery_days(zone), zone, kg, notes)


def format_cents(cents: int) -> str:
    """EUR 12.34; a negative amount as EUR -1.23."""
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"EUR {sign}{whole}.{rest:02d}"


def format_quote(result: Quote) -> str:
    """The quote as aligned text lines, as the shop's checkout shows it."""
    rows = [(line.label, format_cents(line.cents)) for line in result.lines]
    rows += [
        ("Net", format_cents(result.net_cents)),
        (f"VAT {vat_percent(result.zone)} %", format_cents(result.vat_cents)),
        ("Total", format_cents(result.total_cents)),
    ]
    width = max(len(label) for label, _ in rows)
    text = [f"{label.ljust(width)}  {amount.rjust(12)}" for label, amount in rows]
    days = result.delivery_days
    text.append(f"Delivery in {days} working {'day' if days == 1 else 'days'}.")
    text += list(result.notes)
    return "\n".join(text)
