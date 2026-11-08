"""The Files tab and the editor: a project's files where its conversations work."""

import hashlib
from pathlib import Path

import pytest

from app.agent.policy import ToolPolicy
from app.files import editing
from app.targets.ssh import CONNECTIONS
from tests.test_ssh_targets import ready_target, server  # noqa: F401  (the server fixture)
from tests.test_tool_guard import run_steps, start_run


def make_project(client, name: str = "Shop") -> tuple[dict, Path]:
    project = client.post("/api/v1/environments/1/projects", json={"name": name}).json()
    return project, Path(client.app.state.workspace_dir) / project["folder"]


def write(folder: Path, path: str, text: str) -> Path:
    file = folder / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode("utf-8"))
    return file


def listing(client, project: dict, path: str = ".") -> dict:
    response = client.get(f"/api/v1/projects/{project['id']}/files", params={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def search(client, project: dict, query: str, mode: str) -> dict:
    response = client.get(f"/api/v1/projects/{project['id']}/files/search", params={"query": query, "mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_tree_skips_git_and_dependency_folders_and_shows_size_and_last_change(client):
    project, folder = make_project(client)
    write(folder, "src/app.py", "print('hello')\n")
    write(folder, "README.md", "# Shop\n")
    write(folder, "node_modules/left-pad/index.js", "module.exports = 1;\n")
    write(folder, "build/out.js", "x\n")
    root = listing(client, project)
    assert root["location"] == {"kind": "container", "label": "YAAP's container", "checkpoints": True}
    names = [entry["name"] for entry in root["entries"]]
    assert ".git" not in names and "node_modules" not in names and "build" not in names
    # Folders first, then files.
    assert names.index("src") < names.index("README.md")
    readme = next(entry for entry in root["entries"] if entry["name"] == "README.md")
    assert readme["size"] == len("# Shop\n".encode()) and readme["modified"] and readme["is_dir"] is False
    inside = listing(client, project, "src")
    assert [entry["path"] for entry in inside["entries"]] == ["src/app.py"]


def test_the_tree_stays_inside_the_project(client):
    project, _folder = make_project(client)
    response = client.get(f"/api/v1/projects/{project['id']}/files", params={"path": "../.."})
    assert response.status_code == 422
    assert "outside" in response.json()["detail"]
    missing = client.get(f"/api/v1/projects/{project['id']}/files", params={"path": "nowhere"})
    assert missing.status_code == 422


def test_search_by_name_and_by_content_skips_what_the_agent_skips(client):
    project, folder = make_project(client)
    write(folder, "src/Checkout.tsx", "export const total = 1;\n")
    write(folder, "docs/checkout-flow.md", "How the CHECKOUT works.\n")
    write(folder, "node_modules/checkout/index.js", "checkout\n")
    by_name = search(client, project, "checkout", "name")
    assert sorted(entry["path"] for entry in by_name["entries"]) == ["docs/checkout-flow.md", "src/Checkout.tsx"]
    by_content = search(client, project, "checkout works", "content")
    assert by_content["matches"] == [{"path": "docs/checkout-flow.md", "line": 1, "text": "How the CHECKOUT works."}]
    # The query is text, not a regular expression.
    assert search(client, project, "total = 1;", "content")["matches"][0]["path"] == "src/Checkout.tsx"
    assert search(client, project, "(", "content")["matches"] == []


@pytest.fixture
def forget_connections(client):
    yield
    for key in list(CONNECTIONS.open):
        client.portal.call(CONNECTIONS.close, key)


def target_project(client, server):  # noqa: F811
    target = ready_target(client, server)
    project, _folder = make_project(client, "Site")
    response = client.put(
        f"/api/v1/projects/{project['id']}/settings/target",
        json={"value": {"id": target["id"], "folder": "/work/site"}},
    )
    assert response.status_code == 200, response.text
    return project


def test_the_tree_and_search_work_on_a_target(client, server, forget_connections):  # noqa: F811
    remote = server.root / "work" / "site"
    write(remote, "src/main.py", "print('remote')\n")
    write(remote, ".git/HEAD", "ref: refs/heads/main\n")
    write(remote, "node_modules/x/main.py", "print('remote')\n")
    project = target_project(client, server)
    root = listing(client, project)
    assert root["location"] == {"kind": "ssh", "label": "Laptop", "checkpoints": False}
    names = [entry["name"] for entry in root["entries"]]
    assert names == ["src", "index.html"]
    html = root["entries"][1]
    assert html["size"] == (remote / "index.html").stat().st_size and html["modified"]
    assert [entry["path"] for entry in search(client, project, "main", "name")["entries"]] == ["src/main.py"]
    matches = search(client, project, "remote", "content")["matches"]
    assert [match["path"] for match in matches] == ["src/main.py"]


def test_a_target_that_is_not_set_up_is_a_conflict(client, server, forget_connections):  # noqa: F811
    target = client.post(
        "/api/v1/environments/1/targets",
        json={"name": "Laptop", "host": "127.0.0.1", "port": server.port, "folders": ["/work"]},
    ).json()
    project, _folder = make_project(client, "Site")
    client.put(
        f"/api/v1/projects/{project['id']}/settings/target",
        json={"value": {"id": target["id"], "folder": "/work/site"}},
    )
    response = client.get(f"/api/v1/projects/{project['id']}/files")
    assert response.status_code == 409
    assert "not set up" in response.json()["detail"]


# --- The editor -----------------------------------------------------------------------------------


def opened(client, project: dict, path: str) -> dict:
    response = client.get(f"/api/v1/projects/{project['id']}/files/content", params={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def save(client, project: dict, path: str, text: str, base_hash: str | None):
    return client.put(
        f"/api/v1/projects/{project['id']}/files/content",
        json={"path": path, "text": text, "base_hash": base_hash},
    )


def checkpoints(client, project: dict) -> list[dict]:
    return client.get(f"/api/v1/projects/{project['id']}/checkpoints").json()


def test_a_save_checks_the_hash_and_takes_a_checkpoint_first(client):
    project, folder = make_project(client)
    file = write(folder, "notes.md", "one\r\ntwo\r\n")
    shown = opened(client, project, "notes.md")
    assert shown["kind"] == "text" and shown["read_only"] is False
    assert shown["text"] == "one\r\ntwo\r\n" and shown["hash"] == hashlib.sha256(file.read_bytes()).hexdigest()
    response = save(client, project, "notes.md", "one\r\ntwo\r\nthree\r\n", shown["hash"])
    assert response.status_code == 200, response.text
    saved = response.json()
    assert file.read_bytes() == b"one\r\ntwo\r\nthree\r\n"
    assert saved["hash"] == hashlib.sha256(b"one\r\ntwo\r\nthree\r\n").hexdigest() and saved["checkpoint_id"]
    checkpoint = checkpoints(client, project)[0]
    assert checkpoint["id"] == saved["checkpoint_id"] and checkpoint["kind"] == "edit"
    assert checkpoint["summary"] == "Saved notes.md in the editor"
    # The checkpoint holds the state before the save: its changes are the save, and it rolls the save back.
    changes = client.get(f"/api/v1/projects/{project['id']}/checkpoints/{checkpoint['id']}/changes").json()["diff"]
    assert "+three" in changes
    restored = client.post(f"/api/v1/projects/{project['id']}/checkpoints/{checkpoint['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert file.read_bytes() == b"one\r\ntwo\r\n"


def test_a_save_is_refused_when_the_file_changed_since_it_was_opened(client):
    project, folder = make_project(client)
    file = write(folder, "app.py", "x = 1\n")
    shown = opened(client, project, "app.py")
    file.write_bytes(b"x = 2\n")
    before = len(checkpoints(client, project))
    response = save(client, project, "app.py", "x = 3\n", shown["hash"])
    assert response.status_code == 409
    assert "changed after you opened it" in response.json()["detail"]
    assert file.read_bytes() == b"x = 2\n" and len(checkpoints(client, project)) == before
    file.unlink()
    assert "removed" in save(client, project, "app.py", "x = 3\n", shown["hash"]).json()["detail"]


def test_a_new_file_is_created_only_when_it_does_not_exist(client):
    project, folder = make_project(client)
    created = save(client, project, "docs/new.md", "# New\n", None)
    assert created.status_code == 200, created.text
    assert (folder / "docs" / "new.md").read_bytes() == b"# New\n"
    again = save(client, project, "docs/new.md", "# Other\n", None)
    assert again.status_code == 409 and "exists already" in again.json()["detail"]


def test_the_agent_does_not_overwrite_what_the_user_saved(client):
    project, folder = make_project(client)
    write(folder, "app.py", "x = 1\n")
    registry, run = start_run(folder, policy=ToolPolicy(access="full"))
    run_steps(registry, run, [("read_file", {"path": "app.py"})])
    shown = opened(client, project, "app.py")
    assert save(client, project, "app.py", "x = 10\n", shown["hash"]).status_code == 200
    edit = ("edit_file", {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})
    results, _seen = run_steps(registry, run, [edit])
    assert "changed after you read it" in str(results[0])
    assert (folder / "app.py").read_bytes() == b"x = 10\n"


def test_images_and_pdfs_are_shown_and_svg_stays_text(client):
    project, folder = make_project(client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (folder / "logo.png").write_bytes(png)
    (folder / "manual.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\n")
    write(folder, "icon.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>\n')
    (folder / "data.bin").write_bytes(b"\x00\x01\x02\x03" * 10)
    image = opened(client, project, "logo.png")
    assert image["kind"] == "image" and image["text"] is None and image["read_only"] is True
    raw = client.get(f"/api/v1/projects/{project['id']}/files/raw", params={"path": "logo.png"})
    assert raw.status_code == 200 and raw.content == png
    assert raw.headers["content-type"] == "image/png" and raw.headers["content-disposition"].startswith("inline")
    assert opened(client, project, "manual.pdf")["kind"] == "pdf"
    svg = opened(client, project, "icon.svg")
    assert svg["kind"] == "text" and svg["read_only"] is False
    svg_raw = client.get(f"/api/v1/projects/{project['id']}/files/raw", params={"path": "icon.svg"})
    assert svg_raw.headers["content-type"] == "application/octet-stream"
    assert svg_raw.headers["content-disposition"].startswith("attachment")
    assert opened(client, project, "data.bin")["kind"] == "binary"


def test_large_files_open_read_only_and_git_files_cannot_be_saved(client, monkeypatch):
    project, folder = make_project(client)
    write(folder, "big.log", "line\n" * 100)
    monkeypatch.setattr(editing, "MAX_EDIT_BYTES", 100)
    big = opened(client, project, "big.log")
    assert big["read_only"] is True and big["text"] and "read-only" in big["note"]
    monkeypatch.setattr(editing, "MAX_VIEW_BYTES", 200)
    huge = opened(client, project, "big.log")
    assert huge["kind"] == "too_large" and huge["text"] is None
    write(folder, ".git/description", "Unnamed repository\n")
    git_file = opened(client, project, ".git/description")
    assert git_file["read_only"] is True
    refused = save(client, project, ".git/description", "Mine\n", git_file["hash"])
    assert refused.status_code == 422 and ".git" in refused.json()["detail"]


def test_open_and_save_on_a_target_without_checkpoints(client, server, forget_connections):  # noqa: F811
    project = target_project(client, server)
    remote = server.root / "work" / "site" / "index.html"
    shown = opened(client, project, "index.html")
    assert shown["location"]["kind"] == "ssh" and shown["location"]["checkpoints"] is False
    response = save(client, project, "index.html", "<h1>Changed</h1>\n", shown["hash"])
    assert response.status_code == 200, response.text
    assert response.json()["checkpoint_id"] is None and remote.read_bytes() == b"<h1>Changed</h1>\n"
    assert checkpoints(client, project) == []
    refused = save(client, project, "index.html", "<h1>Mine</h1>\n", shown["hash"])
    assert refused.status_code == 409 and remote.read_bytes() == b"<h1>Changed</h1>\n"
