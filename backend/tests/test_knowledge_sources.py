"""Knowledge additions: OCR, web pages and a project's knowledge folder."""

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

from app.attachments import ocr
from app.attachments.files import pdf_text


def scanned_pdf() -> bytes:
    """A PDF whose only page is an image: no text layer, as a scanner makes it."""
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).text((20, 80), "Invoice 2026-114", fill="black")
    output = io.BytesIO()
    image.save(output, "PDF")
    return output.getvalue()


def project(client) -> dict:
    return client.post("/api/v1/environments/1/projects", json={"name": "Docs"}).json()


def test_scanned_pages_are_read_with_ocr(monkeypatch):
    seen = []
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read_image", lambda data: seen.append(data[:4]) or "Invoice 2026-114")
    text, pages = pdf_text(scanned_pdf())
    assert pages == 1 and text == "## Page 1 (read with OCR)\n\nInvoice 2026-114"
    assert seen  # The page's image went to OCR.
    # Without Tesseract the page stays empty, as before.
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert pdf_text(scanned_pdf())[0] == "## Page 1\n\n"


def test_an_image_becomes_knowledge_through_ocr(client, monkeypatch):
    proj = project(client)
    url = f"/api/v1/projects/{proj['id']}/knowledge"
    image = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(image, "PNG")
    monkeypatch.setattr(ocr, "available", lambda: False)
    refused = client.post(url, files={"file": ("scan.png", image.getvalue(), "image/png")})
    assert refused.status_code == 422 and "OCR" in refused.json()["detail"]
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read_image", lambda data: "Meeting notes: ship on Friday.")
    added = client.post(url, files={"file": ("scan.png", image.getvalue(), "image/png")})
    assert added.status_code == 201, added.text
    listed = client.get(url).json()
    assert listed[0]["kind"] == "image" and listed[0]["ocr"] is True


class Page(BaseHTTPRequestHandler):
    body = "<html><head><title>Style guide</title></head><body><p>Use short sentences.</p></body></html>"

    def do_GET(self):  # noqa: N802
        data = Page.body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_a_web_page_is_kept_and_refreshed(client):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proj = project(client)
        address = f"http://127.0.0.1:{server.server_port}/guide"
        added = client.post(f"/api/v1/projects/{proj['id']}/knowledge/web", json={"url": address})
        assert added.status_code == 201, added.text
        document = added.json()
        assert document["name"] == "Style guide" and document["source_url"] == address and document["kind"] == "web"
        assert client.post(f"/api/v1/projects/{proj['id']}/knowledge/web", json={"url": "ftp://example.com/x"}).status_code == 422
        Page.body = "<html><head><title>Style guide v2</title></head><body><p>Use active voice.</p></body></html>"
        refreshed = client.post(f"/api/v1/projects/{proj['id']}/knowledge/{document['id']}/refresh").json()
        assert refreshed["name"] == "Style guide v2" and refreshed["fetched_at"] >= document["fetched_at"]
        from app.knowledge.service import read_text
        from app.storage.models import KnowledgeDocument

        with client.app.state.session_factory() as session:
            text = read_text(client.app.state.data_dir, session.get_one(KnowledgeDocument, document["id"]))
        assert "Use active voice." in text and "short sentences" not in text
    finally:
        server.shutdown()


def test_the_knowledge_folder_is_mirrored(client):
    proj = project(client)
    folder = Path(client.app.state.workspace_dir) / proj["folder"]
    (folder / "docs" / "api").mkdir(parents=True)
    (folder / "docs" / "intro.md").write_text("# Intro\n\nThe service runs on port 9000.\n", encoding="utf-8")
    (folder / "docs" / "api" / "auth.txt").write_text("Tokens expire after one hour.\n", encoding="utf-8")
    (folder / "docs" / "logo.png").write_bytes(b"\x89PNG not text")
    url = f"/api/v1/projects/{proj['id']}/knowledge"
    assert client.put(f"/api/v1/projects/{proj['id']}/settings/knowledge_folder", json={"value": "../elsewhere"}).status_code == 422
    assert client.put(f"/api/v1/projects/{proj['id']}/settings/knowledge_folder", json={"value": "docs"}).status_code == 200
    assert client.post(f"{url}/sync").json() == {"changed": 2}
    assert sorted(item["source_path"] for item in client.get(url).json()) == ["docs/api/auth.txt", "docs/intro.md"]
    assert client.post(f"{url}/sync").json() == {"changed": 0}  # Unchanged files are not read again.
    (folder / "docs" / "intro.md").write_text("# Intro\n\nThe service runs on port 9100.\n", encoding="utf-8")
    (folder / "docs" / "api" / "auth.txt").unlink()
    assert client.post(f"{url}/sync").json() == {"changed": 2}
    listed = client.get(url).json()
    assert [item["source_path"] for item in listed] == ["docs/intro.md"]
    # Switching the folder off removes what came from it.
    client.put(f"/api/v1/projects/{proj['id']}/settings/knowledge_folder", json={"value": ""})
    client.post(f"{url}/sync")
    assert client.get(url).json() == []
