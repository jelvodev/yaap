"""The two background moments of the update check: a few seconds after start, and on the scheduler's minute tick."""

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from app.updates import check
from app.updates.check import UpdateService

logger = logging.getLogger(__name__)


def run_in_thread(service: UpdateService) -> None:
    try:
        service.check()
    except Exception:  # noqa: BLE001 - a background check must never stop the scheduler loop
        logger.exception("The scheduled update check failed.")


def update_tick(app: Any, now: datetime | None = None, wait: bool = False) -> bool:
    """From the minute loop: start the scheduled check when it is due. Returns whether one was started."""
    service: UpdateService = app.state.updates
    if not service.scheduled_check_due(now or datetime.now(UTC)):
        return False
    if wait:
        run_in_thread(service)
    else:
        threading.Thread(target=run_in_thread, args=(service,), name="yaap-update-check", daemon=True).start()
    return True


async def check_at_start(service: UpdateService, delay: float | None = None) -> None:
    """A few seconds after start, unless the check is switched off. It runs in a worker thread, so a slow GitHub never
    holds anything up."""
    await asyncio.sleep(check.START_DELAY_SECONDS if delay is None else delay)
    if service.is_off():
        return
    try:
        await asyncio.to_thread(service.check)
    except Exception:  # noqa: BLE001 - the check at start must never take YAAP down
        logger.exception("The update check at start failed.")
