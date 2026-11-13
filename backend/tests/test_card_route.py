"""A card from start to finish with scripted providers on a temporary repository: a request in the chat becomes a card
in Design, its plan is made and approved, the build runs in its copy, the card reaches Review and is marked done, and the
project's working tree stays clean throughout."""

import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.providers.fake_provider import ScriptedResponse
from tests.board_support import board_project, host_git
from tests.conftest import FakeBackend
from tests.test_commands_and_skills import send, start

HEALTH = "def health():\n    return 'ok'\n"
PLAN = {
    "title": "Add a health endpoint",
    "summary": "A function that answers ok.",
    "steps": [
        {
            "title": "Write health.py",
            "details": "A health function.",
            "files": ["health.py"],
            "checks": [{"text": "health.py exists and answers ok"}],
        }
    ],
}
SLOW = ScriptedResponse(text="Working on it for a while " * 4, delay=1.0)
WORKER_REPORT = "Status: done\nConclusion: health.py written\nChanged files: health.py"


def wait_for(condition: Callable[[], object], what: str, seconds: float = 40.0) -> object:
    deadline = time.monotonic() + seconds
    while True:
        found = condition()
        if found:
            return found
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.2)


class Route:
    def __init__(self, client: TestClient, fake: FakeBackend) -> None:
        self.client, self.fake = client, fake
        self.project = board_project(client)
        self.project_id = self.project["id"]
        self.folder = client.app.state.workspace_dir / self.project["folder"]

    def card(self, card_id: int) -> dict:
        return self.client.get(f"/api/v1/projects/{self.project_id}/board/cards/{card_id}").json()

    def act(self, card_id: int, action: str) -> dict:
        """An action; a write that lost the race against the stage writes still going on in the background (the
        evidence of the review) is tried again, as the user would."""
        for _attempt in range(10):
            response = self.client.post(f"/api/v1/projects/{self.project_id}/board/cards/{card_id}/actions/{action}")
            if response.status_code != 409 or response.json()["detail"]["code"] != "board_conflict":
                break
            time.sleep(0.5)
        assert response.status_code in (200, 202), response.text
        return response.json()

    def column_is(self, card_id: int, column: str) -> Callable[[], bool]:
        return lambda: self.card(card_id)["column"] == column

    def status(self) -> str:
        return host_git("status", "--porcelain", "--untracked-files=all", cwd=self.folder)


@pytest.fixture
def route(client: TestClient, fake: FakeBackend) -> Route:
    return Route(client, fake)


def test_a_chat_request_becomes_a_card_in_design_linked_to_the_conversation(route: Route) -> None:
    conversation = start(route.client, project_id=route.project_id)
    route.fake.responses.append(ScriptedResponse(text="Card 1 is made."))

    events = send(route.client, conversation, "/card add a health endpoint")

    assert not [event for event, _ in events if event == "error"], events
    card = route.card(1)
    assert (card["column"], card["title"], card["origin"]["type"]) == ("design", "add a health endpoint", "chat")
    assert card["origin"]["ref"] == f"conversation:{conversation}"
    assert [(link["id"], link["role"]) for link in card["links"]["conversations"]] == [(conversation, "chat")]
    assert card["next"]["action"] == "plan"


def test_a_card_goes_from_a_chat_request_through_plan_build_and_review_to_done(route: Route) -> None:
    conversation = start(route.client, project_id=route.project_id)
    route.fake.responses.append(ScriptedResponse(text="Card 1 is made."))
    send(route.client, conversation, "/card add a health endpoint")
    before = route.status()

    # Plan: a new conversation answers /plan with a plan; the card moves to Plan and holds it.
    route.fake.responses.append(
        ScriptedResponse(text="Here is the plan.", tool_calls=(("submit_plan", PLAN),))
    )
    planned = route.act(1, "plan")
    assert planned["conversation_id"] is not None
    wait_for(route.column_is(1, "plan"), "the card to reach Plan")
    card = route.card(1)
    assert card["links"]["plan_id"] is not None and "Write health.py" in card["plan_markdown"]
    assert card["next"]["action"] == "approve"

    # Approve and build: the step is done by the main model in the build's copy.
    route.fake.responses.append(
        ScriptedResponse(text=WORKER_REPORT, tool_calls=(("write_file", {"path": "health.py", "content": HEALTH}),))
    )
    route.act(1, "build")
    wait_for(route.column_is(1, "review"), "the card to reach Review", seconds=90)
    card = route.card(1)
    branch = card["links"]["branch"]
    assert branch and "[x]" in card["plan_markdown"].lower()
    assert card["cost_usd"] >= 0

    # The build branch is in the project with the step's commit; the working tree was never touched.
    assert host_git("show", f"{branch}:health.py", cwd=route.folder) == HEALTH
    assert route.status() == before
    assert (route.folder / "health.py").exists() is False
    assert host_git("symbolic-ref", "--short", "HEAD", cwd=route.folder).strip() == "main"

    # Mark done.
    done = route.act(1, "mark_done")
    assert done["card"]["column"] == "done"
    assert route.status() == before


def test_a_restart_in_the_middle_of_a_build_leaves_the_card_interrupted_and_continue_finishes_it(route: Route) -> None:
    conversation = start(route.client, project_id=route.project_id)
    route.fake.responses.append(ScriptedResponse(text="Card 1 is made."))
    send(route.client, conversation, "/card add a health endpoint")
    route.fake.responses.append(ScriptedResponse(text="Here is the plan.", tool_calls=(("submit_plan", PLAN),)))
    route.act(1, "plan")
    wait_for(route.column_is(1, "plan"), "the card to reach Plan")

    # The build is slow, and YAAP restarts while it works.
    route.fake.responses.append(SLOW)
    route.act(1, "build")
    wait_for(lambda: route.card(1)["state"] == "working", "the build to start")
    builds = route.client.app.state.builds
    route.client.portal.call(builds.shutdown)

    card = wait_for(lambda: route.card(1) if route.card(1)["state"] == "interrupted" else None, "the interruption")
    assert card["column"] == "build"
    assert (card["next"]["action"], card["next"]["who"]) == ("continue", "you")
    assert "Continue" in card["next"]["label"]

    # Continue finishes the build from the step that was open.
    builds.shutting_down = False
    route.fake.responses.append(
        ScriptedResponse(text=WORKER_REPORT, tool_calls=(("write_file", {"path": "health.py", "content": HEALTH}),))
    )
    route.act(1, "continue")
    wait_for(route.column_is(1, "review"), "the card to reach Review", seconds=90)
    branch = route.card(1)["links"]["branch"]
    assert host_git("show", f"{branch}:health.py", cwd=route.folder) == HEALTH
    assert route.status() == ""
