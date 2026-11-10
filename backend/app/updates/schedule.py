"""When the update check runs: the setting's validation and the due decision. Pure, so a table can test them."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.scheduling.cron import CronError, next_time, parse

OFF = "off"
DEFAULT_SCHEDULE = "0 9 * * *"
# GitHub allows 60 calls an hour without a token; a check at least this far apart never comes near that.
MIN_INTERVAL = timedelta(hours=1)
# How many consecutive runs are compared; the gaps of any cron expression repeat well within this many.
SAMPLE_RUNS = 100
SAMPLE_START = datetime(2026, 1, 1, tzinfo=UTC)
TOO_OFTEN = "Check at most once an hour: GitHub limits calls that do not use a token."


def is_off(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == OFF


def schedule_error(value: Any) -> str | None:
    """Why a value cannot be the update check schedule, or None: `off` or a cron expression at least an hour apart."""
    if not isinstance(value, str):
        return "Use off or a cron expression such as 0 9 * * *."
    if is_off(value):
        return None
    try:
        cron = parse(value)
    except CronError as error:
        return str(error)
    moment = SAMPLE_START
    for index in range(SAMPLE_RUNS):
        following = next_time(cron, "UTC", moment)
        if following is None:
            return None
        if index > 0 and following - moment < MIN_INTERVAL:
            return TOO_OFTEN
        moment = following
    return None


def check_due(schedule: str, timezone: str, last_check: datetime | None, since: datetime, now: datetime) -> bool:
    """Whether the scheduled check is due: a run of the schedule after the last check (or after start) has come.

    A check missed while YAAP was down needs no catching up: the check at start covers it."""
    if is_off(schedule) or schedule_error(schedule) is not None:
        return False
    after = last_check if last_check is not None else since
    try:
        due = next_time(parse(schedule), timezone, after)
    except CronError:
        return False
    return due is not None and due <= now
