"""What the board shows: cards from the index with their derived state and next step, versions with progress and
cost, and the board as a whole. Built from index rows plus the plans and reviews they link to; nothing here writes."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.board.card import COLUMNS, Card, ChecklistItem, Column, Kind, OriginType
from app.board.costs import BudgetLevel, conversation_costs, version_summary
from app.board.flow import CardFacts, CardState, NextStep, ReviewState, card_state, next_step, review_from_status
from app.board.links import conversation_ids_by_card
from app.board.models import BoardCardRow, BoardVersionRow, ConversationRole, StorageKind
from app.board.versions import BrokenVersion, Version, VersionState
from app.core.settings import Source
from app.plans.model import PlanStatus
from app.plans.models import PlanRecord
from app.storage.models import Review

BoardStateName = Literal["off", "not_started", "ready", "unavailable"]
# A column with more cards is cut off in the answer; its count stays right.
MAX_COLUMN_CARDS = 500


@dataclass(frozen=True)
class CardView:
    id: int
    path: str
    title: str
    kind: Kind | None
    column: Column | None
    version: str | None
    origin_type: OriginType | None
    origin_ref: str | None
    waits_for: tuple[int, ...]
    blocked_by: tuple[int, ...]
    open_questions: int
    cost_usd: float
    plan_id: int | None
    plan_status: PlanStatus | None
    review_id: int | None
    review: ReviewState
    broken: bool
    error: str | None
    updated: datetime | None
    state: CardState
    next: NextStep


@dataclass(frozen=True)
class LinkedConversation:
    id: int
    title: str
    role: ConversationRole


@dataclass(frozen=True)
class CardDetail:
    view: CardView
    # The card as read from its file; None when the file is broken.
    card: Card | None
    # A broken file's text (None when too large or not text), shown with its error.
    broken_text: str | None
    definition_items: tuple[ChecklistItem, ...]
    conversations: tuple[LinkedConversation, ...]
    location: str


@dataclass(frozen=True)
class VersionView:
    name: str
    state: VersionState | None
    budget_usd: float | None
    notes: str
    acceptance_criteria: str
    done: int
    total: int
    cost_usd: float
    level: BudgetLevel
    error: str | None = None


@dataclass(frozen=True)
class ColumnView:
    cards: tuple[CardView, ...]
    count: int
    truncated: bool


@dataclass(frozen=True)
class PushView:
    allowed: bool
    # Whether the board's head is the commit YAAP pushed last.
    pushed: bool


@dataclass(frozen=True)
class BoardView:
    enabled: bool
    enabled_source: Source
    state: BoardStateName
    storage: StorageKind | None = None
    location: str | None = None
    head: str | None = None
    unchanged: bool = False
    columns: Mapping[Column, ColumnView] | None = None
    broken: tuple[CardView, ...] = ()
    versions: tuple[VersionView, ...] = ()
    current_version: str | None = None
    push: PushView | None = None
    message: str | None = None


@dataclass(frozen=True)
class Related:
    """What the index rows link to, read in a few queries for a whole board."""

    plans: Mapping[int, PlanStatus]
    reviews: Mapping[int, str]
    columns: Mapping[int, Column | None]
    costs: Mapping[int, float]

    @classmethod
    def read(cls, session: Session, project_id: int, rows: Sequence[BoardCardRow]) -> "Related":
        plan_ids = {row.plan_id for row in rows if row.plan_id is not None}
        review_ids = {row.review_id for row in rows if row.review_id is not None}
        plan_rows = session.execute(select(PlanRecord.id, PlanRecord.status).where(PlanRecord.id.in_(plan_ids)))
        review_rows = session.execute(select(Review.id, Review.status).where(Review.id.in_(review_ids)))
        plans = {plan_id: status for plan_id, status in plan_rows}
        reviews = {review_id: status for review_id, status in review_rows}
        all_columns = session.execute(
            select(BoardCardRow.card_id, BoardCardRow.board_column).where(BoardCardRow.project_id == project_id)
        )
        linked = conversation_ids_by_card(session, project_id)
        per_conversation = conversation_costs(session, [one for ids in linked.values() for one in ids])
        costs = {
            card_id: round(sum(per_conversation.get(one, 0.0) for one in ids), 2) for card_id, ids in linked.items()
        }
        return cls(plans, reviews, {card_id: cast(Column | None, column) for card_id, column in all_columns}, costs)


def card_facts(row: BoardCardRow, related: Related) -> CardFacts:
    blocked = tuple(card_id for card_id in row.waits_for if related.columns.get(card_id, "done") != "done")
    return CardFacts(
        column=cast(Column, row.board_column or "inbox"),
        plan_status=related.plans.get(row.plan_id) if row.plan_id is not None else None,
        review=review_from_status(related.reviews.get(row.review_id)) if row.review_id is not None else "none",
        evidence_ok=None,
        open_waits=blocked,
        open_questions=row.open_questions,
        has_specification=row.has_specification,
        broken=row.broken,
    )


def card_view(row: BoardCardRow, related: Related) -> CardView:
    facts = card_facts(row, related)
    return CardView(
        id=row.card_id,
        path=row.path,
        title=row.title,
        kind=cast(Kind | None, row.kind),
        column=None if row.broken else facts.column,
        version=row.version,
        origin_type=cast(OriginType | None, row.origin_type),
        origin_ref=row.origin_ref,
        waits_for=tuple(row.waits_for),
        blocked_by=facts.open_waits,
        open_questions=row.open_questions,
        # The file's cost is written at the end of a stage; usage of linked conversations may be newer, and a
        # deleted conversation's usage is gone from it, so the larger of the two is shown.
        cost_usd=max(row.cost_usd, related.costs.get(row.card_id, 0.0)),
        plan_id=row.plan_id,
        plan_status=facts.plan_status,
        review_id=row.review_id,
        review=facts.review,
        broken=row.broken,
        error=row.error,
        updated=row.updated_at,
        state=card_state(facts),
        next=next_step(facts),
    )


def columns_of(cards: Iterable[CardView]) -> dict[Column, ColumnView]:
    grouped: dict[Column, list[CardView]] = {column: [] for column in COLUMNS}
    for card in cards:
        if card.column is not None:
            grouped[card.column].append(card)
    return {
        column: ColumnView(tuple(found[:MAX_COLUMN_CARDS]), len(found), len(found) > MAX_COLUMN_CARDS)
        for column, found in grouped.items()
    }


def version_views(
    rows: Sequence[BoardVersionRow], files: Mapping[str, Version | BrokenVersion], cards: Sequence[CardView]
) -> tuple[VersionView, ...]:
    """Each version with its progress and cost; versions only named on cards are listed too."""
    names = sorted({row.name for row in rows} | {card.version for card in cards if card.version})
    budgets = {row.name: row.budget_usd for row in rows}
    views: list[VersionView] = []
    for name in names:
        members = [(card.column or "", card.cost_usd) for card in cards if card.version == name and not card.broken]
        summary = version_summary(members, budgets.get(name))
        parsed = files.get(name)
        version = parsed if isinstance(parsed, Version) else None
        views.append(
            VersionView(
                name=name,
                state=version.state if version else None,
                budget_usd=summary.budget_usd,
                notes=version.notes if version else "",
                acceptance_criteria=version.acceptance_criteria if version else "",
                done=summary.done,
                total=summary.total,
                cost_usd=summary.cost_usd,
                level=summary.level,
                error=parsed.error if isinstance(parsed, BrokenVersion) else None,
            )
        )
    return tuple(views)


def current_version(versions: Sequence[VersionView]) -> str | None:
    """The first open version by name: what the board's heading shows progress for."""
    return next((version.name for version in versions if version.state == "open"), None)
