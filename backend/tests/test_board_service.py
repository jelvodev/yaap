"""The board service: card ids, conflicts between writers, the checked-out branch, version progress and cost."""

from datetime import datetime, timedelta

import pytest

from app.board.card import render_card
from app.board.errors import BoardCheckedOut, BoardConflict, BoardExists, BoardNotStarted, CardChanged, MoveRefused
from app.board.operations import NewCard
from app.board.paths import card_path
from app.board.repository import Put
from app.board.service import BoardService, Planned
from app.storage.models import Conversation, UsageRecord
from tests.board_service_support import add_card, board_service, board_url, git, project_folder, start_board


def test_card_ids_are_not_given_out_again_after_a_drop(client):
    project = start_board(client)
    service = board_service(client)
    first = add_card(client, project["id"])
    second = add_card(client, project["id"])
    assert (first["id"], second["id"]) == (1, 2)

    service.drop_card(project["id"], second["id"])
    third = add_card(client, project["id"])

    assert third["id"] == 3
    assert [card["id"] for card in client.get(board_url(project["id"])).json()["columns"]["idea"]["cards"]] == [1, 3]


def test_a_card_id_stays_unused_even_when_the_index_is_rebuilt(client):
    project = start_board(client)
    service = board_service(client)
    add_card(client, project["id"])
    last = add_card(client, project["id"])
    service.drop_card(project["id"], last["id"])

    opened = service.opened(project["id"])
    service.indexer.rebuild(project["id"], opened.repository)

    assert add_card(client, project["id"])["id"] == 3


def test_a_card_changed_by_someone_else_is_refused_with_the_current_card(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"], "Original")
    seen = datetime.fromisoformat(card["updated"])
    object.__setattr__(service, "clock", lambda: seen + timedelta(minutes=5))
    service.update_card(project["id"], card["id"], {"title": "Edited elsewhere"})

    with pytest.raises(CardChanged) as raised:
        service.update_card(project["id"], card["id"], {"title": "Mine"}, expected_updated=seen)

    assert raised.value.current.card.title == "Edited elsewhere"
    assert service.get_card(project["id"], card["id"]).card.title == "Edited elsewhere"


def test_the_api_answers_card_changed_with_the_current_card(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"], "Original")
    seen = card["updated"]
    object.__setattr__(service, "clock", lambda: datetime.fromisoformat(seen) + timedelta(minutes=5))
    client.patch(board_url(project["id"], f"/cards/{card['id']}"), json={"title": "Elsewhere"})

    response = client.patch(
        board_url(project["id"], f"/cards/{card['id']}"), json={"title": "Mine", "expected_updated": seen}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "card_changed"
    assert detail["current"]["title"] == "Elsewhere"


def test_the_card_can_be_saved_when_it_is_the_one_the_caller_saw(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"])
    seen = datetime.fromisoformat(card["updated"])

    saved = service.update_card(project["id"], card["id"], {"title": "Renamed"}, expected_updated=seen)

    assert saved.card.title == "Renamed"


def other_writer(service, opened, number):
    """Commit a card file on the board's branch from outside the write, as a hand edit or a second YAAP would."""
    head = opened.repository.head()
    card = BoardService.read_card(opened.repository, 1, head)
    edited = card.model_copy(update={"title": f"Edit {number}"})
    opened.repository.commit_changes([Put(card_path(1), render_card(edited))], f"Hand edit {number}", head, False)


def test_a_write_is_built_again_once_when_the_branch_moved_in_between(client):
    project = start_board(client)
    service = board_service(client)
    add_card(client, project["id"])
    opened = service.opened(project["id"])
    builds: list[str | None] = []

    def build(head):
        builds.append(head)
        if len(builds) == 1:
            other_writer(service, opened, 1)
        card = service.read_card(opened.repository, 1, head)
        return Planned((Put(card_path(2), render_card(card.model_copy(update={"id": 2}))),), {"added": 1}, "done")

    assert service.write(opened, build) == "done"
    assert len(builds) == 2
    assert builds[0] != builds[1]


def test_a_write_gives_up_with_a_conflict_when_the_branch_moves_twice(client):
    project = start_board(client)
    service = board_service(client)
    add_card(client, project["id"])
    opened = service.opened(project["id"])
    builds: list[str | None] = []

    def build(head):
        builds.append(head)
        other_writer(service, opened, len(builds))
        card = service.read_card(opened.repository, 1, head)
        return Planned((Put(card_path(2), render_card(card.model_copy(update={"id": 2}))),), {"added": 1}, None)

    with pytest.raises(BoardConflict):
        service.write(opened, build)
    assert len(builds) == 2


def test_the_api_answers_409_board_conflict_when_the_branch_keeps_moving(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"])
    opened = service.opened(project["id"])
    original = service.read_card
    moves = []

    def moving(repository, card_id, head):
        moves.append(1)
        other_writer(service, opened, len(moves))
        return original(repository, card_id, head)

    object.__setattr__(service, "read_card", staticmethod(moving))
    response = client.patch(board_url(project["id"], f"/cards/{card['id']}"), json={"title": "Mine"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "board_conflict"


def test_writing_is_refused_while_the_board_branch_is_checked_out(client):
    project = start_board(client)
    service = board_service(client)
    add_card(client, project["id"])
    git(project_folder(client, project), "checkout", "yaap-board")

    with pytest.raises(BoardCheckedOut):
        service.create_card(project["id"], NewCard(title="Second"))
    response = client.post(board_url(project["id"], "/cards"), json={"title": "Second"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "board_checked_out"
    # Reading still works.
    assert client.get(board_url(project["id"])).json()["state"] == "ready"


def test_a_board_cannot_be_started_twice_nor_used_before_it_is_started(client):
    project = start_board(client)
    service = board_service(client)
    with pytest.raises(BoardExists):
        service.enable(project["id"])

    fresh = client.post("/api/v1/environments/1/projects", json={"name": "Other"}).json()
    with pytest.raises(BoardNotStarted):
        service.create_card(fresh["id"], NewCard(title="Early"))


def test_a_forbidden_move_is_refused_and_leaves_the_card_where_it_was(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"])

    with pytest.raises(MoveRefused) as raised:
        service.move_card(project["id"], card["id"], "build")

    assert raised.value.refusal_code == "one_column"
    assert service.get_card(project["id"], card["id"]).view.column == "idea"


def mark_done(service, project_id, card_id):
    """A hand edit on the branch that puts the card in Done, shown after the next sync."""
    opened = service.opened(project_id)
    head = opened.repository.head()
    card = service.read_card(opened.repository, card_id, head)
    edited = card.model_copy(update={"column": "done"})
    opened.repository.commit_changes([Put(card_path(card_id), render_card(edited))], "Hand edit", head, False)


def add_usage(service, project, costs, purposes=None):
    """A conversation of the project with usage records of the given costs; returns its id."""
    with service.session_factory() as session:
        conversation = Conversation(
            environment_id=1, project_id=project["id"], title="t", provider="anthropic", model="m", effort="low"
        )
        session.add(conversation)
        session.flush()
        for number, cost in enumerate(costs):
            purpose = purposes[number] if purposes else "main"
            session.add(
                UsageRecord(
                    environment_id=1,
                    conversation_id=conversation.id,
                    provider="anthropic",
                    model="m",
                    cost=cost,
                    purpose=purpose,
                )
            )
        session.commit()
        return conversation.id


def test_a_version_shows_progress_and_cost_against_its_budget(client):
    project = start_board(client)
    service = board_service(client)
    cards = [add_card(client, project["id"], f"Card {number}", version="0.9.0") for number in range(4)]
    mark_done(service, project["id"], cards[0]["id"])
    service.put_version(project["id"], "0.9.0", {"state": "open", "budget_usd": 10.0})
    conversation_id = add_usage(service, project, [3.0, 5.5])
    service.link_conversation(project["id"], cards[1]["id"], conversation_id, "chat")

    version = client.get(board_url(project["id"])).json()["versions"][0]

    assert (version["name"], version["done"], version["total"]) == ("0.9.0", 1, 4)
    assert version["cost_usd"] == 8.5
    assert version["budget_usd"] == 10.0
    assert version["level"] == "warn"


def test_the_cost_of_a_card_counts_its_sub_runs_through_the_conversation(client):
    project = start_board(client)
    service = board_service(client)
    card = add_card(client, project["id"])
    conversation_id = add_usage(service, project, [1.25, 0.5], ["main", "sub"])
    add_usage(service, project, [9.0])  # another conversation, not linked
    service.link_conversation(project["id"], card["id"], conversation_id, "build")

    assert service.get_card(project["id"], card["id"]).view.cost_usd == 1.75


def test_a_version_without_a_budget_has_no_budget_level(client):
    project = start_board(client)
    add_card(client, project["id"], version="1.0.0")

    version = client.get(board_url(project["id"])).json()["versions"][0]

    assert (version["budget_usd"], version["level"], version["done"], version["total"]) == (None, "none", 0, 1)
