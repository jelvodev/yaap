"""The facts behind *Waiting for me*, and a summary per project with its board on.

Reads the board's index, the plans, the reviews, the conversations and the runs that wait for an approval; never git.
The board service keeps the index in step with the card files on every write and every opening, so this is cheap
enough to poll. Which facts wait for the user is decided by the pure `app.board.waiting`.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runs import AgentRun, PendingApproval, RunRegistry
from app.board.models import BoardCardConversation, BoardCardRow, BoardState, BoardVersionRow
from app.board.switch import board_enabled
from app.board.versions import VersionState
from app.board.views import CardView, Related, card_view, current_version, version_views
from app.board.waiting import ApprovalFacts, BuildFacts, CardWaitFacts, WaitingFacts, WaitingItem, waiting_items
from app.core.budget import month_start
from app.plans.models import PlanRecord
from app.plans.progress import build_info_from_json
from app.storage.models import Conversation, Project, Review, UsageRecord


@dataclass(frozen=True)
class ProjectSummary:
    """A project with its board on: its current version's progress, what builds, waits for another card or was
    escalated to the main model, and what its conversations cost this month."""

    project_id: int
    name: str
    version: str | None
    done: int
    total: int
    building: int
    blocked: int
    escalated: int
    cost_month: float


@dataclass(frozen=True)
class WaitingOverview:
    items: tuple[WaitingItem, ...]
    projects: tuple[ProjectSummary, ...]
    # The server's time of the answer: what the browser remembers as the moment the user last looked.
    now: datetime


@dataclass(frozen=True)
class BoardFacts:
    cards: tuple[CardWaitFacts, ...]
    summary: ProjectSummary


@dataclass
class WaitingService:
    session_factory: sessionmaker[Session]
    runs: RunRegistry

    def overview(self, environment_id: int | None, seen: datetime | None) -> WaitingOverview:
        """What waits for the user in the environment's projects (every environment when None), with the Inbox cards
        new since `seen`, and a summary per project with its board on."""
        now = datetime.now(UTC)
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        with self.session_factory() as session:
            query = select(Project).order_by(Project.name)
            if environment_id is not None:
                query = query.where(Project.environment_id == environment_id)
            projects = list(session.scalars(query))
            boards = [project for project in projects if board_on(session, project)]
            costs = month_costs(session, [project.id for project in boards], now)
            per_board = [board_facts(session, project, costs.get(project.id, 0.0)) for project in boards]
            approvals = approval_facts(session, self.live_approvals(environment_id), {p.id for p in boards})
            builds = interrupted_builds(session, [project.id for project in projects])
        facts = WaitingFacts(
            cards=tuple(card for board in per_board for card in board.cards),
            approvals=tuple(approvals),
            builds=tuple(builds),
            seen=seen,
        )
        return WaitingOverview(tuple(waiting_items(facts)), tuple(board.summary for board in per_board), now)

    def live_approvals(self, environment_id: int | None) -> list[tuple[AgentRun, PendingApproval]]:
        """The approvals runs wait for now; copies of the registry's maps, since runs come and go meanwhile."""
        return [
            (run, approval)
            for run in list(self.runs.runs.values())
            if environment_id is None or run.environment_id == environment_id
            for approval in list(run.state.approvals.values())
            if not approval.future.done()
        ]


def board_on(session: Session, project: Project) -> bool:
    """A project shows on the overview when its board is switched on and started."""
    if session.get(BoardState, project.id) is None:
        return False
    return board_enabled(session, project.environment_id, project.id).enabled


def models_of(session: Session, conversation_ids: Iterable[int]) -> dict[int, str]:
    ids = set(conversation_ids)
    if not ids:
        return {}
    rows = session.execute(select(Conversation.id, Conversation.model).where(Conversation.id.in_(ids)))
    return {conversation_id: model for conversation_id, model in rows}


def latest_conversations(session: Session, project_id: int) -> dict[int, int]:
    """The most recently linked conversation per card of the project."""
    rows = session.execute(
        select(BoardCardConversation.card_id, BoardCardConversation.conversation_id)
        .where(BoardCardConversation.project_id == project_id)
        .order_by(BoardCardConversation.created_at, BoardCardConversation.id)
    )
    return {card_id: conversation_id for card_id, conversation_id in rows}


def plan_changed(plan: PlanRecord) -> datetime:
    return plan.finished_at or plan.approved_at or plan.created_at


def board_facts(session: Session, project: Project, cost_month: float) -> BoardFacts:
    """The cards of one board as the overview needs them, and the project's summary."""
    rows = list(session.scalars(select(BoardCardRow).where(BoardCardRow.project_id == project.id)))
    related = Related.read(session, project.id, rows)
    views = [card_view(row, related) for row in rows if not row.broken]
    plans = {
        plan.id: plan
        for plan in session.scalars(select(PlanRecord).where(PlanRecord.id.in_({v.plan_id for v in views})))
    }
    reviews = {
        review.id: review
        for review in session.scalars(select(Review).where(Review.id.in_({v.review_id for v in views})))
    }
    latest = latest_conversations(session, project.id)
    models = models_of(session, [*latest.values(), *(plan.conversation_id for plan in plans.values())])
    origins = {row.card_id: row.origin_type for row in rows}
    cards: list[CardWaitFacts] = []
    for view in views:
        plan = plans.get(view.plan_id) if view.plan_id is not None else None
        review = reviews.get(view.review_id) if view.review_id is not None else None
        conversation_id = latest.get(view.id)
        cards.append(
            CardWaitFacts(
                project_id=project.id,
                card_id=view.id,
                title=view.title,
                next=view.next,
                origin_type=origins.get(view.id),
                updated=view.updated,
                plan_conversation_id=plan.conversation_id if plan else None,
                plan_model=models.get(plan.conversation_id) if plan else None,
                plan_changed=plan_changed(plan) if plan else None,
                review_model=review.reviewer_model if review else None,
                review_finished=review.finished_at if review else None,
                latest_conversation_id=conversation_id,
                latest_model=models.get(conversation_id) if conversation_id is not None else None,
            )
        )
    escalated = sum(1 for view in views if view.column != "done" and was_escalated(plans.get(view.plan_id or 0)))
    summary = project_summary(session, project, views, escalated, cost_month)
    return BoardFacts(tuple(cards), summary)


def was_escalated(plan: PlanRecord | None) -> bool:
    """Whether a step of the plan's build was taken over by the main model."""
    if plan is None:
        return False
    return any("main" in roles for roles in build_info_from_json(plan.body).attempts.values())


def project_summary(
    session: Session, project: Project, views: Sequence[CardView], escalated: int, cost_month: float
) -> ProjectSummary:
    version_rows = list(session.scalars(select(BoardVersionRow).where(BoardVersionRow.project_id == project.id)))
    states = {row.name: cast(VersionState | None, row.state) for row in version_rows}
    versions = [replace(view, state=states.get(view.name)) for view in version_views(version_rows, {}, views)]
    name = current_version(versions)
    current = next((version for version in versions if version.name == name), None)
    return ProjectSummary(
        project_id=project.id,
        name=project.name,
        version=name,
        done=current.done if current else 0,
        total=current.total if current else 0,
        building=sum(1 for view in views if view.plan_status in ("building", "queued")),
        blocked=sum(1 for view in views if view.blocked_by and view.column != "done"),
        escalated=escalated,
        cost_month=cost_month,
    )


def month_costs(session: Session, project_ids: Sequence[int], now: datetime) -> dict[int, float]:
    """Charged usage of each project's conversations since the start of the month."""
    if not project_ids:
        return {}
    rows = session.execute(
        select(Conversation.project_id, func.coalesce(func.sum(UsageRecord.cost), 0.0))
        .join(Conversation, UsageRecord.conversation_id == Conversation.id)
        .where(Conversation.project_id.in_(project_ids), UsageRecord.created_at >= month_start(now))
        .group_by(Conversation.project_id)
    )
    return {project_id: round(float(total or 0.0), 2) for project_id, total in rows if project_id is not None}


def approval_facts(
    session: Session, pending: Sequence[tuple[AgentRun, PendingApproval]], board_projects: set[int]
) -> list[ApprovalFacts]:
    """Every pending approval; one in a conversation of a card names the card while its project has the board on."""
    conversation_ids = {run.conversation_id for run, _approval in pending}
    if not conversation_ids:
        return []
    models = models_of(session, conversation_ids)
    links = session.execute(
        select(BoardCardConversation.conversation_id, BoardCardConversation.project_id, BoardCardConversation.card_id)
        .where(BoardCardConversation.conversation_id.in_(conversation_ids))
    )
    cards = {conversation_id: card_id for conversation_id, project_id, card_id in links if project_id in board_projects}
    return [
        ApprovalFacts(
            approval_id=approval.id,
            conversation_id=run.conversation_id,
            tool=approval.tool,
            summary=approval.summary,
            project_id=run.project_id,
            card_id=cards.get(run.conversation_id),
            model=models.get(run.conversation_id),
        )
        for run, approval in pending
    ]


def interrupted_builds(session: Session, project_ids: Sequence[int]) -> list[BuildFacts]:
    """Interrupted builds of plans that belong to no card; a card's build waits through the card's next step."""
    if not project_ids:
        return []
    plans = list(
        session.scalars(
            select(PlanRecord).where(
                PlanRecord.project_id.in_(project_ids),
                PlanRecord.status == "interrupted",
                PlanRecord.card_id.is_(None),
            )
        )
    )
    models = models_of(session, [plan.conversation_id for plan in plans])
    return [
        BuildFacts(
            plan_id=plan.id,
            project_id=plan.project_id,
            conversation_id=plan.conversation_id,
            title=plan.title,
            model=models.get(plan.conversation_id),
            since=plan_changed(plan),
        )
        for plan in plans
    ]
