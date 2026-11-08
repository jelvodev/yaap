"""Project knowledge: upload, extraction, index, search, the agent's tools, removal."""

from sqlalchemy import func, select

from app.providers.fake_provider import ScriptedResponse
from app.storage.models import SearchChunk
from tests.conftest import parse_events
from tests.test_attachments import docx_bytes, pdf_bytes, png_bytes
from tests.test_delegation import project_conversation
from tests.test_semantic_search import index, search, use_embedder


def upload(client, project_id: int, name: str, data: bytes):
    return client.post(f"/api/v1/projects/{project_id}/knowledge", files={"file": (name, data)})


def knowledge_chunks(client) -> int:
    with client.app.state.session_factory() as session:
        return session.scalar(select(func.count(SearchChunk.id)).where(SearchChunk.kind == "knowledge")) or 0


def test_documents_are_extracted_indexed_and_searchable(client, fake, tmp_path):
    use_embedder(client)
    project, _ = project_conversation(client)

    text = upload(client, project["id"], "runbook.md", b"# Runbook\n\nRestart the storage service with care.\n")
    pdf = upload(client, project["id"], "manual.pdf", pdf_bytes("The printer needs toner monthly"))
    word = upload(client, project["id"], "plan.docx", docx_bytes())
    assert text.status_code == 201, text.text
    assert pdf.status_code == 201, pdf.text
    assert word.status_code == 201, word.text
    assert text.json()["index_state"] == "waiting"

    listed = client.get(f"/api/v1/projects/{project['id']}/knowledge").json()
    assert [document["name"] for document in listed] == ["manual.pdf", "plan.docx", "runbook.md"]
    assert all(document["chars"] > 0 for document in listed)

    index(client)
    listed = client.get(f"/api/v1/projects/{project['id']}/knowledge").json()
    assert {document["index_state"] for document in listed} == {"indexed"}

    hits = search(client, "database", kind="knowledge")  # A synonym of storage for the test embedder.
    assert hits and hits[0]["kind"] == "knowledge" and hits[0]["path"] == "runbook.md"
    assert hits[0]["project_id"] == project["id"]
    assert any(hit["path"] == "manual.pdf" for hit in search(client, "toner", kind="knowledge"))
    assert any(hit["path"] == "plan.docx" for hit in search(client, "first version", kind="knowledge"))

    # The original and the extracted text are kept in the data folder, not in the project.
    folder = tmp_path / "knowledge" / str(project["id"])
    assert folder.is_dir() and len(list(folder.iterdir())) == 3


def test_images_and_empty_files_are_refused(client, fake):
    project, _ = project_conversation(client)
    image = upload(client, project["id"], "photo.png", png_bytes())
    assert image.status_code == 422
    assert "text" in image.json()["detail"]
    empty = upload(client, project["id"], "empty.txt", b"   \n")
    assert empty.status_code == 422
    assert client.get(f"/api/v1/projects/{project['id']}/knowledge").json() == []


def test_the_agent_searches_and_reads_knowledge(client, fake):
    use_embedder(client)
    project, conversation_id = project_conversation(client)
    long_text = "Storage rules.\n\n" + "x" * 60_000 + "\n\nThe end marker."
    upload(client, project["id"], "rules.txt", long_text.encode())
    index(client)

    fake.responses.extend(
        [
            ScriptedResponse(
                text="Looking.",
                tool_calls=(
                    ("search_project", {"query": "opslag", "include": "knowledge"}),
                    ("read_knowledge", {"name": "rules"}),
                    ("read_knowledge", {"name": "rules.txt", "offset": 50_000}),
                    ("read_knowledge", {"name": "missing"}),
                ),
            ),
            ScriptedResponse(text="Done."),
        ]
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "What are the rules?"})
    results = [data for name, data in parse_events(response.text) if name == "tool_result"]
    assert 'Knowledge document "rules.txt"' in results[0]["output"]
    assert results[1]["output"].startswith("# rules.txt\n[Characters 0-50000 of")
    assert "Continue with offset 50000.]\n\nStorage rules." in results[1]["output"]
    assert results[2]["output"].rstrip().endswith("The end marker.")
    assert results[3]["is_error"] and "rules.txt" in results[3]["output"]

    # The system prompt names the documents.
    system = "\n".join(part.text for part in fake.requests[0].system)
    assert "rules.txt" in system and "read_knowledge" in system


def test_removing_a_document_or_project_removes_its_text_and_chunks(client, fake, tmp_path):
    use_embedder(client)
    project, conversation_id = project_conversation(client)
    document = upload(client, project["id"], "notes.txt", b"Some notes about storage.").json()
    upload(client, project["id"], "other.txt", b"Other notes.")
    index(client)
    assert knowledge_chunks(client) == 2

    assert client.delete(f"/api/v1/projects/{project['id']}/knowledge/{document['id']}").status_code == 204
    assert knowledge_chunks(client) == 1
    assert [d["name"] for d in client.get(f"/api/v1/projects/{project['id']}/knowledge").json()] == ["other.txt"]
    assert not (tmp_path / "knowledge" / str(project["id"]) / str(document["id"])).exists()
    assert client.delete(f"/api/v1/projects/{project['id']}/knowledge/{document['id']}").status_code == 404

    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 204
    assert not (tmp_path / "knowledge" / str(project["id"])).exists()
