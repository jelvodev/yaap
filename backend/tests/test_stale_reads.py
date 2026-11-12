"""Reads that went stale: at a trim point the result of a read of a file edited later becomes a stub."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.core.trimming import apply_trim, mark_trim, stale_reads
from app.providers.base import HistoryMessage
from app.core.why import Why
from app.storage.models import AgentRunRecord, Message, RunStep
from app.tracing.notes import run_notes
from tests.test_chat import start_conversation

LONG = "x" * 5000
STUB = "Read of a.py lines 1\u2013400, left out: the file changed since; read it again."


def call(call_id: str, name: str, path: str, is_error: bool = False, **extra) -> dict:
    return {
        "type": "tool_call", "id": call_id, "name": name, "input": {"path": path, **extra},
        "output": LONG, "is_error": is_error,
    }


def reply(blocks: list[dict], raw: list[dict] | None = None, conversation_id: int | None = None) -> Message:
    return Message(
        conversation_id=conversation_id,
        role="assistant",
        content=blocks,
        provider_raw=raw if raw is not None else [{"type": "text", "text": "ok"}],
        status="complete",
    )


def question(conversation_id: int | None = None) -> Message:
    content = [{"type": "text", "text": "Q"}]
    return Message(conversation_id=conversation_id, role="user", content=content, status="complete")


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([call("r", "read_file", "a.py"), call("e", "edit_file", "a.py")], {"r"}),
        ([call("e", "edit_file", "a.py"), call("r", "read_file", "a.py")], set()),
        ([call("r", "read_file", "a.py"), call("e", "edit_file", "a.py", is_error=True)], set()),
        ([call("r", "read_file", "a.py"), call("e", "edit_file", "b.py")], set()),
        ([call("r", "read_file", "./a.py"), call("e", "edit_file", "a.py")], {"r"}),
        ([call("r", "read_file", "src" + chr(92) + "a.py"), call("e", "edit_file", "src/a.py")], {"r"}),
        ([call("r", "read_file", "a.py"), call("w", "write_file", "a.py")], {"r"}),
        (
            [call("r1", "read_file", "a.py"), call("w", "write_file", "a.py"), call("r2", "read_file", "a.py")],
            {"r1"},
        ),
    ],
)
def test_a_read_is_stale_only_when_a_later_successful_write_changed_the_file(calls, expected):
    branch = [question(), reply(calls)]
    assert stale_reads(branch) == expected


def trim_branch(conversation_id: int | None = None) -> list[Message]:
    first = [call("r", "read_file", "a.py"), call("o", "read_file", "b.py")]
    branch = [question(conversation_id), reply(first, conversation_id=conversation_id)]
    branch += [question(conversation_id), reply([call("e", "edit_file", "a.py")], conversation_id=conversation_id)]
    for _ in range(4):
        branch += [question(conversation_id), reply([{"type": "text", "text": "ok"}], conversation_id=conversation_id)]
    return branch


@pytest.mark.parametrize(("setting", "expected"), [("on", {"type": "trim", "stale": ["r"]}), ("off", {"type": "trim"})])
def test_the_trim_block_lists_stale_reads_only_with_context_savings_on(client, setting, expected):
    conversation_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        store_setting(session, SETTING_DEFINITIONS["context_savings"], None, setting)
        branch = trim_branch(conversation_id)
        session.add_all(branch)
        session.commit()
        assert mark_trim(session, branch)
        assert [block for message in branch for block in message.content if block["type"] == "trim"] == [expected]


def stubbed_branch(raw: list[dict]) -> list[Message]:
    first = [call("r", "read_file", "a.py"), call("o", "read_file", "b.py")]
    branch = [question(), reply(first, raw), question(), reply([call("e", "edit_file", "a.py")])]
    branch[3].content.append({"type": "trim", "stale": ["r"]})
    return branch


def trimmed(raw: list[dict]) -> list[dict]:
    branch = stubbed_branch(raw)
    history = [HistoryMessage(role=item.role, content=item.content, provider_raw=item.provider_raw) for item in branch]
    return apply_trim(branch, history)[1].provider_raw


def test_the_stub_replaces_a_stale_result_in_anthropic_turns():
    raw = [{"type": "anthropic_turns", "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "r", "content": [{"type": "text", "text": LONG}]},
            {"type": "tool_result", "tool_use_id": "o", "content": [{"type": "text", "text": LONG}]},
        ]},
    ]}]
    parts = trimmed(raw)[0]["messages"][1]["content"]
    assert parts[0]["content"] == [{"type": "text", "text": STUB}]
    assert "left out to save context" in parts[1]["content"][0]["text"]


def test_the_stub_replaces_a_stale_result_in_chat_messages():
    raw = [{"type": "chat_messages", "messages": [
        {"role": "tool", "tool_call_id": "r", "content": LONG},
        {"role": "tool", "tool_call_id": "o", "content": LONG},
    ]}]
    items = trimmed(raw)[0]["messages"]
    assert items[0]["content"] == STUB and "left out to save context" in items[1]["content"]


def test_the_stub_replaces_a_stale_result_in_openai_items():
    raw = [{"type": "openai_items", "items": [
        {"type": "function_call_output", "call_id": "r", "output": LONG},
        {"type": "function_call_output", "call_id": "o", "output": LONG},
    ]}]
    items = trimmed(raw)[0]["items"]
    assert items[0]["output"] == STUB and "left out to save context" in items[1]["output"]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"offset": 10, "limit": 5}, "Read of a.py lines 10\u201314, left out: the file changed since; read it again."),
        ({"outline": True}, "Outline of a.py, left out: the file changed since; read it again."),
    ],
)
def test_the_stub_names_the_lines_or_the_outline(extra, expected):
    branch = [question(), reply([call("r", "read_file", "a.py", **extra), call("e", "edit_file", "a.py")])]
    output = {"type": "function_call_output", "call_id": "r", "output": LONG}
    branch[1].provider_raw = [{"type": "openai_items", "items": [output]}]
    branch[1].content.append({"type": "trim", "stale": ["r"]})
    history = [HistoryMessage(role=item.role, content=item.content, provider_raw=item.provider_raw) for item in branch]
    assert apply_trim(branch, history)[1].provider_raw[0]["items"][0]["output"] == expected


def test_marking_a_trim_writes_a_note_with_its_reason_on_the_replys_run(client):
    conversation_id = start_conversation(client)
    why = Why("The context reached 90%.", "trim", "trim_at")
    with client.app.state.session_factory() as session:
        branch = trim_branch(conversation_id)
        session.add_all(branch)
        session.commit()
        session.add(AgentRunRecord(
            environment_id=1, conversation_id=conversation_id, model="m", status="complete", message_id=branch[-1].id,
            started_at=datetime.now(UTC),
        ))
        session.commit()

        assert mark_trim(session, branch, why)

        (view,) = run_notes(session, session.scalars(select(AgentRunRecord.id)).all())
    assert view.note.kind == "trim" and view.note.why == why


def test_marking_a_trim_without_a_run_for_the_reply_writes_no_note(client):
    conversation_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        branch = trim_branch(conversation_id)
        session.add_all(branch)
        session.commit()

        assert mark_trim(session, branch)

        assert session.scalar(select(func.count()).select_from(RunStep)) == 0
