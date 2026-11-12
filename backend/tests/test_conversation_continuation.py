"""Continuing a long conversation as a new one that starts from a summary."""

import pytest

from app.core.chat import build_request
from app.core.chat_history import load_branch
from app.core.conversation_continuation import continuation_links, continue_from_summary
from app.core.summaries import SUMMARY_HEADING, SummaryError
from app.core.utility import UtilityContext
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation
from tests.test_chat import add_key, send, start_conversation


def utility_context(client) -> UtilityContext:
    state = client.app.state
    return UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)


def continue_conversation(client, fake, conversation_id: int):
    fake.responses.append(ScriptedResponse(text="## Goal\nTwo questions were asked."))
    return client.app.state.portal.call(continue_from_summary, utility_context(client), conversation_id)


def two_turns(client, fake) -> int:
    add_key(client)
    conversation_id = start_conversation(client)
    for number in range(2):
        fake.responses.append(ScriptedResponse(text=f"Answer {number}."))
        send(client, conversation_id, f"Question {number}?")
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        conversation.preset = "balanced"
        conversation.agent = "reviewer"
        session.commit()
    return conversation_id


def test_continuing_makes_a_linked_conversation_with_the_same_settings(client, fake):
    old_id = two_turns(client, fake)
    result = continue_conversation(client, fake, old_id)
    assert result.covered == 4 and result.conversation_id != old_id
    with client.app.state.session_factory() as session:
        old = session.get_one(Conversation, old_id)
        new = session.get_one(Conversation, result.conversation_id)
        assert new.continued_from_id == old_id
        assert (new.project_id, new.preset, new.agent) == (old.project_id, "balanced", "reviewer")
        assert (new.model, new.effort, new.environment_id) == (old.model, old.effort, old.environment_id)
        assert new.archived_at is None and new.imported_from is None
        links = continuation_links(session, old)
        assert [item.id for item in links.continued_in] == [result.conversation_id]
        assert links.continued_from is None
        assert continuation_links(session, new).continued_from.id == old_id  # type: ignore[union-attr]


def test_the_continued_conversation_starts_from_the_summary(client, fake):
    old_id = two_turns(client, fake)
    result = continue_conversation(client, fake, old_id)
    fake.responses.append(ScriptedResponse(text="Carrying on."))
    send(client, result.conversation_id, "And now?")
    first = fake.requests[-1].messages[0]
    assert first.role == "user" and first.content[0]["text"].startswith(SUMMARY_HEADING)
    assert "Two questions were asked." in first.content[0]["text"]
    assert not any("Question 0?" in str(message.content) for message in fake.requests[-1].messages)
    with client.app.state.session_factory() as session:
        new = session.get_one(Conversation, result.conversation_id)
        branch = load_branch(session, new.id, new.head_message_id)
        request = build_request(new, branch)
        assert request.messages[0].content[0]["text"].startswith(SUMMARY_HEADING)


def test_a_conversation_without_a_reply_cannot_be_continued(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    with pytest.raises(SummaryError, match="nothing to summarise"):
        client.app.state.portal.call(continue_from_summary, utility_context(client), conversation_id)


def test_deleting_the_earlier_conversation_unlinks_the_new_one(client, fake):
    old_id = two_turns(client, fake)
    result = continue_conversation(client, fake, old_id)
    assert client.delete(f"/api/v1/conversations/{old_id}").status_code == 204
    with client.app.state.session_factory() as session:
        new = session.get_one(Conversation, result.conversation_id)
        assert new.continued_from_id is None
        assert continuation_links(session, new).continued_from is None
