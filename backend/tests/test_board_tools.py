"""The board tools of the agent: which of them a conversation is offered, and that `update_card` changes only the
conversation's own card and only its specification and open questions."""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.providers.fake_provider import ScriptedResponse
from tests.board_support import add_card, board_project
from tests.conftest import FakeBackend
from tests.test_commands_and_skills import send, start

CARD_TOOLS = {"read_card", "update_card"}
INTAKE_TOOLS = {"add_card", "add_to_card"}
SPECIFICATION = "Show the status.\n\n### Acceptance criteria\n\n1. `GET /health` answers 200."


@dataclass
class World:
    client: TestClient
    fake: FakeBackend
    project_id: int
    card_id: int
    other_card_id: int
    linked: int
    unlinked: int

    def card(self, card_id: int | None = None) -> dict:
        found = self.client.get(f"/api/v1/projects/{self.project_id}/board/cards/{card_id or self.card_id}")
        assert found.status_code == 200, found.text
        return found.json()

    def offered(self, conversation_id: int) -> set[str]:
        self.fake.responses.append(ScriptedResponse(text="Hello."))
        send(self.client, conversation_id, "Hi")
        return {tool.name for tool in self.fake.requests[-1].tool_box.definitions}

    def call(self, conversation_id: int, name: str, arguments: dict) -> str:
        """Call a tool in a reply; what the tool answered."""
        self.fake.responses.append(ScriptedResponse(text="Done.", tool_calls=((name, arguments),)))
        events = send(self.client, conversation_id, "Go")
        results = [data for event, data in events if event == "tool_result"]
        assert results, events
        return str(results[-1])


@pytest.fixture
def world(client: TestClient, fake: FakeBackend) -> World:
    project = board_project(client)
    card = add_card(client, project["id"], "Health endpoint", column="idea")
    other = add_card(client, project["id"], "Something else", column="idea")
    linked = start(client, project_id=project["id"])
    unlinked = start(client, project_id=project["id"])
    client.app.state.board.link_conversation(project["id"], card["id"], linked, "specify")
    return World(client, fake, project["id"], card["id"], other["id"], linked, unlinked)


def test_card_tools_are_offered_only_in_a_conversation_linked_to_a_card(world: World) -> None:
    assert CARD_TOOLS <= world.offered(world.linked)
    assert not CARD_TOOLS & world.offered(world.unlinked)


def test_card_tools_are_not_offered_outside_a_project(world: World) -> None:
    assert not (CARD_TOOLS | INTAKE_TOOLS) & world.offered(start(world.client))


def test_intake_tools_are_offered_in_every_project_conversation_while_the_board_is_on(world: World) -> None:
    assert INTAKE_TOOLS <= world.offered(world.linked)
    assert INTAKE_TOOLS <= world.offered(world.unlinked)


def test_no_board_tool_is_offered_with_the_board_switched_off(world: World) -> None:
    off = world.client.put(f"/api/v1/projects/{world.project_id}/settings/project_board", json={"value": False})
    assert off.status_code == 200, off.text
    assert not (CARD_TOOLS | INTAKE_TOOLS) & world.offered(world.linked)
    assert not (CARD_TOOLS | INTAKE_TOOLS) & world.offered(world.unlinked)


def test_intake_tools_are_not_offered_in_a_project_whose_board_was_never_started(world: World) -> None:
    project = world.client.post("/api/v1/environments/1/projects", json={"name": "Fresh"}).json()
    world.client.put(f"/api/v1/projects/{project['id']}/settings/project_board", json={"value": True})
    conversation = start(world.client, project_id=project["id"])
    assert not (CARD_TOOLS | INTAKE_TOOLS) & world.offered(conversation)


def test_read_card_returns_the_linked_card_as_data(world: World) -> None:
    answer = world.call(world.linked, "read_card", {})
    assert "Health endpoint" in answer
    assert "Something else" not in answer


def test_read_card_ignores_a_card_number_it_is_given(world: World) -> None:
    answer = world.call(world.linked, "read_card", {"card_id": world.other_card_id})
    assert "Health endpoint" in answer
    assert "Something else" not in answer


def test_update_card_sets_the_specification_and_adds_a_question_on_the_linked_card(world: World) -> None:
    world.call(world.linked, "update_card", {"specification": SPECIFICATION, "question": "Which port?"})
    card = world.card()
    assert card["specification"].strip() == SPECIFICATION
    assert [question["text"] for question in card["questions"]] == ["Which port?"]
    assert card["title"] == "Health endpoint"


def test_update_card_changes_nothing_but_the_two_fields_whatever_else_is_passed(world: World) -> None:
    before = world.card()
    world.call(
        world.linked,
        "update_card",
        {
            "specification": SPECIFICATION,
            "title": "Hijacked",
            "column": "done",
            "card_id": world.other_card_id,
            "waits_for": [world.other_card_id],
            "kind": "bug",
        },
    )
    after = world.card()
    for field in ("title", "column", "kind", "waits_for", "version", "origin", "description"):
        assert after[field] == before[field], field
    assert {key: value for key, value in after["links"].items() if key != "conversations"} == {
        key: value for key, value in before["links"].items() if key != "conversations"
    }
    assert after["specification"].strip() == SPECIFICATION
    assert world.card(world.other_card_id)["specification"] == ""
    assert world.card(world.other_card_id)["title"] == "Something else"


@pytest.mark.parametrize(
    "arguments",
    [{}, {"specification": "   "}, {"question": ""}, {"specification": "x" * 20_001}, {"question": "x" * 1001}],
)
def test_update_card_refuses_nothing_to_change_an_empty_text_and_a_text_over_its_limit(
    world: World, arguments: dict
) -> None:
    before = world.card()
    world.call(world.linked, "update_card", arguments)
    after = world.card()
    assert (after["specification"], after["questions"], after["title"]) == (
        before["specification"],
        before["questions"],
        before["title"],
    )


def test_update_card_is_not_available_in_a_conversation_without_a_card(world: World) -> None:
    world.fake.responses.append(ScriptedResponse(text="Done.", tool_calls=(("update_card", {"specification": "x"}),)))
    send(world.client, world.unlinked, "Go")
    assert world.card()["specification"] == ""
    assert world.card(world.other_card_id)["specification"] == ""
