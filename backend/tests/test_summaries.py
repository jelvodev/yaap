"""Summarising long conversations: when, what the model then gets, and the marker."""

from sqlalchemy import select

from app.core.summaries import KEEP_TURNS, SUMMARY_HEADING
from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import UsageRecord
from tests.test_chat import add_key, send, start_conversation


def chat_turns(client, fake, conversation_id: int, count: int, usage: TokenUsage | None = None, start: int = 0) -> None:
    for number in range(start, start + count):
        response = ScriptedResponse(text=f"Answer {number}.") if usage is None else ScriptedResponse(text=f"Answer {number}.", usage=usage)
        fake.responses.append(response)
        send(client, conversation_id, f"Question {number}?")


def history_texts(request) -> list[str]:
    return [
        " ".join(block.get("text", "") for block in message.content if block.get("type") == "text")
        for message in request.messages
    ]


def test_summarise_now_replaces_the_earlier_messages_for_the_model(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, 3)
    # Too little: the last turns always stay whole.
    early = client.post(f"/api/v1/conversations/{conversation_id}/summary")
    assert early.status_code == 409 and "not enough" in early.json()["detail"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/context").json()["can_summarise"] is False

    chat_turns(client, fake, conversation_id, 4, start=3)
    assert client.get(f"/api/v1/conversations/{conversation_id}/context").json()["can_summarise"] is True
    fake.responses.append(ScriptedResponse(text="## Goal\nThe user asked seven questions."))
    summary = client.post(f"/api/v1/conversations/{conversation_id}/summary")
    assert summary.status_code == 200, summary.text
    # 7 turns, the last KEEP_TURNS stay: the first 3 turns (6 messages) are covered.
    assert summary.json()["covered"] == (7 - KEEP_TURNS) * 2
    prompt = history_texts(fake.requests[-1])[0]
    assert "Question 0?" in prompt and "Answer 2." in prompt and "Question 3?" not in prompt

    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    marked = [message for message in conversation["messages"] if any(b["type"] == "summary" for b in message["content"])]
    assert len(marked) == 1 and marked[0]["id"] == summary.json()["message_id"]
    assert marked[0]["content"][-1]["text"].startswith("## Goal")

    fake.responses.append(ScriptedResponse(text="Next."))
    send(client, conversation_id, "And now?")
    texts = history_texts(fake.requests[-1])
    assert texts[0].startswith(SUMMARY_HEADING) and "The user asked seven questions." in texts[0]
    assert "Question 3?" in texts[0]  # The summary goes in front of the first kept message.
    assert not any("Question 2?" in text for text in texts)
    assert texts[-1].startswith("And now? [Context for this message]")

    breakdown = client.get(f"/api/v1/conversations/{conversation_id}/context").json()
    assert breakdown["summarised_messages"] == 6
    assert any(part["label"] == "Summary of 6 earlier messages" for part in breakdown["parts"])
    with client.app.state.session_factory() as session:
        record = session.scalar(select(UsageRecord).where(UsageRecord.task == "summary"))
        assert record is not None and record.purpose == "utility"


def test_a_full_context_is_summarised_after_the_reply(client, fake):
    client.app.state.background_summaries = False
    add_key(client)
    conversation_id = start_conversation(client)
    window = client.app.state.providers.find_model("claude-opus-5-5").context_window
    chat_turns(client, fake, conversation_id, KEEP_TURNS + 1, TokenUsage(input_tokens=int(window * 0.5), output_tokens=10))
    with client.app.state.session_factory() as session:
        assert session.scalar(select(UsageRecord).where(UsageRecord.task == "summary")) is None

    fake.responses.extend(
        [
            ScriptedResponse(text="Answer 5.", usage=TokenUsage(input_tokens=int(window * 0.75), output_tokens=10)),
            ScriptedResponse(text="Summary of the start."),
        ]
    )
    send(client, conversation_id, "Question 5?")
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    summaries = [b for message in conversation["messages"] for b in message["content"] if b["type"] == "summary"]
    assert [block["text"] for block in summaries] == ["Summary of the start."]


def test_claude_code_conversations_are_not_summarised_by_yaap(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, KEEP_TURNS + 2)
    refused = client.post(f"/api/v1/conversations/{conversation_id}/summary")
    assert refused.status_code == 409 and "Claude Code" in refused.json()["detail"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/context").json()["can_summarise"] is False
