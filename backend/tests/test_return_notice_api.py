"""The notice after a pause and the continuation from a summary, over HTTP."""

from app.storage.models import Conversation
from tests.test_chat import add_key, start_conversation


def test_a_fresh_conversation_has_no_return_notice(client):
    add_key(client)
    conversation_id = start_conversation(client)
    response = client.get(f"/api/v1/conversations/{conversation_id}/return-notice")
    assert response.status_code == 200 and response.json() == {"notice": None}


def test_the_continuation_names_the_earlier_and_the_later_conversation(client):
    add_key(client)
    earlier_id = start_conversation(client)
    later_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        session.get_one(Conversation, later_id).continued_from_id = earlier_id
        session.commit()
    later = client.get(f"/api/v1/conversations/{later_id}/continuation").json()
    earlier = client.get(f"/api/v1/conversations/{earlier_id}/continuation").json()
    assert later["continued_from"]["id"] == earlier_id and later["continued_in"] == []
    assert earlier["continued_from"] is None
    assert [item["id"] for item in earlier["continued_in"]] == [later_id]


def test_continuing_a_conversation_without_a_reply_is_refused(client):
    add_key(client)
    conversation_id = start_conversation(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/continue-from-summary")
    assert response.status_code == 409
