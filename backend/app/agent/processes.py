"""Background processes. Shell sessions are in shells.py.

`start_process` starts a command that keeps running after the reply, such as a dev server. YAAP keeps its output
(the last 1 MB) so `read_process_output` can return what is new. A process ends on `stop_process`, from the project
page, or after an hour without activity in its conversation. Each process is a row in `processes`, so the project
page also shows recently ended ones; a YAAP restart ends every process.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agent.toolkit import ToolError
from app.core.subprocess_env import safe_subprocess_env
from app.targets.base import CommandExecutor
from app.targets.sessions import ShellState
from app.targets.ssh import SshTarget
from app.storage.models import ProcessRecord

PROCESS_IDLE_SECONDS = 60 * 60
MAX_BUFFER_BYTES = 1024 * 1024
MAX_READ_CHARS = 30_000
MAX_RUNNING_PER_PROJECT = 8
STOP_GRACE_SECONDS = 5


# --- Background processes ------------------------------------------------------------------------------------------


@dataclass
class ManagedProcess:
    id: int
    environment_id: int
    project_id: int | None
    conversation_id: int | None
    command: str
    port: int | None
    started_at: datetime
    process: asyncio.subprocess.Process
    buffer: bytearray = field(default_factory=bytearray)
    # Absolute offset of buffer[0] in all output, and how far the agent has read.
    buffer_start: int = 0
    read_offset: int = 0
    last_touched: float = field(default_factory=time.monotonic)
    status: str = "running"
    exit_code: int | None = None
    ended_reason: str | None = None
    reader: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    # Where it runs: the runner, a sandbox or a computer over SSH; None for YAAP's own process. The preview reaches
    # its port the same way.
    target: SshTarget | None = None

    @property
    def total(self) -> int:
        return self.buffer_start + len(self.buffer)

    def append(self, chunk: bytes) -> None:
        self.buffer += chunk
        overflow = len(self.buffer) - MAX_BUFFER_BYTES
        if overflow > 0:
            del self.buffer[:overflow]
            self.buffer_start += overflow

    def tail(self, chars: int = 2000) -> str:
        return bytes(self.buffer[-chars * 2 :]).decode(errors="replace")[-chars:]

    def describe(self) -> str:
        state = self.status if self.exit_code is None else f"{self.status}, exit code {self.exit_code}"
        port = f", port {self.port}" if self.port else ""
        return f"Process {self.id} ({state}{port}): {self.command}"


class ProcessManager:
    def __init__(self) -> None:
        self.processes: dict[int, ManagedProcess] = {}
        self.session_factory: sessionmaker[Session] | None = None
        self.next_id = 1

    def bind(self, session_factory: sessionmaker[Session]) -> None:
        """Use the database; processes that were running before a restart are gone, so mark them ended."""
        self.session_factory = session_factory
        with session_factory() as session:
            session.execute(
                update(ProcessRecord)
                .where(ProcessRecord.status == "running")
                .values(status="ended", ended_reason="YAAP restarted", ended_at=datetime.now(UTC))
            )
            session.commit()

    def record(self, values: dict[str, Any]) -> int:
        if self.session_factory is None:
            self.next_id += 1
            return self.next_id - 1
        with self.session_factory() as session:
            row = ProcessRecord(**values)
            session.add(row)
            session.commit()
            return row.id

    def save_end(self, managed: ManagedProcess) -> None:
        if self.session_factory is None:
            return
        with self.session_factory() as session:
            row = session.get(ProcessRecord, managed.id)
            if row is not None:
                row.status = managed.status
                row.exit_code = managed.exit_code
                row.ended_reason = managed.ended_reason
                row.ended_at = datetime.now(UTC)
                row.output_tail = managed.tail()
                session.commit()

    def running_in_project(self, project_id: int | None) -> list[ManagedProcess]:
        return [p for p in self.processes.values() if p.project_id == project_id and p.status == "running"]

    async def start(
        self,
        *,
        command: str,
        executor: CommandExecutor,
        env: dict[str, str],
        environment_id: int,
        project_id: int | None,
        conversation_id: int | None,
        port: int | None = None,
        session: ShellState | None = None,
        target: SshTarget | None = None,
    ) -> ManagedProcess:
        if len(self.running_in_project(project_id)) >= MAX_RUNNING_PER_PROJECT:
            raise ToolError(f"{MAX_RUNNING_PER_PROJECT} processes already run in this project; stop one first.")
        process = await executor.spawn(command, env, session)
        started_at = datetime.now(UTC)
        process_id = self.record(
            {
                "environment_id": environment_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "command": command,
                "port": port,
                "status": "running",
                "started_at": started_at,
            }
        )
        managed = ManagedProcess(
            process_id, environment_id, project_id, conversation_id, command, port, started_at, process, target=target
        )
        self.processes[process_id] = managed
        managed.reader = asyncio.create_task(self.follow(managed))
        return managed

    async def follow(self, managed: ManagedProcess) -> None:
        assert managed.process.stdout is not None
        while chunk := await managed.process.stdout.read(65536):
            managed.append(chunk)
        managed.exit_code = await managed.process.wait()
        if managed.status == "running":
            managed.status = "exited"
        self.save_end(managed)
        managed.done.set()

    def find(self, process_id: int, project_id: int | None) -> ManagedProcess:
        managed = self.processes.get(process_id)
        if managed is None or managed.project_id != project_id:
            raise ToolError(f"No process {process_id} in this project. list_processes shows the processes.")
        return managed

    def read(self, managed: ManagedProcess) -> str:
        managed.last_touched = time.monotonic()
        skipped = max(managed.buffer_start - managed.read_offset, 0)
        start = max(managed.read_offset - managed.buffer_start, 0)
        text = bytes(managed.buffer[start:]).decode(errors="replace")
        managed.read_offset = managed.total
        notes = []
        if skipped:
            notes.append(f"[{skipped} older bytes were dropped.]")
        if len(text) > MAX_READ_CHARS:
            notes.append(f"[{len(text) - MAX_READ_CHARS} characters skipped; the newest output follows.]")
            text = text[-MAX_READ_CHARS:]
        body = "\n".join([*notes, text.rstrip()]) if text.strip() else "\n".join([*notes, "No new output."])
        return f"{managed.describe()}\n{body}"

    async def stop(self, managed: ManagedProcess, reason: str) -> ManagedProcess:
        if managed.status != "running":
            return managed
        managed.status = "stopped"
        managed.ended_reason = reason
        self.signal(managed, signal.SIGTERM)
        try:
            await asyncio.wait_for(managed.done.wait(), timeout=STOP_GRACE_SECONDS)
        except TimeoutError:
            self.signal(managed, getattr(signal, "SIGKILL", signal.SIGTERM))
            try:
                await asyncio.wait_for(managed.done.wait(), timeout=STOP_GRACE_SECONDS)
            except TimeoutError:
                pass
        return managed

    @staticmethod
    def signal(managed: ManagedProcess, number: int) -> None:
        if managed.process.pid < 0:
            # A process over SSH (a computer, a sandbox, the runner) has no local process group: closing its channel
            # ends it and what it started.
            managed.process.kill()
            return
        try:
            if sys.platform != "win32":
                os.killpg(managed.process.pid, number)
            else:
                # The shell's children (the actual server) go too.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(managed.process.pid)],
                    capture_output=True,
                    check=False,
                    env=safe_subprocess_env(),
                )
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def touch_conversation(self, conversation_id: int) -> None:
        for managed in self.processes.values():
            if managed.conversation_id == conversation_id:
                managed.last_touched = time.monotonic()

    async def stop_idle(self) -> None:
        now = time.monotonic()
        for managed in list(self.processes.values()):
            if now - managed.last_touched <= PROCESS_IDLE_SECONDS:
                continue
            if managed.status == "running":
                await self.stop(managed, "an hour without activity in its conversation")
            else:
                # Ended an hour ago and not read since: its row keeps the last output for the project page.
                self.processes.pop(managed.id, None)

    async def stop_idle_periodically(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.stop_idle()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.stop(managed, "YAAP stopped") for managed in self.processes.values() if managed.status == "running")
        )

    def rows(self, session: Session, project_id: int, limit: int = 20) -> list[ProcessRecord]:
        query = select(ProcessRecord).where(ProcessRecord.project_id == project_id).order_by(ProcessRecord.id.desc())
        return list(session.scalars(query.limit(limit)))
