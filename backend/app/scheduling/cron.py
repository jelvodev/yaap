"""Cron schedules: five fields (minute, hour, day of month, month, day of week) in a time zone.

Fields take `*`, numbers, ranges (`1-5`), lists (`1,15`), steps (`*/15`, `9-17/2`) and the names of months and days
(`jan`, `mon-fri`). Day 0 and 7 are Sunday. As in cron, when both day fields are restricted a day matching either
one counts.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {name: number for number, name in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAYS = {name: number for number, name in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
LIMIT_DAYS = 366 * 5


class CronError(ValueError):
    """The expression cannot be read; the message is safe to show."""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool
    expression: str

    def day_matches(self, moment: datetime) -> bool:
        weekday = (moment.weekday() + 1) % 7
        in_days, in_weekdays = moment.day in self.days, weekday in self.weekdays
        if self.days_restricted and self.weekdays_restricted:
            return in_days or in_weekdays
        return in_days and in_weekdays


def parse_field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> frozenset[int]:
    values: set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"'{step_text}' is not a step.")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            first, last = part.split("-", 1)
            start, end = number(first, names), number(last, names)
        else:
            start = number(part, names)
            end = high if step > 1 else start
        if high == 7 and end == 7:
            values.add(0)
            end = 6 if start <= 6 else 7
        if start < low or end > high or start > end:
            raise CronError(f"'{part}' is outside {low}-{high}.")
        values.update(value % 7 if high == 7 else value for value in range(start, end + 1, step))
    return frozenset(values)


def number(text: str, names: dict[str, int] | None) -> int:
    if names and text in names:
        return names[text]
    if not text.isdigit():
        raise CronError(f"'{text}' is not a number.")
    return int(text)


def parse(expression: str) -> Cron:
    fields = expression.split()
    if len(fields) != 5:
        raise CronError("Use five fields: minute, hour, day of month, month, day of week (for example 0 7 * * 1-5).")
    minute, hour, day, month, weekday = fields
    return Cron(
        minutes=parse_field(minute, 0, 59),
        hours=parse_field(hour, 0, 23),
        days=parse_field(day, 1, 31),
        months=parse_field(month, 1, 12, MONTHS),
        weekdays=parse_field(weekday, 0, 7, DAYS),
        days_restricted=day != "*",
        weekdays_restricted=weekday != "*",
        expression=" ".join(fields),
    )


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise CronError(f"Unknown time zone {name!r}.") from error


def next_time(cron: Cron, timezone: str, after: datetime) -> datetime | None:
    """The first moment after `after` (aware) that the schedule matches, in UTC."""
    tz = zone(timezone)
    local = after.astimezone(tz).replace(second=0, microsecond=0, tzinfo=None) + timedelta(minutes=1)
    end = local + timedelta(days=LIMIT_DAYS)
    while local < end:
        if local.month not in cron.months:
            local = (local.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            continue
        if not cron.day_matches(local):
            local = (local + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if local.hour not in cron.hours:
            local = (local + timedelta(hours=1)).replace(minute=0)
            continue
        if local.minute not in cron.minutes:
            local += timedelta(minutes=1)
            continue
        return local.replace(tzinfo=tz).astimezone(UTC)
    return None


def describe(cron: Cron, timezone: str) -> str:
    """A readable form of the common schedules; others are shown as they are written."""
    fields = cron.expression.split()
    minute, hour, day, month, weekday = fields
    where = f" ({timezone})" if timezone else ""
    if minute.startswith("*/") and hour == day == month == weekday == "*":
        return f"Every {minute[2:]} minutes"
    if minute.isdigit() and hour == day == month == weekday == "*":
        return f"Every hour at :{int(minute):02d}"
    if minute.isdigit() and hour.isdigit() and day == "*" and month == "*":
        at = f"{int(hour):02d}:{int(minute):02d}{where}"
        if weekday == "*":
            return f"Every day at {at}"
        if weekday in ("1-5", "mon-fri"):
            return f"Weekdays at {at}"
        if weekday.isdigit() or weekday.lower() in DAYS:
            index = DAYS.get(weekday.lower(), int(weekday) % 7 if weekday.isdigit() else 0)
            return f"Every {DAY_NAMES[index]} at {at}"
    if minute.isdigit() and hour.isdigit() and day.isdigit() and month == "*" and weekday == "*":
        return f"Monthly on day {int(day)} at {int(hour):02d}:{int(minute):02d}{where}"
    return f"Cron {cron.expression}{where}"
