"""What waits for the user: which facts become which rows, in which order, and what the overview API answers."""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.board.flow import NextStep
from app.board.waiting import (
    ApprovalFacts,
    BuildFacts,
    CardWaitFacts,
    WaitingFacts,
    waiting_items,
)
from app.board.waiting_facts import WaitingService
from tests.board_support import add_card, board_project
from tests.test_commands_and_skills import start

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def later(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def card(card_id: int, action: str, who: str = "you", **fields: object) -> CardWaitFacts:
    return CardWaitFacts(
        project_id=1,
        card_id=card_id,
        title=f"Card {card_id}",
        next=NextStep(action, who, f"{action} label"),  # type: ignore[arg-type]  # the table passes plain strings
        **fields,  # type: ignore[arg-type]  # optional facts of the table
    )


def kinds(facts: WaitingFacts) -> list[str]:
    return [item.kind for item in waiting_items(facts)]


@pytest.mark.parametrize(
    ("action", "who", "kind", "button"),
    [
        ("approve", "you", "approve_plan", "approve"),
        ("judge", "you", "judge_review", "open"),
        ("answer", "you", "answer_question", "answer"),
        ("continue", "you", "continue_build", "continue"),
        ("accept", "you", "new_inbox", "triage"),
        ("approve", "yaap", None, None),
        ("specify", "you", None, None),
        ("plan", "you", None, None),
        ("review", "you", None, None),
        ("merge", "you", None, None),
        ("build", "you", None, None),
        ("none", "you", None, None),
    ],
)
def test_a_card_waits_only_when_its_next_step_is_the_users_and_one_of_the_waiting_kinds(
    action: str, who: str, kind: str | None, button: str | None
) -> None:
    items = waiting_items(WaitingFacts(cards=(card(5, action, who),)))
    if kind is None:
        assert items == []
    else:
        assert [(item.kind, item.action, item.card_id, item.project_id) for item in items] == [(kind, button, 5, 1)]


@pytest.mark.parametrize(
    ("updated", "seen", "shown"),
    [
        (later(2), later(1), True),
        (later(1), later(1), False),
        (later(0), later(1), False),
        (None, later(1), False),
        (later(0), None, True),
        (None, None, True),
    ],
)
def test_an_inbox_card_is_new_only_when_it_changed_after_the_user_last_looked(
    updated: datetime | None, seen: datetime | None, shown: bool
) -> None:
    facts = WaitingFacts(cards=(card(5, "accept", updated=updated),), seen=seen)
    assert (kinds(facts) == ["new_inbox"]) is shown


def test_other_waiting_cards_do_not_depend_on_when_the_user_last_looked() -> None:
    facts = WaitingFacts(cards=(card(5, "approve", updated=later(0)),), seen=later(9))
    assert kinds(facts) == ["approve_plan"]


@pytest.mark.parametrize(
    ("origin", "detail"),
    [
        ("chat", "from a conversation"),
        ("agent", "by the agent"),
        ("trigger", "by a trigger"),
        ("issue", "from an issue"),
        ("review", "from a review"),
        ("learning", "from learning"),
        ("user", "added by you"),
        ("something-new", "added by you"),
        (None, "added by you"),
    ],
)
def test_a_new_inbox_card_says_where_it_came_from(origin: str | None, detail: str) -> None:
    (item,) = waiting_items(WaitingFacts(cards=(card(5, "accept", origin_type=origin),)))
    assert item.detail == detail


def test_a_plan_to_approve_names_the_plan_conversation_its_model_and_when_it_was_made() -> None:
    row = card(
        5,
        "approve",
        updated=later(1),
        plan_conversation_id=40,
        plan_model="model-a",
        plan_changed=later(3),
        latest_conversation_id=41,
        latest_model="model-b",
    )
    (item,) = waiting_items(WaitingFacts(cards=(row,)))
    assert (item.conversation_id, item.model, item.since, item.detail) == (40, "model-a", later(3), "approve label")


def test_a_plan_to_approve_falls_back_to_the_latest_conversation_and_the_cards_time() -> None:
    row = card(5, "approve", updated=later(1), latest_conversation_id=41, latest_model="model-b")
    (item,) = waiting_items(WaitingFacts(cards=(row,)))
    assert (item.conversation_id, item.model, item.since) == (41, "model-b", later(1))


def test_a_review_to_judge_names_the_reviewer_model_and_when_it_finished() -> None:
    row = card(5, "judge", updated=later(1), review_model="reviewer", review_finished=later(4), latest_model="builder")
    (item,) = waiting_items(WaitingFacts(cards=(row,)))
    assert (item.model, item.since) == ("reviewer", later(4))


def test_a_pending_approval_is_a_row_with_its_tool_and_summary() -> None:
    approval = ApprovalFacts("ap-1", 7, "run_command", "pytest -q", project_id=2, card_id=9, model="m")
    (item,) = waiting_items(WaitingFacts(approvals=(approval,)))
    assert (item.kind, item.action, item.approval_id, item.title, item.detail) == (
        "approve_action",
        "open",
        "ap-1",
        "pytest -q",
        "run_command",
    )
    assert (item.project_id, item.card_id, item.conversation_id, item.model) == (2, 9, 7, "m")
    assert item.since is None


def test_an_approval_outside_every_card_still_waits() -> None:
    (item,) = waiting_items(WaitingFacts(approvals=(ApprovalFacts("ap-2", 7, "write_file", "x"),)))
    assert (item.project_id, item.card_id) == (None, None)


def test_an_interrupted_build_without_a_card_waits_for_continue() -> None:
    build = BuildFacts(plan_id=3, project_id=2, conversation_id=8, title="Add health", model="m", since=later(2))
    (item,) = waiting_items(WaitingFacts(builds=(build,)))
    assert (item.kind, item.action, item.plan_id, item.card_id, item.since) == (
        "continue_build",
        "continue",
        3,
        None,
        later(2),
    )


def test_rows_are_in_section_order_then_oldest_first_and_rows_without_a_time_come_last() -> None:
    facts = WaitingFacts(
        cards=(
            card(1, "accept", updated=later(0)),
            card(2, "approve", plan_changed=later(5)),
            card(3, "approve", plan_changed=later(2)),
            card(4, "approve"),
            card(5, "answer", updated=later(1)),
            card(6, "judge", review_finished=later(1)),
            card(7, "continue", plan_changed=later(1)),
        ),
        approvals=(ApprovalFacts("ap-1", 7, "run_command", "x"),),
    )
    order = [(item.kind, item.card_id) for item in waiting_items(facts)]
    assert order == [
        ("approve_action", None),
        ("approve_plan", 3),
        ("approve_plan", 2),
        ("approve_plan", 4),
        ("judge_review", 6),
        ("answer_question", 5),
        ("new_inbox", 1),
        ("continue_build", 7),
    ]


def test_nothing_waits_for_an_empty_set_of_facts() -> None:
    assert waiting_items(WaitingFacts()) == []


# --- The overview API ------------------------------------------------------------------------------------------------


def waiting(client: TestClient, **params: object) -> dict:
    response = client.get("/api/v1/waiting", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_overview_lists_new_inbox_cards_and_a_summary_per_project_with_its_board_on(client: TestClient) -> None:
    project = board_project(client)
    plain = client.post("/api/v1/environments/1/projects", json={"name": "No board"}).json()
    card = add_card(client, project["id"], "Look at this", column="inbox")
    add_card(client, project["id"], "Not yet", column="idea")

    overview = waiting(client)

    assert [(item["kind"], item["card_id"], item["project_id"], item["title"]) for item in overview["items"]] == [
        ("new_inbox", card["id"], project["id"], "Look at this")
    ]
    assert overview["items"][0]["action"] == "triage"
    assert [item["project_id"] for item in overview["projects"]] == [project["id"]]
    assert plain["id"] not in [item["project_id"] for item in overview["projects"]]
    summary = overview["projects"][0]
    assert (summary["name"], summary["building"], summary["blocked"], summary["escalated"]) == ("Site", 0, 0, 0)
    assert summary["cost_month"] == 0.0


def test_inbox_cards_seen_by_the_user_drop_out_until_they_change(client: TestClient) -> None:
    project = board_project(client)
    add_card(client, project["id"], "Look at this", column="inbox")
    first = waiting(client)
    assert len(first["items"]) == 1

    assert waiting(client, since=first["now"])["items"] == []

    # Card times have a resolution of one second; the moment the user looked is exact.
    time.sleep(1.1)
    newer = add_card(client, project["id"], "Another", column="inbox")
    assert [item["card_id"] for item in waiting(client, since=first["now"])["items"]] == [newer["id"]]


def test_the_overview_can_be_limited_to_one_environment(client: TestClient) -> None:
    project = board_project(client)
    add_card(client, project["id"], "Look at this", column="inbox")
    assert len(waiting(client, environment_id=1)["items"]) == 1
    elsewhere = waiting(client, environment_id=999)
    assert (elsewhere["items"], elsewhere["projects"]) == ([], [])


@pytest.mark.parametrize("params", [{"environment_id": 0}, {"since": "yesterday"}])
def test_a_malformed_query_is_refused(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/api/v1/waiting", params=params).status_code == 422


def test_every_pending_approval_waits_and_names_its_card_when_the_board_is_on(client: TestClient) -> None:
    project = board_project(client)
    card = add_card(client, project["id"], "Look at this", column="idea")
    conversation = start(client, project_id=project["id"])
    other = start(client)
    linked = client.app.state.board
    linked.link_conversation(project["id"], card["id"], conversation, "plan")

    def run(conversation_id: int, project_id: int | None, approval_id: str) -> SimpleNamespace:
        future = SimpleNamespace(done=lambda: False)
        pending = SimpleNamespace(id=approval_id, tool="run_command", summary=f"summary {approval_id}", future=future)
        return SimpleNamespace(
            environment_id=1,
            conversation_id=conversation_id,
            project_id=project_id,
            state=SimpleNamespace(approvals={approval_id: pending}),
        )

    runs = SimpleNamespace(runs={"a": run(conversation, project["id"], "ap-1"), "b": run(other, None, "ap-2")})
    service = WaitingService(client.app.state.session_factory, runs)  # type: ignore[arg-type]  # a registry stand-in
    overview = service.overview(None, None)

    found = {
        item.approval_id: (item.card_id, item.project_id, item.conversation_id)
        for item in overview.items
        if item.approval_id
    }
    assert found == {"ap-1": (card["id"], project["id"], conversation), "ap-2": (None, None, other)}
    assert overview.items[0].kind == "approve_action"
