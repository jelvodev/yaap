import pytest

from freight.rates import NotAccepted, zone_of


@pytest.mark.parametrize(
    ("country", "zone"),
    [
        ("NL", 0),
        ("nl", 0),
        (" NL ", 0),
        ("BE", 1),
        ("LU", 1),
        ("DE", 1),
        ("de", 1),
        ("FR", 2),
        ("AT", 2),
        ("DK", 2),
        ("IT", 2),
        ("ES", 2),
        ("es", 2),
        ("PL", 3),
        ("SE", 3),
        ("FI", 3),
        ("IE", 3),
        ("PT", 3),
        ("GR", 3),
        ("CZ", 3),
        ("HU", 3),
        ("RO", 3),
        ("BG", 3),
        ("MT", 3),
        ("CY", 3),
        ("EE", 3),
        ("GB", 4),
        ("CH", 4),
        ("NO", 4),
        ("US", 4),
        ("JP", 4),
        ("AU", 4),
        ("br", 4),
    ],
)
def test_a_country_is_in_its_zone(country: str, zone: int) -> None:
    assert zone_of(country) == zone


@pytest.mark.parametrize("country", ["", "N", "NLD", "1A", "--"])
def test_a_country_code_has_two_letters(country: str) -> None:
    with pytest.raises(NotAccepted):
        zone_of(country)
