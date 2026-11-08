"""The search index and semantic search, with a small word-hashing embedder instead of the model."""

import hashlib
import re

import numpy as np
from sqlalchemy import func, select

from app.core.embeddings import Embedding
from app.core.search_index import CHUNK_CHARS, chunk_text
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import SearchChunk, UsageRecord
from tests.conftest import parse_events
from tests.test_chat import add_key, send, start_conversation
from tests.test_delegation import project_conversation
from tests.test_blueprint import put

# Words that mean the same thing for the test embedder, so a search can find a text without sharing its words.
SYNONYMS = {"database": "storage", "opslag": "storage", "storage": "storage"}


class WordEmbedder:
    """Each word lands in a fixed dimension; texts sharing words (or synonyms) are close."""

    name = "test-words"
    dimensions = 64
    max_distance = 0.7

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str], purpose: str) -> Embedding:
        self.calls += 1
        vectors = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float32)
            for word in re.findall(r"\w+", text.lower()):
                word = SYNONYMS.get(word, word)
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
            norm = np.linalg.norm(vector)
            vectors.append(vector / norm if norm else vector)
        return Embedding(vectors, sum(len(text.split()) for text in texts))


def use_embedder(client) -> WordEmbedder:
    embedder = WordEmbedder()
    client.app.state.indexer.set_embedder(embedder)
    return embedder


def index(client) -> int:
    return client.app.state.indexer.index_pass()


def search(client, q: str, **params) -> list[dict]:
    response = client.get("/api/v1/environments/1/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def chunk_count(client, kind: str | None = None) -> int:
    with client.app.state.session_factory() as session:
        query = select(func.count(SearchChunk.id))
        if kind:
            query = query.where(SearchChunk.kind == kind)
        return session.scalar(query) or 0


def test_chunks_follow_paragraphs_and_stay_small():
    text = "\n\n".join(["Short paragraph."] * 3 + ["word " * 600])
    chunks = chunk_text(text)
    assert chunks[0].startswith("Short paragraph.\n\nShort paragraph.")
    assert all(len(chunk) <= CHUNK_CHARS for chunk in chunks)
    assert len(chunks) >= 3
    assert chunk_text("   \n\n  ") == []


def test_messages_are_indexed_once_and_found_by_meaning(client, fake):
    embedder = use_embedder(client)
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Use SQLite as the database for the shop."))
    send(client, conversation_id, "What should we use?")

    assert client.get("/api/v1/search-index/status").json()["waiting_messages"] == 2
    assert index(client) == 2
    assert index(client) == 0  # Nothing changed: nothing embedded again.
    assert client.get("/api/v1/search-index/status").json() == {"model": "test-words", "chunks": 2, "waiting_messages": 0}
    calls = embedder.calls

    # "opslag" shares no word with the reply, but means the same to the embedder.
    hits = search(client, "opslag")
    assert hits[0]["kind"] == "message" and "SQLite" in hits[0]["snippet"]
    assert hits[0]["conversation_id"] == conversation_id and hits[0]["role"] == "assistant"
    assert embedder.calls == calls + 1  # Only the query.

    # Embedding usage is recorded per environment, without cost.
    with client.app.state.session_factory() as session:
        usage = session.scalars(select(UsageRecord).where(UsageRecord.purpose == "embedding")).all()
    assert usage and all(record.cost == 0 and record.environment_id == 1 for record in usage)

    # Deleting the conversation takes its chunks with it.
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert chunk_count(client, "message") == 0


def test_blueprint_and_memory_are_indexed_by_hash_and_searchable(client, fake):
    use_embedder(client)
    put(client, "standards/storage.md", "---\nsummary: Storage\n---\n\nKeep backups of every database nightly.\n", environment_id=1)
    project, _conversation_id = project_conversation(client)
    memory = client.get(f"/api/v1/projects/{project['id']}/memory")
    assert memory.status_code == 200, memory.text
    content = memory.json()["content"].replace("## Decisions\n", "## Decisions\n\n- Payments go through Mollie.\n")
    assert client.put(f"/api/v1/projects/{project['id']}/memory", json={"content": content}).status_code == 200

    first = index(client)
    assert first >= 2
    assert index(client) == 0

    hits = search(client, "backups")
    assert [(hit["kind"], hit["path"], hit["level"]) for hit in hits] == [
        ("blueprint", "standards/storage.md", "environment")
    ]
    hits = search(client, "Mollie", kind=["memory"])
    assert [(hit["kind"], hit["project_id"]) for hit in hits] == [("memory", project["id"])]
    assert "\x02Mollie\x03" in hits[0]["snippet"]

    # The kind filter keeps other kinds out.
    assert search(client, "backups", kind=["message", "memory"]) == []

    # A changed file is embedded again; a removed one leaves the index.
    put(client, "standards/storage.md", "---\nsummary: Storage\n---\n\nKeep backups weekly.\n", environment_id=1)
    assert index(client) == 1
    client.delete("/api/v1/blueprint/file", params={"path": "standards/storage.md", "environment_id": 1})
    index(client)
    assert search(client, "backups") == []


def test_full_text_still_works_without_a_model(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Use SQLite as the database."))
    send(client, conversation_id, "What should we use?")
    put(client, "standards/storage.md", "Keep backups of every database.\n", environment_id=1)
    index(client)

    assert chunk_count(client, "message") == 0  # Messages have their own full-text index.
    kinds = sorted(hit["kind"] for hit in search(client, "database"))
    assert kinds == ["blueprint", "message"]
    assert search(client, "opslag") == []  # No meaning without a model.
    assert client.get("/api/v1/search-index/status").json()["model"] is None


def test_search_project_tool_finds_earlier_project_conversations(client, fake):
    use_embedder(client)
    project, first = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="We chose SQLite as the database."))
    send(client, first, "Which storage?")
    index(client)

    second = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.extend(
        [
            ScriptedResponse(text="Looking.", tool_calls=(("search_project", {"query": "opslag"}),)),
            ScriptedResponse(text="We use SQLite."),
        ]
    )
    response = client.post(f"/api/v1/conversations/{second}/messages", json={"text": "What did we pick?"})
    events = parse_events(response.text)
    result = next(data for name, data in events if name == "tool_result")
    assert "We chose SQLite" in result["output"]
    assert "What did we pick?" not in result["output"]  # The current conversation is left out.
    assert not result.get("is_error")
