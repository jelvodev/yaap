"""Routing: per reply the utility model names the kind of message and the preset's mapping picks
which of its models answers, within the model policy; the reply keeps which model answered and why."""

import pytest
from sqlalchemy import select

from app.core.routing import ROUTE_KINDS, RouteModel, classify, decide_route, preset_routes
from app.core.reply_routing import routed_text
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, Message, UsageRecord
from tests.conftest import VALID_KEY
from tests.test_chat import send

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
MAIN = RouteModel(OPUS, "medium")
WORKER = RouteModel(SONNET, "low")
UTILITY = RouteModel(HAIKU, "low")
ROUTES = {"question": "utility", "change": "worker", "planning": "main", "review": "worker"}


def allowed(_: RouteModel) -> str | None:
    return None


@pytest.mark.parametrize(
    ("answer", "routes", "roles", "expected_model", "expected_role", "expected_kind"),
    [
        ("question", ROUTES, {"worker": WORKER, "utility": UTILITY}, HAIKU, "utility", "question"),
        ("change", ROUTES, {"worker": WORKER, "utility": UTILITY}, SONNET, "worker", "change"),
        ("Planning.", ROUTES, {"worker": WORKER, "utility": UTILITY}, OPUS, "main", "planning"),
        ("  REVIEW\n", ROUTES, {"worker": WORKER, "utility": UTILITY}, SONNET, "worker", "review"),
        # Everything to the main model unless the preset says otherwise.
        ("question", preset_routes(None), {"worker": WORKER, "utility": UTILITY}, OPUS, "main", "question"),
        # A role the preset leaves empty falls back to the main model.
        ("change", ROUTES, {"worker": None, "utility": UTILITY}, OPUS, "main", "change"),
    ],
)
def test_a_kind_of_message_and_a_preset_give_the_expected_model(
    answer, routes, roles, expected_model, expected_role, expected_kind
):
    route = decide_route(answer, routes, MAIN, roles, allowed, str)
    assert (route.model, route.role, route.kind) == (expected_model, expected_role, expected_kind)
    assert route.reason


def test_a_model_the_policy_forbids_is_never_picked_and_the_reason_says_why():
    def refusal(candidate: RouteModel) -> str | None:
        return "Refused by the model policy of the environment Work." if candidate.model == HAIKU else None

    route = decide_route("question", ROUTES, MAIN, {"worker": WORKER, "utility": UTILITY}, refusal, str)

    assert (route.model, route.effort, route.role, route.kind) == (OPUS, "medium", "main", "question")
    assert "Refused by the model policy" in route.reason and route.reason.endswith("The main model answers.")


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (None, "gave no answer"),
        ("", "gave no answer"),
        ("banana", "named no kind"),
        # Two kinds at once is no answer either.
        ("a question or a review", "named no kind"),
    ],
)
def test_an_invalid_classifier_answer_falls_back_to_the_main_model(answer, reason):
    route = decide_route(answer, ROUTES, MAIN, {"worker": WORKER, "utility": UTILITY}, allowed, str)
    assert (route.model, route.role, route.kind) == (OPUS, "main", None)
    assert reason in route.reason


@pytest.mark.parametrize(
    ("answer", "kind"),
    [("question", "question"), ("Kind: change", "change"), ("planning\n", "planning"), ("questions", None), ("", None)],
)
def test_the_classification_is_the_one_kind_the_answer_names(answer, kind):
    assert classify(answer) == kind


def test_a_long_refusal_keeps_the_reason_short_and_its_ending():
    route = decide_route("question", ROUTES, MAIN, {"utility": UTILITY}, lambda _: "x" * 1000, str)
    assert len(route.reason) <= 300 and route.reason.endswith("The main model answers.")


def test_stored_routes_are_completed_and_invalid_roles_become_main():
    assert preset_routes({"question": "utility", "change": "boss", "other": "worker"}) == {
        "question": "utility",
        "change": "main",
        "planning": "main",
        "review": "main",
    }


def test_a_goal_turn_routes_on_its_goal_text():
    goal_turn = Message(content=[{"type": "goal", "text": "Continue with the import.", "turn": 2, "max_turns": 5}])
    typed = Message(content=[{"type": "text", "text": "Rename x"}, {"type": "command", "text": "expanded"}])
    assert routed_text(goal_turn) == "Continue with the import."
    assert routed_text(typed) == "Rename x"


# Through the reply service, with the fake provider.


def preset_id(client, name: str = "Balanced") -> int:
    return next(item["id"] for item in client.get("/api/v1/environments/1/presets").json() if item["name"] == name)


def balanced_conversation(client) -> int:
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    response = client.post(
        "/api/v1/environments/1/conversations", json={"model": OPUS, "effort": "medium", "preset": "Balanced"}
    )
    return response.json()["id"]


def route_questions_to_the_worker(client, **roles: str) -> None:
    body = {"routing": True, "routes": {"question": "worker"}, **roles}
    assert client.patch(f"/api/v1/presets/{preset_id(client)}", json=body).status_code == 200


def utility_tasks(client) -> list[str | None]:
    with client.app.state.session_factory() as session:
        return list(session.scalars(select(UsageRecord.task).where(UsageRecord.purpose == "utility")))


def test_the_preset_editor_turns_routing_on_and_assigns_roles(client):
    preset = next(item for item in client.get("/api/v1/environments/1/presets").json() if item["name"] == "Balanced")
    assert preset["routing"] is False
    assert [(item["kind"], item["role"]) for item in preset["routes"]] == [(kind, "main") for kind in ROUTE_KINDS]

    changed = client.patch(f"/api/v1/presets/{preset['id']}", json={"routing": True, "routes": {"change": "worker"}})
    assert changed.status_code == 200
    assert changed.json()["routing"] is True
    assert {item["kind"]: item["role"] for item in changed.json()["routes"]}["change"] == "worker"
    # Turning routing off keeps the assignment.
    off = client.patch(f"/api/v1/presets/{preset['id']}", json={"routing": False}).json()
    assert off["routing"] is False and {item["kind"]: item["role"] for item in off["routes"]}["change"] == "worker"

    assert client.patch(f"/api/v1/presets/{preset['id']}", json={"routes": {"change": "boss"}}).status_code == 422
    assert client.patch(f"/api/v1/presets/{preset['id']}", json={"routes": {"chat": "main"}}).status_code == 422


def test_a_routed_reply_uses_the_mapped_model_and_keeps_the_reason(client, fake):
    conversation_id = balanced_conversation(client)
    route_questions_to_the_worker(client)
    fake.responses.extend([ScriptedResponse(text="question"), ScriptedResponse(text="A venv keeps packages apart.")])

    events = send(client, conversation_id, "Why use a virtual environment?")

    assert len(fake.requests) == 2
    assert fake.requests[0].model == HAIKU and "Why use a virtual environment?" in str(fake.requests[0].messages)
    assert (fake.requests[1].model, fake.requests[1].effort) == (SONNET, "low")
    route = events[-1][1]["message"]["route"]
    assert (route["model"], route["kind"], route["role"]) == (SONNET, "question", "worker")
    assert route["model_name"] and route["reason"] == "A question: the worker model answers."
    stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"][-1]
    assert stored["route"] == route
    assert utility_tasks(client) == ["route"]
    with client.app.state.session_factory() as session:
        assert session.scalars(select(AgentRunRecord.model)).all()[-1] == SONNET


def test_with_routing_off_the_main_model_answers_without_a_utility_call(client, fake):
    conversation_id = balanced_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hi."))

    events = send(client, conversation_id, "Why use a virtual environment?")

    assert len(fake.requests) == 1 and fake.requests[0].model == OPUS
    assert events[-1][1]["message"]["route"] is None
    assert utility_tasks(client) == []


def test_a_model_the_policy_forbids_falls_back_to_the_main_model(client, fake):
    conversation_id = balanced_conversation(client)
    # The worker is Haiku, which the policy leaves out; the utility model (Sonnet) is allowed and names the kind.
    route_questions_to_the_worker(client, worker_model=HAIKU, utility_model=SONNET)
    with client.app.state.session_factory() as session:
        store_setting(session, SETTING_DEFINITIONS["model_policy"], 1, {"models": [OPUS, SONNET]})
    fake.responses.extend([ScriptedResponse(text="question"), ScriptedResponse(text="Answer.")])

    events = send(client, conversation_id, "What does this function do?")

    assert [request.model for request in fake.requests] == [SONNET, OPUS]
    route = events[-1][1]["message"]["route"]
    assert (route["model"], route["role"], route["kind"]) == (OPUS, "main", "question")
    assert "Refused by the model policy" in route["reason"]


def test_an_unknown_classifier_answer_lets_the_main_model_answer(client, fake):
    conversation_id = balanced_conversation(client)
    route_questions_to_the_worker(client)
    fake.responses.extend([ScriptedResponse(text="banana"), ScriptedResponse(text="Answer.")])

    events = send(client, conversation_id, "Rename foo to bar.")

    assert fake.requests[1].model == OPUS
    route = events[-1][1]["message"]["route"]
    assert route["kind"] is None and "named no kind" in route["reason"]
