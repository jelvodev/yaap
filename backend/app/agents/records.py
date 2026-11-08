"""Stored agent runs: one row per reply, and one per delegated sub-run under it.

A reply on a connection that runs no agents gets a row too (`start_reply_record`), so the model calls of every reply
have a run to be recorded under as steps (app/tracing).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runs import AgentRun
from app.storage.models import AgentRunRecord, Conversation, Project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunPage:
    """Which runs of an environment to list, newest first: a page of `limit` runs with an id below `before`."""

    agent: str | None = None
    # With sub-runs, they are listed beside their run; without, only the runs themselves (a sub-run is part of its
    # run's trace), and with an agent, its runs at any depth.
    sub_runs: bool = True
    before: int | None = None
    limit: int = 50


RunRow = tuple[AgentRunRecord, str, str | None]


def list_run_records(session: Session, environment_id: int, page: RunPage) -> list[RunRow]:
    """A page of the environment's runs, newest first, each with its conversation's title and project's name."""
    query = (
        select(AgentRunRecord, Conversation.title, Project.name)
        .join(Conversation, Conversation.id == AgentRunRecord.conversation_id)
        .outerjoin(Project, Project.id == AgentRunRecord.project_id)
        .where(AgentRunRecord.environment_id == environment_id)
    )
    if page.agent and page.sub_runs:
        parents = select(AgentRunRecord.id).where(
            AgentRunRecord.environment_id == environment_id, AgentRunRecord.agent == page.agent
        )
        query = query.where(or_(AgentRunRecord.agent == page.agent, AgentRunRecord.parent_run_id.in_(parents)))
    elif page.agent:
        query = query.where(AgentRunRecord.agent == page.agent)
    elif not page.sub_runs:
        query = query.where(AgentRunRecord.parent_run_id.is_(None))
    if page.before is not None:
        query = query.where(AgentRunRecord.id < page.before)
    rows = session.execute(query.order_by(AgentRunRecord.id.desc()).limit(page.limit))
    return [(record, title, project_name) for record, title, project_name in rows]


def start_run_record(session: Session, run: AgentRun, model: str) -> int:
    record = AgentRunRecord(
        environment_id=run.environment_id,
        conversation_id=run.conversation_id,
        project_id=run.project_id,
        agent=run.agent,
        model=model,
        status="running",
        approvals=[],
        started_at=datetime.now(UTC),
    )
    session.add(record)
    session.commit()
    return record.id


def start_reply_record(session: Session, conversation: Conversation, model: str | None = None) -> int:
    """The run record of a reply without an agent run (a connection that runs no agents); model is the reply's when
    its preset routed it to another than the conversation's."""
    record = AgentRunRecord(
        environment_id=conversation.environment_id,
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        agent=conversation.agent,
        model=model or conversation.model,
        status="running",
        approvals=[],
        started_at=datetime.now(UTC),
    )
    session.add(record)
    session.commit()
    return record.id


def finish_run_record(
    session_factory: sessionmaker[Session], run: AgentRun, message_id: int | None, status: str, tool_calls: int
) -> None:
    if run.state.record_id is not None:
        finish_record(session_factory, run.state.record_id, message_id, status, tool_calls, run.state.approval_log)


def finish_record(
    session_factory: sessionmaker[Session],
    record_id: int,
    message_id: int | None,
    status: str,
    tool_calls: int,
    approvals: list[dict[str, str]] | None = None,
) -> None:
    try:
        with session_factory() as session:
            record = session.get(AgentRunRecord, record_id)
            if record is None:
                return
            record.message_id = message_id
            record.status = status
            record.tool_calls = tool_calls
            record.approvals = list(approvals or [])
            record.finished_at = datetime.now(UTC)
            session.commit()
    except Exception:  # noqa: BLE001 - the reply is stored; a missing run record must not break the stream
        logger.warning("Storing the agent run record failed.", exc_info=True)


def close_stale_runs(session: Session) -> int:
    """Runs and sub-runs still running when YAAP starts were cut off by the restart: every run lives in YAAP's own
    process, which no longer has them. They end as incomplete, as a stopped reply's run does."""
    result = session.execute(
        update(AgentRunRecord)
        .where(AgentRunRecord.status == "running")
        .values(status="incomplete", finished_at=datetime.now(UTC))
    )
    session.commit()
    return result.rowcount
