import pytest

from freight.rates import NotAccepted, Parcel, chargeable_weight, round_up_to_half_kg, volumetric_weight


@pytest.mark.parametrize(
    ("sides", "kg"),
    [
        ((10, 10, 10), 0.2),
        ((50, 40, 30), 12.0),
        ((100, 50, 50), 50.0),
        ((25, 20, 10), 1.0),
        ((60, 40, 40), 19.2),
        ((30, 30, 30), 5.4),
    ],
)
def test_the_volumetric_weight_divides_the_volume_by_5000(sides: tuple[int, int, int], kg: float) -> None:
    assert volumetric_weight(Parcel(1.0, *sides)) == pytest.approx(kg)


@pytest.mark.parametrize(
    ("weight", "rounded"),
    [
        (0.1, 0.5),
        (0.5, 0.5),
        (0.6, 1.0),
        (1.0, 1.0),
        (1.2, 1.5),
        (2.0, 2.0),
        (2.01, 2.5),
        (2.5, 2.5),
        (2.51, 3.0),
        (4.9, 5.0),
        (7.1, 7.5),
        (12.2, 12.5),
        (19.6, 20.0),
        (30.05, 30.5),
    ],
)
def test_a_weight_rounds_up_to_the_next_half_kilogram(weight: float, rounded: float) -> None:
    assert round_up_to_half_kg(weight) == rounded


@pytest.mark.parametrize(
    ("parcel", "kg"),
    [
        (Parcel(1.3, 20, 15, 10), 1.5),
        (Parcel(0.05, 10, 10, 5), 0.5),
        (Parcel(2.0, 30, 20, 10), 2.0),
        (Parcel(3.0, 50, 40, 30), 12.0),
        (Parcel(14.0, 50, 40, 30), 14.0),
        (Parcel(1.0, 60, 40, 40), 19.5),
        (Parcel(4.2, 30, 30, 30), 5.5),
        (Parcel(31.5, 40, 30, 20), 31.5),
    ],
)
def test_the_chargeable_weight_is_the_larger_weight_rounded_up(parcel: Parcel, kg: float) -> None:
    assert chargeable_weight(parcel) == kg


@pytest.mark.parametrize(
    "parcel",
    [
        Parcel(0.0, 10, 10, 10),
        Parcel(1.0, 0, 10, 10),
        Parcel(31.6, 40, 30, 20),
        Parcel(1.0, 100, 60, 30),
    ],
)
def test_a_parcel_without_weight_or_over_the_maximum_is_refused(parcel: Parcel) -> None:
    with pytest.raises(NotAccepted):
        chargeable_weight(parcel)
