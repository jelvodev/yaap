"""OpenTelemetry export: a reply's runs as OTLP JSON, downloaded or sent to a collector."""

import json
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.reply_services import ReplyServices
from app.providers.base import HistoryMessage, SystemPart
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, AuditRecord, RunStep
from app.tracing.otlp import hex_id, nanos, run_span_id, step_span_id, trace_id
from app.tracing.otlp_export import export_reply
from app.tracing.otlp_settings import traces_url, validate_endpoint, validate_headers
from app.tracing.snapshots import ContextSnapshot, SnapshotStore, trace_dir
from tests.test_chat import start_conversation
from tests.test_delegation import project_conversation

T0 = datetime(2026, 11, 8, 9, 0, 0, tzinfo=UTC)
SYSTEM_TEXT = "You are YAAP, working in the shop project."
PROMPT_TEXT = "Make hello.txt with {{secret:API_TOKEN}} in it"
TASK_TEXT = "Create hello.txt"
SUMMARY_TEXT = "write hello.txt (3 lines)"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")
VALUE_KINDS = {"stringValue", "intValue", "doubleValue", "boolValue", "arrayValue", "kvlistValue", "bytesValue"}


# The OTLP/JSON shape, checked as a collector reads it.


def check_attributes(items: object) -> dict[str, object]:
    assert isinstance(items, list)
    found: dict[str, object] = {}
    for item in items:
        assert set(item) == {"key", "value"} and isinstance(item["key"], str)
        (kind,) = item["value"].keys()
        assert kind in VALUE_KINDS
        if kind == "intValue":
            assert re.fullmatch(r"-?\d+", item["value"][kind])
            found[item["key"]] = int(item["value"][kind])
        else:
            found[item["key"]] = item["value"][kind]
    return found


def otlp_spans(document: dict) -> list[dict]:
    """The spans of a valid OTLP/JSON trace export, each with its attributes read into a dict."""
    spans = []
    assert isinstance(document["resourceSpans"], list)
    for resource_spans in document["resourceSpans"]:
        resource = check_attributes(resource_spans["resource"]["attributes"])
        assert resource["service.name"] == "yaap"
        for scope_spans in resource_spans["scopeSpans"]:
            assert scope_spans["scope"]["name"]
            for item in scope_spans["spans"]:
                assert HEX32.fullmatch(item["traceId"]) and item["traceId"].strip("0")
                assert HEX16.fullmatch(item["spanId"]) and item["spanId"].strip("0")
                assert item["parentSpanId"] == "" or HEX16.fullmatch(item["parentSpanId"])
                assert isinstance(item["name"], str) and item["name"]
                assert item["kind"] in (1, 2, 3, 4, 5)
                start, end = int(item["startTimeUnixNano"]), int(item["endTimeUnixNano"])
                assert isinstance(item["startTimeUnixNano"], str) and 0 < start <= end
                spans.append({**item, "attrs": check_attributes(item["attributes"])})
    ids = [item["spanId"] for item in spans]
    assert len(ids) == len(set(ids))
    known = set(ids)
    assert all(item["parentSpanId"] in known for item in spans if item["parentSpanId"])
    return spans


# A fixture run: a reply with a model call, a delegate call that starts a sub-run, and a second model call.


def fixture_run(client) -> tuple[int, int]:
    """The ids of the reply's run and its sub-run."""
    conversation_id = start_conversation(client)
    store = SnapshotStore(trace_dir(client.app.state.data_dir))
    snapshot = store.save(
        ContextSnapshot(
            [SystemPart(SYSTEM_TEXT, source="base")],
            [],
            [HistoryMessage(role="user", content=[{"type": "text", "text": PROMPT_TEXT}])],
        ),
        {},
    )
    with client.app.state.session_factory() as session:
        root = AgentRunRecord(
            environment_id=1,
            conversation_id=conversation_id,
            model="claude-opus-5-5",
            status="complete",
            tool_calls=1,
            started_at=T0,
            finished_at=T0 + timedelta(seconds=10),
        )
        session.add(root)
        session.flush()
        sub = AgentRunRecord(
            environment_id=1,
            conversation_id=conversation_id,
            parent_run_id=root.id,
            model="claude-sonnet-5-5",
            status="complete",
            tool_calls=1,
            task=TASK_TEXT,
            started_at=T0 + timedelta(seconds=2.5),
            finished_at=T0 + timedelta(seconds=7),
        )
        session.add(sub)
        session.flush()
        session.add_all(
            [
                model_step(root.id, 1, 0.1, 2000, snapshot, input_tokens=120, output_tokens=30, cost=0.0042),
                tool_step(root.id, 2, 2.2, 5000, "delegate", "delegate: Create hello.txt"),
                model_step(root.id, 3, 7.5, 1000, snapshot, input_tokens=200, output_tokens=12),
                model_step(sub.id, 1, 2.6, 1500, None, model="claude-sonnet-5-5", input_tokens=300, output_tokens=40),
                tool_step(sub.id, 2, 4.2, 300, "write_file", SUMMARY_TEXT, is_error=True, approval="approved"),
            ]
        )
        session.commit()
        return root.id, sub.id


def model_step(
    run_id: int,
    position: int,
    at: float,
    duration_ms: int,
    snapshot: str | None,
    model: str = "claude-opus-5-5",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float | None = None,
) -> RunStep:
    return RunStep(
        run_id=run_id,
        position=position,
        kind="model",
        started_at=T0 + timedelta(seconds=at),
        duration_ms=duration_ms,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=1000,
        cache_write_tokens=0,
        cost=cost,
        model_calls=1,
        snapshot=snapshot,
        is_error=False,
    )


def tool_step(
    run_id: int, position: int, at: float, duration_ms: int, tool: str, summary: str, is_error: bool = False,
    approval: str | None = None,
) -> RunStep:
    return RunStep(
        run_id=run_id,
        position=position,
        kind="tool",
        started_at=T0 + timedelta(seconds=at),
        duration_ms=duration_ms,
        tool=tool,
        summary=summary,
        is_error=is_error,
        approval=approval,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )


def export_run(client, run_id: int) -> dict:
    response = client.get(f"/api/v1/telemetry/runs/{run_id}/otlp")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    return response.json()


def test_the_export_of_a_fixture_run_is_valid_otlp_with_the_expected_spans_and_attributes(client):
    root_id, sub_id = fixture_run(client)
    spans = otlp_spans(export_run(client, root_id))

    assert [item["name"] for item in spans] == [
        "invoke_agent YAAP",
        "chat claude-opus-5-5",
        "execute_tool delegate",
        "chat claude-opus-5-5",
        "invoke_agent sub-run",
        "chat claude-sonnet-5-5",
        "execute_tool write_file",
    ]
    assert {item["traceId"] for item in spans} == {trace_id(root_id)}
    root, first, delegate, second, sub, sub_model, write = spans
    assert root["parentSpanId"] == "" and root["spanId"] == run_span_id(root_id)
    assert first["parentSpanId"] == root["spanId"] and second["parentSpanId"] == root["spanId"]
    # The sub-run is a child span of the delegate call that started it; its steps hang under the sub-run.
    assert sub["spanId"] == run_span_id(sub_id) and sub["parentSpanId"] == delegate["spanId"]
    assert sub_model["parentSpanId"] == sub["spanId"] and write["parentSpanId"] == sub["spanId"]
    # Real start times and durations from the steps.
    assert first["startTimeUnixNano"] == nanos(T0 + timedelta(seconds=0.1))
    assert int(first["endTimeUnixNano"]) - int(first["startTimeUnixNano"]) == 2_000_000_000
    assert int(root["endTimeUnixNano"]) - int(root["startTimeUnixNano"]) == 10_000_000_000

    assert (first["kind"], delegate["kind"], root["kind"]) == (3, 1, 1)
    assert first["attrs"] == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.system": "anthropic",
        "gen_ai.request.model": "claude-opus-5-5",
        "gen_ai.response.model": "claude-opus-5-5",
        "gen_ai.conversation.id": first["attrs"]["gen_ai.conversation.id"],
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 30,
        "yaap.usage.cache_read_tokens": 1000,
        "yaap.usage.cache_write_tokens": 0,
        "yaap.cost_usd": 0.0042,
        "yaap.model_calls": 1,
    }
    assert delegate["attrs"]["gen_ai.operation.name"] == "execute_tool"
    assert delegate["attrs"]["gen_ai.tool.name"] == "delegate"
    assert write["attrs"]["yaap.tool.approval"] == "approved" and write["attrs"]["error.type"] == "tool_error"
    assert write["status"]["code"] == 2 and root["status"] == {}
    assert root["attrs"]["gen_ai.operation.name"] == "invoke_agent" and root["attrs"]["yaap.run.id"] == root_id
    assert sub_model["attrs"]["gen_ai.request.model"] == "claude-sonnet-5-5"


def test_the_ids_are_derived_from_the_run_and_step_ids(client):
    root_id, _ = fixture_run(client)
    first = export_run(client, root_id)
    assert export_run(client, root_id) == first
    with client.app.state.session_factory() as session:
        step = session.scalars(select(RunStep).where(RunStep.run_id == root_id).order_by(RunStep.position)).first()
    assert otlp_spans(first)[1]["spanId"] == step_span_id(step.id)


@pytest.mark.parametrize(("kind", "length"), [("trace", 32), ("run", 16), ("step", 16)])
def test_ids_are_hex_of_the_otlp_length_and_differ_per_kind(kind, length):
    value = hex_id(kind, 7, length)
    assert len(value) == length and int(value, 16) != 0
    assert value != hex_id(kind, 8, length)


def test_the_export_holds_no_prompt_content_unless_the_setting_includes_it(client):
    root_id, _ = fixture_run(client)
    without = json.dumps(export_run(client, root_id))
    for text in (SYSTEM_TEXT, "Make hello.txt", TASK_TEXT, SUMMARY_TEXT):
        assert text not in without
    assert "gen_ai.input.messages" not in without and "gen_ai.system_instructions" not in without

    assert client.put("/api/v1/settings/otlp_include_content", json={"value": "on"}).status_code == 200
    spans = otlp_spans(export_run(client, root_id))
    first = spans[1]["attrs"]
    assert json.loads(first["gen_ai.system_instructions"]) == [{"type": "text", "content": SYSTEM_TEXT}]
    # The messages as the snapshot stored them: a secret only as its placeholder.
    assert json.loads(first["gen_ai.input.messages"]) == [
        {"role": "user", "parts": [{"type": "text", "content": PROMPT_TEXT}]}
    ]
    assert spans[6]["attrs"]["gen_ai.tool.call.arguments"] == SUMMARY_TEXT
    assert spans[4]["attrs"]["yaap.run.task"] == TASK_TEXT


def test_a_sub_run_exports_its_whole_reply(client):
    root_id, sub_id = fixture_run(client)
    assert export_run(client, sub_id) == export_run(client, root_id)


def test_access_is_refused_for_a_run_the_user_cannot_see(client):
    root_id, _ = fixture_run(client)
    assert client.get("/api/v1/telemetry/runs/9999/otlp").status_code == 404
    # A run whose conversation is not in the run's environment is not shown anywhere, so it is not exported.
    other = client.post("/api/v1/environments", json={"name": "Other", "colour": "#123456"}).json()["id"]
    with client.app.state.session_factory() as session:
        session.get_one(AgentRunRecord, root_id).environment_id = other
        session.commit()
    assert client.get(f"/api/v1/telemetry/runs/{root_id}/otlp").status_code == 404
    period = {"since": "2026-11-01T00:00:00Z", "until": "2026-11-30T00:00:00Z"}
    assert otlp_spans(client.get("/api/v1/telemetry/otlp", params=period).json()) == []
    # Without a session nothing is exported at all.
    cookies = dict(client.cookies)
    client.cookies.clear()
    try:
        assert client.get(f"/api/v1/telemetry/runs/{root_id}/otlp").status_code == 401
        assert client.get("/api/v1/telemetry/otlp", params=period).status_code == 401
    finally:
        for name, value in cookies.items():
            client.cookies.set(name, value)


def test_the_period_export_holds_the_replies_that_started_in_it(client):
    root_id, sub_id = fixture_run(client)
    later_id, _ = fixture_run(client)
    with client.app.state.session_factory() as session:
        later = session.get_one(AgentRunRecord, later_id)
        later.started_at += timedelta(days=3)
        later.finished_at = later.started_at + timedelta(seconds=10)
        session.commit()

    response = client.get(
        "/api/v1/telemetry/otlp", params={"since": "2026-11-08T00:00:00Z", "until": "2026-11-09T00:00:00Z"}
    )
    assert response.status_code == 200
    spans = otlp_spans(response.json())
    assert {item["traceId"] for item in spans} == {trace_id(root_id)}
    assert run_span_id(sub_id) in {item["spanId"] for item in spans}

    both = client.get("/api/v1/telemetry/otlp", params={"since": "2026-11-01", "until": "2026-11-30", "environment_id": 1})
    assert {item["traceId"] for item in otlp_spans(both.json())} == {trace_id(root_id), trace_id(later_id)}
    other = client.get("/api/v1/telemetry/otlp", params={"since": "2026-11-01", "until": "2026-11-30", "environment_id": 99})
    assert other.status_code == 404
    backwards = client.get("/api/v1/telemetry/otlp", params={"since": "2026-11-09", "until": "2026-11-08"})
    assert backwards.status_code == 422


# Sending to a collector after each reply.


class Collector:
    """A fake OTLP/HTTP collector behind httpx's mock transport."""

    def __init__(self, status_code: int = 200, fail: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.fail = fail

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(self.status_code, json={})


def configure(client, collector: Collector, include_content: bool = False) -> None:
    client.app.state.telemetry_transport = httpx.MockTransport(collector.handle)
    client.put("/api/v1/environments/1/secrets/OTLP_TOKEN", json={"value": "collector-token-123"})
    assert client.put("/api/v1/settings/otlp_endpoint", json={"value": "http://collector.test:4318/"}).status_code == 200
    headers = {"Authorization": "Bearer {{secret:OTLP_TOKEN}}"}
    assert client.put("/api/v1/settings/otlp_headers", json={"value": headers}).status_code == 200
    if include_content:
        client.put("/api/v1/settings/otlp_include_content", json={"value": "on"})


def telemetry_rows(client) -> list[AuditRecord]:
    with client.app.state.session_factory() as session:
        return list(session.scalars(select(AuditRecord).where(AuditRecord.purpose == "telemetry")))


def wait_for(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def reply_with_sub_run(client, fake) -> int:
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="The worker made it.", tool_calls=(("delegate", {"task": "Create hello.txt"}),)),
            ScriptedResponse(text="Created.", tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),)),
        ]
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Make hello"})
    assert response.status_code == 200 and "event: done" in response.text
    return conversation_id


def test_the_sender_posts_the_trace_to_the_endpoint_after_a_reply(client, fake):
    collector = Collector()
    configure(client, collector)
    conversation_id = reply_with_sub_run(client, fake)
    wait_for(lambda: len(collector.requests) == 1)

    (request,) = collector.requests
    assert str(request.url) == "http://collector.test:4318/v1/traces"
    assert request.headers["authorization"] == "Bearer collector-token-123"
    assert request.headers["content-type"] == "application/json"
    spans = otlp_spans(json.loads(request.content))
    assert [item["name"] for item in spans] == [
        "invoke_agent YAAP",
        "chat claude-opus-5-5",
        "execute_tool delegate",
        "invoke_agent sub-run",
        "chat claude-sonnet-5-5",
        "execute_tool write_file",
    ]
    assert spans[3]["parentSpanId"] == spans[2]["spanId"]
    assert "Make hello" not in request.content.decode()
    (row,) = telemetry_rows(client)
    assert (row.outcome, row.provider, row.model, row.conversation_id) == ("sent", "otlp", "collector.test:4318", conversation_id)
    assert row.secret_names == ["OTLP_TOKEN"] and row.data_classes == ["secrets"]


def test_a_failing_endpoint_does_not_break_the_reply(client, fake):
    collector = Collector(fail=True)
    configure(client, collector)
    conversation_id = reply_with_sub_run(client, fake)
    wait_for(lambda: len(collector.requests) == 1)
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert messages[-1]["role"] == "assistant"

    with client.app.state.session_factory() as session:
        record_id = session.scalars(select(AgentRunRecord.id).where(AgentRunRecord.parent_run_id.is_(None))).one()
    sender = ReplyServices.from_state(client.app.state).telemetry
    assert client.portal.call(export_reply, sender, record_id) == "failed"
    collector.fail, collector.status_code = False, 503
    assert client.portal.call(export_reply, sender, record_id) == "failed"
    collector.status_code = 200
    assert client.portal.call(export_reply, sender, record_id) == "sent"


def test_nothing_is_sent_without_an_endpoint(client, fake):
    collector = Collector()
    client.app.state.telemetry_transport = httpx.MockTransport(collector.handle)
    reply_with_sub_run(client, fake)
    with client.app.state.session_factory() as session:
        record_id = session.scalars(select(AgentRunRecord.id).where(AgentRunRecord.parent_run_id.is_(None))).one()
    assert client.portal.call(export_reply, ReplyServices.from_state(client.app.state).telemetry, record_id) == "off"
    assert collector.requests == [] and telemetry_rows(client) == []


def test_with_content_the_export_passes_the_outgoing_check_first(client, fake):
    collector = Collector()
    configure(client, collector, include_content=True)
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Noted."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Order ABC-12345 shipped"})
    wait_for(lambda: len(collector.requests) == 1)
    assert "ABC-12345" in collector.requests[0].content.decode()

    # A pattern the model policy lists stops the export before it leaves, and the audit log says why.
    policy = {"patterns": [r"ABC-\d{5}"]}
    assert client.put("/api/v1/environments/1/settings/model_policy", json={"value": policy}).status_code == 200
    with client.app.state.session_factory() as session:
        record_id = session.scalars(
            select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == conversation_id)
        ).one()
    assert client.portal.call(export_reply, ReplyServices.from_state(client.app.state).telemetry, record_id) == "refused"
    assert len(collector.requests) == 1
    refused = [row for row in telemetry_rows(client) if row.outcome == "refused"]
    assert len(refused) == 1 and "ABC" in (refused[0].reason or "")
    assert refused[0].data_classes == ["conversation", "files", "secrets"]


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ({}, True),
        ({"Authorization": "Bearer {{secret:OTLP_TOKEN}}"}, True),
        ({"x-api-key": "{{secret:KEY}}"}, True),
        ({"Authorization": "Bearer abc123"}, False),
        ({"Content-Type": "{{secret:X}}"}, False),
        ({"bad name": "{{secret:X}}"}, False),
        ({"A": "{{secret:X}}", "a": "{{secret:Y}}"}, False),
        ({"A": "{{secret:X}}\nB: y"}, False),
        ({f"H{n}": "{{secret:X}}" for n in range(11)}, False),
        ([], False),
    ],
)
def test_headers_name_secrets_only_as_placeholders(value, ok):
    assert (validate_headers(value) is None) == ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        ("http://otel-collector:4318", True),
        ("https://otlp.example.com/otlp", True),
        ("ftp://collector", False),
        ("http://collector:4318?token=x", False),
        ("not an address", False),
    ],
)
def test_the_endpoint_is_an_http_address_without_query(value, ok):
    assert (validate_endpoint(value) is None) == ok


@pytest.mark.parametrize(
    ("endpoint", "url"),
    [
        ("http://collector:4318", "http://collector:4318/v1/traces"),
        ("http://collector:4318/", "http://collector:4318/v1/traces"),
        ("https://example.com/otlp", "https://example.com/otlp/v1/traces"),
        ("http://collector:4318/v1/traces", "http://collector:4318/v1/traces"),
    ],
)
def test_the_export_is_posted_to_v1_traces(endpoint, url):
    assert traces_url(endpoint) == url

