import asyncio
import io
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from app.agent.runs import AttachmentRef, RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.attachments import files
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import VALID_KEY, parse_events


def png_bytes(width: int = 40, height: int = 30, colour=(200, 30, 30)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(output, format="PNG")
    return output.getvalue()


def pdf_bytes(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 20 50 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    output += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return output


def zip_bytes(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return output.getvalue()


def docx_bytes() -> bytes:
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Plan</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Ship the </w:t></w:r><w:r><w:t>first version.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    return zip_bytes({"word/document.xml": document.encode(), "[Content_Types].xml": b"<Types/>"})


def xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Devices"
    sheet.append(["Name", "OS"])
    sheet.append(["PC-01", "Windows 11"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def start_conversation(client, project_id=None) -> int:
    body = {"model": "claude-opus-5-5", "effort": "medium", "project_id": project_id}
    return client.post("/api/v1/environments/1/conversations", json=body).json()["id"]


def upload(client, conversation_id: int, name: str, data: bytes):
    return client.post(f"/api/v1/conversations/{conversation_id}/attachments", files={"file": (name, data)})


def test_files_are_recognised_by_content(client):
    conversation_id = start_conversation(client)
    cases = {
        "screenshot.png": (png_bytes(), "image", "image/png"),
        "report.pdf": (pdf_bytes("YAAP code 4711"), "pdf", "application/pdf"),
        "notes.md": ("# Notes\n\nÉén regel.".encode(), "text", "text/plain"),
        "plan.docx": (docx_bytes(), "office", None),
        "devices.xlsx": (xlsx_bytes(), "office", None),
        "project.zip": (zip_bytes({"README.md": b"# Demo"}), "archive", "application/zip"),
        "blob.bin": (bytes(range(256)) * 4, "other", "application/octet-stream"),
    }
    for name, (data, kind, media_type) in cases.items():
        response = upload(client, conversation_id, name, data)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == kind, name
        if media_type:
            assert body["media_type"] == media_type
    pdf = upload(client, conversation_id, "again.pdf", pdf_bytes("x")).json()
    assert pdf["details"]["pages"] == 1


def test_large_images_get_a_smaller_copy_for_the_model(client, tmp_path):
    conversation_id = start_conversation(client)
    body = upload(client, conversation_id, "wide.png", png_bytes(3000, 1000)).json()
    assert body["details"] == {"width": 3000, "height": 1000}
    stored = next((tmp_path / "files").rglob("model.png"))
    with Image.open(stored) as image:
        assert max(image.size) == files.MAX_IMAGE_SIDE


def test_archives_are_unpacked_safely_with_an_overview(client, tmp_path):
    conversation_id = start_conversation(client)
    data = zip_bytes(
        {
            "demo/pyproject.toml": b"[project]\nname = 'demo'\n",
            "demo/README.md": b"# Demo\n\nA small tool.",
            "demo/src/app.py": b"print('hi')\n",
            "../escape.txt": b"outside",
            "demo/nested.zip": zip_bytes({"a.txt": b"a"}),
        },
        symlink="demo/link",
    )
    body = upload(client, conversation_id, "demo.zip", data).json()
    assert body["details"]["file_count"] == 4
    unpacked = next((tmp_path / "files").rglob("unpacked"))
    assert not (unpacked.parent / "escape.txt").exists()
    assert not (unpacked / "demo" / "link").exists()
    attachment_folder = unpacked.parent
    from sqlalchemy import select

    from app.storage.models import Attachment

    with client.app.state.session_factory() as session:
        overview = session.scalar(select(Attachment)).details["overview"]
    assert "a Python project" in overview
    assert "demo/src/app.py" in overview
    assert "A small tool." in overview
    assert "not unpacked): demo/nested.zip" in overview
    assert "Skipped 2 entries" in overview
    assert attachment_folder.exists()


def test_archive_limits_stop_a_zip_bomb(client, monkeypatch, tmp_path):
    monkeypatch.setattr(files, "MAX_UNPACKED_BYTES", 1000)
    conversation_id = start_conversation(client)
    response = upload(client, conversation_id, "bomb.zip", zip_bytes({"big.txt": b"0" * 5000}))
    assert response.status_code == 422
    assert "unpacks to more than" in response.json()["detail"]
    # Nothing of the failed upload stays behind.
    assert not any((tmp_path / "files").rglob("big.txt"))


def test_office_text_is_extracted(tmp_path):
    docx = tmp_path / "plan.docx"
    docx.write_bytes(docx_bytes())
    assert files.extract_office_text(docx) == "# Plan\n\nShip the first version."
    xlsx = tmp_path / "devices.xlsx"
    xlsx.write_bytes(xlsx_bytes())
    assert files.extract_office_text(xlsx) == "## Sheet: Devices\n\n| Name | OS |\n| --- | --- |\n| PC-01 | Windows 11 |"


def test_only_raster_images_are_shown_inline(client):
    conversation_id = start_conversation(client)
    image = upload(client, conversation_id, "shot.png", png_bytes()).json()
    page = upload(client, conversation_id, "page.html", b"<script>alert(1)</script>").json()
    shown = client.get(f"/api/v1/attachments/{image['id']}/content")
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline")
    downloaded = client.get(f"/api/v1/attachments/{page['id']}/content")
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment")


def test_image_message_reaches_the_model_and_gets_attachment_tools(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = start_conversation(client)
    image = upload(client, conversation_id, "shot.png", png_bytes()).json()
    fake.responses.append(ScriptedResponse(text="A red square."))

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "", "attachment_ids": [image["id"]]}
    )

    events = parse_events(response.text)
    user_message = events[0][1]
    assert user_message["content"][0]["type"] == "attachment"
    assert user_message["content"][0]["name"] == "shot.png"
    request = fake.requests[0]
    content = request.messages[-1].content
    assert content[0]["text"].startswith('<attachment id="')
    assert content[1]["type"] == "image" and content[1]["media_type"] == "image/png"
    # No project, but the attachment gets its read-only tools.
    assert request.tool_server is not None and request.working_folder is None
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert conversation["title"] == "shot.png"
    # Once sent, the attachment stays part of the conversation.
    assert client.delete(f"/api/v1/attachments/{image['id']}").status_code == 409


def test_without_tools_an_archive_brings_its_text_files(client, fake):
    from dataclasses import replace

    # A connection that cannot run tools, as a future provider might be.
    specs = client.app.state.providers.specs
    specs[0] = replace(specs[0], runs_agents=False)
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    conversation_id = start_conversation(client)
    archive = upload(client, conversation_id, "site.zip", zip_bytes({"index.html": b"<h1>Hi</h1>", "logo.png": png_bytes()})).json()
    fake.responses.append(ScriptedResponse(text="A small site."))

    client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Wat is dit?", "attachment_ids": [archive["id"]]}
    )

    request = fake.requests[0]
    text = "\n".join(block.get("text", "") for block in request.messages[-1].content)
    assert "Archive site.zip: 2 files" in text
    assert "--- index.html ---\n<h1>Hi</h1>" in text
    assert request.tool_server is None


def test_unsent_attachments_can_be_removed_and_others_are_refused(client):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    first = start_conversation(client)
    second = start_conversation(client)
    attachment = upload(client, first, "a.txt", b"a").json()
    refused = client.post(f"/api/v1/conversations/{second}/messages", json={"text": "x", "attachment_ids": [attachment["id"]]})
    assert refused.status_code == 422
    assert client.delete(f"/api/v1/attachments/{attachment['id']}").status_code == 204
    assert client.get(f"/api/v1/attachments/{attachment['id']}").status_code == 404


@pytest.fixture
def archive_run(tmp_path):
    folder = tmp_path / "attachment"
    (folder / "original").mkdir(parents=True)
    (folder / "original" / "demo.zip").write_bytes(b"")
    unpacked = folder / "unpacked"
    (unpacked / "src").mkdir(parents=True)
    (unpacked / "src" / "app.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    (unpacked / "logo.png").write_bytes(png_bytes())
    (unpacked / "readme.md").write_text("# Demo\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "readme.md").write_text("old\n", encoding="utf-8")
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=True),
        project_id=1,
        project_folder=project,
        attachments=[AttachmentRef(7, "demo.zip", "archive", "application/zip", folder)],
    ))
    return registry, run, project


def call(registry, run, name, arguments):
    return asyncio.run(call_tool(registry, run, name, arguments))


def test_attachment_tools_look_inside_an_archive(archive_run):
    registry, run, _ = archive_run
    listing, _ = call(registry, run, "list_attachment", {"attachment": "demo.zip"})
    assert "src/app.py" in listing and "logo.png" in listing
    code, error = call(registry, run, "read_attachment", {"attachment": "7", "path": "src/app.py"})
    assert not error and "return 42" in code
    image, _ = call(registry, run, "read_attachment", {"attachment": "7", "path": "logo.png"})
    assert image.images and image.images[0][0] == "image/png"
    found, _ = call(registry, run, "search_attachment", {"pattern": r"def \w+"})
    assert found == "demo.zip/src/app.py:1: def main():"
    outside, error = call(registry, run, "read_attachment", {"attachment": "7", "path": "../original/demo.zip"})
    assert error and "outside the archive" in outside


def test_importing_asks_only_when_it_would_overwrite(archive_run):
    registry, run, project = archive_run

    async def scenario():
        created = await call_tool(registry, run, "import_attachment", {"attachment": "7", "path": "src", "target": "src"})

        async def approve():
            while not run.state.approvals:
                await asyncio.sleep(0.01)
            registry.decide(next(iter(run.state.approvals)), "approve")

        approval = asyncio.create_task(approve())
        replaced = await call_tool(registry, run, "import_attachment", {"attachment": "7", "path": "readme.md"})
        await approval
        return created, replaced

    created, replaced = asyncio.run(scenario())
    assert created == ("Imported 1 files (1 new, 0 replaced): src/app.py.", False)
    assert replaced == ("Imported 1 files (0 new, 1 replaced): readme.md.", False)
    assert (project / "readme.md").read_text(encoding="utf-8") == "# Demo\n"
    assert run.events.qsize() == 2  # approval requested and resolved, only for the overwrite


def test_tools_outside_the_run_are_refused(archive_run):
    registry, run, _ = archive_run
    run.config = replace(run.config, tool_names=tools_for(has_project=False, has_attachments=True))
    output, error = call(registry, run, "write_file", {"path": "x.txt", "content": "x"})
    assert error and output == "Unknown tool 'write_file'."
    assert Path(run.project_folder / "x.txt").exists() is False
