"""Context control: the context limit, the reply length, automatic compaction and /compact."""

import pytest
from sqlalchemy import select

from app.core.context_control import ContextControl, resolve_context_control
from app.core.trimming import summary_why, trim_why
from app.providers.base import TokenUsage
from app.providers.claude_code_provider import claude_code_environment
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation, Message
from tests.test_chat import add_key, send, start_conversation
from tests.test_commands_and_skills import put_item
from tests.test_summaries import chat_turns

OPUS = "claude-opus-5-5"


def control(client, conversation_id: int):
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        return resolve_context_control(session, client.app.state.providers, conversation)


def summaries(client, conversation_id: int) -> list[str]:
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    return [block["text"] for message in conversation["messages"] for block in message["content"] if block["type"] == "summary"]


def project_with_conversation(client, **extra) -> tuple[int, int]:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    response = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": OPUS, "effort": "medium", "project_id": project["id"], **extra},
    )
    assert response.status_code == 201, response.text
    return project["id"], response.json()["id"]


def test_without_a_limit_the_window_counts(client):
    conversation_id = start_conversation(client)
    found = control(client, conversation_id)
    assert (found.window, found.limit, found.source) == (1_000_000, 1_000_000, "default")
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert shown["context_control"]["limit"] == 1_000_000 and shown["context_limit"] is None


def test_the_nearest_level_sets_the_limit_and_the_conversation_wins(client):
    project_id, conversation_id = project_with_conversation(client)
    assert client.put("/api/v1/settings/context_limit", json={"value": 400_000}).status_code == 200
    assert (control(client, conversation_id).limit, control(client, conversation_id).source) == (400_000, "global")
    client.put("/api/v1/environments/1/settings/context_limit", json={"value": 300_000})
    assert control(client, conversation_id).limit == 300_000
    client.put(f"/api/v1/projects/{project_id}/settings/context_limit", json={"value": 200_000})
    assert (control(client, conversation_id).limit, control(client, conversation_id).source) == (200_000, "project")
    # "window" at a nearer level returns to the model's window.
    client.put(f"/api/v1/projects/{project_id}/settings/context_limit", json={"value": "window"})
    assert (control(client, conversation_id).limit, control(client, conversation_id).source) == (1_000_000, "default")

    changed = client.patch(f"/api/v1/conversations/{conversation_id}", json={"context_limit": 100_000})
    assert changed.status_code == 200 and changed.json()["context_limit"] == 100_000
    assert (control(client, conversation_id).limit, control(client, conversation_id).source) == (100_000, "conversation")
    cleared = client.patch(f"/api/v1/conversations/{conversation_id}", json={"context_limit": None})
    assert cleared.json()["context_limit"] is None and control(client, conversation_id).source == "default"


def test_limits_are_checked_and_never_exceed_the_window(client):
    assert client.put("/api/v1/settings/context_limit", json={"value": "half"}).status_code == 422
    assert client.put("/api/v1/settings/context_limit", json={"value": 500}).status_code == 422
    assert client.put("/api/v1/settings/context_limit", json={"value": 5_000_000}).status_code == 200
    conversation_id = start_conversation(client)
    assert control(client, conversation_id).limit == 1_000_000
    too_large = client.post(
        "/api/v1/environments/1/conversations", json={"model": OPUS, "effort": "medium", "context_limit": 2_000_000}
    )
    assert too_large.status_code == 422 and "window" in too_large.json()["detail"]
    chosen = client.post(
        "/api/v1/environments/1/conversations", json={"model": OPUS, "effort": "medium", "context_limit": 150_000}
    )
    assert chosen.status_code == 201 and chosen.json()["context_limit"] == 150_000
    assert client.patch(f"/api/v1/conversations/{conversation_id}", json={"context_limit": 10}).status_code == 422


def test_the_limit_wins_over_the_window_for_summaries(client, fake):
    client.app.state.background_summaries = False
    add_key(client)
    conversation_id = start_conversation(client)
    # 150K tokens is 15% of the window: no summary.
    chat_turns(client, fake, conversation_id, 5, TokenUsage(input_tokens=150_000, output_tokens=10))
    assert summaries(client, conversation_id) == []

    client.patch(f"/api/v1/conversations/{conversation_id}", json={"context_limit": 200_000})
    fake.responses.extend(
        [ScriptedResponse(text="Answer.", usage=TokenUsage(input_tokens=150_000, output_tokens=10)), ScriptedResponse(text="Short.")]
    )
    send(client, conversation_id, "One more?")
    assert summaries(client, conversation_id) == ["Short."]


def test_the_limit_wins_over_the_window_for_trimming(client, fake):
    from app.core.trimming import needs_trim

    add_key(client)
    conversation_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        providers = client.app.state.providers
        assert not needs_trim(session, providers, conversation, 120_000)
        conversation.context_limit = 200_000
        session.commit()
        assert needs_trim(session, providers, conversation, 120_000)


def test_the_reply_length_comes_from_the_preset(client, fake):
    add_key(client)
    models = {model["id"]: model for model in client.get("/api/v1/models").json()}
    assert models[OPUS]["max_output_tokens"] == 128_000
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": OPUS, "effort": "medium", "preset": "Balanced"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Hi."))
    send(client, conversation_id, "Hello")
    assert fake.requests[-1].max_tokens == 128_000  # The model's maximum by default.

    balanced = next(preset for preset in client.get("/api/v1/environments/1/presets").json() if preset["name"] == "Balanced")
    too_long = client.patch(f"/api/v1/presets/{balanced['id']}", json={"max_output_tokens": 200_000})
    assert too_long.status_code == 422 and "128,000" in too_long.json()["detail"]
    changed = client.patch(f"/api/v1/presets/{balanced['id']}", json={"max_output_tokens": 20_000})
    assert changed.status_code == 200 and changed.json()["max_output_tokens"] == 20_000
    fake.responses.append(ScriptedResponse(text="Hi again."))
    send(client, conversation_id, "Again")
    assert fake.requests[-1].max_tokens == 20_000

    # A name change leaves it alone; null returns to the model's maximum.
    assert client.patch(f"/api/v1/presets/{balanced['id']}", json={"name": "Middle"}).json()["max_output_tokens"] == 20_000
    assert client.patch(f"/api/v1/presets/{balanced['id']}", json={"max_output_tokens": None}).json()["max_output_tokens"] is None
    created = client.post(
        "/api/v1/environments/1/presets",
        json={"name": "Short", "model": "claude-haiku-5-5", "effort": "low", "max_output_tokens": 4000},
    )
    assert created.status_code == 201 and created.json()["max_output_tokens"] == 4000


def test_claude_code_gets_the_reply_length():
    assert claude_code_environment(20_000)["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "20000"


def test_auto_compact_off_leaves_the_conversation_alone(client, fake):
    client.app.state.background_summaries = False
    add_key(client)
    assert client.put("/api/v1/environments/1/settings/auto_compact", json={"value": "off"}).status_code == 200
    conversation_id = start_conversation(client)
    window = 1_000_000
    chat_turns(client, fake, conversation_id, 6, TokenUsage(input_tokens=int(window * 0.9), output_tokens=10))
    assert summaries(client, conversation_id) == []
    with client.app.state.session_factory() as session:
        stored = session.scalars(select(Message).where(Message.conversation_id == conversation_id)).all()
        assert not any(block.get("type") == "trim" for message in stored for block in message.content)
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()["context_control"]
    assert shown["auto_compact"] is False
    # By hand it still works.
    fake.responses.append(ScriptedResponse(text="By hand."))
    assert client.post(f"/api/v1/conversations/{conversation_id}/summary").status_code == 200
    assert summaries(client, conversation_id) == ["By hand."]


def test_compact_at_and_trim_at_replace_the_fixed_shares(client, fake):
    client.app.state.background_summaries = False
    add_key(client)
    assert client.put("/api/v1/settings/compact_at", json={"value": 5}).status_code == 422
    assert client.put("/api/v1/settings/trim_at", json={"value": "half"}).status_code == 422
    client.put("/api/v1/settings/compact_at", json={"value": 40})
    client.put("/api/v1/settings/trim_at", json={"value": 20})
    conversation_id = start_conversation(client)
    found = control(client, conversation_id)
    assert (found.compact_at, found.trim_at, found.auto_compact) == (40, 20, True)
    assert found.needs_trim(250_000) and not found.needs_summary(350_000) and found.needs_summary(400_000)
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()["context_control"]
    assert (shown["compact_at"], shown["trim_at"], shown["compacts_itself"]) == (40, 20, False)

    # 45% of the window: past 40%, so summarised.
    chat_turns(client, fake, conversation_id, 4, TokenUsage(input_tokens=10, output_tokens=10))
    fake.responses.extend(
        [ScriptedResponse(text="Answer.", usage=TokenUsage(input_tokens=450_000, output_tokens=10)), ScriptedResponse(text="At 40.")]
    )
    send(client, conversation_id, "Big question?")
    assert summaries(client, conversation_id) == ["At 40."]


def test_yaap_compacts_ollama_but_not_claude_code_or_codex(client):
    from app.core.context_control import compacts_itself
    from app.providers.registry import default_provider_registry

    registry = default_provider_registry()
    local = Conversation(provider="ollama", model="ollama/qwen3:8b", environment_id=1)
    with client.app.state.session_factory() as session:
        assert not compacts_itself(session, registry, local)
        client.put("/api/v1/environments/1/settings/openai_connection", json={"value": "codex"})
        assert compacts_itself(session, registry, Conversation(provider="openai", model="gpt-6.1-sol", environment_id=1))
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = start_conversation(client)
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()["context_control"]
    assert shown["compacts_itself"] is True
    assert not control(client, conversation_id).needs_summary(900_000)


def prompt_of(request) -> str:
    return " ".join(block.get("text", "") for message in request.messages for block in message.content)


def test_compact_summarises_now_with_and_without_a_focus(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, 3)
    too_early = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/compact"})
    assert too_early.status_code == 409 and "not enough" in too_early.json()["detail"]

    chat_turns(client, fake, conversation_id, 3, start=3)
    before = len(client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"])
    fake.responses.append(ScriptedResponse(text="Plain summary."))
    events = send(client, conversation_id, "/compact")
    assert [name for name, _ in events] == ["compacted"] and events[0][1]["focus"] is None
    assert "focus on" not in prompt_of(fake.requests[-1])
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert len(conversation["messages"]) == before  # Nothing is added but the summary.
    assert summaries(client, conversation_id) == ["Plain summary."]

    chat_turns(client, fake, conversation_id, 2, start=6)
    fake.responses.append(ScriptedResponse(text="Focused summary."))
    events = send(client, conversation_id, "/compact   keep the API decisions")
    assert events[0][0] == "compacted" and events[0][1]["focus"] == "keep the API decisions"
    assert "focus on: keep the API decisions." in prompt_of(fake.requests[-1])
    assert "Plain summary." in prompt_of(fake.requests[-1])  # It builds on the previous summary.
    blocks = [
        block
        for message in client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
        for block in message["content"]
        if block["type"] == "summary"
    ]
    assert [(block["text"], block["focus"]) for block in blocks] == [
        ("Plain summary.", None),
        ("Focused summary.", "keep the API decisions"),
    ]


def test_summarise_now_takes_a_focus_and_failures_are_reported(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, 6)
    fake.responses.append(ScriptedResponse(text="Kept."))
    done = client.post(f"/api/v1/conversations/{conversation_id}/summary", json={"focus": "the database"})
    assert done.status_code == 200 and "focus on: the database." in prompt_of(fake.requests[-1])
    chat_turns(client, fake, conversation_id, 1, start=6)
    fake.responses.append(ScriptedResponse(text=""))  # No text: no summary.
    events = send(client, conversation_id, "/compact")
    assert events[0][0] == "compact_failed" and "No model" in events[0][1]["detail"]


def test_a_blueprint_command_named_compact_wins(client, fake):
    add_key(client)
    put_item(client, "commands/compact.md", "Make the code more compact: $ARGUMENTS\n")
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Shorter now."))
    events = send(client, conversation_id, "/compact utils.py")
    assert events[-1][0] == "done"
    assert "Make the code more compact: utils.py" in prompt_of(fake.requests[-1])
    assert summaries(client, conversation_id) == []


def test_compact_is_refused_where_the_connection_compacts_itself(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, 6)
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/compact"})
    assert refused.status_code == 409 and "compact their own" in refused.json()["detail"]


@pytest.mark.parametrize(
    ("control", "used", "trim", "summary"),
    [
        (ContextControl(None, 100_000, "default"), 49_000, False, False),
        (ContextControl(None, 100_000, "default"), 50_000, True, False),
        (ContextControl(None, 100_000, "default"), 70_000, True, True),
        (ContextControl(None, 100_000, "default", auto_compact=False), 90_000, False, False),
        (ContextControl(None, 100_000, "default", compacts_itself=True), 90_000, False, False),
        (ContextControl(None, None, "default"), 90_000, False, False),
        (ContextControl(None, 100_000, "default"), None, False, False),
        (ContextControl(None, 100_000, "default", compact_at=80, trim_at=60), 65_000, True, False),
    ],
)
def test_trim_and_summary_reasons_exist_exactly_when_the_thresholds_are_reached(control, used, trim, summary):
    trimmed, summarised = trim_why(control, used), summary_why(control, used)
    assert (trimmed is not None, summarised is not None) == (trim, summary)
    assert (trimmed is None) == (not control.needs_trim(used))
    assert (summarised is None) == (not control.needs_summary(used))
    if trimmed:
        assert (trimmed.source, trimmed.setting) == ("trim", "trim_at")
        assert f"{control.trim_at}%" in trimmed.rule and f"{control.limit:,}" in trimmed.rule
    if summarised:
        assert (summarised.source, summarised.setting) == ("summary", "compact_at")
        assert f"{control.compact_at}%" in summarised.rule
