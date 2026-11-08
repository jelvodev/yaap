"""The preview panel: the stream events that reload it, and screenshots through the agent's browser."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.preview.screenshot import Screenshot, ScreenshotError, browser_sees, preview_url, screenshot_preview
from app.providers.fake_provider import ScriptedResponse
from tests.test_browser import with_browser
from tests.test_chat import send
from tests.test_delegation import project_conversation
from tests.test_preview import start_dev_server

PNG = b"\x89PNG\r\n\x1a\nfake"
# The tools whose successful result reloads the panel (FILE_CHANGING_TOOLS in frontend/src/pages/chat/fileChanges.ts).
FILE_CHANGING_TOOLS = ("write_file", "edit_file", "rename_symbol")


@dataclass
class FakeBrowser:
    opened: list[str] = field(default_factory=list)

    async def open(self, url: str) -> None:
        self.opened.append(url)

    async def screenshot(self) -> Screenshot:
        return Screenshot("image/png", PNG)


def test_a_screenshot_opens_the_preview_url_in_the_browser_and_returns_the_image() -> None:
    browser = FakeBrowser()
    shot = asyncio.run(screenshot_preview(browser, preview_url(5173, "/cart")))
    assert browser.opened == ["http://localhost:5173/cart"]
    assert shot == Screenshot("image/png", PNG)


@pytest.mark.parametrize("url", ["https://evil.example/", "http://10.0.0.5:5173/", "file:///etc/passwd"])
def test_a_screenshot_is_only_of_a_local_preview(url: str) -> None:
    browser = FakeBrowser()
    with pytest.raises(ScreenshotError):
        asyncio.run(screenshot_preview(browser, url))
    assert browser.opened == []


@pytest.mark.parametrize(
    ("port", "path", "expected"),
    [
        (5173, "/", "http://localhost:5173/"),
        (3000, "", "http://localhost:3000/"),
        (3000, "/a?b=1#c", "http://localhost:3000/a?b=1#c"),
        (3000, "//evil.example/", None),
        (3000, "@evil.example/", None),
        (3000, "/\\evil", None),
        (3000, "/a b", None),
        (0, "/", None),
    ],
)
def test_the_preview_url_stays_on_the_dev_server(port: int, path: str, expected: str | None) -> None:
    if expected is None:
        with pytest.raises(ScreenshotError):
            preview_url(port, path)
    else:
        assert preview_url(port, path) == expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (None, True),
        (SimpleNamespace(kind="runner"), True),
        (SimpleNamespace(kind="sandbox"), False),
        (SimpleNamespace(kind="ssh"), False),
    ],
)
def test_the_browser_sees_processes_in_its_own_place_only(target: object, expected: bool) -> None:
    assert browser_sees(SimpleNamespace(target=target)) is expected  # type: ignore[arg-type]  # only .target is read


def test_the_panel_takes_a_screenshot_of_a_process_through_the_agents_browser(client, tmp_path):
    with_browser(client)
    project, conversation_id = project_conversation(client)
    managed = start_dev_server(client, project["id"], tmp_path)
    try:
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/preview/screenshot",
            json={"process_id": managed.id, "path": "/shop"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"number": 1, "url": f"http://localhost:{managed.port}/shop"}
        shot = client.get(f"/api/v1/conversations/{conversation_id}/browser/shots/1")
        assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG")
        refused = client.post(
            f"/api/v1/conversations/{conversation_id}/preview/screenshot",
            json={"process_id": managed.id, "path": "//x"},
        )
        assert refused.status_code == 409
        assert client.post(
            f"/api/v1/conversations/{conversation_id}/preview/screenshot", json={"process_id": 999}
        ).status_code == 404
    finally:
        client.portal.call(client.app.state.processes.stop, managed, "test")


def test_without_the_browser_no_screenshot_is_taken(client, tmp_path):
    client.app.state.browser_command = None
    project, conversation_id = project_conversation(client)
    managed = start_dev_server(client, project["id"], tmp_path)
    try:
        address = f"/api/v1/conversations/{conversation_id}/preview/screenshot"
        response = client.post(address, json={"process_id": managed.id})
        assert response.status_code == 409 and "browser" in response.json()["detail"]
    finally:
        client.portal.call(client.app.state.processes.stop, managed, "test")


def test_a_file_change_of_the_agent_streams_the_events_that_reload_the_panel(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(
            text="Changed.",
            tool_calls=(
                ("write_file", {"path": "index.html", "content": "<h1>Shop</h1>"}),
                ("edit_file", {"path": "index.html", "old_string": "Shop", "new_string": "Cake shop"}),
                ("edit_file", {"path": "index.html", "old_string": "missing", "new_string": "x"}),
                ("read_file", {"path": "index.html"}),
            ),
        )
    )
    events = send(client, conversation_id, "Rename the shop")
    calls = {data["id"]: data["name"] for name, data in events if name == "tool_call"}
    results = [(calls[data["id"]], data["is_error"]) for name, data in events if name == "tool_result"]
    # Each result names its call by id; the panel reloads after a successful write_file or edit_file only.
    assert results == [("write_file", False), ("edit_file", False), ("edit_file", True), ("read_file", False)]
    reloads = [tool for tool, failed in results if tool in FILE_CHANGING_TOOLS and not failed]
    assert reloads == ["write_file", "edit_file"]
