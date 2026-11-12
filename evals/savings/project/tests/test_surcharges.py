import pytest

from freight.rates import (
    Destination,
    NotAccepted,
    Options,
    Parcel,
    dangerous_goods_surcharge,
    fuel_surcharge,
    heavy_surcharge,
    insurance_surcharge,
    oversize_surcharge,
    remote_area_surcharge,
    saturday_surcharge,
    signature_surcharge,
)

BOX = Parcel(1.0, 30, 20, 10)
DANGEROUS = Parcel(1.0, 30, 20, 10, dangerous_goods=True)


@pytest.mark.parametrize(
    ("base", "cents"),
    [(695, 83), (795, 95), (1155, 139), (1450, 174), (2995, 359), (9995, 1199)],
)
def test_fuel_is_twelve_percent_of_the_discounted_base_rate(base: int, cents: int) -> None:
    assert fuel_surcharge(base) == cents


@pytest.mark.parametrize(
    ("destination", "cents"),
    [
        (Destination("NL", "1791 AB"), 450),
        (Destination("NL", "1797 RN"), 450),
        (Destination("NL", "8881 AA"), 450),
        (Destination("NL", "8884 HG"), 450),
        (Destination("NL", "8885 AA"), 0),
        (Destination("NL", "8899 AB"), 450),
        (Destination("NL", "8898 ZZ"), 0),
        (Destination("NL", "9161 AA"), 450),
        (Destination("NL", "9171 AA"), 0),
        (Destination("NL", "1012 AB"), 0),
        (Destination("nl", "8882ab"), 450),
        (Destination("BE", "1790"), 0),
    ],
)
def test_the_wadden_islands_have_a_remote_area_surcharge(destination: Destination, cents: int) -> None:
    assert remote_area_surcharge(destination) == cents


@pytest.mark.parametrize(
    ("parcel", "cents"),
    [
        (Parcel(1.0, 121, 10, 10), 1500),
        (Parcel(1.0, 120, 30, 30), 0),
        (Parcel(1.0, 100, 50, 50), 0),
        (Parcel(1.0, 100, 51, 50), 1500),
        (Parcel(1.0, 40, 130, 10), 1500),
        (Parcel(1.0, 60, 60, 60), 0),
        (Parcel(1.0, 80, 60, 60), 1500),
    ],
)
def test_a_long_side_or_a_large_girth_is_oversize(parcel: Parcel, cents: int) -> None:
    assert oversize_surcharge(parcel) == cents


@pytest.mark.parametrize(("kg", "cents"), [(5.0, 0), (20.0, 0), (20.5, 800), (31.5, 800)])
def test_parcels_over_twenty_kilograms_are_heavy(kg: float, cents: int) -> None:
    assert heavy_surcharge(kg) == cents


@pytest.mark.parametrize(("zone", "cents"), [(0, 2500), (1, 2500), (2, 2500)])
def test_dangerous_goods_cost_extra_in_zones_0_to_2(zone: int, cents: int) -> None:
    assert dangerous_goods_surcharge(DANGEROUS, zone) == cents


@pytest.mark.parametrize("zone", [3, 4])
def test_dangerous_goods_are_refused_further_away(zone: int) -> None:
    with pytest.raises(NotAccepted):
        dangerous_goods_surcharge(DANGEROUS, zone)


def test_a_parcel_without_dangerous_goods_has_no_such_surcharge() -> None:
    assert dangerous_goods_surcharge(BOX, 4) == 0


def test_saturday_delivery_costs_extra_in_the_netherlands() -> None:
    assert saturday_surcharge(Options(saturday=True), 0) == 600


@pytest.mark.parametrize("zone", [1, 2, 4])
def test_saturday_delivery_is_refused_abroad(zone: int) -> None:
    with pytest.raises(NotAccepted):
        saturday_surcharge(Options(saturday=True), zone)


def test_without_saturday_delivery_there_is_no_saturday_surcharge() -> None:
    assert saturday_surcharge(Options(), 3) == 0


@pytest.mark.parametrize(("options", "cents"), [(Options(signature=True), 150), (Options(), 0)])
def test_a_signature_costs_extra(options: Options, cents: int) -> None:
    assert signature_surcharge(options, BOX) == cents


@pytest.mark.parametrize(
    ("declared", "cents"),
    [(0, 0), (20000, 0), (50000, 0), (50001, 500), (50100, 501), (60050, 601), (100000, 1000), (250000, 2500)],
)
def test_insurance_is_one_percent_of_a_declared_value_over_500_euro(declared: int, cents: int) -> None:
    assert insurance_surcharge(declared) == cents


def test_a_negative_declared_value_is_refused() -> None:
    with pytest.raises(NotAccepted):
        insurance_surcharge(-1)
