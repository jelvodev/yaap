"""The board's routes: each route and each error, with the sign-in check. Pushing goes through a stub pusher, because
the production URL rule allows only https remotes."""

import os
import shutil
from datetime import UTC, datetime

import pytest

from app.board.card import CardLinks, Question
from app.board.errors import NoRemote, PublicRemote, PushFailed
from app.plans.models import PlanRecord
from app.storage.models import Conversation
from tests.board_service_support import (
    add_card,
    board_service,
    board_url,
    hand_edit,
    make_project,
    project_folder,
    start_board,
    switch_board,
    write_raw,
)


def error(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]
    return detail


def test_every_board_route_needs_a_signed_in_user(client):
    project = start_board(client)
    add_card(client, project["id"])
    client.cookies.clear()
    routes = [
        ("get", ""),
        ("post", ""),
        ("post", "/cards"),
        ("get", "/cards/1"),
        ("patch", "/cards/1"),
        ("post", "/cards/1/move"),
        ("post", "/cards/1/answer"),
        ("delete", "/cards/1"),
        ("put", "/versions/1.0.0"),
        ("post", "/push"),
        ("post", "/pull"),
        ("post", "/check-remote"),
        ("post", "/import-issues"),
        ("post", "/cards/from-finding"),
    ]
    for method, tail in routes:
        response = getattr(client, method)(board_url(project["id"], tail), **({} if method in ("get", "delete") else {"json": {}}))
        assert response.status_code == 401, f"{method} {tail} answered {response.status_code}"


def test_a_board_route_for_an_unknown_project_answers_404(client):
    error(client.get(board_url(9999)), 404, "project_not_found")
    error(client.post(board_url(9999, "/cards"), json={"title": "x"}), 404, "project_not_found")
    error(client.delete(board_url(9999, "/cards/1")), 404, "project_not_found")


def test_a_project_without_a_board_reads_as_not_started(client):
    project = make_project(client)

    board = client.get(board_url(project["id"])).json()

    assert board["state"] == "not_started" and board["enabled"] is True
    assert board["columns"] is None


def test_starting_a_board_needs_a_repository_unless_one_is_asked_for(client):
    project = make_project(client)
    shutil.rmtree(project_folder(client, project) / ".git", onexc=lambda function, path, _: (os.chmod(path, 0o700), function(path)))
    assert client.get(board_url(project["id"])).json()["message"]

    error(client.post(board_url(project["id"]), json={"storage": "branch"}), 409, "no_repository")
    started = client.post(board_url(project["id"]), json={"storage": "branch", "create_repository": True})

    assert started.status_code == 201
    assert started.json()["state"] == "ready" and started.json()["head"] is None
    assert list(started.json()["columns"]) == ["inbox", "idea", "design", "plan", "build", "review", "done"]
    error(client.post(board_url(project["id"]), json={"storage": "branch"}), 409, "board_exists")


def test_a_switched_off_board_answers_off_and_its_routes_409(client):
    project = start_board(client)
    add_card(client, project["id"])
    switch_board(client, project["id"], False)

    board = client.get(board_url(project["id"])).json()
    assert (board["state"], board["enabled"]) == ("off", False)
    assert board["columns"] is None
    for response in (
        client.post(board_url(project["id"], "/cards"), json={"title": "x"}),
        client.get(board_url(project["id"], "/cards/1")),
        client.post(board_url(project["id"]), json={"storage": "branch"}),
        client.put(board_url(project["id"], "/versions/1.0.0"), json={}),
    ):
        error(response, 409, "board_off")
    switch_board(client, project["id"], True)
    assert client.get(board_url(project["id"], "/cards/1")).status_code == 200


def test_the_board_is_unchanged_when_since_is_its_head(client):
    project = start_board(client)
    add_card(client, project["id"])
    head = client.get(board_url(project["id"])).json()["head"]

    same = client.get(board_url(project["id"]), params={"since": head}).json()
    other = client.get(board_url(project["id"]), params={"since": "0" * 40}).json()

    assert same["unchanged"] is True and same["columns"] is None
    assert other["unchanged"] is False and other["columns"]["idea"]["count"] == 1


def test_a_card_is_created_read_and_listed(client):
    project = start_board(client)

    created = client.post(
        board_url(project["id"], "/cards"),
        json={"title": "Fix it", "kind": "bug", "column": "inbox", "version": "0.9.0", "description": "Details"},
    )

    assert created.status_code == 201
    card = created.json()
    assert (card["id"], card["column"], card["kind"], card["version"]) == (1, "inbox", "bug", "0.9.0")
    assert card["origin"] == {"type": "user", "ref": None}
    assert client.get(board_url(project["id"], "/cards/1")).json()["description"] == "Details"
    assert client.get(board_url(project["id"])).json()["columns"]["inbox"]["cards"][0]["title"] == "Fix it"


@pytest.mark.parametrize(
    "body",
    [{}, {"title": ""}, {"title": "x" * 201}, {"title": "x", "kind": "epic"}, {"title": "x", "column": "build"}],
)
def test_a_card_that_is_not_valid_is_refused_with_422(client, body):
    project = start_board(client)

    assert client.post(board_url(project["id"], "/cards"), json=body).status_code == 422


def test_a_card_with_a_version_name_that_is_not_one_is_refused(client):
    project = start_board(client)

    response = client.post(board_url(project["id"], "/cards"), json={"title": "x", "version": "bad name!"})

    error(response, 422, "invalid_card")


def test_creating_a_card_before_the_board_started_answers_409(client):
    project = make_project(client)

    error(client.post(board_url(project["id"], "/cards"), json={"title": "x"}), 409, "board_not_started")


def test_an_unknown_card_answers_404_on_every_card_route(client):
    project = start_board(client)
    add_card(client, project["id"])

    for response in (
        client.get(board_url(project["id"], "/cards/99")),
        client.patch(board_url(project["id"], "/cards/99"), json={"title": "x"}),
        client.post(board_url(project["id"], "/cards/99/move"), json={"column": "design"}),
        client.post(board_url(project["id"], "/cards/99/answer"), json={"index": 0, "text": "x"}),
        client.delete(board_url(project["id"], "/cards/99")),
    ):
        error(response, 404, "card_not_found")


def test_a_card_is_patched_in_its_text_and_not_in_its_column(client):
    project = start_board(client)
    add_card(client, project["id"])
    url = board_url(project["id"], "/cards/1")

    patched = client.patch(url, json={"title": "New", "specification": "Do X", "kind": "chore", "waits_for": []})

    assert patched.status_code == 200
    assert (patched.json()["title"], patched.json()["specification"], patched.json()["kind"]) == ("New", "Do X", "chore")
    for forbidden in ("column", "id", "origin", "cost_usd", "links", "created"):
        assert client.patch(url, json={forbidden: 1}).status_code == 422, forbidden
    assert client.get(url).json()["column"] == "idea"


def test_a_card_cannot_wait_for_itself_or_for_a_card_that_does_not_exist(client):
    project = start_board(client)
    add_card(client, project["id"])
    url = board_url(project["id"], "/cards/1")

    assert client.patch(url, json={"waits_for": [1]}).status_code in (409, 422)
    assert client.patch(url, json={"waits_for": [77]}).status_code in (409, 422)
    assert client.get(url).json()["waits_for"] == []


def test_a_broken_card_is_listed_as_broken_and_refuses_changes(client):
    project = start_board(client)
    add_card(client, project["id"])
    write_raw(client, project["id"], "cards/2.md", "this is not a card\n")

    board = client.get(board_url(project["id"])).json()
    assert [card["id"] for card in board["broken"]] == [2] and board["broken"][0]["error"]
    assert board["columns"]["idea"]["count"] == 1
    detail = client.get(board_url(project["id"], "/cards/2")).json()
    assert detail["broken"] is True and detail["broken_text"] == "this is not a card\n"
    error(client.patch(board_url(project["id"], "/cards/2"), json={"title": "x"}), 409, "card_broken")
    error(client.post(board_url(project["id"], "/cards/2/move"), json={"column": "design"}), 409, "card_broken")


def test_a_move_one_column_on_works_and_a_forbidden_move_says_which_rule(client):
    project = start_board(client)
    add_card(client, project["id"])
    url = board_url(project["id"], "/cards/1/move")

    refused = client.post(url, json={"column": "build"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "move_refused"
    assert refused.json()["detail"]["reason"] == "one_column"
    moved = client.post(url, json={"column": "design"})

    assert moved.status_code == 200 and moved.json()["column"] == "design"
    assert client.post(url, json={"column": "inbox"}).json()["column"] == "inbox"
    assert client.post(url, json={"column": "nowhere"}).status_code == 422


def test_a_move_from_a_stale_view_is_refused_with_card_changed(client):
    project = start_board(client)
    add_card(client, project["id"])
    stale = "2020-01-01T00:00:00Z"

    detail = error(
        client.post(board_url(project["id"], "/cards/1/move"), json={"column": "design", "expected_updated": stale}),
        409,
        "card_changed",
    )

    assert detail["current"]["id"] == 1 and detail["current"]["column"] == "idea"


def test_a_question_is_answered_once(client):
    project = start_board(client)
    add_card(client, project["id"])
    hand_edit(client, project["id"], 1, questions=[Question(asked=datetime.now(UTC), text="Which API?")])
    url = board_url(project["id"], "/cards/1/answer")

    assert client.get(board_url(project["id"], "/cards/1")).json()["open_questions"] == 1
    answered = client.post(url, json={"index": 0, "text": "The new one"})

    assert answered.status_code == 200
    assert answered.json()["questions"][0]["answer"] == "The new one"
    assert answered.json()["open_questions"] == 0
    error(client.post(url, json={"index": 0, "text": "Again"}), 409, "already_answered")
    assert client.post(url, json={"index": 5, "text": "x"}).status_code in (409, 422)
    assert client.post(url, json={"index": 0, "text": ""}).status_code == 422
    assert client.post(url, json={"index": 20, "text": "x"}).status_code == 422


def test_a_dropped_card_is_gone_and_its_number_stays_unused(client):
    project = start_board(client)
    add_card(client, project["id"])

    assert client.delete(board_url(project["id"], "/cards/1")).status_code == 204
    error(client.get(board_url(project["id"], "/cards/1")), 404, "card_not_found")
    assert add_card(client, project["id"])["id"] == 2


def test_a_card_being_built_cannot_be_dropped(client):
    project = start_board(client)
    service = board_service(client)
    add_card(client, project["id"])
    with service.session_factory() as session:
        conversation = Conversation(
            environment_id=1, project_id=project["id"], title="t", provider="anthropic", model="m", effort="low"
        )
        session.add(conversation)
        session.flush()
        plan = PlanRecord(
            project_id=project["id"], conversation_id=conversation.id, card_id=1, status="building", title="p", body={}
        )
        session.add(plan)
        session.commit()
        plan_id = plan.id
    hand_edit(client, project["id"], 1, column="build", links=CardLinks(plan_id=plan_id))

    error(client.delete(board_url(project["id"], "/cards/1")), 409, "card_building")
    assert client.get(board_url(project["id"], "/cards/1")).json()["state"] == "working"


def test_a_version_is_made_changed_and_must_have_a_valid_name(client):
    project = start_board(client)

    made = client.put(board_url(project["id"], "/versions/0.9.0"), json={"state": "open", "budget_usd": 5})
    changed = client.put(board_url(project["id"], "/versions/0.9.0"), json={"notes": "Release notes"})

    assert made.status_code == 200 and made.json()["budget_usd"] == 5 and made.json()["state"] == "open"
    assert changed.json()["notes"] == "Release notes" and changed.json()["budget_usd"] == 5
    assert client.get(board_url(project["id"])).json()["current_version"] == "0.9.0"
    error(client.put(board_url(project["id"], "/versions/bad name"), json={}), 422, "invalid_version")
    assert client.put(board_url(project["id"], "/versions/1.0.0"), json={"budget_usd": -1}).status_code == 422
    assert client.put(board_url(project["id"], "/versions/1.0.0"), json={"state": "weird"}).status_code == 422


def test_a_version_cannot_be_made_before_the_board_started(client):
    project = make_project(client)

    error(client.put(board_url(project["id"], "/versions/1.0.0"), json={}), 409, "board_not_started")


# --- push ----------------------------------------------------------------------------------------------------------


class StubPusher:
    """Stands for BoardPusher: records what would be pushed, or raises what a push would."""

    def __init__(self, failure=None):
        self.failure, self.calls = failure, []

    def push(self, place, branch, commit, token, acknowledge_public):
        self.calls.append((branch, commit, acknowledge_public))
        if self.failure is not None and not (isinstance(self.failure, PublicRemote) and acknowledge_public):
            raise self.failure


def allow_push(client, project_id, pusher):
    client.put(f"/api/v1/projects/{project_id}/settings/board_push", json={"value": True})
    object.__setattr__(board_service(client), "pusher", pusher)


def test_pushing_the_board_is_refused_unless_the_setting_allows_it(client):
    project = start_board(client)
    add_card(client, project["id"])

    error(client.post(board_url(project["id"], "/push"), json={}), 409, "push_not_allowed")
    assert client.get(board_url(project["id"])).json()["push"] == {"allowed": False, "pushed": False}


def test_a_push_sends_only_the_board_branch_at_its_head_and_remembers_it(client):
    project = start_board(client)
    add_card(client, project["id"])
    pusher = StubPusher()
    allow_push(client, project["id"], pusher)
    head = client.get(board_url(project["id"])).json()["head"]

    response = client.post(board_url(project["id"], "/push"), json={})

    assert response.status_code == 200 and response.json() == {"pushed_commit": head}
    assert pusher.calls == [("yaap-board", head, False)]
    assert client.get(board_url(project["id"])).json()["push"] == {"allowed": True, "pushed": True}
    add_card(client, project["id"])
    assert client.get(board_url(project["id"])).json()["push"]["pushed"] is False


def test_a_public_remote_needs_the_users_confirmation_before_the_board_is_pushed(client):
    project = start_board(client)
    add_card(client, project["id"])
    pusher = StubPusher(PublicRemote())
    allow_push(client, project["id"], pusher)

    error(client.post(board_url(project["id"], "/push"), json={}), 409, "public_remote")
    confirmed = client.post(board_url(project["id"], "/push"), json={"acknowledge_public": True})

    assert confirmed.status_code == 200
    assert [call[2] for call in pusher.calls] == [False, True]


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [(NoRemote(), 409, "no_remote"), (PushFailed("The board could not be pushed: refused"), 502, "push_failed")],
)
def test_a_push_that_cannot_be_made_says_why(client, failure, status, code):
    project = start_board(client)
    add_card(client, project["id"])
    allow_push(client, project["id"], StubPusher(failure))

    error(client.post(board_url(project["id"], "/push"), json={}), status, code)
    assert client.get(board_url(project["id"])).json()["push"]["pushed"] is False


def test_the_real_pusher_reports_a_project_without_a_remote(client):
    project = start_board(client)
    add_card(client, project["id"])
    client.put(f"/api/v1/projects/{project['id']}/settings/board_push", json={"value": True})

    error(client.post(board_url(project["id"], "/push"), json={}), 409, "no_remote")


def test_there_is_nothing_to_push_before_the_first_card(client):
    project = start_board(client)
    allow_push(client, project["id"], StubPusher())

    error(client.post(board_url(project["id"], "/push"), json={}), 409, "board_not_started")
