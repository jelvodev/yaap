"""The reply stream's events match the schema the API publishes, and every event goes through `server_sent_event`."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter

from app.core import reply_event_models as models
from app.core.chat_events import server_sent_event
from app.core.conversation_schemas import ConversationSummaryOut, MessageOut
from app.core.reply_event_models import ReplyEvent
from app.core.utility_skips import UtilitySkipOut
from app.core.why import WhyOut
from tests.conftest import FakeBackend, make_test_app

APP_DIR = Path(__file__).resolve().parent.parent / "app"
NOW = datetime(2026, 11, 13, tzinfo=UTC)


def summary() -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=1, project_id=None, title="T", provider="anthropic", model="claude-opus-5-5", effort="high", updated_at=NOW
    )


def message() -> MessageOut:
    return MessageOut(
        id=2,
        parent_id=1,
        role="assistant",
        content=[],
        status="complete",
        stop_reason=None,
        error=None,
        created_at=NOW,
    )


def examples() -> list[BaseModel]:
    why = WhyOut(rule="rule", source="permission", setting=None, layer=None)
    return [
        models.TextDeltaEvent(text="hi"),
        models.ReasoningDeltaEvent(text="hmm"),
        models.ToolCallEvent(
            id="t1", name="read_file", input={"path": "a"}, call_key="k", checkpointable=False, writes_files=False
        ),
        models.ToolResultEvent(id="t1", output="ok", is_error=False),
        models.CallRestartedEvent(attempt=2, reason="dropped"),
        models.ToolOutputEvent(call_key="k", text="line"),
        models.SteeringEvent(call_key="k", text="steer"),
        models.CheckpointEvent(checkpoint_id=3, call_key="k", tool="write_file"),
        models.DelegateProgressEvent(run_id=4, label="worker", step="read_file"),
        models.PlanSubmittedEvent(plan_id=5),
        models.ApprovalRequestEvent(approval_id="a1", tool="run", summary="s", detail="d", why=why),
        models.ApprovalResolvedEvent(approval_id="a1", decision="approve"),
        models.DocumentOpenEvent(path="a.md"),
        models.DocumentWritingEvent(path="a.md"),
        models.DocumentChangeEvent(path="a.md", hash=None),
        models.DocumentSeenEvent(call_key="k", path="a.md"),
        models.UserMessageEvent(**dict(message())),
        models.DoneEvent(message=message(), conversation=summary()),
        models.TitleEvent(conversation=summary()),
        models.UtilitySkippedEvent(**dict(UtilitySkipOut(id=1, time=NOW, task=None, label="l", reason="r"))),
        models.StageEvent(id="route", stage="route", lane="main", label="Route"),
        models.StageRemovedEvent(id="route"),
        models.CompactedEvent(message_id=2, covered=3, focus=None),
        models.CompactFailedEvent(detail="no"),
        models.EvalStartedEvent(run_id=1, project_id=2, detail="started"),
    ]


def union_members() -> set[type[BaseModel]]:
    inner = get_args(ReplyEvent)[0]
    members: set[type[BaseModel]] = set()
    for member in get_args(inner):
        # RunEvent is itself a union nested in the reply union.
        members.update(get_args(member) or (member,))
    return members


def event_name(member: type[BaseModel]) -> str:
    name = member.model_fields["event"].default
    assert isinstance(name, str)
    return name


def parse_wire(wire: str) -> tuple[str, dict[str, Any]]:
    head, data = wire.rstrip("\n").split("\n", 1)
    assert head.startswith("event: ") and data.startswith("data: ")
    return head.removeprefix("event: "), json.loads(data.removeprefix("data: "))


@pytest.fixture(scope="module")
def openapi_document() -> dict[str, Any]:
    app = make_test_app(FakeBackend())
    return app.openapi()


def test_every_member_of_the_reply_union_has_an_example() -> None:
    covered = {type(example) for example in examples()}
    assert covered == union_members()
    assert {event_name(type(example)) for example in examples()} == {event_name(member) for member in union_members()}


@pytest.mark.parametrize("example", examples(), ids=lambda example: type(example).__name__)
def test_an_event_on_the_wire_validates_against_the_published_reply_event_schema(
    example: BaseModel, openapi_document: dict[str, Any]
) -> None:
    schemas = openapi_document["components"]["schemas"]
    # The response model is the ReplyEvent root model; its schema is the union with the event name as discriminator.
    schema = {"$ref": "#/components/schemas/ReplyEvent", "components": {"schemas": schemas}}
    name, data = parse_wire(server_sent_event(example))  # type: ignore[arg-type]  # every example is a ReplyEvent member
    assert name == data["event"]
    Draft202012Validator(schema).validate(data)


@pytest.mark.parametrize("example", examples(), ids=lambda example: type(example).__name__)
def test_an_event_round_trips_through_the_reply_event_union(example: BaseModel) -> None:
    name, data = parse_wire(server_sent_event(example))  # type: ignore[arg-type]  # every example is a ReplyEvent member
    parsed = TypeAdapter(ReplyEvent).validate_python(data)
    assert type(parsed) is type(example)
    assert set(data) == set(type(example).model_fields)
    assert parsed.event == name  # type: ignore[union-attr]  # every member has an event name


def test_no_server_sent_event_call_gets_a_dict_literal() -> None:
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"server_sent_event\(\s*(\S)", text):
            if match.group(1) == "{" and not text[: match.start()].rstrip().endswith("def"):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(APP_DIR)}:{line}")
    assert offenders == []


def test_the_published_schema_refuses_an_event_with_a_wrong_shape(openapi_document: dict[str, Any]) -> None:
    schemas = openapi_document["components"]["schemas"]
    validator = Draft202012Validator({"$ref": "#/components/schemas/ReplyEvent", "components": {"schemas": schemas}})
    assert not validator.is_valid({"event": "text_delta"})
    assert not validator.is_valid({"event": "no_such_event"})
