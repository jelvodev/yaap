"""Reading a stored reason back."""

import pytest

from app.core.why import Why, why_from


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            {"rule": "r", "source": "permission", "setting": "access", "layer": "project"},
            Why("r", "permission", "access", "project"),
        ),
        ({"rule": "r", "source": "trim"}, Why("r", "trim")),
        ({"rule": "r", "source": "trim", "setting": "", "layer": None}, Why("r", "trim")),
        ({"source": "trim"}, None),
        ({"rule": "", "source": "trim"}, None),
        ({"rule": "r", "source": "nonsense"}, None),
        ({"rule": "r", "source": "guard", "setting": "s", "layer": "nowhere"}, Why("r", "guard", "s", None)),
        ("text", None),
        (None, None),
        ([], None),
    ],
)
def test_why_from_reads_a_stored_reason_or_nothing(value, expected):
    assert why_from(value) == expected


def test_a_reason_reads_back_as_stored():
    why = Why("r", "cache", "ttl", "global")

    assert why_from(why.stored()) == why
