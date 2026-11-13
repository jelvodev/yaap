"""The update check: asks GitHub for the newest release tag of YAAP's repository and reads its changelog section.

One check runs at a time; a check that arrives meanwhile waits for that one's result. The result is kept in memory
and in the settings table, so it survives a restart. Offline or refused, the last result stays and the error shows.
Nothing here blocks YAAP: callers run a check in a worker thread, and every request has a short timeout.

The release notes come from outside. They are stored as text, shown by the frontend through its sanitizing Markdown
renderer and never given to a model.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.settings import SETTING_DEFINITIONS, find_setting_row, resolve_setting, resolve_text
from app.credentials.crypto import CredentialCipher
from app.github.token import github_token_for
from app.storage.models import Setting
from app.updates.changelog import changelog_section
from app.updates.schedule import check_due, is_off
from app.updates.source import is_source
from app.updates.versions import is_newer, newest_tag
from app.version import VERSION

logger = logging.getLogger(__name__)

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
TIMEOUT = 15.0
# GitHub's limit per page; far more Releases than YAAP will have in a long time.
RELEASES_PER_PAGE = 100
MAX_NOTES_CHARS = 20_000
STATE_KEY = "update_state"
PRIVATE_HINT = (
    "YAAP's repository could not be read; a private repository needs a GitHub token (Settings › Git and GitHub)."
)
# Seconds after start before the check at start, so it never competes with starting.
START_DELAY_SECONDS = 5.0


@dataclass(frozen=True)
class UpdateState:
    """What the last check found. `error` is set when the last check failed; the other fields then still hold the
    result of the check before."""

    checked_at: datetime | None = None
    source: str = ""
    etag: str | None = None
    newest_version: str | None = None
    notes: str = ""
    error: str | None = None
    # The newest version a notification was given for, so a version is announced once.
    announced_version: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["checked_at"] = self.checked_at.isoformat() if self.checked_at else None
        return data

    @staticmethod
    def from_json(value: Any) -> "UpdateState":
        if not isinstance(value, dict):
            return UpdateState()
        checked = value.get("checked_at")
        try:
            moment = datetime.fromisoformat(checked) if isinstance(checked, str) else None
        except ValueError:
            moment = None

        def text(key: str) -> str | None:
            found = value.get(key)
            return found if isinstance(found, str) else None

        return UpdateState(
            checked_at=moment,
            source=text("source") or "",
            etag=text("etag"),
            newest_version=text("newest_version"),
            notes=text("notes") or "",
            error=text("error"),
            announced_version=text("announced_version"),
        )

    @property
    def update_available(self) -> bool:
        return self.newest_version is not None and is_newer(self.newest_version, VERSION)

    @property
    def release_url(self) -> str | None:
        """The page of the newest version on github.com."""
        if not self.newest_version or not self.source:
            return None
        return f"https://github.com/{self.source}/releases/tag/v{self.newest_version}"


@dataclass(frozen=True)
class CheckOutcome:
    state: UpdateState
    # True when this check found a version nobody was told about yet: the one notification for that version.
    announce: bool = False


class UpdateService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        cipher: CredentialCipher,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cipher = cipher
        self.transport = transport
        self.lock = threading.Lock()
        self.state: UpdateState | None = None
        self.started_at = datetime.now(UTC)

    # --- State -----------------------------------------------------------------------------------------------

    def current(self) -> UpdateState:
        """The last result: from memory, else from the settings table."""
        if self.state is None:
            with self.session_factory() as session:
                row = find_setting_row(session, STATE_KEY, None)
                self.state = UpdateState.from_json(row.value if row is not None else None)
        return self.state

    def remember(self, state: UpdateState) -> None:
        with self.session_factory() as session:
            row = find_setting_row(session, STATE_KEY, None)
            if row is None:
                row = Setting(key=STATE_KEY, environment_id=None, project_id=None)
                session.add(row)
            row.value = state.to_json()
            row.updated_at = datetime.now(UTC)
            session.commit()
        self.state = state

    @property
    def checking(self) -> bool:
        return self.lock.locked()

    # --- Settings ---------------------------------------------------------------------------------------------

    def schedule(self) -> str:
        with self.session_factory() as session:
            return str(resolve_setting(session, SETTING_DEFINITIONS["update_check_schedule"], None).value)

    def is_off(self) -> bool:
        return is_off(self.schedule())

    def scheduled_check_due(self, now: datetime) -> bool:
        with self.session_factory() as session:
            timezone = resolve_text(session, "timezone", None) or "UTC"
        schedule = self.schedule()
        return not self.checking and check_due(schedule, timezone, self.current().checked_at, self.started_at, now)

    # --- The check --------------------------------------------------------------------------------------------

    def check(self) -> CheckOutcome:
        """Ask GitHub now. A check that runs already is waited for, and its result is returned."""
        if not self.lock.acquire(blocking=False):
            with self.lock:
                return CheckOutcome(self.current())
        try:
            return self.run_check()
        except Exception as error:  # noqa: BLE001 - a failed check must never reach the caller as a crash
            logger.exception("The update check failed.")
            return CheckOutcome(self.fail(self.current(), f"The update check failed: {error}"))
        finally:
            self.lock.release()

    def fail(self, last: UpdateState, message: str) -> UpdateState:
        state = UpdateState(**{**asdict(last), "checked_at": datetime.now(UTC), "error": message})
        self.remember(state)
        return state

    def run_check(self) -> CheckOutcome:
        last = self.current()
        with self.session_factory() as session:
            source = str(resolve_setting(session, SETTING_DEFINITIONS["update_source"], None).value).strip()
            token = github_token_for(session, self.cipher, None)
        if not is_source(source):
            return CheckOutcome(self.fail(last, "The update source must look like owner/repository."))
        # A cached answer belongs to the repository it came from.
        if last.source != source:
            last = UpdateState(announced_version=last.announced_version)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"YAAP/{VERSION}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(transport=self.transport, timeout=TIMEOUT, headers=headers) as http:
            try:
                releases = self.read_releases(http, source, last.etag, token is not None)
                if releases is None:
                    # 304: nothing changed since the last answer.
                    unchanged = {**asdict(last), "checked_at": datetime.now(UTC), "error": None, "source": source}
                    return CheckOutcome(UpdateState(**unchanged))
                names, etag = releases
                newest = newest_tag(names)
                if newest is None:
                    return CheckOutcome(self.fail(last, f"{source} has no GitHub Release of a tag like v1.2.3."))
                known = newest == last.newest_version and last.notes
                read = last.notes if known else self.read_notes(http, source, newest)
            except UpdateCheckError as error:
                return CheckOutcome(self.fail(last, str(error)))
        # Without the ETag the next check asks for the tags again and so reads the notes again.
        notes, etag = ("", None) if read is None else (read, etag)
        announce = is_newer(newest, VERSION) and newest != last.announced_version
        state = UpdateState(
            checked_at=datetime.now(UTC),
            source=source,
            etag=etag,
            newest_version=newest,
            notes=notes,
            error=None,
            announced_version=newest if announce else last.announced_version,
        )
        self.remember(state)
        return CheckOutcome(state, announce)

    def read_releases(
        self, http: httpx.Client, source: str, etag: str | None, with_token: bool
    ) -> tuple[list[str], str | None] | None:
        """The tags of the repository's published GitHub Releases and the ETag of the answer; None when GitHub says
        nothing changed. Releases, not bare tags: a Release is published only after the version's images are, so a
        version is never offered before it can be installed. Drafts and pre-releases are not offered."""
        headers = {"If-None-Match": etag} if etag else {}
        try:
            response = http.get(
                f"{API}/repos/{source}/releases", params={"per_page": RELEASES_PER_PAGE}, headers=headers
            )
        except httpx.HTTPError as error:
            raise UpdateCheckError(f"GitHub cannot be reached: {error}") from error
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return None
        if response.status_code == httpx.codes.NOT_FOUND:
            raise UpdateCheckError(f"{source} was not found on GitHub." if with_token else PRIVATE_HINT)
        if response.status_code != httpx.codes.OK:
            raise UpdateCheckError(f"GitHub answered {response.status_code} when asked for the releases of {source}.")
        try:
            items = response.json()
        except ValueError as error:
            raise UpdateCheckError("GitHub sent an answer YAAP cannot read.") from error
        if not isinstance(items, list):
            raise UpdateCheckError("GitHub sent an answer YAAP cannot read.")
        names = [
            item["tag_name"]
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("tag_name"), str)
            and not item.get("draft")
            and not item.get("prerelease")
        ]
        return names, response.headers.get("ETag")

    def read_notes(self, http: httpx.Client, source: str, version: str) -> str | None:
        """The changelog section of a version (empty without one), or None when the file cannot be read."""
        try:
            response = http.get(f"{RAW}/{source}/v{version}/CHANGELOG.md")
        except httpx.HTTPError:
            return None
        if response.status_code != httpx.codes.OK:
            return None
        return (changelog_section(response.text, version) or "")[:MAX_NOTES_CHARS]


class UpdateCheckError(Exception):
    """A check failed; the message says why and can be shown."""

