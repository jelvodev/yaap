"""Request and answer shapes of the board's routes, and how the board's views become them."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.board.card import Column, Kind, OriginType
from app.board.costs import BudgetLevel
from app.board.flow import CardState, NextAction, ReviewState, Who
from app.board.models import ConversationRole, StorageKind
from app.board.versions import VersionState
from app.board.views import BoardStateName, BoardView, CardDetail, CardView, ColumnView, VersionView
from app.core.settings import Source
from app.plans.model import PlanStatus

# --- requests ------------------------------------------------------------------------------------------------------


class BoardStart(BaseModel):
    storage: StorageKind = "branch"
    # Make an empty git repository in the project folder when it has none (branch storage).
    create_repository: bool = False
    # The https URL of the separate private repository (repository storage).
    repository_url: str | None = Field(default=None, max_length=500)


class CardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Kind = "feature"
    column: Literal["inbox", "idea"] = "idea"
    version: str | None = Field(default=None, max_length=40)
    description: str = Field(default="", max_length=50_000)


class CardPatch(BaseModel):
    # Column, id, origin, cost, links and created are not changed here; sending one is an error, not ignored.
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Kind | None = None
    version: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=50_000)
    specification: str | None = Field(default=None, max_length=50_000)
    # The definition of done as a Markdown checklist.
    definition_of_done: str | None = Field(default=None, max_length=50_000)
    waits_for: list[int] | None = Field(default=None, max_length=20)
    # The card's `updated` as the caller last saw it; a different one answers 409 card_changed.
    expected_updated: datetime | None = None


class CardMove(BaseModel):
    column: Column
    expected_updated: datetime | None = None


class CardAnswer(BaseModel):
    index: int = Field(ge=0, le=19)
    text: str = Field(min_length=1, max_length=4000)


class VersionPut(BaseModel):
    state: VersionState | None = None
    budget_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    notes: str | None = Field(default=None, max_length=50_000)
    acceptance_criteria: str | None = Field(default=None, max_length=50_000)


class BoardPush(BaseModel):
    # The user confirmed that pushing may publish the board (a public remote, or one YAAP cannot judge).
    acknowledge_public: bool = False


# --- answers -------------------------------------------------------------------------------------------------------


class OriginOut(BaseModel):
    type: OriginType | None
    ref: str | None


class NextOut(BaseModel):
    action: NextAction
    who: Who
    label: str


class CardPlanOut(BaseModel):
    id: int
    status: PlanStatus | None


class CardOut(BaseModel):
    id: int
    path: str
    title: str
    kind: Kind | None
    # None for a broken card.
    column: Column | None
    version: str | None
    origin: OriginOut
    waits_for: list[int]
    # The cards it waits for that are not done yet.
    blocked_by: list[int]
    open_questions: int
    cost_usd: float
    state: CardState
    next: NextOut
    plan: CardPlanOut | None
    review_id: int | None
    review: ReviewState
    broken: bool
    error: str | None
    updated: datetime | None


class ChecklistItemOut(BaseModel):
    text: str
    done: bool


class QuestionOut(BaseModel):
    asked: datetime
    text: str
    answer: str | None


class ConversationLinkOut(BaseModel):
    id: int
    title: str
    role: ConversationRole


class CardLinksOut(BaseModel):
    conversations: list[ConversationLinkOut]
    plan_id: int | None
    review_id: int | None
    branch: str | None
    pull_request: int | None


class CardDetailOut(CardOut):
    """A card with its text. Card text can come from anyone who can write the branch: it is data, shown as such."""

    description: str
    definition_of_done: str
    definition_items: list[ChecklistItemOut]
    specification: str
    plan_markdown: str
    evidence: str
    questions: list[QuestionOut]
    links: CardLinksOut
    created: datetime | None
    extra: dict[str, object]
    # A broken file's text, to show with its error; None when it was too large or not text.
    broken_text: str | None
    location: str


class BoardVersionOut(BaseModel):
    name: str
    # None for a version only named on cards or whose file is broken.
    state: VersionState | None
    budget_usd: float | None
    notes: str
    acceptance_criteria: str
    done: int
    total: int
    cost_usd: float
    level: BudgetLevel
    error: str | None


class ColumnOut(BaseModel):
    cards: list[CardOut]
    count: int
    truncated: bool


class BoardPushOut(BaseModel):
    allowed: bool
    pushed: bool


class BoardOut(BaseModel):
    enabled: bool
    enabled_source: Source
    state: BoardStateName
    storage: StorageKind | None
    location: str | None
    head: str | None
    # True when `since` is the head: nothing changed, so no columns are sent.
    unchanged: bool
    columns: dict[Column, ColumnOut] | None
    broken: list[CardOut]
    versions: list[BoardVersionOut]
    current_version: str | None
    push: BoardPushOut | None
    message: str | None


class PushedOut(BaseModel):
    pushed_commit: str


# --- conversions ---------------------------------------------------------------------------------------------------


def card_out(view: CardView) -> CardOut:
    return CardOut(**card_fields(view))


def card_fields(view: CardView) -> dict[str, object]:
    return {
        "id": view.id,
        "path": view.path,
        "title": view.title,
        "kind": view.kind,
        "column": view.column,
        "version": view.version,
        "origin": OriginOut(type=view.origin_type, ref=view.origin_ref),
        "waits_for": list(view.waits_for),
        "blocked_by": list(view.blocked_by),
        "open_questions": view.open_questions,
        "cost_usd": view.cost_usd,
        "state": view.state,
        "next": NextOut(action=view.next.action, who=view.next.who, label=view.next.label),
        "plan": CardPlanOut(id=view.plan_id, status=view.plan_status) if view.plan_id is not None else None,
        "review_id": view.review_id,
        "review": view.review,
        "broken": view.broken,
        "error": view.error,
        "updated": view.updated,
    }


def card_detail_out(detail: CardDetail) -> CardDetailOut:
    card = detail.card
    links = CardLinksOut(
        conversations=[ConversationLinkOut(id=one.id, title=one.title, role=one.role) for one in detail.conversations],
        plan_id=card.links.plan_id if card else None,
        review_id=card.links.review_id if card else None,
        branch=card.links.branch if card else None,
        pull_request=card.links.pull_request if card else None,
    )
    return CardDetailOut(
        **card_fields(detail.view),
        description=card.description if card else "",
        definition_of_done=card.definition_of_done if card else "",
        definition_items=[ChecklistItemOut(text=item.text, done=item.done) for item in detail.definition_items],
        specification=card.specification if card else "",
        plan_markdown=card.plan if card else "",
        evidence=card.evidence if card else "",
        questions=[
            QuestionOut(asked=question.asked, text=question.text, answer=question.answer)
            for question in (card.questions if card else [])
        ],
        links=links,
        created=card.created if card else None,
        extra=dict(card.extra) if card else {},
        broken_text=detail.broken_text,
        location=detail.location,
    )


def version_out(view: VersionView) -> BoardVersionOut:
    return BoardVersionOut(
        name=view.name,
        state=view.state,
        budget_usd=view.budget_usd,
        notes=view.notes,
        acceptance_criteria=view.acceptance_criteria,
        done=view.done,
        total=view.total,
        cost_usd=view.cost_usd,
        level=view.level,
        error=view.error,
    )


def column_out(view: ColumnView) -> ColumnOut:
    return ColumnOut(cards=[card_out(card) for card in view.cards], count=view.count, truncated=view.truncated)


def board_out(view: BoardView) -> BoardOut:
    return BoardOut(
        enabled=view.enabled,
        enabled_source=view.enabled_source,
        state=view.state,
        storage=view.storage,
        location=view.location,
        head=view.head,
        unchanged=view.unchanged,
        columns={column: column_out(found) for column, found in view.columns.items()} if view.columns else None,
        broken=[card_out(card) for card in view.broken],
        versions=[version_out(version) for version in view.versions],
        current_version=view.current_version,
        push=BoardPushOut(allowed=view.push.allowed, pushed=view.push.pushed) if view.push else None,
        message=view.message,
    )
