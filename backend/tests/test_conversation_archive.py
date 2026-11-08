"""Renaming, archiving, restoring and deleting conversations from the sidebar menu."""

from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.test_attachments import png_bytes, upload
from tests.test_chat import add_key, send, start_conversation


def listed(client, archived: bool = False) -> list[int]:
    suffix = "?archived=true" if archived else ""
    return [item["id"] for item in client.get(f"/api/v1/environments/1/conversations{suffix}").json()]


def test_rename_rejects_an_empty_title(client):
    conversation_id = start_conversation(client)
    renamed = client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "  Plan  "})
    assert renamed.json()["title"] == "Plan"
    assert client.patch(f"/api/v1/conversations/{conversation_id}", json={"title": "   "}).status_code == 422


def test_archived_conversations_leave_the_list_and_come_back(client, fake):
    kept, archived = start_conversation(client), start_conversation(client)
    response = client.patch(f"/api/v1/conversations/{archived}", json={"archived": True})
    assert response.json()["archived_at"] is not None
    assert listed(client) == [kept]
    assert listed(client, archived=True) == [archived]
    # Still readable.
    assert client.get(f"/api/v1/conversations/{archived}").status_code == 200

    client.patch(f"/api/v1/conversations/{archived}", json={"archived": False})
    assert sorted(listed(client)) == sorted([kept, archived])

    # Writing in an archived conversation restores it.
    add_key(client)
    client.patch(f"/api/v1/conversations/{archived}", json={"archived": True})
    send(client, archived, "Hallo")
    assert archived in listed(client)


def test_deleting_removes_everything_but_keeps_usage(client, fake, tmp_path):
    add_key(client)
    conversation_id = start_conversation(client)
    image = upload(client, conversation_id, "shot.png", png_bytes()).json()
    fake.responses.append(ScriptedResponse(text="Gezien", usage=TokenUsage(input_tokens=100, output_tokens=10)))
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "uniekwoord", "attachment_ids": [image["id"]]}
    )
    assert response.status_code == 200
    files = [path for path in (tmp_path / "files").rglob("*") if path.is_file()]
    assert files
    cost_before = client.get("/api/v1/environments/1/usage").json()["cost"]

    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204

    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert conversation_id not in listed(client)
    assert not [path for path in (tmp_path / "files").rglob("*") if path.is_file()]
    assert client.get("/api/v1/environments/1/search", params={"q": "uniekwoord"}).json() == []
    assert client.get("/api/v1/environments/1/usage").json()["cost"] == cost_before > 0
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 404
