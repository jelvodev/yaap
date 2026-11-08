"""Plugin pages without a build step: plain HTML with ES modules and the plugin SDK. A page reaches YAAP's API only
through YAAP's own page, which makes the call with the session and names the plugin in X-YAAP-Plugin; such a call
reaches only the routes the manifest lists."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.plugins.manifest import ManifestError, parse_manifest
from app.plugins.page_access import ApiRoute, PageGrant, RouteError, page_refusal, parse_route, parse_routes
from tests.test_plugins import plugin, restart, write_plugin

# A page as an author writes it: plain HTML and an ES module that imports the SDK YAAP serves. No build, no npm.
PAGE_HTML = """<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Projects</title></head>
  <body>
    <ul id="projects"></ul>
    <script type="module" src="app.js"></script>
  </body>
</html>
"""
PAGE_SCRIPT = """import { connect } from "/plugin-sdk.js";

const yaap = await connect();
const { data } = await yaap.api.GET("/api/v1/environments/{environment_id}/projects", {
  params: { path: { environment_id: yaap.environment.id } },
});
document.getElementById("projects").textContent = data.map((project) => project.name).join(", ");
"""
ROUTES = [
    "GET /api/v1/environments",
    "GET /api/v1/environments/{environment_id}/projects",
    "PUT /api/v1/settings/{key}",
]
SDK_SOURCE = b"export async function connect() {}\n"


def write_projects_plugin(data_dir: Path, routes: list[str] | None = None) -> Path:
    manifest = {
        "name": "projects",
        "version": "1.0.0",
        "description": "Lists the projects.",
        "page": "page/index.html",
        "api": ROUTES if routes is None else routes,
    }
    files = {"page/index.html": PAGE_HTML, "page/app.js": PAGE_SCRIPT}
    return write_plugin(data_dir, "projects", "def register(yaap):\n    pass\n", manifest, files)


def for_plugin(name: str = "projects") -> dict[str, str]:
    """The header with which YAAP's page makes a call for a plugin page (frontend/src/api/plugin-calls.ts)."""
    return {"X-YAAP-Plugin": name}


@pytest.mark.parametrize(
    ("route", "method", "path", "allowed"),
    [
        ("GET /api/v1/environments", "GET", "/api/v1/environments", True),
        ("GET /api/v1/environments", "POST", "/api/v1/environments", False),
        ("GET /api/v1/environments", "GET", "/api/v1/environments/1", False),
        ("GET /api/v1/environments/{environment_id}/projects", "GET", "/api/v1/environments/7/projects", True),
        ("GET /api/v1/environments/{environment_id}/projects", "GET", "/api/v1/environments//projects", False),
        ("GET /api/v1/environments/{environment_id}/projects", "GET", "/api/v1/environments/7/8/projects", False),
        ("GET /api/v1/environments/{environment_id}/projects", "GET", "/api/v1/environments/../plugins", False),
        ("PUT /api/v1/settings/{key}", "PUT", "/api/v1/settings/interface", True),
        ("PUT /api/v1/settings/{key}", "DELETE", "/api/v1/settings/interface", False),
    ],
)
def test_a_page_token_reaches_only_the_routes_its_manifest_lists(route, method, path, allowed):
    grant = PageGrant("demo", (parse_route(route),))
    assert (page_refusal(grant, method, path) is None) is allowed


@pytest.mark.parametrize(
    ("method", "path", "reason"),
    [
        ("GET", "/api/v1/auth/tokens", "account, token or plugin"),
        ("GET", "/api/v1/plugins", "account, token or plugin"),
        ("POST", "/api/v1/plugins/restart", "account, token or plugin"),
        ("GET", "/mcp", "only call YAAP's API"),
        ("GET", "/api/v1/./environments", "only call YAAP's API"),
    ],
)
def test_some_routes_stay_closed_to_a_page_token_even_when_listed(method, path, reason):
    # A grant that would match anything with these segments still refuses them.
    grant = PageGrant("demo", (ApiRoute("GET", "/api/v1/{a}/{b}"), ApiRoute("POST", "/api/v1/{a}/{b}")))
    refusal = page_refusal(grant, method, path)
    assert refusal is not None and reason in refusal


def test_a_call_for_a_plugin_that_is_not_loaded_with_a_page_is_refused():
    refusal = page_refusal(None, "GET", "/api/v1/environments")
    assert refusal is not None and "not loaded with a page" in refusal


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("FETCH /api/v1/environments", "must start with one of"),
        ("GET environments", "must name a path under /api/v1/"),
        ("GET /api/v1/environments/", "must name a path under /api/v1/"),
        ("GET /api/v1/environments/*", "cannot match"),
        ("GET /api/v1/../auth/tokens", "cannot match"),
        ("POST /api/v1/auth/tokens", "not open to plugin pages"),
        ("PUT /api/v1/plugins/{name}", "not open to plugin pages"),
    ],
)
def test_manifest_routes_that_cannot_be_used_are_refused(value, problem):
    with pytest.raises(RouteError, match=problem):
        parse_route(value)


def test_manifest_route_lists_are_checked():
    assert parse_routes(None) == ()
    assert [route.text for route in parse_routes(["GET /api/v1/environments"])] == ["GET /api/v1/environments"]
    with pytest.raises(RouteError, match="twice"):
        parse_routes(["GET /api/v1/environments", "GET /api/v1/environments"])
    with pytest.raises(RouteError, match="list of routes"):
        parse_routes("GET /api/v1/environments")


def test_a_manifest_with_api_routes_needs_a_page(tmp_path):
    folder = write_plugin(tmp_path, "nopage", "", {"name": "nopage", "version": "1", "api": ROUTES[:1]})
    with pytest.raises(ManifestError, match="needs a page"):
        parse_manifest(folder)
    page = write_projects_plugin(tmp_path)
    assert [route.text for route in parse_manifest(page).api] == ROUTES


def test_a_plain_html_page_loads_without_a_build_and_its_calls_reach_only_its_routes(client, tmp_path, fake, master_key):
    folder = write_projects_plugin(tmp_path)
    listed = plugin(client, "projects")
    assert listed["api_routes"] == ROUTES and "page_api_token" not in listed
    client.put("/api/v1/plugins/projects", json={"enabled": True})

    with restart(fake, master_key) as again:
        page_url = plugin(again, "projects")["page_url"]

        # The page is the author's files as they are on disk, served to the sandboxed frame.
        page = again.get(page_url)
        assert page.content == (folder / "page/index.html").read_bytes()
        policy = page.headers["content-security-policy"]
        assert f"script-src 'unsafe-inline' http://testserver{page_url} http://testserver/plugin-sdk.js;" in policy
        # No calls to YAAP's API from the frame: the SDK asks YAAP's page by postMessage.
        assert f"connect-src http://testserver{page_url};" in policy and "/api/v1/;" not in policy
        assert "sandbox allow-scripts allow-modals" in policy and "allow-same-origin" not in policy
        # An opaque origin loads ES modules in CORS mode, with Origin: null.
        frame = {"Origin": "null", "Sec-Fetch-Site": "cross-site"}
        module = TestClient(again.app).get(page_url + "app.js", headers=frame)
        assert module.status_code == 200 and module.content == (folder / "page/app.js").read_bytes()
        assert module.headers["content-type"] == "text/javascript; charset=utf-8"
        assert module.headers["access-control-allow-origin"] == "*"

        # Calls YAAP's page makes for the plugin: listed routes answer with the session, also a change.
        environments = again.get("/api/v1/environments", headers=for_plugin())
        assert environments.status_code == 200 and "access-control-allow-origin" not in environments.headers
        assert again.get("/api/v1/environments/1/projects", headers=for_plugin()).status_code == 200
        changed = again.put("/api/v1/settings/interface", json={"value": {"theme": "light"}}, headers=for_plugin())
        assert changed.status_code == 200, changed.text

        # Routes outside the manifest are refused on the server too.
        outside = again.get("/api/v1/settings", headers=for_plugin())
        assert outside.status_code == 403 and "access-control-allow-origin" not in outside.headers
        assert outside.json()["detail"] == (
            "The plugin projects does not list GET /api/v1/settings under api in its plugin.json."
        )
        assert again.post("/api/v1/environments", json={"name": "x"}, headers=for_plugin()).status_code == 403
        assert again.delete("/api/v1/settings/interface", headers=for_plugin()).status_code == 403
        assert again.get("/api/v1/plugins", headers=for_plugin()).status_code == 403
        assert again.post("/api/v1/auth/tokens", json={}, headers=for_plugin()).status_code == 403
        assert again.get("/api/v1/auth/state", headers=for_plugin()).status_code == 403
        assert again.get("/api/v1/environments", headers=for_plugin("unknown")).status_code == 403
        # The header narrows a session, it never replaces one.
        assert TestClient(again.app).get("/api/v1/environments", headers=for_plugin()).status_code == 401

        # The frame itself cannot call the API: an opaque origin is refused at the boundary, with or without a
        # session, and no CORS preflight is answered.
        assert again.get("/api/v1/environments", headers=frame).status_code == 403
        assert again.get("/api/v1/environments", headers={**frame, **for_plugin()}).status_code == 403
        assert TestClient(again.app).get("/api/v1/environments", headers=frame).status_code == 403
        preflight = TestClient(again.app).options(
            "/api/v1/environments",
            headers={**frame, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
        )
        assert preflight.status_code == 403 and "access-control-allow-origin" not in preflight.headers


def test_a_plugin_without_a_page_gets_no_calls(client, tmp_path, fake, master_key):
    write_plugin(tmp_path, "quiet", "def register(yaap):\n    pass\n", {"name": "quiet", "version": "1"})
    client.put("/api/v1/plugins/quiet", json={"enabled": True})
    with restart(fake, master_key) as again:
        listed = plugin(again, "quiet")
        assert listed["loaded"] is True and listed["page_url"] is None and listed["api_routes"] == []
        assert again.get("/api/v1/environments", headers=for_plugin("quiet")).status_code == 403


def test_the_plugin_sdk_is_served_from_the_frontend_build_for_opaque_origins(client, tmp_path):
    missing = TestClient(client.app).get("/plugin-sdk.js")
    assert missing.status_code == 404 and "frontend build" in missing.json()["detail"]

    build = tmp_path / "static"
    build.mkdir()
    (build / "plugin-sdk.js").write_bytes(SDK_SOURCE)
    (build / "plugin-sdk.d.ts").write_text("export declare function connect(): Promise<void>;\n", encoding="utf-8")
    client.app.state.plugins.sdk_folder = build

    sdk = TestClient(client.app).get("/plugin-sdk.js", headers={"Origin": "null"})
    assert sdk.status_code == 200 and sdk.content == SDK_SOURCE
    assert sdk.headers["content-type"] == "text/javascript; charset=utf-8"
    assert sdk.headers["access-control-allow-origin"] == "*"
    declarations = TestClient(client.app).get("/plugin-sdk.d.ts")
    assert declarations.status_code == 200 and declarations.headers["content-type"].startswith("text/plain")
