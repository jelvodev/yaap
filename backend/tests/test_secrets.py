"""Secret placeholders: values inserted when a command runs, asked for, and masked everywhere."""

import asyncio
import base64
import sys
from dataclasses import replace
from urllib.parse import quote

from app.agent.policy import ToolPolicy
from app.agent.runs import RunRegistry
from app.agent.gate import call_tool
from app.providers.fake_provider import ScriptedResponse
from app.secrets.service import SecretError, mask, substitute
from tests.test_agent_tools import project, start_run  # noqa: F401 - the project fixture
from tests.test_delegation import project_conversation

VALUE = "s3cr3t-Value/with+chars"


def test_substitution_and_masking():
    command, variables = substitute("curl -H 'X: {{secret:TOKEN}}' {{ secret:TOKEN }}", {"TOKEN": VALUE})
    assert command == "curl -H 'X: ${YAAP_SECRET_TOKEN}' ${YAAP_SECRET_TOKEN}"
    assert variables == {"YAAP_SECRET_TOKEN": VALUE}
    try:
        substitute("echo {{secret:OTHER}}", {"TOKEN": VALUE})
        raise AssertionError("an unknown secret must be refused")
    except SecretError as error:
        assert "OTHER" in str(error) and "TOKEN" in str(error)

    encoded = base64.b64encode(VALUE.encode()).decode()
    text = f"plain {VALUE} url {quote(VALUE, safe='')} b64 {encoded} short ab"
    assert mask(text, {"TOKEN": VALUE, "SHORT": "ab"}) == (
        "plain [secret:TOKEN] url [secret:TOKEN] b64 [secret:TOKEN] short ab"
    )


def test_a_command_with_a_secret_always_asks_and_its_output_is_masked(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    run.services = replace(run.services, secrets={"TOKEN": VALUE})
    used: list[tuple[list[str], str]] = []
    run.services = replace(run.services, on_secret_use=lambda names, by: used.append((names, by)))
    # Even an allowlisted command asks when it uses a secret.
    run.config = replace(run.config, policy=ToolPolicy(commands=("*",)))
    command = f'"{sys.executable}" -c "import os; print(os.environ[\'YAAP_SECRET_TOKEN\'])" {{{{secret:TOKEN}}}}'
    asked: list[dict] = []

    async def approve_when_asked():
        event = (await run.events.get()).model_dump()
        asked.append(event)
        registry.decide(event["approval_id"], "approve")

    async def scenario():
        approver = asyncio.create_task(approve_when_asked())
        result = await call_tool(registry, run, "run_command", {"command": command})
        await approver
        return result

    output, is_error = asyncio.run(scenario())
    assert not is_error, output
    assert "[secret:TOKEN]" in output and VALUE not in output
    assert asked[0]["summary"].endswith("(uses secret TOKEN)")
    assert VALUE not in asked[0]["detail"]
    # The vault's log learns which secret was used, never the value.
    assert used == [(["TOKEN"], "Command in a conversation")]


def test_other_tools_refuse_placeholders_and_unknown_names_fail(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    run.services = replace(run.services, secrets={"TOKEN": VALUE})
    output, is_error = asyncio.run(
        call_tool(registry, run, "write_file", {"path": ".env", "content": "TOKEN={{secret:TOKEN}}\n"})
    )
    assert is_error and "only in the command" in output and not (project / ".env").exists()
    output, is_error = asyncio.run(call_tool(registry, run, "run_command", {"command": "echo {{secret:NOPE}}"}))
    assert is_error and "Unknown secret NOPE" in output


def test_secrets_api_is_write_only_with_project_overrides(client, fake):
    project, conversation_id = project_conversation(client)
    assert client.put("/api/v1/environments/1/secrets/db_password", json={"value": "x"}).status_code == 422
    assert client.put("/api/v1/environments/1/secrets/DB_PASSWORD", json={"value": "env-value"}).status_code == 200
    assert client.put(
        "/api/v1/environments/1/secrets/DB_PASSWORD", params={"project_id": project["id"]}, json={"value": "project-value"}
    ).status_code == 200
    client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": "token-value"})

    environment_level = client.get("/api/v1/environments/1/secrets").json()
    assert [(item["name"], item["level"]) for item in environment_level] == [
        ("API_TOKEN", "environment"),
        ("DB_PASSWORD", "environment"),
    ]
    project_level = client.get("/api/v1/environments/1/secrets", params={"project_id": project["id"]}).json()
    assert [(item["name"], item["level"]) for item in project_level] == [
        ("API_TOKEN", "environment"),
        ("DB_PASSWORD", "project"),
    ]
    assert "value" not in project_level[0]

    # The agent learns the names, never the values.
    fake.responses.append(ScriptedResponse(text="Ok."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hi"})
    system = "\n".join(part.text for part in fake.requests[-1].system)
    assert "{{secret:NAME}}: API_TOKEN, DB_PASSWORD" in system
    assert "project-value" not in system and "env-value" not in system

    assert client.delete("/api/v1/environments/1/secrets/DB_PASSWORD", params={"project_id": project["id"]}).status_code == 204
    assert client.delete("/api/v1/environments/1/secrets/DB_PASSWORD", params={"project_id": project["id"]}).status_code == 404
