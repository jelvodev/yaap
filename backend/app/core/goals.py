"""Goal mode: a conversation keeps working towards an objective over several turns.

`/goal <objective>` in a project conversation starts a goal. After each reply that completes, YAAP sends the next
turn by itself, on the server, so the work goes on when the browser tab is closed. The agent ends the goal with the
finish_goal tool (done or blocked). The goal also stops at its turn or cost limit, when a reply fails or is refused,
when a budget is used up, and when the user pauses or stops it. A reply the user stops pauses the goal.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import Conversation, Goal, Message, UsageRecord

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_COST = 5.0
GOAL_COMMAND = re.compile(r"^/goal(?:\s+(.*))?$", re.DOTALL)
ACTIVE = "active"
PAUSED = "paused"
OPEN_STATUSES = (ACTIVE, PAUSED)


class GoalError(Exception):
    """A goal action that cannot be done; the message is safe to show."""


def goal_objective(text: str) -> str | None:
    """The objective of a `/goal …` message, or None when the message is not one."""
    match = GOAL_COMMAND.match(text.strip())
    if match is None:
        return None
    objective = (match.group(1) or "").strip()
    if not objective:
        raise GoalError("Write the objective after /goal, for example: /goal make the test suite pass.")
    return objective


def start_text(objective: str, max_turns: int) -> str:
    return (
        f"Goal: {objective}\n\n"
        f"Work towards this goal over several turns (at most {max_turns}). After each of your replies YAAP sends "
        "the next turn by itself, so do a meaningful step per turn and say briefly what you did. When the goal is "
        "reached, call finish_goal with status done and a short summary. When you cannot go on without the user "
        "(a decision, access, missing information), call finish_goal with status blocked and say what you need."
    )


def continue_text(objective: str, turn: int, max_turns: int) -> str:
    return (
        f"Continue towards the goal: {objective}\n\n"
        f"This is turn {turn} of at most {max_turns}. Take the next step. Call finish_goal with status done when "
        "the goal is reached, or blocked when you need the user."
    )


def goal_block(text: str, turn: int, max_turns: int) -> dict[str, Any]:
    """A user message's goal part: shown as a marker, sent to the model as text (like a /command)."""
    return {"type": "goal", "name": "goal", "text": text, "turn": turn, "max_turns": max_turns}


def open_goal(session: Session, conversation_id: int) -> Goal | None:
    return session.scalar(
        select(Goal)
        .where(Goal.conversation_id == conversation_id, Goal.status.in_(OPEN_STATUSES))
        .order_by(Goal.id.desc())
    )


def latest_goal(session: Session, conversation_id: int) -> Goal | None:
    return session.scalar(select(Goal).where(Goal.conversation_id == conversation_id).order_by(Goal.id.desc()))


def last_ended_goal(session: Session, conversation_id: int) -> Goal | None:
    """The conversation's newest goal when it has ended (done, blocked, at a limit or stopped), else None. The
    project's current state is refreshed from it."""
    goal = latest_goal(session, conversation_id)
    return goal if goal is not None and goal.status not in OPEN_STATUSES and goal.finished_at is not None else None


def goal_cost(session: Session, goal: Goal) -> float:
    """What the goal has cost so far: charged usage of its conversation since it started."""
    total = session.scalar(
        select(func.coalesce(func.sum(UsageRecord.cost), 0.0)).where(
            UsageRecord.conversation_id == goal.conversation_id, UsageRecord.created_at >= goal.created_at
        )
    )
    return float(total or 0.0)


def create_goal(session: Session, conversation: Conversation, objective: str) -> Goal:
    if conversation.project_id is None:
        raise GoalError("Goal mode works in a project conversation, where the agent has its tools.")
    if open_goal(session, conversation.id) is not None:
        raise GoalError("This conversation already has a goal. Stop it first.")
    goal = Goal(
        conversation_id=conversation.id,
        objective=objective,
        status=ACTIVE,
        max_turns=DEFAULT_MAX_TURNS,
        max_cost=DEFAULT_MAX_COST,
        turns=1,
        created_at=datetime.now(UTC),
    )
    session.add(goal)
    session.flush()
    return goal


def end_goal(goal: Goal, status: str, summary: str | None = None) -> None:
    goal.status = status
    if summary:
        goal.summary = summary
    goal.finished_at = datetime.now(UTC)


@dataclass(frozen=True)
class NextTurn:
    goal_id: int
    turn: int
    max_turns: int
    text: str


def next_turn(
    session: Session, conversation_id: int, resuming: bool = False, goal_id: int | None = None
) -> NextTurn | None:
    """Whether the goal goes on after the reply that just ended; ends it when it may not. Commits.

    Resuming after a pause skips the check of the last reply: a reply the user stopped is what paused it.
    With goal_id, only that goal goes on: the follow-up of a reply belongs to the goal the reply worked for,
    not to a newer goal started in the conversation while the follow-up waited (a scheduled task's next run).
    """
    goal = open_goal(session, conversation_id)
    if goal is None or goal.status != ACTIVE or (goal_id is not None and goal.id != goal_id):
        return None
    conversation = session.get(Conversation, conversation_id)
    head = session.get(Message, conversation.head_message_id) if conversation and conversation.head_message_id else None
    if not resuming and (head is None or head.role != "assistant" or head.status != "complete"):
        end_goal(goal, "stopped", "The last reply did not complete, so the goal stopped.")
    elif goal.turns >= goal.max_turns:
        end_goal(goal, "limit", f"The goal used all {goal.max_turns} turns.")
    elif goal.max_cost > 0 and goal_cost(session, goal) >= goal.max_cost:
        end_goal(goal, "limit", f"The goal reached its cost limit of ${goal.max_cost:.2f}.")
    else:
        goal.turns += 1
        session.commit()
        return NextTurn(goal.id, goal.turns, goal.max_turns, continue_text(goal.objective, goal.turns, goal.max_turns))
    session.commit()
    return None


@dataclass
class GoalHandle:
    """What finish_goal needs: the goal of the run and where to store its end."""

    goal_id: int
    session_factory: sessionmaker[Session]

    def pause(self, reason: str) -> None:
        """Pause the goal, for example when the agent repeats itself."""
        with self.session_factory() as session:
            goal = session.get(Goal, self.goal_id)
            if goal is not None and goal.status == ACTIVE:
                goal.status = PAUSED
                goal.summary = reason
                session.commit()

    def finish(self, status: str, summary: str) -> str:
        with self.session_factory() as session:
            goal = session.get(Goal, self.goal_id)
            if goal is None or goal.status not in OPEN_STATUSES:
                return "This goal has already ended."
            end_goal(goal, status, summary)
            session.commit()
        return f"The goal is marked {status}. YAAP sends no further turns; finish this reply for the user."
