"""The work YAAP starts by itself on a schedule or an interval, for the page that lists it.

The list describes what the scheduler and the periodic loops in main.py do; a setting that controls an entry is read
here, never changed.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.processes import PROCESS_IDLE_SECONDS
from app.agent.shells import SESSION_IDLE
from app.backups.runs import read_schedule
from app.browser.service import IDLE_SECONDS as BROWSER_IDLE_SECONDS
from app.core.search_index import PASS_INTERVAL_SECONDS
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.git.periodic import FETCH_INTERVAL_SECONDS
from app.mcp_client.manager import IDLE_SECONDS as MCP_IDLE_SECONDS
from app.sandbox.service import SANDBOX_IDLE
from app.scheduling.service import LOOP_SECONDS
from app.storage.models import Project, ScheduledTask
from app.updates.schedule import is_off

DAILY_CRON = re.compile(r"^(\d{1,2}) (\d{1,2}) \* \* \*$")


@dataclass(frozen=True)
class ScheduledWork:
    name: str
    description: str
    # The schedule or interval in words.
    schedule: str
    enabled: bool
    # The setting that switches or times the entry; None when it has none.
    setting_key: str | None


def describe_cron(cron: str) -> str:
    """A cron expression in words when it is a daily time, else the expression itself."""
    daily = DAILY_CRON.match(cron.strip())
    if daily is not None:
        return f"Every day at {int(daily.group(2)):02d}:{int(daily.group(1)):02d}"
    return f"On the schedule {cron.strip()}"


def describe_interval(seconds: float) -> str:
    """An interval in words: minutes below an hour, hours otherwise."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"Every {int(seconds)} seconds"
    if minutes == 1:
        return "Every minute"
    if minutes < 60:
        return f"Every {minutes} minutes"
    return "Every hour" if minutes == 60 else f"Every {minutes // 60} hours"


def idle_after(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"After {minutes} minutes without use"
    return "After 1 hour without use" if minutes == 60 else f"After {minutes // 60} hours without use"


def active_scheduled_tasks(session: Session, environment_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ScheduledTask)
            .join(Project, Project.id == ScheduledTask.project_id)
            .where(Project.environment_id == environment_id, ScheduledTask.enabled.is_(True))
        )
        or 0
    )


def scheduled_work(session: Session, environment_id: int) -> list[ScheduledWork]:
    backup = read_schedule(session)
    update = str(resolve_setting(session, SETTING_DEFINITIONS["update_check_schedule"], None).value)
    embedding = resolve_setting(session, SETTING_DEFINITIONS["embedding_model"], None).value
    loop = describe_interval(LOOP_SECONDS)
    return [
        ScheduledWork(
            "Backups", "Copies YAAP's data to the backup destinations and removes the oldest backups.",
            describe_cron(str(backup["cron"])), bool(backup["enabled"]), "backup_schedule",
        ),
        ScheduledWork(
            "Update check", "Asks GitHub whether a newer version of YAAP exists.",
            "A few seconds after start and " + ("never again" if is_off(update) else describe_cron(update).lower()),
            not is_off(update), "update_check_schedule",
        ),
        ScheduledWork(
            "Scheduled tasks", "Starts the project tasks that are due and ends the finished runs.", loop,
            active_scheduled_tasks(session, environment_id) > 0, None,
        ),
        ScheduledWork(
            "Housekeeping",
            "Removes old tool output, traces, checkpoints, eval workspaces, vault uses and audit rows.",
            loop, True, None,
        ),
        ScheduledWork(
            "Fetching repositories",
            "Fetches each repository that has a remote, so the Git panel shows what changed elsewhere.",
            describe_interval(FETCH_INTERVAL_SECONDS), True, None,
        ),
        ScheduledWork(
            "Search index", "Indexes new conversations, memory and Blueprint files for search by meaning.",
            describe_interval(PASS_INTERVAL_SECONDS), embedding != "off", "embedding_model",
        ),
        *idle_stops(),
    ]


def idle_stops() -> list[ScheduledWork]:
    stops = [
        ("Idle background processes", "Stops processes the agent started.", PROCESS_IDLE_SECONDS),
        ("Idle shell sessions", "Ends the agent's shell sessions.", SESSION_IDLE.total_seconds()),
        ("Idle MCP servers", "Stops MCP servers that run over stdio.", MCP_IDLE_SECONDS),
        ("Idle browsers", "Stops a conversation's headless browser.", BROWSER_IDLE_SECONDS),
        ("Idle sandboxes and runners", "Stops a sandbox or runner that runs no process.", SANDBOX_IDLE.total_seconds()),
    ]
    return [
        ScheduledWork(name, description, idle_after(seconds), True, None)
        for name, description, seconds in stops
    ]
