"""Steps and context snapshots: what a run did, step by step, and what the model saw."""

import gzip
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.providers.base import (
    AgentEvent,
    AgentRequest,
    HistoryMessage,
    ModelProvider,
    SystemPart,
    TokenUsage,
    ToolDefinition,
)
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, UsageRecord
from app.tracing.provider import ModelTrace, traced_provider
from app.tracing.recorder import ModelStep, ToolStep, TraceRecorder
from app.tracing.snapshots import ContextSnapshot, JsonValue, SnapshotStore, trace_dir
from app.tracing.steps import conversation_runs, model_usage, run_steps
from tests.test_delegation import project_conversation

FIRST = TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=1000, cache_write_tokens=50)
SECOND = TokenUsage(input_tokens=80, output_tokens=60, cache_read_tokens=1100)
SECRET_VALUE = "s3cr3t-token-value-42"


class MemoryRecorder(TraceRecorder):
    """Keeps the steps in memory, for adapters tested without a database."""

    def __init__(self) -> None:
        super().__init__(sessionmaker())
        self.steps: list[tuple[int, ModelStep | ToolStep]] = []

    def write(self, run_id: int, step: ModelStep | ToolStep) -> None:
        self.steps.append((run_id, step))


async def traced_events(provider: ModelProvider, request: AgentRequest) -> tuple[list[AgentEvent], MemoryRecorder]:
    """A reply's events through the tracing wrapper, and the steps it recorded under run 7."""
    recorder = MemoryRecorder()
    traced = traced_provider(provider, ModelTrace(recorder, 7, lambda _model, _usage: None))
    events: list[AgentEvent] = []
    try:
        async for event in traced.stream(request):
            events.append(event)
    finally:
        await traced.close()
    return events, recorder


def send(client, conversation_id: int, text: str) -> None:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200


def blob_texts(data_dir: Path) -> list[str]:
    return [
        gzip.decompress(path.read_bytes()).decode("utf-8")
        for path in (trace_dir(data_dir) / "blobs").glob("*/*.json.gz")
    ]


def user(text: str) -> HistoryMessage:
    return HistoryMessage(role="user", content=[{"type": "text", "text": text}])


def names(value: JsonValue, key: str, kind: str | None) -> list[str]:
    """The names of the objects under key (of the given type), in a stored JSON object."""
    items = value.get(key) if isinstance(value, dict) else None
    return [
        str(item.get("name"))
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and (kind is None or item.get("type") == kind)
    ]


def two_calls_three_tools() -> ScriptedResponse:
    return ScriptedResponse(
        text="Done.",
        tool_calls=(
            ("write_file", {"path": "a.txt", "content": "hello\n"}),
            ("read_file", {"path": "a.txt"}),
            ("list_files", {}),
        ),
        call_usages=(FIRST, SECOND),
    )


def test_a_reply_with_two_model_calls_and_three_tool_calls_writes_five_steps_in_order(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(two_calls_three_tools())
    send(client, conversation_id, "Write and check a.txt")

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        steps = run_steps(session, run.id)

    assert [(step.kind, step.tool) for step in steps] == [
        ("model", None),
        ("tool", "write_file"),
        ("tool", "read_file"),
        ("tool", "list_files"),
        ("model", None),
    ]
    assert [step.position for step in steps] == [1, 2, 3, 4, 5]
    assert steps[0].usage == FIRST and steps[4].usage == SECOND
    assert all(step.model == "claude-opus-5-5" and step.model_calls == 1 for step in steps if step.kind == "model")
    assert steps[1].summary and "a.txt" in steps[1].summary
    assert all(step.approval is None and not step.is_error for step in steps if step.kind == "tool")
    starts = [step.started_at for step in steps]
    assert starts == sorted(starts)
    assert all(step.snapshot for step in steps if step.kind == "model")


def test_the_usage_row_equals_the_sum_of_the_steps(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(two_calls_three_tools())
    send(client, conversation_id, "Write and check a.txt")

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        steps = run_steps(session, run.id)
        usage = session.scalars(select(UsageRecord)).one()

    total = model_usage(steps)
    assert usage.message_id == run.message_id
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
        total.input_tokens,
        total.output_tokens,
        total.cache_read_tokens,
        total.cache_write_tokens,
    )
    assert usage.cost == pytest.approx(sum(step.cost or 0 for step in steps))


def test_the_second_model_call_saw_the_tool_calls_of_the_first(client, fake, tmp_path):
    _, conversation_id = project_conversation(client)
    fake.responses.append(two_calls_three_tools())
    send(client, conversation_id, "Write and check a.txt")

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        first, *_, last = run_steps(session, run.id)
    store = SnapshotStore(trace_dir(tmp_path))
    before, after = store.load(first.snapshot or ""), store.load(last.snapshot or "")
    assert before is not None and after is not None and after.complete
    assert before.system[0].source == "base"
    assert any(part.source == "project" for part in before.system)
    assert len(after.messages) == len(before.messages) + 1
    assert names(after.messages[-1], "content", "tool_call") == ["write_file", "read_file", "list_files"]
    assert {"read_file", "write_file"} <= set(names({"tools": after.tools}, "tools", None))


def test_a_sub_runs_steps_hang_under_the_sub_run(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="The worker made it.", tool_calls=(("delegate", {"task": "Create hello.txt"}),)),
            ScriptedResponse(
                text="Created.",
                tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),),
                usage=TokenUsage(input_tokens=300, output_tokens=40),
            ),
        ]
    )
    send(client, conversation_id, "Make hello")

    with client.app.state.session_factory() as session:
        (root,) = conversation_runs(session, conversation_id)
        (child,) = root.children
        parent_steps = run_steps(session, root.id)
        child_steps = run_steps(session, child.id)

    assert child.task == "Create hello.txt"
    assert [(step.kind, step.tool) for step in parent_steps] == [("model", None), ("tool", "delegate")]
    assert [(step.kind, step.tool) for step in child_steps] == [("model", None), ("tool", "write_file")]
    # A provider that only says Completed is one step with the reply's usage, of unknown call count.
    assert child_steps[0].usage == TokenUsage(input_tokens=300, output_tokens=40)
    assert child_steps[0].model == "claude-sonnet-5-5" and child_steps[0].model_calls is None


def test_a_refused_call_records_the_decision(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Ok.", tool_calls=(("write_file", {"path": "x.txt", "content": "x"}),)))
    client.patch(f"/api/v1/conversations/{conversation_id}", json={"access": "read_only"})
    send(client, conversation_id, "Write x")

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        tool = next(step for step in run_steps(session, run.id) if step.kind == "tool")
    assert tool.is_error and tool.approval == "refused"


def test_two_calls_with_the_same_system_part_store_it_once(tmp_path):
    store = SnapshotStore(tmp_path / "traces")
    system = [SystemPart("The same rules for every call.", source="rules")]
    tools = [ToolDefinition("read_file", "Read a file.", {"type": "object"})]
    first = store.save(ContextSnapshot(system, tools, [user("Hi")]), {})
    second = store.save(ContextSnapshot(system, tools, [user("Hi"), user("And now?")]), {})

    files = list((tmp_path / "traces" / "blobs").glob("*/*.json.gz"))
    # The system part, the tools, two messages and two manifests.
    assert len(files) == 6
    one, two = store.load(first), store.load(second)
    assert one is not None and two is not None
    assert one.system[0].text == two.system[0].text == "The same rules for every call."
    assert one.system[0].source == "rules"
    assert two.messages[-1] == {"role": "user", "content": [{"type": "text", "text": "And now?"}]}


def test_no_snapshot_holds_a_vault_value_only_placeholders(tmp_path):
    import base64

    store = SnapshotStore(tmp_path / "traces")
    encoded = base64.b64encode(SECRET_VALUE.encode()).decode()
    leaked = HistoryMessage(
        role="assistant",
        content=[{"type": "tool_call", "name": "write_file", "input": {"content": SECRET_VALUE}, "output": encoded}],
    )
    snapshot = ContextSnapshot(
        [SystemPart("Use {{secret:API_TOKEN}} in commands; never " + SECRET_VALUE)],
        [],
        [user("Deploy with {{secret:API_TOKEN}}"), leaked],
    )
    store.save(snapshot, {"API_TOKEN": SECRET_VALUE})

    texts = blob_texts(tmp_path)
    assert not any(SECRET_VALUE in text or encoded in text for text in texts)
    assert any("{{secret:API_TOKEN}}" in text for text in texts)
    assert any("[secret:API_TOKEN]" in text for text in texts)


def test_a_reply_snapshot_masks_a_secret_the_model_wrote(client, fake, tmp_path):
    _, conversation_id = project_conversation(client)
    client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": SECRET_VALUE})
    fake.responses.append(
        ScriptedResponse(
            text="Saved.",
            tool_calls=(("write_file", {"path": "token.txt", "content": SECRET_VALUE}),),
            call_usages=(FIRST, SECOND),
        )
    )
    send(client, conversation_id, "Save the token")

    texts = blob_texts(tmp_path)
    assert texts and not any(SECRET_VALUE in text for text in texts)
    assert any("{{secret:NAME}}" in text or "API_TOKEN" in text for text in texts)
    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        summaries = [step.summary or "" for step in run_steps(session, run.id)]
    assert not any(SECRET_VALUE in summary for summary in summaries)


def make_old(folder: Path, days: int) -> None:
    then = time.time() - days * 86400
    for path in folder.glob("*/*.json.gz"):
        os.utime(path, (then, then))


def test_pruning_keeps_pinned_snapshots(tmp_path):
    store = SnapshotStore(tmp_path / "traces")
    system = [SystemPart("Shared rules.", source="rules")]
    pinned = store.save(ContextSnapshot(system, [], [user("Kept for the eval case")]), {})
    loose = store.save(ContextSnapshot(system, [], [user("Gone after 30 days")]), {})
    store.pin(pinned, "eval-case:1")
    make_old(store.blobs, 40)

    assert store.prune() > 0
    kept = store.load(pinned)
    assert kept is not None and kept.complete
    assert store.load(loose) is None

    store.unpin(pinned, "eval-case:1")
    make_old(store.blobs, 40)
    store.prune()
    assert store.load(pinned) is None


def test_pruning_removes_the_oldest_unpinned_snapshots_above_the_size_limit(tmp_path):
    store = SnapshotStore(tmp_path / "traces")
    pinned = store.save(ContextSnapshot([], [], [user("pinned " * 200)]), {})
    store.pin(pinned, "replay:1")
    make_old(store.blobs, 20)
    oldest = store.save(ContextSnapshot([], [], [user("oldest " * 200)]), {})
    make_old(store.blobs, 10)
    newest = store.save(ContextSnapshot([], [], [user("newest " * 200)]), {})
    sizes = {path.name: path.stat().st_size for path in store.blobs.glob("*/*.json.gz")}

    store.prune(max_bytes=sum(sizes.values()) - 1)

    assert store.load(pinned) is not None
    assert store.load(newest) is not None
    gone = store.load(oldest)
    assert gone is None or not gone.complete
