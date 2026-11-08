"""Web search: SearXNG behind the SearchProvider interface, and the agent's web_search tool."""

import io
import json
import urllib.error

import pytest

from app.providers.fake_provider import ScriptedResponse
from app.websearch import service
from app.websearch.service import SearchError, SearxngSearch
from tests.conftest import parse_events
from tests.test_delegation import add_key, project_conversation

SEARXNG_BODY = {
    "query": "fastapi docs",
    "results": [
        {"title": "FastAPI <b>docs</b>", "url": "https://fastapi.tiangolo.com/", "content": "Fast &amp; <b>modern</b>.", "engine": "brave"},
        {"title": "No URL", "content": "Skipped."},
        {"title": "Starlette", "url": "https://www.starlette.io/", "content": "ASGI toolkit.", "engine": "google"},
        {"title": "Uvicorn", "url": "https://www.uvicorn.org/", "content": "ASGI server.", "engine": "bing"},
    ],
    "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_searxng_results_are_plain_text_and_capped(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(json.dumps(SEARXNG_BODY).encode())

    monkeypatch.setattr(service.urllib.request, "urlopen", urlopen)
    results = SearxngSearch("http://searxng:8080/").search("fastapi docs", 2)

    assert seen["url"] == "http://searxng:8080/search?q=fastapi+docs&format=json"
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("FastAPI docs", "https://fastapi.tiangolo.com/", "Fast & modern."),
        ("Starlette", "https://www.starlette.io/", "ASGI toolkit."),
    ]


@pytest.mark.parametrize(("code", "message"), [(403, "json format"), (429, "limits the number"), (500, "HTTP 500")])
def test_searxng_errors_are_readable(monkeypatch, code, message):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "error", {}, None)

    monkeypatch.setattr(service.urllib.request, "urlopen", urlopen)
    with pytest.raises(SearchError, match=message):
        SearxngSearch("http://searxng:8080").search("x", 5)


def test_an_unreachable_service_is_readable(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(service.urllib.request, "urlopen", urlopen)
    with pytest.raises(SearchError, match="Could not reach the search service"):
        SearxngSearch("http://searxng:8080").search("x", 5)


def test_the_agent_searches_the_web_through_searxng(client, fake, monkeypatch):
    queries = []

    def request(self, query):
        queries.append((self.base_url, query))
        return SEARXNG_BODY

    monkeypatch.setattr(SearxngSearch, "request", request)
    client.app.state.web_search = SearxngSearch("http://searxng:8080")
    project, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="Searching.", tool_calls=(("web_search", {"query": "fastapi docs", "count": 3}),)),
            ScriptedResponse(text="Found it."),
        ]
    )

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Find the docs"})
    events = parse_events(response.text)

    result = next(data for name, data in events if name == "tool_result")
    assert not result["is_error"], result
    assert "1. FastAPI docs\n   https://fastapi.tiangolo.com/\n   Fast & modern." in result["output"]
    assert "3. Uvicorn" in result["output"]
    assert queries == [("http://searxng:8080", "fastapi docs")]
    # Searching runs without asking.
    assert not any(name == "approval" for name, _ in events)
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "web_search" in names


def test_a_conversation_outside_a_project_can_search_the_web(client, fake, monkeypatch):
    monkeypatch.setattr(SearxngSearch, "request", lambda self, query: SEARXNG_BODY)
    client.app.state.web_search = SearxngSearch("http://searxng:8080")
    add_key(client)
    conversation = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium", "project_id": None}
    ).json()
    fake.responses.extend(
        [
            ScriptedResponse(text="Searching.", tool_calls=(("web_search", {"query": "weer Barneveld morgen"}),)),
            ScriptedResponse(text="Dry and mild."),
        ]
    )
    response = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"text": "Weer morgen?"})
    result = next(data for name, data in parse_events(response.text) if name == "tool_result")
    assert not result["is_error"], result
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    # Web tools, but nothing that needs a project folder.
    assert {"web_search", "web_fetch"} <= names
    assert not names & {"run_command", "write_file", "read_file", "git_commit", "update_memory", "delegate"}


def test_without_the_search_service_there_is_no_web_search_tool(client, fake):
    project, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hi."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "web_search" not in names and "web_fetch" in names


def test_web_search_needs_no_key():
    from app.credentials.types import default_credential_types

    assert "brave_search_api_key" not in default_credential_types()
