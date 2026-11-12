import pytest

from freight.rates import delivery_days, percent_of, vat_cents, vat_percent


@pytest.mark.parametrize(
    ("cents", "percent", "result"),
    [
        (795, 12, 95),
        (950, 5, 48),
        (1250, 21, 263),
        (1000, 21, 210),
        (60050, 1, 601),
        (0, 21, 0),
        (1, 50, 1),
        (3, 10, 0),
    ],
)
def test_a_percentage_rounds_half_a_cent_up(cents: int, percent: int, result: int) -> None:
    assert percent_of(cents, percent) == result


@pytest.mark.parametrize(("zone", "percent"), [(0, 21), (1, 21), (2, 21), (3, 21), (4, 0)])
def test_vat_is_21_percent_inside_the_eu_and_nothing_on_export(zone: int, percent: int) -> None:
    assert vat_percent(zone) == percent


@pytest.mark.parametrize(
    ("net", "zone", "cents"),
    [(1000, 0, 210), (1250, 1, 263), (1950, 2, 410), (2349, 3, 493), (1000, 4, 0), (5000, 4, 0)],
)
def test_vat_is_charged_over_the_net_total(net: int, zone: int, cents: int) -> None:
    assert vat_cents(net, zone) == cents


@pytest.mark.parametrize(("zone", "days"), [(0, 1), (1, 2), (2, 3), (3, 5), (4, 8)])
def test_delivery_takes_longer_further_away(zone: int, days: int) -> None:
    assert delivery_days(zone) == days
