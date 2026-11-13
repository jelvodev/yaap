"""What a card has cost and how that compares with a budget."""

import pytest

from app.board.costs import (
    VersionSummary,
    budget_level,
    card_attempts,
    card_cost,
    conversation_costs,
    version_summary,
)
from app.storage.models import AgentRunRecord, UsageRecord
from tests.board_support import sessions_in


@pytest.mark.parametrize(
    ("spent", "budget", "level"),
    [
        (0.0, None, "none"),
        (500.0, None, "none"),
        (0.0, 10.0, "ok"),
        (7.99, 10.0, "ok"),
        (8.0, 10.0, "warn"),
        (9.99, 10.0, "warn"),
        (10.0, 10.0, "warn"),
        (10.01, 10.0, "over"),
        (0.0, 0.0, "ok"),
        (0.01, 0.0, "over"),
    ],
)
def test_a_budget_level_is_warn_from_eighty_percent_and_over_above_the_budget(spent, budget, level) -> None:
    assert budget_level(spent, budget) == level


def usage(conversation_id: int | None, cost: float | None) -> UsageRecord:
    return UsageRecord(
        environment_id=1, conversation_id=conversation_id, provider="fake", model="m", cost=cost, billing="metered"
    )


def test_a_cards_cost_adds_the_usage_of_its_conversations_including_subruns_and_ignores_others(tmp_path) -> None:
    sessions = sessions_in(tmp_path)
    with sessions() as session:
        session.add_all(
            [
                usage(1, 0.50),
                usage(1, 0.25),
                # A delegated sub-run records its usage on the same conversation.
                usage(1, 1.10),
                usage(2, 2.00),
                usage(3, 99.0),
                usage(2, None),
                usage(None, 5.0),
            ]
        )
        session.commit()
        assert card_cost(session, [1]) == 1.85
        assert card_cost(session, [1, 2]) == 3.85
        assert card_cost(session, [4]) == 0.0
        assert card_cost(session, []) == 0.0
        assert conversation_costs(session, [1, 2, 4]) == pytest.approx({1: 1.85, 2: 2.0})
        assert conversation_costs(session, []) == {}


def test_step_attempts_are_the_subruns_of_the_cards_build_runs(tmp_path) -> None:
    sessions = sessions_in(tmp_path)

    def run(parent: int | None) -> AgentRunRecord:
        return AgentRunRecord(environment_id=1, conversation_id=1, model="m", parent_run_id=parent)

    with sessions() as session:
        session.add_all([run(None), run(None), run(1), run(1), run(1), run(2), run(99)])
        session.commit()
        assert card_attempts(session, [1]) == 3
        assert card_attempts(session, [1, 2]) == 4
        assert card_attempts(session, [5]) == 0
        assert card_attempts(session, []) == 0


@pytest.mark.parametrize(
    ("cards", "budget", "expected"),
    [
        ([], None, VersionSummary(0, 0, 0.0, None, "none")),
        ([("done", 1.0), ("build", 2.5), ("done", 0.25)], None, VersionSummary(2, 3, 3.75, None, "none")),
        ([("done", 4.0), ("review", 4.5)], 10.0, VersionSummary(1, 2, 8.5, 10.0, "warn")),
        ([("done", 4.0), ("idea", 3.0)], 10.0, VersionSummary(1, 2, 7.0, 10.0, "ok")),
        ([("done", 11.0)], 10.0, VersionSummary(1, 1, 11.0, 10.0, "over")),
        ([("inbox", 0.0)], 0.0, VersionSummary(0, 1, 0.0, 0.0, "ok")),
    ],
)
def test_a_version_summary_counts_done_cards_and_judges_the_cost_against_the_budget(cards, budget, expected) -> None:
    assert version_summary(cards, budget) == expected
