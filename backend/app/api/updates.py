from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.updates.check import UpdateService, UpdateState
from app.version import VERSION

router = APIRouter(tags=["updates"])


class UpdateStatusOut(BaseModel):
    current_version: str
    newest_version: str | None
    update_available: bool
    # The newest version's changelog section: outside text, shown through the sanitizing Markdown renderer only.
    notes: str
    # The page of the newest version on github.com.
    release_url: str | None
    last_checked_at: datetime | None
    # Why the last check failed; the fields above are then the result of the check before.
    error: str | None
    checking: bool
    # Whether YAAP checks by itself (at start and on the schedule); a check by hand always works.
    automatic: bool
    # The newest version a notification was given for.
    announced_version: str | None


def status_out(service: UpdateService, state: UpdateState) -> UpdateStatusOut:
    newest = state.newest_version
    return UpdateStatusOut(
        current_version=VERSION,
        newest_version=newest,
        update_available=state.update_available,
        notes=state.notes,
        release_url=f"https://github.com/{state.source}/releases/tag/v{newest}" if newest and state.source else None,
        last_checked_at=state.checked_at,
        error=state.error,
        checking=service.checking,
        automatic=not service.is_off(),
        announced_version=state.announced_version,
    )


@router.get("/updates", response_model=UpdateStatusOut)
def read_updates(request: Request) -> UpdateStatusOut:
    """The result of the last update check."""
    service: UpdateService = request.app.state.updates
    return status_out(service, service.current())


@router.post("/updates/check", response_model=UpdateStatusOut)
def check_updates(request: Request) -> UpdateStatusOut:
    """Check now. A check that already runs is waited for; a failed check is reported in `error`, not as an error."""
    service: UpdateService = request.app.state.updates
    return status_out(service, service.check().state)
