"""Quantities with an explicit unit, as an eval case writes its budget and timeouts.

A bare number is refused: `40` could be steps, seconds or thousands of tokens, and a case is read by people and
agents who should not have to guess. Each parser returns the value in YAAP's base unit (steps, tokens, USD,
seconds) or raises UnitError with a message that shows how to write it.
"""

import re
from collections.abc import Collection

QUANTITY = re.compile(r"^(?P<dollar>\$)?(?P<number>\d+(?:\.\d+)?)(?P<scale>[kM])?\s*(?P<unit>[A-Za-z]+)?$")
SCALES = {"k": 1_000, "M": 1_000_000}
STEP_UNITS = frozenset({"step", "steps"})
TOKEN_UNITS = frozenset({"token", "tokens"})
COST_UNITS = frozenset({"usd", "USD"})
SECONDS_PER_UNIT = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
}
EXAMPLES = {"steps": "40 steps", "tokens": "200k tokens", "cost": "0.50 USD", "time": "10 min"}


class UnitError(ValueError):
    """A quantity without a unit, with an unknown unit, or not a positive number; the message says how to write it."""


def quantity(value: object, what: str) -> re.Match[str]:
    """The parts of a quantity written as text; numbers without text have no unit."""
    example = EXAMPLES[what]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise UnitError(f"write it as text with a unit, like '{example}'")
    if not isinstance(value, str):
        raise UnitError(f"'{value}' has no unit; write it like '{example}'")
    match = QUANTITY.match(value.strip())
    if match is None:
        raise UnitError(f"'{value}' is not a number with a unit; write it like '{example}'")
    if float(match["number"]) <= 0:
        raise UnitError(f"'{value}' must be more than zero")
    return match


def plain(match: re.Match[str], value: object, what: str, units: Collection[str]) -> float:
    """The number of a quantity that allows neither a dollar sign nor a scale, in one of `units`."""
    example = EXAMPLES[what]
    if match["unit"] is None:
        raise UnitError(f"'{value}' has no unit; write it like '{example}'")
    if match["dollar"] or match["scale"] or match["unit"] not in units:
        raise UnitError(f"'{value}' has an unknown unit; write it like '{example}'")
    return float(match["number"])


def parse_steps(value: object) -> int:
    match = quantity(value, "steps")
    number = plain(match, value, "steps", STEP_UNITS)
    if not number.is_integer():
        raise UnitError(f"'{value}' must be a whole number of steps")
    return int(number)


def parse_tokens(value: object) -> int:
    match = quantity(value, "tokens")
    if match["unit"] is None:
        raise UnitError(f"'{value}' has no unit; write it like '{EXAMPLES['tokens']}'")
    if match["dollar"] or match["unit"] not in TOKEN_UNITS:
        raise UnitError(f"'{value}' has an unknown unit; write it like '{EXAMPLES['tokens']}'")
    return round(float(match["number"]) * SCALES.get(match["scale"] or "", 1))


def parse_cost(value: object) -> float:
    """An amount in USD, written as `0.50 USD` or `$0.50`."""
    match = quantity(value, "cost")
    if match["dollar"] and match["unit"] is None and match["scale"] is None:
        return float(match["number"])
    return plain(match, value, "cost", COST_UNITS)


def parse_seconds(value: object) -> float:
    """A duration in seconds, written in s, min or h (`90 s`, `10 min`, `1 h`)."""
    match = quantity(value, "time")
    return plain(match, value, "time", SECONDS_PER_UNIT.keys()) * SECONDS_PER_UNIT[match["unit"]]
