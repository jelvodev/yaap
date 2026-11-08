"""A small A2A client: read an agent's card, send it a message and wait for its answer.

Only the JSON-RPC binding of A2A 0.3 is used: `message/send` and, for a task that is still working, `tasks/get`
until it ends or the agent's timeout passes. The token goes only to the address the user configured: an agent card
that names an endpoint on another origin is refused, so a card cannot send the token elsewhere.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

CARD_PATH = "/.well-known/agent-card.json"
PROTOCOL_VERSION = "0.3.0"
POLL_SECONDS = 2.0
REQUEST_TIMEOUT = 60.0
MAX_REPLY_CHARS = 50_000
MAX_CARD_BYTES = 256 * 1024
TERMINAL = frozenset({"completed", "failed", "canceled", "rejected"})
# The agent waits for something from the caller; YAAP reports it instead of waiting.
INTERRUPTED = frozenset({"input-required", "auth-required"})


class A2AError(Exception):
    """The agent cannot be reached or answered something YAAP cannot use; the message is safe to show."""


@dataclass(frozen=True)
class AgentCard:
    name: str
    description: str
    endpoint: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class AgentReply:
    text: str
    state: str
    context_id: str | None
    task_id: str | None


def card_url(url: str) -> str:
    """The configured url is the card itself (ends in .json) or the address the card is found under."""
    return url if urlsplit(url).path.endswith(".json") else url.rstrip("/") + CARD_PATH


def origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def headers(token: str | None) -> dict[str, str]:
    values = {"Accept": "application/json", "A2A-Version": PROTOCOL_VERSION}
    if token:
        values["Authorization"] = f"Bearer {token}"
    return values


def parse_card(data: Any, configured: str) -> AgentCard:
    if not isinstance(data, dict):
        raise A2AError("The agent card is not a JSON object.")
    endpoint = data.get("url")
    transport = str(data.get("preferredTransport") or "JSONRPC").upper()
    if transport != "JSONRPC":
        others = data.get("additionalInterfaces") or []
        match = next(
            (item for item in others if isinstance(item, dict) and str(item.get("transport", "")).upper() == "JSONRPC"),
            None,
        )
        if match is None:
            raise A2AError(f"The agent speaks {transport}; YAAP speaks only A2A over JSON-RPC.")
        endpoint = match.get("url")
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        raise A2AError("The agent card has no http(s) url to send messages to.")
    if origin(endpoint) != origin(configured):
        raise A2AError(
            f"The agent card sends messages to {urlsplit(endpoint).netloc}, not to the address you configured; "
            "YAAP does not send the token there."
        )
    skills = tuple(
        str(skill.get("name") or skill.get("id"))
        for skill in data.get("skills") or []
        if isinstance(skill, dict) and (skill.get("name") or skill.get("id"))
    )
    return AgentCard(str(data.get("name") or ""), str(data.get("description") or ""), endpoint, skills)


async def read_card(client: httpx.AsyncClient, url: str, token: str | None) -> AgentCard:
    try:
        response = await client.get(card_url(url), headers=headers(token))
    except httpx.HTTPError as error:
        raise A2AError(f"The agent card could not be read: {error}") from error
    if response.status_code != 200:
        raise A2AError(f"The agent card could not be read: HTTP {response.status_code}.")
    if len(response.content) > MAX_CARD_BYTES:
        raise A2AError("The agent card is too large.")
    try:
        data = response.json()
    except ValueError as error:
        raise A2AError("The agent card is not JSON.") from error
    return parse_card(data, url)


async def rpc(client: httpx.AsyncClient, endpoint: str, token: str | None, method: str, params: dict[str, Any]) -> Any:
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
    try:
        response = await client.post(endpoint, json=body, headers=headers(token))
    except httpx.HTTPError as error:
        raise A2AError(f"The agent could not be reached: {error}") from error
    if response.status_code in (401, 403):
        raise A2AError(f"The agent refused the request (HTTP {response.status_code}); check its token.")
    try:
        data = response.json()
    except ValueError as error:
        raise A2AError(f"The agent answered HTTP {response.status_code} without JSON.") from error
    if not isinstance(data, dict):
        raise A2AError("The agent's answer is not a JSON-RPC response.")
    if data.get("error"):
        error = data["error"] if isinstance(data["error"], dict) else {}
        raise A2AError(f"The agent answered with an error: {error.get('message') or data['error']}")
    if "result" not in data:
        raise A2AError("The agent's answer has no result.")
    return data["result"]


def parts_text(parts: Any) -> list[str]:
    texts = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif kind == "data":
            texts.append(json.dumps(part.get("data"), ensure_ascii=False, indent=2))
        elif kind == "file":
            file = part.get("file") if isinstance(part.get("file"), dict) else {}
            texts.append(f"[File: {file.get('name') or file.get('uri') or 'unnamed'}]")
    return texts


def task_text(task: dict[str, Any]) -> str:
    texts = []
    for artifact in task.get("artifacts") or []:
        if isinstance(artifact, dict):
            texts += parts_text(artifact.get("parts"))
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    message = status.get("message") if isinstance(status.get("message"), dict) else None
    if message is not None:
        texts += [text for text in parts_text(message.get("parts")) if text not in texts]
    return "\n\n".join(text for text in texts if text)


def reply_of(result: Any) -> AgentReply:
    if not isinstance(result, dict):
        raise A2AError("The agent's result is neither a message nor a task.")
    if result.get("kind") == "message" or ("parts" in result and "status" not in result):
        return AgentReply("\n\n".join(parts_text(result.get("parts"))), "completed", result.get("contextId"), result.get("taskId"))
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    return AgentReply(task_text(result), str(status.get("state") or "unknown"), result.get("contextId"), result.get("id"))


async def send_message(
    url: str,
    token: str | None,
    text: str,
    context_id: str | None = None,
    timeout: float = 300,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AgentReply:
    """Send text to the agent and return its answer once its task ends (or the timeout passes)."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(
        transport=transport, timeout=min(REQUEST_TIMEOUT, timeout), follow_redirects=False
    ) as client:
        card = await read_card(client, url, token)
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}],
        }
        if context_id:
            message["contextId"] = context_id
        params = {
            "message": message,
            "configuration": {"blocking": True, "acceptedOutputModes": ["text/plain", "text/markdown", "application/json"]},
        }
        reply = reply_of(await rpc(client, card.endpoint, token, "message/send", params))
        while reply.task_id and reply.state not in TERMINAL | INTERRUPTED:
            if time.monotonic() >= deadline:
                return AgentReply(reply.text, "timeout", reply.context_id, reply.task_id)
            await asyncio.sleep(POLL_SECONDS)
            reply = reply_of(await rpc(client, card.endpoint, token, "tasks/get", {"id": reply.task_id}))
        text = reply.text if len(reply.text) <= MAX_REPLY_CHARS else reply.text[:MAX_REPLY_CHARS] + "\n[Reply shortened.]"
        return AgentReply(text, reply.state, reply.context_id, reply.task_id)


async def probe_agent(url: str, token: str | None, transport: httpx.AsyncBaseTransport | None = None) -> AgentCard:
    async with httpx.AsyncClient(transport=transport, timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
        return await read_card(client, url, token)
