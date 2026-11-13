"""Where a board card and its plan meet: the facts the flow needs about a card, the plans a card should point at, and
the plan a card sent back leaves behind."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.board.card import Card
from app.board.flow import CardFacts, review_from_status
from app.board.models import BoardCardRow
from app.plans.model import next_status
from app.plans.models import PlanRecord
from app.plans.store import PlanStore
from app.storage.models import Review


def card_facts(session_factory: sessionmaker[Session], project_id: int, card: Card) -> CardFacts:
    """The flow's facts for a card as just read, with its plan, review and the cards it waits for."""
    with session_factory() as session:
        plan = session.get(PlanRecord, card.links.plan_id) if card.links.plan_id is not None else None
        review = session.get(Review, card.links.review_id) if card.links.review_id is not None else None
        query = select(BoardCardRow.card_id, BoardCardRow.board_column).where(
            BoardCardRow.project_id == project_id, BoardCardRow.card_id.in_(card.waits_for)
        )
        columns = {card_id: column for card_id, column in session.execute(query)}
    return CardFacts(
        column=card.column,
        plan_status=plan.status if plan is not None else None,
        review=review_from_status(review.status if review is not None else None),
        open_waits=tuple(card_id for card_id in card.waits_for if columns.get(card_id, "done") != "done"),
        open_questions=sum(1 for question in card.questions if question.answer is None),
        has_specification=bool(card.specification.strip()),
    )


def cancel_plan(session_factory: sessionmaker[Session], plan_id: int) -> None:
    """The plan of a card sent back is made again, so the old one is cancelled when its status allows it."""
    with session_factory() as session:
        store = PlanStore(session)
        record = store.get(plan_id)
        if record is not None and next_status(record.status, "replan") == "cancelled":
            store.set_status(record.id, record.status, "cancelled")
            session.commit()


def record_plan_card(session_factory: sessionmaker[Session], project_id: int, card_id: int, plan_id: int) -> None:
    """Note on the plan which card it belongs to, when the plan is the project's."""
    with session_factory() as session:
        record = session.get(PlanRecord, plan_id)
        if record is not None and record.project_id == project_id:
            record.card_id = card_id
            session.commit()


def stale_plan_links(session_factory: sessionmaker[Session], project_id: int) -> dict[int, int]:
    """Cards whose newest (not cancelled) plan is not the one the card points at, as card id to plan id."""
    with session_factory() as session:
        query = select(BoardCardRow.card_id, BoardCardRow.plan_id).where(
            BoardCardRow.project_id == project_id, BoardCardRow.broken.is_(False)
        )
        linked = {card_id: plan_id for card_id, plan_id in session.execute(query)}
        newest: dict[int, int] = {}
        for plan_id, card_id in session.execute(
            select(PlanRecord.id, PlanRecord.card_id)
            .where(
                PlanRecord.project_id == project_id,
                PlanRecord.card_id.is_not(None),
                PlanRecord.status != "cancelled",
            )
            .order_by(PlanRecord.id)
        ):
            if card_id is not None and card_id in linked:
                newest[card_id] = plan_id
    return {card_id: plan_id for card_id, plan_id in newest.items() if linked.get(card_id) != plan_id}
