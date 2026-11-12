import pytest

from freight.rates import (
    Account,
    Destination,
    Line,
    NotAccepted,
    Options,
    Parcel,
    format_cents,
    format_quote,
    quote,
)


def test_a_small_domestic_parcel() -> None:
    result = quote(Parcel(1.3, 20, 15, 10), Destination("NL", "1012 AB"))
    assert result.lines == (Line("Base rate", 695), Line("Fuel", 83))
    assert (result.net_cents, result.vat_cents, result.total_cents) == (778, 163, 941)
    assert result.delivery_days == 1


def test_a_loyal_account_with_a_signature_to_belgium() -> None:
    result = quote(Parcel(4.0, 40, 30, 20), Destination("BE"), Options(signature=True), Account(100, 3))
    assert result.chargeable_kg == 5.0
    assert result.lines == (
        Line("Base rate", 1150),
        Line("Discount 7 %", -81),
        Line("Fuel", 128),
        Line("Signature", 150),
    )
    assert (result.net_cents, result.vat_cents, result.total_cents) == (1347, 283, 1630)
    assert result.delivery_days == 2


def test_a_heavy_insured_export_has_no_vat() -> None:
    result = quote(Parcel(22.0, 60, 40, 40, declared_value_cents=80000), Destination("US"))
    assert result.lines == (Line("Base rate", 9995), Line("Fuel", 1199), Line("Heavy", 800), Line("Insurance", 800))
    assert (result.net_cents, result.vat_cents, result.total_cents) == (12794, 0, 12794)
    assert result.notes == ("Export: no VAT.",)


def test_every_surcharge_on_one_parcel_to_vlieland() -> None:
    parcel = Parcel(2.0, 130, 20, 10, dangerous_goods=True)
    result = quote(parcel, Destination("NL", "8899 AB"), Options(saturday=True))
    assert result.chargeable_kg == 5.5
    assert [line.label for line in result.lines] == [
        "Base rate",
        "Fuel",
        "Remote area",
        "Oversize",
        "Dangerous goods",
        "Saturday delivery",
    ]
    assert (result.net_cents, result.vat_cents, result.total_cents) == (6164, 1294, 7458)


def test_a_discount_is_rounded_before_the_fuel_surcharge() -> None:
    result = quote(Parcel(2.01, 20, 20, 20), Destination("FR"), account=Account(500, 0))
    assert result.lines == (Line("Base rate", 1695), Line("Discount 10 %", -170), Line("Fuel", 183))
    assert (result.net_cents, result.vat_cents, result.total_cents) == (1708, 359, 2067)


@pytest.mark.parametrize(
    ("destination", "options"),
    [(Destination("US"), Options()), (Destination("DE"), Options(saturday=True))],
)
def test_a_quote_refuses_what_cannot_be_shipped(destination: Destination, options: Options) -> None:
    parcel = Parcel(1.0, 20, 20, 20, dangerous_goods=destination.country == "US")
    with pytest.raises(NotAccepted):
        quote(parcel, destination, options)


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "EUR 0.00"), (5, "EUR 0.05"), (1234, "EUR 12.34"), (-81, "EUR -0.81"), (100000, "EUR 1000.00")],
)
def test_amounts_are_formatted_in_euros(cents: int, text: str) -> None:
    assert format_cents(cents) == text


def test_a_formatted_quote_lists_every_line_and_the_delivery() -> None:
    result = quote(Parcel(4.0, 40, 30, 20), Destination("BE"), Options(signature=True), Account(100, 3))
    lines = format_quote(result).splitlines()
    assert lines[0] == "Base rate        EUR 11.50"
    assert lines[1] == "Discount 7 %     EUR -0.81"
    assert lines[-2] == "Total            EUR 16.30"
    assert lines[-1] == "Delivery in 2 working days."


def test_a_formatted_export_quote_says_there_is_no_vat() -> None:
    text = format_quote(quote(Parcel(1.0, 10, 10, 10), Destination("JP")))
    assert "VAT 0 %" in text
    assert text.endswith("Export: no VAT.")
    assert "Delivery in 8 working days." in text
