import pytest

from freight.rates import NotAccepted, base_rate, weight_band


@pytest.mark.parametrize(
    ("kg", "band"),
    [
        (0.5, 0),
        (1.5, 0),
        (2.0, 0),
        (2.5, 1),
        (5.0, 1),
        (5.5, 2),
        (10.0, 2),
        (10.5, 3),
        (20.0, 3),
        (20.5, 4),
        (31.5, 4),
    ],
)
def test_a_band_includes_its_upper_limit(kg: float, band: int) -> None:
    assert weight_band(kg) == band


@pytest.mark.parametrize(
    ("zone", "kg", "cents"),
    [
        (0, 0.5, 695),
        (0, 3.0, 795),
        (0, 7.5, 995),
        (0, 15.0, 1450),
        (0, 25.0, 1995),
        (1, 1.0, 950),
        (1, 4.5, 1150),
        (1, 9.5, 1595),
        (1, 12.0, 2250),
        (1, 30.0, 2995),
        (2, 1.5, 1350),
        (2, 3.5, 1695),
        (2, 6.0, 2250),
        (2, 18.5, 3195),
        (2, 22.0, 4250),
        (3, 2.0, 1695),
        (3, 4.0, 2150),
        (3, 8.0, 2895),
        (3, 11.0, 4150),
        (3, 31.5, 5595),
        (4, 0.5, 2495),
        (4, 2.5, 3450),
        (4, 9.0, 4995),
        (4, 16.0, 7450),
        (4, 27.5, 9995),
    ],
)
def test_the_base_rate_follows_the_table(zone: int, kg: float, cents: int) -> None:
    assert base_rate(zone, kg) == cents


@pytest.mark.parametrize(("zone", "kg"), [(5, 1.0), (-1, 1.0), (0, 32.0)])
def test_an_unknown_zone_or_a_weight_over_the_bands_is_refused(zone: int, kg: float) -> None:
    with pytest.raises(NotAccepted):
        base_rate(zone, kg)
