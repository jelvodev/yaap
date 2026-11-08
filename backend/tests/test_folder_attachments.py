"""Folders as attachments: one attachment of kind folder, with its paths, read with the tools."""

import asyncio

from sqlalchemy import select

from app.agent.runs import AttachmentRef, RunConfig, RunRegistry
from app.agent.file_tools import SKIPPED_FOLDERS
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.attachments.files import SKIPPED_FOLDER_NAMES
from app.attachments.service import attachment_folder
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Attachment
from tests.conftest import parse_events
from tests.test_attachments import png_bytes, start_conversation, zip_bytes


def upload_folder(client, conversation_id: int, name: str, data: bytes):
    return client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": (name, data)},
        data={"kind": "folder"},
    )


SITE = {
    "README.md": b"# Site\n\nA small shop.",
    "src/app.py": b"def checkout():\n    return 'paid'\n",
    "src/static/logo.png": png_bytes(),
    "node_modules/left-pad/index.js": b"module.exports = 1;\n",
    ".git/HEAD": b"ref: refs/heads/main\n",
}


def test_a_folder_is_one_attachment_with_its_paths_without_git_and_dependencies(client, tmp_path):
    conversation_id = start_conversation(client)
    response = upload_folder(client, conversation_id, "site.zip", zip_bytes(SITE))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "folder" and body["filename"] == "site"
    assert body["details"]["file_count"] == 3 and body["details"]["left_out"] == 2
    unpacked = next((tmp_path / "files").rglob("unpacked"))
    assert (unpacked / "src" / "app.py").read_bytes() == SITE["src/app.py"]
    assert not (unpacked / "node_modules").exists() and not (unpacked / ".git").exists()
    with client.app.state.session_factory() as session:
        overview = session.scalar(select(Attachment)).details["overview"]
    assert overview.startswith("Folder site: 3 files")
    assert "src/app.py" in overview and "A small shop." in overview
    assert "Left out 2 files in .git and dependency folders." in overview
    download = client.get(f"/api/v1/attachments/{body['id']}/content")
    assert download.headers["content-type"] == "application/octet-stream"
    assert 'filename="site.zip"' in download.headers["content-disposition"]


def test_a_folder_must_arrive_as_a_zip(client):
    conversation_id = start_conversation(client)
    response = upload_folder(client, conversation_id, "notes", b"just text")
    assert response.status_code == 422
    assert "did not arrive as a zip" in response.json()["detail"]
    # A zip sent as a file stays an archive.
    archive = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments", files={"file": ("site.zip", zip_bytes(SITE))}
    ).json()
    assert archive["kind"] == "archive" and archive["details"]["file_count"] == 5


def test_the_client_and_the_server_leave_out_the_same_folders_as_the_agent():
    assert SKIPPED_FOLDER_NAMES == SKIPPED_FOLDERS


def call(registry, run, name, arguments):
    return asyncio.run(call_tool(registry, run, name, arguments))


def test_a_folder_reaches_the_model_and_its_files_are_read_with_the_tools(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = start_conversation(client)
    folder = upload_folder(client, conversation_id, "site.zip", zip_bytes(SITE)).json()
    fake.responses.append(ScriptedResponse(text="A shop."))
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "What is this?", "attachment_ids": [folder["id"]]}
    )
    stored = parse_events(response.text)[0][1]["content"]
    assert any(block.get("type") == "attachment" and block["kind"] == "folder" for block in stored)
    sent = fake.requests[0].messages[-1].content
    text = next(block["text"] for block in sent if block.get("type") == "text" and "<attachment" in block["text"])
    assert 'kind="folder"' in text and "Folder site:" in text and "list_attachment" in text

    with client.app.state.session_factory() as session:
        attachment = session.get_one(Attachment, folder["id"])
        stored_folder = attachment_folder(client.app.state.files_dir, attachment)
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=conversation_id,
        environment_id=1,
        blueprint_layers=[],
        tool_names=tools_for(has_project=False, has_attachments=True),
        attachments=[AttachmentRef(folder["id"], "site", "folder", "application/zip", stored_folder)],
    ))
    listing, _ = call(registry, run, "list_attachment", {"attachment": "site"})
    assert "src/app.py" in listing and "src/static/logo.png" in listing and "node_modules" not in listing
    code, error = call(registry, run, "read_attachment", {"attachment": "site", "path": "src/app.py"})
    assert not error and "return 'paid'" in code
    image, _ = call(registry, run, "read_attachment", {"attachment": "site", "path": "src/static/logo.png"})
    assert image.images and image.images[0][0] == "image/png"
    found, _ = call(registry, run, "search_attachment", {"pattern": r"def \w+"})
    assert found == "site/src/app.py:1: def checkout():"
    whole, error = call(registry, run, "read_attachment", {"attachment": "site"})
    assert error and "site is a folder. Give the path" in whole
