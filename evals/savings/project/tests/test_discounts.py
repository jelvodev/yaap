import pytest

from freight.rates import Account, discount_cents, discount_percent, volume_discount_percent


@pytest.mark.parametrize(
    ("parcels", "percent"),
    [(0, 0), (99, 0), (100, 5), (499, 5), (500, 10), (999, 10), (1000, 15), (5000, 15)],
)
def test_the_volume_discount_grows_with_the_parcels_per_month(parcels: int, percent: int) -> None:
    assert volume_discount_percent(parcels) == percent


@pytest.mark.parametrize(
    ("account", "percent"),
    [
        (Account(0, 0), 0),
        (Account(0, 3), 2),
        (Account(100, 2), 5),
        (Account(100, 3), 7),
        (Account(500, 5), 12),
        (Account(999, 10), 12),
        (Account(1000, 0), 15),
        (Account(1000, 3), 15),
    ],
)
def test_loyal_accounts_get_two_points_more_up_to_fifteen_percent(account: Account, percent: int) -> None:
    assert discount_percent(account) == percent


@pytest.mark.parametrize(
    ("base", "account", "cents"),
    [
        (950, Account(100, 0), 48),
        (995, Account(500, 0), 100),
        (1450, Account(1000, 0), 218),
        (2250, Account(100, 3), 158),
        (695, Account(), 0),
    ],
)
def test_the_discount_is_rounded_to_whole_cents(base: int, account: Account, cents: int) -> None:
    assert discount_cents(base, account) == cents
