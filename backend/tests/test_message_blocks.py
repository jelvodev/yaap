"""Message blocks are validated when stored, normalized when read, and the server alone owns the call key."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.checkpoints.service import call_key
from app.core.chat import store_assistant_reply
from app.core.chat_events import ReplyContent, ReplyOutcome
from app.core.conversation_schemas import message_to_out
from app.core.message_blocks import InvalidBlock, ToolCallBlockOut, blocks_out, validate_blocks
from app.providers.base import ToolCallStarted
from app.storage.models import Conversation, Message

TOOL = {
    "type": "tool_call",
    "id": "t1",
    "name": "read_file",
    "input": {"path": "a"},
    "call_key": "k",
    "checkpointable": False,
}


@pytest.mark.parametrize(
    "block",
    [
        {"type": "text", "text": "hi"},
        {"type": "text"},
        {"type": "reasoning", "text": "hmm"},
        TOOL,
        {k: v for k, v in TOOL.items() if k not in ("call_key", "checkpointable")},
        {"type": "attachment", "attachment_id": 1, "name": "a.png"},
        {"type": "command", "name": "card", "text": "x", "card_id": 3},
        {"type": "skill", "name": "s"},
        {"type": "skill_removed", "name": "s"},
        {"type": "goal", "text": "t", "turn": 1, "max_turns": 5},
        {"type": "summary", "text": "t", "covered": 2},
        {"type": "trim"},
        {"type": "steering", "text": "t"},
        {"type": "note", "text": "t"},
        {"type": "repo_map", "key": "internal"},
        {"type": "plan", "plan_id": 1},
    ],
)
def test_a_valid_block_of_each_kind_is_stored_unchanged(block: dict[str, Any]) -> None:
    content = [block]
    assert validate_blocks(content) == [block]


@pytest.mark.parametrize(
    "block, field",
    [
        ({"type": "tool_call", "name": "read_file", "input": {}}, "id"),
        ({"type": "tool_call", "id": "t", "input": {}}, "name"),
        ({"type": "tool_call", "id": "t", "name": "x"}, "input"),
        ({"type": "tool_call", "id": "t", "name": "x", "input": []}, "input"),
        ({"type": "attachment", "name": "a"}, "attachment_id"),
        ({"type": "command", "name": "card"}, "text"),
        ({"type": "goal", "text": "t", "turn": "one", "max_turns": 5}, "turn"),
        ({"type": "summary", "text": "t"}, "covered"),
        ({"type": "plan"}, "plan_id"),
        ({"type": "no_such_kind", "text": "t"}, "-"),
        ({"text": "no type"}, "-"),
    ],
)
def test_an_invalid_block_is_refused_and_the_error_names_the_field_not_the_text(
    block: dict[str, Any], field: str
) -> None:
    with pytest.raises(InvalidBlock, match=field) as error:
        validate_blocks([{"type": "text", "text": "SECRET"}, block])
    assert "Block 1" in str(error.value)
    assert "SECRET" not in str(error.value)


def test_a_reply_with_a_tool_call_without_an_id_is_refused_at_store(client: TestClient) -> None:
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        user = Message(conversation_id=conversation_id, role="user", content=[{"type": "text", "text": "q"}])
        session.add(user)
        session.commit()
        parent_id = user.id
    broken = [{"type": "tool_call", "name": "read_file", "input": {}}]
    with pytest.raises(InvalidBlock):
        store_assistant_reply(
            session_factory,
            client.app.state.providers,
            conversation_id,
            parent_id,
            broken,
            ReplyOutcome("complete", None, None),
            None,
        )
    with session_factory() as session:
        assert session.get_one(Conversation, conversation_id).head_message_id != parent_id + 1
        assert session.query(Message).filter_by(role="assistant").count() == 0


def stored_message(message_id: int, content: list[dict[str, Any]]) -> Message:
    return Message(
        id=message_id,
        conversation_id=1,
        parent_id=None,
        role="assistant",
        content=content,
        status="complete",
        created_at=datetime(2026, 11, 13, tzinfo=UTC),
    )


def test_a_legacy_tool_call_without_an_id_loads_with_a_legacy_id_and_an_unknown_kind_is_left_out() -> None:
    content = [
        {"type": "text", "text": "a"},
        {"type": "hologram", "text": "from the future"},
        {"type": "tool_call", "name": "read_file", "input": {"path": "a"}, "output": "x"},
    ]
    shown = message_to_out(stored_message(7, content)).content
    assert [block.type for block in shown] == ["text", "tool_call"]
    call = shown[1]
    assert isinstance(call, ToolCallBlockOut)
    # The id carries the block's index in the stored content, the left-out block included.
    assert call.id == "legacy-7-2"
    assert call.name == "read_file" and call.output == "x"


def test_a_legacy_tool_call_without_name_or_input_still_loads() -> None:
    (call,) = blocks_out(3, [{"type": "tool_call", "id": "t"}])
    assert isinstance(call, ToolCallBlockOut)
    assert (call.name, call.input, call.call_key) == ("tool", {}, call_key("tool", {}))


@pytest.mark.parametrize(
    "name, imported, checkpointable",
    [("write_file", False, True), ("edit_file", False, True), ("read_file", False, False), ("write_file", True, False)],
)
def test_a_legacy_tool_call_without_a_call_key_gets_the_servers_key(
    name: str, imported: bool, checkpointable: bool
) -> None:
    tool_input = {"path": "a", "content": "x"}
    block = {"type": "tool_call", "id": "t", "name": name, "input": tool_input, "imported": imported}
    (call,) = blocks_out(1, [block])
    assert isinstance(call, ToolCallBlockOut)
    assert call.call_key == call_key(name, tool_input)
    assert call.checkpointable is checkpointable
    assert "call_key" not in block


def test_the_call_key_of_a_block_equals_the_key_of_the_tool_call_event_for_the_same_call() -> None:
    tool_input = {"timeout_seconds": 5.0}
    reply = ReplyContent()
    event = reply.start_tool(ToolCallStarted(id="t1", name="run_command", input=tool_input))
    assert event.call_key == call_key("run_command", tool_input)
    (block,) = reply.content()[:1]
    assert block["call_key"] == event.call_key
    # What is stored and read back is the same key, and a block without one is given that same key.
    (loaded,) = blocks_out(1, [block])
    (legacy,) = blocks_out(1, [{k: v for k, v in block.items() if k not in ("call_key", "checkpointable")}])
    assert isinstance(loaded, ToolCallBlockOut) and isinstance(legacy, ToolCallBlockOut)
    assert loaded.call_key == legacy.call_key == event.call_key
