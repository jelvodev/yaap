"""Shell sessions of a conversation.

`run_command` with a `session` name keeps the working folder and exported variables between commands, also across
replies: a session belongs to the conversation, not to one reply. How a command restores and saves the state is the
target's business (app/targets/sessions.py); this module keeps the sessions themselves, one row each in
`shell_sessions`, with the state of container sessions in `data/shells/<conversation>/<token>/`.

A session ends after two hours without use, when its conversation is deleted, or with *End session* in the project
panel. Using a session after it ended starts it again, empty. A session also starts again when the conversation now
works somewhere else (another target, or the container) than where the session was made. Sessions and their state
survive a YAAP restart, since nothing keeps running between commands.
"""

import asyncio
import re
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.toolkit import ToolError
from app.storage.models import Conversation, ShellSessionRecord
from app.targets.sessions import ShellState

SESSION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SESSION_IDLE = timedelta(hours=2)
CONTAINER_PLACE = "container"
SHELLS_FOLDER = "shells"


def shells_dir(data_dir: Path) -> Path:
    return data_dir / SHELLS_FOLDER


def remove_conversation_shells(data_dir: Path, conversation_id: int) -> None:
    """The state folders of a conversation's container sessions; its rows go with the conversation."""
    shutil.rmtree(shells_dir(data_dir) / str(conversation_id), ignore_errors=True)


def check_name(name: str) -> None:
    if not SESSION_NAME.match(name):
        raise ToolError("A session name uses letters, digits, - and _ (at most 40).")


class ShellSessions:
    """The shell sessions of all conversations. Bound to the database and the data folder when YAAP starts."""

    def __init__(self) -> None:
        self.session_factory: sessionmaker[Session] | None = None
        self.data_dir: Path | None = None

    def bind(self, session_factory: sessionmaker[Session], data_dir: Path) -> None:
        self.session_factory = session_factory
        self.data_dir = data_dir

    def factory(self) -> sessionmaker[Session]:
        if self.session_factory is None:
            raise ToolError("Shell sessions are not available here.")
        return self.session_factory

    def folder(self, row: ShellSessionRecord) -> Path | None:
        if row.place != CONTAINER_PLACE or self.data_dir is None:
            return None
        return shells_dir(self.data_dir) / str(row.conversation_id) / row.token

    def state(self, row: ShellSessionRecord, save: bool) -> ShellState:
        return ShellState(row.token, self.folder(row), save)

    def open(self, conversation_id: int, name: str, place: str, create: bool) -> ShellSessionRecord | None:
        """The conversation's session `name` where it works now, marked as used; started when `create` is set.

        A session that ended by idleness, or that was made somewhere else, is gone and starts again empty.
        """
        check_name(name)
        for _attempt in range(2):
            with self.factory()() as session:
                row = session.scalar(
                    select(ShellSessionRecord).where(
                        ShellSessionRecord.conversation_id == conversation_id, ShellSessionRecord.name == name
                    )
                )
                now = datetime.now(UTC)
                if row is not None and (expired(row, now) or row.place != place):
                    self.remove_state(row)
                    session.delete(row)
                    session.flush()
                    row = None
                if row is None and not create:
                    session.commit()
                    return None
                if row is None:
                    row = ShellSessionRecord(
                        conversation_id=conversation_id, name=name, place=place, token=secrets.token_hex(8)
                    )
                    session.add(row)
                row.last_used_at = now
                try:
                    session.commit()
                except IntegrityError:
                    # Another run of the conversation (a sub-run) started the same session at this moment.
                    session.rollback()
                    continue
                return row
        raise ToolError(f"The session '{name}' could not be opened; try again.")

    def remove_state(self, row: ShellSessionRecord) -> None:
        """Remove what the session kept in the container. A state file on a target stays until it is a day old;
        a session that starts again gets a new token, so it never reads it."""
        folder = self.folder(row)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

    def end(self, session: Session, row: ShellSessionRecord) -> None:
        self.remove_state(row)
        session.delete(row)
        session.commit()

    def for_project(self, session: Session, project_id: int) -> list[tuple[ShellSessionRecord, Conversation]]:
        """The open sessions of a project's conversations, most recently used first."""
        now = datetime.now(UTC)
        query = (
            select(ShellSessionRecord, Conversation)
            .join(Conversation, Conversation.id == ShellSessionRecord.conversation_id)
            .where(Conversation.project_id == project_id)
            .order_by(ShellSessionRecord.last_used_at.desc())
        )
        return [(row, conversation) for row, conversation in session.execute(query).all() if not expired(row, now)]

    def end_idle(self) -> int:
        """End the sessions unused for two hours, and remove state folders no session owns any more."""
        if self.session_factory is None:
            return 0
        now = datetime.now(UTC)
        with self.session_factory() as session:
            rows = list(session.scalars(select(ShellSessionRecord)))
            idle = [row for row in rows if expired(row, now)]
            for row in idle:
                self.remove_state(row)
            if idle:
                session.execute(delete(ShellSessionRecord).where(ShellSessionRecord.id.in_([row.id for row in idle])))
                session.commit()
            kept = {(str(row.conversation_id), row.token) for row in rows if row not in idle}
        self.remove_strays(kept)
        return len(idle)

    def remove_strays(self, kept: set[tuple[str, str]]) -> None:
        """Folders of sessions that are gone (a conversation removed some other way); only old ones, so a session
        started during this pass keeps its folder."""
        if self.data_dir is None or not shells_dir(self.data_dir).is_dir():
            return
        oldest = (datetime.now(UTC) - SESSION_IDLE).timestamp()
        for conversation in shells_dir(self.data_dir).iterdir():
            for folder in conversation.iterdir() if conversation.is_dir() else []:
                if (conversation.name, folder.name) not in kept and folder.stat().st_mtime < oldest:
                    shutil.rmtree(folder, ignore_errors=True)
            if conversation.is_dir() and not any(conversation.iterdir()):
                conversation.rmdir()

    async def end_idle_periodically(self, interval: float = 300) -> None:
        while True:
            await asyncio.sleep(interval)
            await anyio.to_thread.run_sync(self.end_idle)


def expired(row: ShellSessionRecord, now: datetime) -> bool:
    return now - row.last_used_at > SESSION_IDLE
