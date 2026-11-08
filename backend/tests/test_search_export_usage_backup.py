from pathlib import Path
import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime

from app.core.search import MATCH_END, MATCH_START, match_expression
from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key, send, start_conversation


def conversation_with(client, fake, question: str, answer: str, usage: TokenUsage | None = None) -> int:
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text=answer, usage=usage or TokenUsage(input_tokens=10, output_tokens=5)))
    send(client, conversation_id, question)
    return conversation_id


def test_match_expression_quotes_words_and_completes_the_last():
    assert match_expression('docker "compose') == '"docker" "compose"*'
    assert match_expression("  ...  ") is None


def test_messages_are_found_with_the_match_marked(client, fake):
    add_key(client)
    first = conversation_with(client, fake, "How do I rebuild the container?", "Run docker compose up with --build.")
    conversation_with(client, fake, "What is a tuple?", "An immutable sequence.")

    hits = client.get("/api/v1/environments/1/search", params={"q": "docker comp"}).json()

    assert [(hit["conversation_id"], hit["role"]) for hit in hits] == [(first, "assistant")]
    assert f"{MATCH_START}docker{MATCH_END}" in hits[0]["snippet"]
    assert client.get("/api/v1/environments/2/search", params={"q": "docker"}).json() == []
    assert client.get("/api/v1/environments/1/search", params={"q": "docker", "project_id": 99}).json() == []


def test_the_search_index_follows_changes_and_deletes(client, fake):
    add_key(client)
    conversation_id = conversation_with(client, fake, "Tell me about kangaroos", "They hop.")
    assert len(client.get("/api/v1/environments/1/search", params={"q": "kangaroos"}).json()) == 1

    with client.app.state.session_factory() as session:
        from sqlalchemy import delete, select

        from app.storage.models import AgentRunRecord, Message, RunStep, UsageRecord

        question = session.scalar(select(Message).where(Message.conversation_id == conversation_id, Message.role == "user"))
        question.content = [{"type": "text", "text": "Tell me about wombats"}]
        session.commit()
        assert client.get("/api/v1/environments/1/search", params={"q": "kangaroos"}).json() == []
        assert len(client.get("/api/v1/environments/1/search", params={"q": "wombats"}).json()) == 1

        session.execute(delete(UsageRecord).where(UsageRecord.conversation_id == conversation_id))
        # Every reply has a run record with its steps; the steps go first.
        runs = select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == conversation_id)
        session.execute(delete(RunStep).where(RunStep.run_id.in_(runs)))
        session.execute(delete(AgentRunRecord).where(AgentRunRecord.conversation_id == conversation_id))
        session.execute(delete(Message).where(Message.conversation_id == conversation_id))
        session.commit()
    assert client.get("/api/v1/environments/1/search", params={"q": "wombats hop"}).json() == []


def test_a_conversation_exports_as_markdown_and_json(client, fake):
    add_key(client)
    conversation_id = conversation_with(
        client, fake, "Explain `git rebase`", "Rebase replays commits.", TokenUsage(input_tokens=1000, output_tokens=100)
    )

    markdown = client.get(f"/api/v1/conversations/{conversation_id}/export")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert 'filename="yaap-' in markdown.headers["content-disposition"]
    text = markdown.text
    assert text.startswith("# Explain `git rebase`")
    assert "## You\n\nExplain `git rebase`" in text and "## YAAP\n\nRebase replays commits." in text

    exported = client.get(f"/api/v1/conversations/{conversation_id}/export", params={"format": "json"}).json()
    assert exported["yaap_export"] == 1
    assert [message["role"] for message in exported["messages"]] == ["user", "assistant"]
    assert exported["usage"]["input_tokens"] == 1000 and exported["usage"]["cost"] > 0

    empty = start_conversation(client)
    assert client.get(f"/api/v1/conversations/{empty}/export").status_code == 409


def test_tool_calls_are_collapsed_in_markdown():
    from app.core.export import markdown_block

    block = {"type": "tool_call", "name": "read_file", "input": {"path": "a.py"}, "output": "x = 1", "is_error": False}
    rendered = markdown_block(block)
    assert rendered.startswith("<details>\n<summary>Tool: read_file</summary>")
    assert '"path": "a.py"' in rendered and "x = 1" in rendered
    assert markdown_block({"type": "reasoning", "text": "secret thoughts"}) is None


def test_the_usage_report_splits_by_model_purpose_and_billing(client, fake):
    add_key(client)
    expensive = conversation_with(client, fake, "Big", "Answer", TokenUsage(input_tokens=200_000, output_tokens=10_000))
    conversation_with(client, fake, "Small", "Answer", TokenUsage(input_tokens=100, output_tokens=10))
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_with(client, fake, "Covered", "Answer", TokenUsage(input_tokens=1_000, output_tokens=1_000))

    report = client.get("/api/v1/environments/1/usage/report").json()

    assert report["month"] == f"{datetime.now(UTC):%Y-%m}"
    assert len(report["months"]) == 12 and report["months"][-1]["key"] == report["month"]
    assert report["totals"]["calls"] == 3
    billing = {group["key"]: group["totals"] for group in report["by_billing"]}
    assert billing["metered"]["cost"] > 0 and billing["metered"]["subscription_value"] == 0
    assert billing["subscription"]["cost"] == 0 and billing["subscription"]["subscription_value"] > 0
    assert report["by_model"][0]["label"] == "Claude Opus 5.5"
    assert report["by_purpose"][0]["key"] == "main"
    assert report["top_conversations"][0]["key"] == str(expensive)
    assert report["top_conversations"][0]["label"] == "Big"

    assert client.get("/api/v1/environments/1/usage/report", params={"month": "2020-01"}).json()["totals"]["calls"] == 0
    assert client.get("/api/v1/environments/1/usage/report", params={"month": "2999-01"}).status_code == 422
    assert client.get("/api/v1/environments/1/usage/report", params={"month": "2026-13"}).status_code == 422


def test_a_backup_holds_the_database_and_folders_but_no_sign_in(client, fake, tmp_path):
    add_key(client)
    conversation_with(client, fake, "Remember this", "Noted.")
    (tmp_path / "workspace" / "demo" / "node_modules").mkdir(parents=True)
    (tmp_path / "workspace" / "demo" / "main.py").write_text("print(1)\n")
    (tmp_path / "workspace" / "demo" / "node_modules" / "big.js").write_text("x")
    (tmp_path / "claude-code").mkdir(exist_ok=True)
    (tmp_path / "claude-code" / "credentials.json").write_text("{}")

    created = client.post("/api/v1/backups")
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    assert [item["name"] for item in client.get("/api/v1/backups").json()] == [name]

    download = client.get(f"/api/v1/backups/{name}")
    assert download.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    names = set(archive.namelist())
    assert {"yaap.db", "manifest.json", "workspace/demo/main.py"} <= names
    assert not any(item.startswith("claude-code/") or "node_modules" in item for item in names)
    manifest = json.loads(archive.read("manifest.json"))
    versions = Path(__file__).parents[1] / "app" / "storage" / "migrations" / "versions"
    assert manifest["database_revision"] == max(path.stem for path in versions.glob("0*.py"))

    copy = tmp_path / "restored.db"
    copy.write_bytes(archive.read("yaap.db"))
    with sqlite3.connect(copy) as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone()[0] == 2

    assert client.get("/api/v1/backups/..%2Fyaap.db").status_code == 404
    assert client.get("/api/v1/backups/yaap-backup-20200101-000000.zip").status_code == 404


def test_only_the_newest_backups_are_kept(tmp_path):
    from app.core.backup import create_backup, list_backups

    (tmp_path / "yaap.db").touch()
    with sqlite3.connect(tmp_path / "yaap.db") as database:
        database.execute("CREATE TABLE alembic_version (version_num TEXT)")
    for day in range(1, 5):
        create_backup(tmp_path, keep=2, now=datetime(2026, 11, day, tzinfo=UTC))
    assert [item.name for item in list_backups(tmp_path)] == [
        "yaap-backup-20261104-000000.zip",
        "yaap-backup-20261103-000000.zip",
    ]


def test_usage_is_split_per_project_and_per_environment(client, fake):
    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Report"}).json()
    inside = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]}
    ).json()["id"]
    fake.responses += [ScriptedResponse(text="A"), ScriptedResponse(text="B")]
    send(client, inside, "In the project")
    send(client, start_conversation(client), "Outside")

    report = client.get("/api/v1/environments/1/usage/report").json()
    assert {group["label"]: group["totals"]["calls"] for group in report["by_project"]} == {"Report": 1, "Outside a project": 1}
    assert report["by_environment"] == []

    overall = client.get("/api/v1/usage/report").json()
    assert [(group["label"], group["totals"]["calls"]) for group in overall["by_environment"]] == [("Private", 2)]
    assert overall["by_project"] == [] and overall["totals"]["calls"] == 2


def test_models_say_whether_the_environment_can_use_them(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "api_key"})
    models = client.get("/api/v1/environments/1/models").json()
    assert models[0]["provider_label"] == "Anthropic"
    assert not models[0]["available"] and "Anthropic API key" in models[0]["unavailable_reason"]

    add_key(client)
    assert all(model["available"] for model in client.get("/api/v1/environments/1/models").json())
    assert not client.get("/api/v1/environments/2/models").json()[0]["available"]
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    assert client.get("/api/v1/environments/2/models").json()[0]["available"]

    conversation_with(client, fake, "Hi", "Hello")
    report = client.get("/api/v1/environments/1/usage/report").json()
    assert [(group["label"], group["totals"]["calls"]) for group in report["by_provider"]] == [("Anthropic", 1)]
