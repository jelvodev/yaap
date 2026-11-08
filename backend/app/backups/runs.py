"""Backups on a schedule and by hand, copied to the destinations.

The global setting backup_schedule (on by default: daily at 03:00 in the global time zone, the last 7 kept) is
checked by the scheduler's minute loop. A backup missed while YAAP was down runs once at start. No backup starts
while a restore is pending or another backup runs. Every backup is recorded in backup_runs with the result per
destination; a failed scheduled backup shows in the header until a later one succeeds.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backups.destinations import (
    Destination,
    DestinationConfig,
    DestinationError,
    FolderDestination,
    copy_to,
    parse_destinations,
    prune_destination,
)
from app.core.backup import BackupError, backup_path, create_backup
from app.core.restore import PENDING
from app.scheduling.cron import CronError, next_time, parse
from app.storage.models import BackupRun

logger = logging.getLogger(__name__)

SCHEDULE_DEFAULT: dict[str, Any] = {"enabled": True, "cron": "0 3 * * *", "keep": 7}
SCHEDULED = ("schedule", "missed")
# A due time more than this long ago was missed while YAAP was down.
MISSED_AFTER = timedelta(minutes=2)
_lock = threading.Lock()


class BackupBusy(Exception):
    """A backup cannot start now; the message says why."""


def validate_schedule(value: Any, _providers: Any) -> str | None:
    if not isinstance(value, dict) or not set(value) <= set(SCHEDULE_DEFAULT):
        return "Use an object with enabled, cron and keep."
    if "enabled" in value and not isinstance(value["enabled"], bool):
        return "enabled is true or false."
    if "keep" in value and (not isinstance(value["keep"], int) or isinstance(value["keep"], bool) or not 1 <= value["keep"] <= 365):
        return "Keep between 1 and 365 backups."
    if "cron" in value:
        try:
            parse(str(value["cron"]))
        except CronError as error:
            return str(error)
    return None


def read_schedule(session: Session) -> dict[str, Any]:
    from app.core.settings import SETTING_DEFINITIONS, resolve_setting  # noqa: PLC0415 - settings imports this module

    value = resolve_setting(session, SETTING_DEFINITIONS["backup_schedule"], None).value
    return {**SCHEDULE_DEFAULT, **(value if isinstance(value, dict) else {})}


def read_destinations(session: Session) -> list[DestinationConfig]:
    from app.core.settings import SETTING_DEFINITIONS, resolve_setting  # noqa: PLC0415

    value = resolve_setting(session, SETTING_DEFINITIONS["backup_destinations"], None).value
    try:
        return parse_destinations(value or [])
    except DestinationError:
        logger.exception("The backup destinations setting cannot be read.")
        return []


def make_destination(config: DestinationConfig, session: Session, cipher: Any) -> Destination:
    if config.kind == "folder":
        return FolderDestination(config)
    from app.backups.smb import SmbDestination  # noqa: PLC0415 - only loaded when a share is configured

    return SmbDestination.from_config(config, session, cipher)


def timezone_name(session: Session) -> str:
    from app.core.settings import resolve_text  # noqa: PLC0415

    return resolve_text(session, "timezone", None) or "UTC"


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def last_scheduled(session: Session) -> BackupRun | None:
    return session.scalar(
        select(BackupRun).where(BackupRun.trigger.in_(SCHEDULED)).order_by(BackupRun.started_at.desc()).limit(1)
    )


def next_backup_at(session: Session, since: datetime) -> datetime | None:
    """When the next scheduled backup is due: after the last scheduled one, or after `since` when there is none."""
    schedule = read_schedule(session)
    if not schedule["enabled"]:
        return None
    try:
        cron = parse(str(schedule["cron"]))
    except CronError:
        return None
    last = last_scheduled(session)
    base = as_utc(last.started_at) if last is not None else since
    return next_time(cron, timezone_name(session), base)


def due_trigger(session: Session, since: datetime, now: datetime) -> str | None:
    due = next_backup_at(session, since)
    if due is None or due > now:
        return None
    return "missed" if now - due > MISSED_AFTER else "schedule"


def finish(session: Session, run: BackupRun, status: str, error: str | None = None) -> None:
    run.status, run.error, run.finished_at = status, error, datetime.now(UTC)
    session.commit()


def run_backup(state: Any, trigger: str) -> BackupRun:
    """Make a backup, copy it to every destination that is on, and record the run. Raises BackupBusy."""
    if not _lock.acquire(blocking=False):
        raise BackupBusy("A backup is being made right now; wait for it to finish.")
    try:
        if (state.data_dir / PENDING).exists():
            raise BackupBusy("A restore is waiting for the restart; no backup is made until it is done.")
        with state.session_factory() as session:
            run = BackupRun(trigger=trigger, status="running", started_at=datetime.now(UTC), results=[])
            session.add(run)
            session.commit()
            keep = int(read_schedule(session)["keep"])
            try:
                made = create_backup(state.data_dir, keep)
            except BackupError as error:
                finish(session, run, "failed", str(error))
                return run
            run.name, run.size = made.name, made.size
            results = []
            for config in read_destinations(session):
                if not config.enabled:
                    continue
                try:
                    destination = make_destination(config, session, state.cipher)
                    message = copy_to(destination, backup_path(state.data_dir, made.name))
                    removed = prune_destination(destination, config.keep)
                    if removed:
                        message += f" Removed {removed} older."
                    results.append({"destination": config.name, "ok": True, "message": message})
                except DestinationError as error:
                    results.append({"destination": config.name, "ok": False, "message": str(error)})
                except Exception as error:  # noqa: BLE001 - one destination must not stop the others
                    logger.exception("Copying the backup to %s failed.", config.name)
                    results.append({"destination": config.name, "ok": False, "message": f"Unexpected error: {error}"})
            run.results = results
            failed = [item["destination"] for item in results if not item["ok"]]
            finish(session, run, "partial" if failed else "ok", f"Not copied to {', '.join(failed)}." if failed else None)
            return run
    finally:
        _lock.release()


def backup_running() -> bool:
    return _lock.locked()


def backup_tick(app: Any, now: datetime | None = None, wait: bool = False) -> str | None:
    """From the minute loop: start the scheduled backup when it is due. Returns its trigger, or None."""
    now = now or datetime.now(UTC)
    if backup_running() or (app.state.data_dir / PENDING).exists():
        return None
    with app.state.session_factory() as session:
        trigger = due_trigger(session, getattr(app.state, "backup_since", now), now)
    if trigger is None:
        return None

    def work() -> None:
        try:
            run_backup(app.state, trigger)
        except BackupBusy:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("The scheduled backup failed.")

    if wait:
        work()
    else:
        threading.Thread(target=work, name="yaap-backup", daemon=True).start()
    return trigger


def recover_backup_runs(session: Session) -> int:
    """A backup that was running when YAAP stopped did not finish."""
    runs = session.scalars(select(BackupRun).where(BackupRun.status == "running")).all()
    for run in runs:
        run.status, run.finished_at = "failed", datetime.now(UTC)
        run.error = "YAAP stopped while the backup was being made."
    session.commit()
    return len(runs)
