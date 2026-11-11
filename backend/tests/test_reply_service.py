"""The reply service: every entry point starts a reply through it, and its errors keep their HTTP codes."""

import ast
import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.a2a.server import start_task
from app.api.conversations import reply_http_error
from app.auth.tokens import TokenGrant, verify_token
from app.core.replies import ReplyJob, ReplyRegistry
from app.core.reply_errors import (
    CommandRefused,
    InvalidReplyRequest,
    ReplyBusy,
    ReplyConflict,
    ReplyError,
    ReplyUnavailable,
)
from app.core.summaries import TOO_LITTLE
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from app.scheduling.service import claim, start_run
from app.storage.models import AgentRunRecord, Message, ScheduledTask
from tests.test_a2a import make_project, token
from tests.test_chat import add_key, start_conversation
from tests.test_scheduled_tasks import project, task_body

APP = Path(__file__).resolve().parents[1] / "app"


def refuse_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make building an HTTP request object fail, to show a code path needs none."""

    def refuse(self: Request, *args: object, **kwargs: object) -> None:
        raise AssertionError("An HTTP request object was built.")

    monkeypatch.setattr(Request, "__init__", refuse)


def in_worker_thread(client, function: Callable[..., object], *args: object) -> object:
    """Run function in a worker thread of the app's event loop, as the scheduler and A2A do."""
    return client.app.state.portal.call(anyio.to_thread.run_sync, function, *args)


def reply_texts(client, conversation_id: int) -> list[str]:
    """The assistant texts of the conversation, once its reply has ended."""
    for _ in range(200):
        if client.app.state.replies.running(conversation_id) is None:
            break
        time.sleep(0.05)
    with client.app.state.session_factory() as session:
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")
        )
        blocks = [block for message in messages for block in message.content]
        return [block.get("text", "").strip() for block in blocks if block.get("type") == "text"]


def test_a_scheduled_task_starts_its_reply_without_a_request(client, fake, monkeypatch):
    proj = project(client)
    task = client.post(f"/api/v1/projects/{proj['id']}/tasks", json=task_body(schedule_kind="manual")).json()
    fake.responses.append(
        ScriptedResponse(text="Checked.", tool_calls=(("finish_goal", {"status": "done", "summary": "Checked."}),))
    )
    with client.app.state.session_factory() as session:
        assert claim(session, session.get_one(ScheduledTask, task["id"]), "manual", datetime.now(UTC))
    with monkeypatch.context() as patch:
        refuse_requests(patch)
        started = in_worker_thread(client, start_run, client.app, task["id"], "manual")
    assert started is not None
    assert "Checked." in reply_texts(client, started.conversation_id)


def test_an_a2a_task_starts_its_reply_without_a_request(client, fake, monkeypatch):
    proj = make_project(client)
    value = token(client, "a2a", project_id=proj["id"])
    with client.app.state.session_factory() as session:
        found = verify_token(session, value)
    assert found is not None and found.environment_id is not None
    grant = TokenGrant(found.entry_id, found.purpose, found.environment_id, found.project_id)
    fake.responses.append(ScriptedResponse(text="All good."))
    with monkeypatch.context() as patch:
        refuse_requests(patch)
        _, conversation_id = in_worker_thread(client, start_task, client.app, grant, "Status?", None)
    assert reply_texts(client, conversation_id) == ["All good."]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ReplyBusy("YAAP is still replying in this conversation; wait or stop it first."), 409),
        (ReplyUnavailable("Add an Anthropic API key first."), 409),
        (CommandRefused("/check works in a project conversation."), 409),
        (ReplyConflict("Something in the way."), 409),
        (InvalidReplyRequest("Only a reply in this conversation can be made again."), 422),
        (ReplyError("Any other reason."), 409),
    ],
)
def test_each_reply_error_maps_to_its_status_code_with_its_message(error, status_code):
    translated = reply_http_error(error)
    assert translated.status_code == status_code and translated.detail == str(error)


def test_reply_registry_releases_reservations_on_start_failure_and_keeps_new_jobs(monkeypatch):
    registry = ReplyRegistry()
    reservation = registry.reserve(7)

    async def events():
        yield "done"

    def fail_start(_work, name=None):
        raise RuntimeError("event loop unavailable")

    monkeypatch.setattr(registry.work, "start", fail_start)

    async def start_and_check():
        with pytest.raises(RuntimeError, match="event loop unavailable"):
            registry.start(reservation, 3, events())

    asyncio.run(start_and_check())
    assert registry.running(7) is None
    assert registry.busy(7)
    reservation.release()
    assert not registry.busy(7)

    old, current = ReplyJob(7, 3), ReplyJob(7, 3)
    registry.jobs[7] = current
    registry._remove_job(7, old)
    assert registry.running(7) is current
    assert registry.cancel_job(7, old.id) is None
    assert registry.running(7) is current


def test_failed_job_registration_closes_provider_and_revokes_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_key(client)
    conversation_id = start_conversation(client)
    closed: list[bool] = []

    def refuse_start(*_args: object, **_kwargs: object) -> None:
        raise ReplyUnavailable("The reply could not start.")

    async def close_provider(_self: FakeProvider) -> None:
        closed.append(True)

    monkeypatch.setattr(client.app.state.replies, "start", refuse_start)
    monkeypatch.setattr(FakeProvider, "close", close_provider)
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})

    assert response.status_code == 409
    assert closed == [True]
    assert client.app.state.runs.for_conversation(conversation_id) is None
    assert not client.app.state.replies.busy(conversation_id)
    with client.app.state.session_factory() as session:
        record = session.scalar(select(AgentRunRecord).where(AgentRunRecord.conversation_id == conversation_id))
        assert record is not None and record.status == "incomplete"


def test_failed_run_record_creation_revokes_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    add_key(client)
    conversation_id = start_conversation(client)

    def fail_record(*_args: object, **_kwargs: object) -> None:
        raise ReplyUnavailable("The run record could not be created.")

    monkeypatch.setattr("app.core.reply_run.start_run_record", fail_record)
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})

    assert response.status_code == 409
    assert client.app.state.runs.for_conversation(conversation_id) is None
    assert not client.app.state.replies.busy(conversation_id)


def test_reply_registry_rejects_released_foreign_and_consumed_reservations():
    registry, foreign_registry = ReplyRegistry(), ReplyRegistry()

    async def events():
        yield "done"

    async def check():
        foreign = foreign_registry.reserve(8)
        with pytest.raises(ReplyBusy):
            registry.start(foreign, 1, events())
        foreign.release()

        released = registry.reserve(8)
        released.release()
        with pytest.raises(ReplyBusy):
            registry.start(released, 1, events())

        live = registry.reserve(8)
        job = registry.start(live, 1, events())
        with pytest.raises(ReplyBusy):
            registry.start(live, 1, events())
        assert job.task is not None
        await job.task

    asyncio.run(check())


def test_reply_launch_rejects_a_reservation_for_another_conversation(client):
    from types import SimpleNamespace

    from app.core.reply_service import ReplyService
    from app.core.reply_services import ReplyServices

    service = ReplyService(ReplyServices.from_state(client.app.state))
    reservation = client.app.state.replies.reserve(7)
    request = SimpleNamespace(conversation=SimpleNamespace(id=8))
    try:
        with pytest.raises(ReplyBusy, match="different conversation"):
            service.launch(None, None, request, reservation)
    finally:
        reservation.release()


def test_provider_setup_failure_releases_the_conversation_reservation(client, fake):
    conversation_id = start_conversation(client)
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "First"})
    assert refused.status_code == 409
    add_key(client)
    fake.responses.append(ScriptedResponse(text="Now it starts."))

    accepted = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Retry"})
    assert accepted.status_code == 200, accepted.text


def first_messages(client, fake) -> tuple[int, int, int]:
    """A conversation with one exchange: its id, the user's message and the reply."""
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hi."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    return conversation_id, messages[0]["id"], messages[1]["id"]


def busy(client, fake):
    conversation_id, _, _ = first_messages(client, fake)
    client.app.state.replies.jobs[conversation_id] = ReplyJob(conversation_id, 1)
    try:
        return client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Again"})
    finally:
        client.app.state.replies.jobs.pop(conversation_id)


def without_key(client, fake):
    return client.post(f"/api/v1/conversations/{start_conversation(client)}/messages", json={"text": "Hi"})


def edit_of_unknown(client, fake):
    conversation_id, _, reply_id = first_messages(client, fake)
    return client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "No", "edit_of": reply_id})


def unknown_attachment(client, fake):
    conversation_id, _, _ = first_messages(client, fake)
    body = {"text": "See", "attachment_ids": [999]}
    return client.post(f"/api/v1/conversations/{conversation_id}/messages", json=body)


def regenerate_a_question(client, fake):
    conversation_id, question_id, _ = first_messages(client, fake)
    return client.post(f"/api/v1/conversations/{conversation_id}/messages/{question_id}/regenerate")


def check_without_standards(client, fake):
    conversation_id, _, _ = first_messages(client, fake)
    return client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/check"})


def compact_too_early(client, fake):
    conversation_id, _, _ = first_messages(client, fake)
    return client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/compact"})


@pytest.mark.parametrize(
    ("scenario", "status_code", "detail"),
    [
        (busy, 409, "YAAP is still replying in this conversation; wait or stop it first."),
        (without_key, 409, None),
        (edit_of_unknown, 422, "Only your own messages in this conversation can be edited."),
        (unknown_attachment, 422, None),
        (regenerate_a_question, 422, "Only a reply in this conversation can be made again."),
        (check_without_standards, 409, "This project has no standards to check. Add them in the Blueprint first."),
        (compact_too_early, 409, TOO_LITTLE),
    ],
)
def test_a_reply_that_cannot_start_answers_as_before(client, fake, scenario, status_code, detail):
    response = scenario(client, fake)
    assert response.status_code == status_code, response.text
    answer = response.json()["detail"]
    assert answer == (detail if detail is not None else answer) and answer


@pytest.mark.parametrize(
    ("first_action", "second_action"), [("send", "send"), ("send", "regenerate"), ("regenerate", "send")]
)
def test_overlapping_reply_starts_conflict_before_writing_second_message(
    client, fake, monkeypatch, first_action, second_action
):
    from app.core.reply_service import ReplyService

    conversation_id, _, reply_id = first_messages(client, fake)
    entered_prepare, release_prepare = threading.Event(), threading.Event()
    original_prepare = ReplyService.prepare

    def blocked_prepare(self, session, conversation, query=""):
        entered_prepare.set()
        assert release_prepare.wait(10)
        return original_prepare(self, session, conversation, query)

    monkeypatch.setattr(ReplyService, "prepare", blocked_prepare)
    fake.responses.append(ScriptedResponse(text="One reply."))
    url = (
        f"/api/v1/conversations/{conversation_id}/messages"
        if first_action == "send"
        else f"/api/v1/conversations/{conversation_id}/messages/{reply_id}/regenerate"
    )
    body = {"text": "Concurrent send"} if first_action == "send" else None
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, url, json=body) if body is not None else pool.submit(client.post, url)
        assert entered_prepare.wait(5)
        second_url = (
            f"/api/v1/conversations/{conversation_id}/messages"
            if second_action == "send"
            else f"/api/v1/conversations/{conversation_id}/messages/{reply_id}/regenerate"
        )
        second = (
            client.post(second_url, json={"text": "Second send"})
            if second_action == "send"
            else client.post(second_url)
        )
        assert second.status_code == 409
        release_prepare.set()
        first_response = first.result(timeout=15)
    assert first_response.status_code == 200, first_response.text
    with client.app.state.session_factory() as session:
        messages = list(session.scalars(select(Message).where(Message.conversation_id == conversation_id)))
        assert sum(message.role == "user" for message in messages) == (2 if first_action == "send" else 1)
        assert sum(message.role == "assistant" for message in messages) == 2


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_nothing_outside_the_http_layer_imports_from_it():
    """Only api/ and main.py, which mounts the routers, may import app.api (AGENTS.md, Code standards)."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative.startswith("api/") or relative == "main.py":
            continue
        for name in imports_of(path):
            if name == "app.api" or name.startswith("app.api."):
                offenders.append(f"{relative}: {name}")
    assert offenders == []
