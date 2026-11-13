"""The column actions of the board: which action runs in which column and plan state, and the code of every refusal.

The table is run through the API on one board with cards seeded in each state (a plan row of that status, a review
row, the card file in its column). An action that is allowed is asserted to be accepted; one that is refused, to answer
409 with the code and to leave the card as it was."""

from dataclasses import dataclass, field
from pathlib import Path
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.board.card import Card, revise
from app.board.errors import ActionRunning, NoPlan
from app.board.links import with_plan, with_review
from app.board.route import CardRoute
from app.plans.model import Plan, PlanStep, StepCheck, plan_to_json
from app.plans.models import PlanRecord
from app.storage.models import Review
from tests.board_support import add_card, board_project
from tests.test_commands_and_skills import start

BASE = "a" * 40
ACTIONS = ("specify", "plan", "build", "continue", "stop", "review", "mark_done")


def plan_for(number: int) -> Plan:
    """A plan whose files are its own, so the build queue never makes two seeded builds wait for each other."""
    return Plan("Add health", steps=(PlanStep(1, "Add it", files=(f"file{number}.py",), checks=(StepCheck("done"),)),))


# state: (column, plan status, review status)
STATES: dict[str, tuple[str, str | None, str | None]] = {
    "inbox": ("inbox", None, None),
    "idea": ("idea", None, None),
    "design": ("design", None, None),
    "plan without plan": ("plan", None, None),
    "plan draft": ("plan", "draft", None),
    "plan failed": ("plan", "failed", None),
    "plan building": ("plan", "building", None),
    "build building": ("build", "building", None),
    "build queued": ("build", "queued", None),
    "build approved": ("build", "approved", None),
    "build interrupted": ("build", "interrupted", None),
    "build failed": ("build", "failed", None),
    "build done": ("build", "done", None),
    "build cancelled": ("build", "cancelled", None),
    "review without plan": ("review", None, None),
    "review plan not done": ("review", "building", None),
    "review done plan, no base": ("review", "done", None),
    "review done plan, review running": ("review", "done", "running"),
    "review reviewed": ("review", "done", "done"),
    "done": ("done", None, None),
}
# The states in which each action runs; every other state refuses it with the code below.
ALLOWED: dict[str, set[str]] = {
    "specify": {"idea"},
    "plan": {"design", "plan without plan", "plan failed"},
    "build": {"plan draft"},
    "continue": {"build interrupted", "build failed"},
    # A build holds its card in whatever column it is in.
    "stop": {"build building", "build queued", "plan building", "review plan not done"},
    "review": {"review reviewed"},
    "mark_done": {
        "review without plan",
        "review plan not done",
        "review done plan, no base",
        "review done plan, review running",
        "review reviewed",
    },
}
REFUSED_WITH: dict[str, str] = {
    "specify": "wrong_column",
    "plan": "wrong_column",
    "build": "no_plan",
    "continue": "no_plan",
    "stop": "no_plan",
    "review": "wrong_column",
    "mark_done": "wrong_column",
}
# Refused with another code than the action's usual one, for a state of the right column.
REFUSED_SPECIALLY: dict[tuple[str, str], str] = {
    ("review", "review without plan"): "no_plan",
    ("review", "review plan not done"): "no_plan",
    ("review", "review done plan, no base"): "no_plan",
    ("review", "review done plan, review running"): "action_running",
}
# Running these needs a reviewer of another vendor and a branch with changes (see test_card_route.py), and approving
# a draft starts a build; the table asserts only their refusals.
NOT_RUN = {("review", "review reviewed"), ("build", "plan draft")}


@dataclass
class Starter:
    """Starts stage conversations without a reply: the table is about the actions' rules, not about the replies."""

    client: TestClient
    project_id: int
    sent: list[tuple[int, str]] = field(default_factory=list)

    def create(self, project_id: int, title: str) -> int:
        return start(self.client, project_id=project_id)

    def send(self, conversation_id: int, text: str) -> None:
        self.sent.append((conversation_id, text))

    def busy(self, conversation_id: int) -> bool:
        return False


@dataclass
class Board:
    client: TestClient
    project_id: int
    conversation_id: int
    starter: Starter
    folder: Path
    count: int = 0

    def seed(self, state: str, waits_for: tuple[int, ...] = ()) -> int:
        """A card in the state: its file in the column, a plan row and a review row linked when the state has them."""
        column, plan_status, review_status = STATES[state]
        self.count += 1
        card_id = add_card(self.client, self.project_id, f"{state} {self.count}")["id"]
        board = self.client.app.state.board
        with self.client.app.state.session_factory() as session:
            plan_id = review_id = None
            if plan_status is not None:
                done = plan_status == "done"
                # A build that can be continued still has its copy.
                copy = f".yaap/builds/{self.count}" if plan_status in ("interrupted", "failed") else None
                if copy is not None:
                    (self.folder / copy / ".git").mkdir(parents=True)
                record = PlanRecord(
                    project_id=self.project_id,
                    conversation_id=self.conversation_id,
                    card_id=card_id,
                    status=plan_status,
                    title="Add health",
                    body=plan_to_json(plan_for(self.count)),
                    build_branch="yaap/card-1" if (done and "no base" not in state) or copy else None,
                    base_commit=BASE if done and "no base" not in state else None,
                    build_folder=copy,
                )
                session.add(record)
                session.flush()
                plan_id = record.id
            if review_status is not None:
                review = Review(
                    environment_id=1,
                    project_id=self.project_id,
                    card_id=card_id,
                    source="build",
                    status=review_status,
                    created_at=datetime.now(UTC),
                )
                session.add(review)
                session.flush()
                review_id = review.id
            session.commit()

        def change(card: Card) -> Card:
            moved = revise(card, {"column": column, "waits_for": list(waits_for)})
            return with_review(with_plan(moved, plan_id), review_id)

        board.write_card(board.opened(self.project_id), card_id, change, {"changed": 1})
        return card_id

    def act(self, card_id: int, action: str):  # noqa: ANN201 - the response of the test client
        return self.client.post(f"/api/v1/projects/{self.project_id}/board/cards/{card_id}/actions/{action}")

    def card(self, card_id: int) -> dict:
        return self.client.get(f"/api/v1/projects/{self.project_id}/board/cards/{card_id}").json()


@pytest.fixture
def board(client: TestClient) -> Board:
    project = board_project(client)
    conversation_id = start(client, project_id=project["id"])
    starter = Starter(client, project["id"])
    client.put(f"/api/v1/projects/{project['id']}/settings/board_parallel_builds", json={"value": "4"})
    actions = client.app.state.board_actions
    actions.starter = starter
    actions.route.starter = starter
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    return Board(client, project["id"], conversation_id, starter, folder)


def expected_for(action: str, state: str) -> str:
    """"ok" or the refusal's code."""
    if state in ALLOWED[action]:
        return "ok"
    return REFUSED_SPECIALLY.get((action, state), REFUSED_WITH[action])


def test_every_action_runs_only_in_its_columns_and_plan_states_and_refuses_with_a_code(board: Board) -> None:
    mismatches: list[str] = []
    for state in STATES:
        shared = board.seed(state)
        before = board.card(shared)
        for action in ACTIONS:
            if (action, state) in NOT_RUN:
                continue
            expected = expected_for(action, state)
            response = board.act(board.seed(state) if expected == "ok" else shared, action)
            found = "ok" if response.status_code in (200, 202) else response.json().get("detail", {}).get("code")
            if found != expected:
                mismatches.append(f"{action} in {state}: expected {expected}, got {found} ({response.text[:150]})")
            elif expected != "ok" and response.status_code != 409:
                mismatches.append(f"{action} in {state}: refused with {response.status_code}, not 409")
        if board.card(shared) != before:
            mismatches.append(f"{state}: a refusal changed the card")
    assert not mismatches, "\n".join(mismatches)


def test_specify_and_plan_start_a_conversation_linked_to_the_card_and_answer_202(board: Board) -> None:
    idea, design = board.seed("idea"), board.seed("design")
    specified, planned = board.act(idea, "specify"), board.act(design, "plan")
    assert (specified.status_code, planned.status_code) == (202, 202)
    roles = {
        card_id: board.card(card_id)["links"]["conversations"][0]["role"] for card_id in (idea, design)
    }
    assert roles == {idea: "specify", design: "plan"}
    assert specified.json()["conversation_id"] == board.card(idea)["links"]["conversations"][0]["id"]
    first = {conversation_id: text for conversation_id, text in board.starter.sent}
    assert first[planned.json()["conversation_id"]].startswith("/plan ")
    assert "Write a specification" in first[specified.json()["conversation_id"]]


def test_approve_and_build_is_refused_while_the_card_waits_for_open_cards(board: Board) -> None:
    other = board.seed("idea")
    waiting = board.seed("plan draft", waits_for=(other,))
    response = board.act(waiting, "build")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["waits_for"]) == ("waits_for_open", [other])
    assert board.card(waiting)["column"] == "plan"


def test_approve_and_build_is_not_held_by_a_card_it_waited_for_that_is_done(board: Board) -> None:
    finished = board.seed("done")
    free = board.seed("plan draft", waits_for=(finished,))
    detail = board.act(free, "build").json().get("detail", {})
    assert detail.get("code") != "waits_for_open"


def test_a_second_plan_is_refused_while_the_plan_conversation_is_replying(board: Board) -> None:
    card_id = board.seed("design")
    assert board.act(card_id, "plan").status_code == 202
    board.starter.busy = lambda conversation_id: True  # type: ignore[method-assign]  # the reply is still running
    response = board.act(card_id, "plan")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "action_running"


def test_actions_are_refused_with_the_boards_switch_off(board: Board) -> None:
    card_id = board.seed("design")
    off = board.client.put(f"/api/v1/projects/{board.project_id}/settings/project_board", json={"value": False})
    assert off.status_code == 200
    response = board.act(card_id, "plan")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "board_off"


def test_an_unknown_action_and_a_missing_card_are_refused(board: Board) -> None:
    card_id = board.seed("design")
    assert board.act(card_id, "launch").status_code == 422
    assert board.act(9999, "plan").status_code == 404


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("build failed", "ok"),
        ("build interrupted", "ok"),
        ("build done", "ok"),
        ("build cancelled", "ok"),
        ("build building", "build_running"),
        ("build queued", "build_running"),
        ("build approved", "build_running"),
        ("review reviewed", "invalid_card"),
        ("review without plan", "no_plan"),
        ("design", "no_plan"),
        ("plan draft", "no_plan"),
        ("done", "no_plan"),
    ],
)
def test_send_back_from_build_takes_only_a_build_that_has_ended_and_says_why_in_the_card(
    board: Board, state: str, code: str
) -> None:
    card_id = board.seed(state)
    response = board.client.post(
        f"/api/v1/projects/{board.project_id}/board/cards/{card_id}/send-back", json={}
    )
    if code == "ok":
        assert response.status_code == 200, response.text
        sent = response.json()
        assert sent["column"] == "design"
        assert "Sent back" in sent["description"]
    else:
        assert response.status_code == (422 if code == "invalid_card" else 409), response.text
        assert response.json()["detail"]["code"] == code
        assert board.card(card_id)["column"] == STATES[state][0]


# --- One review start at a time -------------------------------------------------------------------------------------


def test_a_review_started_by_a_click_is_refused_while_the_automatic_one_after_a_build_is_starting() -> None:
    route = CardRoute(None, None, None, None)  # type: ignore[arg-type]  # only the start guard is exercised
    view = SimpleNamespace(status="done", base_commit=BASE, build_branch="yaap/card-1", project_id=1)
    starts: list[str] = []

    def begin(_view: object, card_id: int) -> int:
        starts.append("begun")
        # While this start's criteria checks run, the user clicks Review (the same call as the click makes).
        with pytest.raises(ActionRunning, match="starting"):
            route.start_review(view, card_id)  # type: ignore[arg-type]  # a plan view stand-in
        return 12

    route.begin_review = begin  # type: ignore[method-assign]  # replaces the review service
    assert route.start_review(view, 3) == 12  # type: ignore[arg-type]
    assert starts == ["begun"]


def test_a_review_may_start_again_when_the_first_start_ended_or_failed_and_for_another_card() -> None:
    route = CardRoute(None, None, None, None)  # type: ignore[arg-type]  # only the start guard is exercised
    view = SimpleNamespace(status="done", base_commit=BASE, build_branch="yaap/card-1", project_id=1)
    calls: list[int] = []

    def begin(_view: object, card_id: int) -> int:
        calls.append(card_id)
        if len(calls) == 1:
            raise RuntimeError("the review could not start")
        if card_id == 3:
            with pytest.raises(ActionRunning):
                route.start_review(view, 3)  # type: ignore[arg-type]
            assert route.start_review(view, 4) == 4  # type: ignore[arg-type]  # another card is not held
        return card_id

    route.begin_review = begin  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        route.start_review(view, 3)  # type: ignore[arg-type]
    assert route.starting_reviews == set()
    assert route.start_review(view, 3) == 3  # type: ignore[arg-type]
    assert route.starting_reviews == set()


@pytest.mark.parametrize(
    ("status", "base", "branch"),
    [("building", BASE, "b"), ("done", None, "b"), ("done", BASE, None), ("failed", BASE, "b")],
)
def test_a_review_of_a_build_that_has_not_finished_is_refused_before_anything_starts(
    status: str, base: str | None, branch: str | None
) -> None:
    route = CardRoute(None, None, None, None)  # type: ignore[arg-type]
    view = SimpleNamespace(status=status, base_commit=base, build_branch=branch, project_id=1)
    with pytest.raises(NoPlan):
        route.start_review(view, 3)  # type: ignore[arg-type]
    assert route.starting_reviews == set()
