"""Plugins: own packages in data/plugins, switched on in Settings, loaded at the next start."""

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.tools import TOOLS_BY_NAME, tools_for
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import PluginState
from tests.conftest import make_test_app, parse_events, sign_in
from tests.test_chat import add_key, start_conversation

GREETER = '''
from pathlib import Path

from app.plugins.interface import STRING, Tool, schema

from .words import greeting

# Proof of import for the tests: a plugin that is not switched on never gets here.
Path(__file__).with_name("imported.txt").write_text("yes", encoding="utf-8")


async def greet(run, arguments):
    return greeting(arguments.get("name") or "world")


def register(yaap):
    yaap.register_tool(
        Tool(
            "greeter_greet",
            "Greet someone by name. Returns the greeting.",
            schema({"name": STRING}, []),
            "auto",
            greet,
            scope="always",
            read_only=True,
        )
    )
'''


def write_plugin(data_dir: Path, name: str, code: str, manifest: dict | None = None, files: dict | None = None) -> Path:
    folder = data_dir / "plugins" / name
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(
        json.dumps(manifest or {"name": name, "version": "1.0.0", "description": f"The {name} plugin."}),
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(code, encoding="utf-8")
    for path, text in (files or {}).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text, encoding="utf-8")
    return folder


def write_greeter(data_dir: Path, page: bool = False) -> Path:
    manifest = {
        "name": "greeter",
        "version": "1.2.0",
        "description": "Greets people.",
        "registers": {"tools": ["greeter_greet"]},
    }
    files = {"words.py": "def greeting(name):\n    return f'Hello, {name}!'\n"}
    if page:
        manifest["page"] = "page/index.html"
        files["page/index.html"] = "<!doctype html><title>Greeter</title><script src='app.js'></script>"
        files["page/app.js"] = "document.title = 'ready';"
    return write_plugin(data_dir, "greeter", GREETER, manifest, files)


def plugin(client, name: str) -> dict:
    return next(item for item in client.get("/api/v1/plugins").json() if item["name"] == name)


@contextmanager
def restart(fake, master_key: str) -> Iterator[TestClient]:
    """A new start of YAAP on the same data folder, as after a restart, signed in again. The first start took the
    master key out of the environment, as YAAP does; Docker gives it again at every start."""
    os.environ["YAAP_MASTER_KEY"] = master_key
    with TestClient(make_test_app(fake)) as restarted:
        sign_in(restarted)
        yield restarted


def test_a_plugin_that_is_not_switched_on_is_listed_but_not_imported(client, tmp_path, fake, master_key):
    folder = write_greeter(tmp_path)

    listed = plugin(client, "greeter")
    assert listed["version"] == "1.2.0"
    assert listed["description"] == "Greets people."
    assert listed["tools"] == ["greeter_greet"]
    assert listed["enabled"] is False and listed["loaded"] is False and listed["problem"] is None

    with restart(fake, master_key) as again:
        assert plugin(again, "greeter")["loaded"] is False
        assert "greeter_greet" not in {tool["name"] for tool in again.get("/api/v1/agent-tools").json()}
    assert not (folder / "imported.txt").exists()
    assert "yaap_plugins.greeter" not in sys.modules


def test_a_switched_on_plugin_registers_its_tool_at_the_next_start(client, tmp_path, fake, master_key):
    folder = write_greeter(tmp_path)

    switched = client.put("/api/v1/plugins/greeter", json={"enabled": True}).json()
    assert switched["enabled"] is True and switched["loaded"] is False and switched["restart_needed"] is True
    assert not (folder / "imported.txt").exists()

    with restart(fake, master_key) as again:
        listed = plugin(again, "greeter")
        assert listed["loaded"] is True and listed["restart_needed"] is False and listed["error"] is None
        assert (folder / "imported.txt").exists()
        tools = {tool["name"]: tool for tool in again.get("/api/v1/agent-tools").json()}
        assert tools["greeter_greet"]["description"] == "Greet someone by name"
        assert "greeter_greet" in tools_for(has_project=False, has_attachments=False)

        # The agent uses it like any other tool.
        add_key(again)
        conversation_id = start_conversation(again)
        fake.responses.extend(
            [
                ScriptedResponse(text="Greeting.", tool_calls=(("greeter_greet", {"name": "Sam"}),)),
                ScriptedResponse(text="Done."),
            ]
        )
        events = parse_events(
            again.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Greet me"}).text
        )
        result = next(data for name, data in events if name == "tool_result")
        assert result["output"] == "Hello, Sam!" and not result["is_error"]

        # Switching off takes effect at the next start too.
        off = again.put("/api/v1/plugins/greeter", json={"enabled": False}).json()
        assert off["loaded"] is True and off["restart_needed"] is True
    # Stopping YAAP takes the registrations back out of this process.
    assert "greeter_greet" not in TOOLS_BY_NAME
    assert "yaap_plugins.greeter" not in sys.modules


def test_a_plugin_that_fails_at_import_is_switched_off_with_its_error(client, tmp_path, fake, master_key):
    write_plugin(tmp_path, "broken", "import os\n\nraise RuntimeError('missing setting')\n")
    write_greeter(tmp_path)
    client.put("/api/v1/plugins/broken", json={"enabled": True})
    client.put("/api/v1/plugins/greeter", json={"enabled": True})

    with restart(fake, master_key) as again:
        broken = plugin(again, "broken")
        assert broken["enabled"] is False and broken["loaded"] is False
        assert broken["error"] == "RuntimeError: missing setting (__init__.py, line 3)"
        assert broken["failed_at"] is not None
        # YAAP still started, with the other plugin.
        assert plugin(again, "greeter")["loaded"] is True
        assert again.get("/api/v1/environments").status_code == 200
        assert "yaap_plugins.broken" not in sys.modules

        # Switching it on again clears the error until the next attempt.
        retried = again.put("/api/v1/plugins/broken", json={"enabled": True}).json()
        assert retried["error"] is None and retried["restart_needed"] is True


def test_a_plugin_that_registers_what_its_manifest_does_not_list_is_switched_off(client, tmp_path, fake, master_key):
    code = (
        "from app.plugins.interface import Tool, schema\n\n"
        "async def run(run, arguments):\n    return 'x'\n\n"
        "def register(yaap):\n"
        "    yaap.register_tool(Tool('sneaky_tool', 'Not listed.', schema({}, []), 'auto', run, scope='always'))\n"
    )
    write_plugin(tmp_path, "sneaky", code)
    clash = {"name": "clash", "version": "1", "registers": {"tools": ["read_file"]}}
    write_plugin(tmp_path, "clash", "def register(yaap):\n    pass\n", clash)
    client.put("/api/v1/plugins/sneaky", json={"enabled": True})
    client.put("/api/v1/plugins/clash", json={"enabled": True})

    with restart(fake, master_key) as again:
        assert "not listed under registers.tools" in plugin(again, "sneaky")["error"]
        assert plugin(again, "clash")["error"] == "plugin.json lists what register() did not register: tool read_file."
    assert "sneaky_tool" not in TOOLS_BY_NAME


def test_a_plugin_registers_a_credential_type_and_a_provider(client, tmp_path, fake, master_key):
    code = '''
from app.plugins.interface import CredentialTestResult, CredentialType, ModelInfo, ModelPricing, PriceTier, ProviderSpec


async def test_key(value):
    return CredentialTestResult(value == "good-key-1234", "Checked.")


def register(yaap):
    yaap.register_credential_type(CredentialType("localai_key", "LocalAI key", "For LocalAI.", test_key))
    free = ModelPricing(PriceTier(0, 0, 0, 0))
    yaap.register_provider(
        ProviderSpec("localai", "LocalAI", "localai_key", lambda key: None, metered=False),
        [ModelInfo("localai", "local-llama", "Local Llama", 32_000, ["low"], "low", free)],
    )
'''
    manifest = {
        "name": "localai",
        "version": "0.1.0",
        "registers": {"providers": ["localai"], "credential_types": ["localai_key"]},
    }
    write_plugin(tmp_path, "localai", code, manifest)
    client.put("/api/v1/plugins/localai", json={"enabled": True})

    with restart(fake, master_key) as again:
        assert plugin(again, "localai")["loaded"] is True
        kinds = {kind["kind"] for kind in again.get("/api/v1/vault/kinds").json()}
        assert "localai_key" in kinds
        models = {model["id"]: model for model in again.get("/api/v1/environments/1/models").json()}
        assert models["local-llama"]["available"] is False
        refused = again.put("/api/v1/environments/1/credentials/localai_key", json={"value": "bad-key-12345"})
        assert refused.status_code == 422
        stored = again.put("/api/v1/environments/1/credentials/localai_key", json={"value": "good-key-1234"})
        assert stored.status_code == 200, stored.text
        models = {model["id"]: model for model in again.get("/api/v1/environments/1/models").json()}
        assert models["local-llama"]["available"] is True
    assert "localai_key" not in client.app.state.credential_types


def test_a_plugin_page_is_served_sandboxed_to_whoever_has_its_address(client, tmp_path, fake, master_key):
    write_greeter(tmp_path, page=True)
    assert plugin(client, "greeter")["page_url"] is None
    client.put("/api/v1/plugins/greeter", json={"enabled": True})

    with restart(fake, master_key) as again:
        listed = plugin(again, "greeter")
        assert listed["has_page"] is True
        page_url = listed["page_url"]
        assert page_url.startswith("/api/v1/plugins/greeter/page/") and page_url.endswith("/")

        page = again.get(page_url)
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        policy = page.headers["content-security-policy"]
        assert "default-src 'none'" in policy and "sandbox allow-scripts" in policy
        assert f"connect-src http://testserver{page_url};" in policy
        assert "form-action 'none'" in policy
        assert again.get(page_url + "app.js").text == "document.title = 'ready';"

        token = page_url.split("/")[-2]
        assert again.get(page_url.replace(token, "wrong-token")).status_code == 404
        assert again.get(page_url + "../__init__.py").status_code == 404
        assert again.get(page_url + "..%2F__init__.py").status_code == 404
        assert again.get(page_url + "..%2Fplugin.json").status_code == 404


def test_a_manifest_problem_is_shown_and_blocks_switching_on(client, tmp_path):
    write_plugin(tmp_path, "nameless", "def register(yaap):\n    pass\n", {"name": "other", "version": "1"})
    (tmp_path / "plugins" / "Bad-Name").mkdir()

    listed = {item["name"]: item for item in client.get("/api/v1/plugins").json()}
    assert "folder's name" in listed["nameless"]["problem"]
    assert "lowercase" in listed["Bad-Name"]["problem"]
    refused = client.put("/api/v1/plugins/nameless", json={"enabled": True})
    assert refused.status_code == 422
    assert client.put("/api/v1/plugins/unknown", json={"enabled": True}).status_code == 404


def test_a_plugin_that_stopped_yaap_while_loading_is_switched_off(client, tmp_path, fake, master_key):
    folder = write_greeter(tmp_path)
    with client.app.state.session_factory() as session:
        session.add(PluginState(name="greeter", enabled=True, loading_since=datetime.now(UTC)))
        session.commit()

    with restart(fake, master_key) as again:
        listed = plugin(again, "greeter")
        assert listed["enabled"] is False and listed["loaded"] is False
        assert "YAAP stopped while loading this plugin" in listed["error"]
    assert not (folder / "imported.txt").exists()


def test_restart_asks_yaap_to_restart(client):
    asked = []
    client.app.state.request_restart = lambda: asked.append(True)
    assert client.post("/api/v1/plugins/restart").json() == {"restarting": True}
    assert asked == [True]
