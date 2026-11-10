"""OpenAI models through Codex: runs the unmodified `codex` CLI that the user signed in with their ChatGPT account.

The OpenAI counterpart of the Claude Code connection. YAAP never sees the ChatGPT credentials; Codex keeps
its own sign-in under CODEX_HOME. Each reply is one `codex exec --json` run. History lives in Codex sessions: a reply
forks the session of the previous reply, so every reply has its own session and branches stay independent.

Codex's own tools stay off. It runs in a read-only sandbox without approvals, in an empty folder, without the user's
config, with every feature that adds a tool switched off; an agent run gets only YAAP's tools over MCP, where YAAP's
permission gate decides.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import signal
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.subprocess_env import safe_subprocess_env
from app.providers.base import (
    AgentEvent,
    AgentRequest,
    Completed,
    ContentBlock,
    HistoryMessage,
    ModelCallFinished,
    ProviderError,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
    ToolCallFinished,
    ToolCallStarted,
)
from app.providers.claude_code_provider import render_transcript
from app.providers.cli_install import tool_command
from app.providers.cli_sign_in import SignInSpec
from app.providers.messages_content import to_messages_content

logger = logging.getLogger(__name__)

# Installed into the data folder at the first sign-in (providers/cli_install.py), at the version this release pins.
TOOL = "codex"
DEFAULT_COMMAND = tool_command(TOOL)
NOT_INSTALLED_MESSAGE = (
    "Codex is not installed yet. Sign in to it once under Settings › Connections › Providers; that installs it."
)
SESSION_BLOCK_TYPE = "codex_session"
SIGN_IN_COMMAND = "docker compose exec -it yaap codex login --device-auth"
# Where Codex's sign-in link may point, and the shape of the one-time code it shows for the user to enter there.
SIGN_IN_HOSTS = frozenset({"auth.openai.com"})
USER_CODE_PATTERN = re.compile(r"\b[A-Z0-9]{3,8}-[A-Z0-9]{3,8}\b")
STREAM_LINE_LIMIT = 16 * 1024 * 1024
STDERR_TAIL = 2000
TOOL_SERVER_NAME = "yaap"
TOKEN_VARIABLE = "YAAP_TOOL_TOKEN"
# Longer than YAAP's approval timeout, so a tool call that waits for the user is not cut off by Codex.
TOOL_TIMEOUT_SECONDS = 35 * 60
# Features that give the model tools of its own. Codex refuses unknown names, so this list follows the Codex version
# pinned in the Dockerfile; check it with `codex features list` when that version changes.
DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "remote_plugin",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "multi_agent",
    "multi_agent_v2",
    "goals",
    "memories",
    "skill_search",
    "tool_suggest",
    "view_image",
    "hooks",
    "sleep_tool",
)
CODEX_INSTRUCTION_BLOCKS = (
    "include_permissions_instructions",
    "include_collaboration_mode_instructions",
    "include_apps_instructions",
    "include_skills_usage_instructions",
    "include_plugin_usage_instructions",
    "skills.include_instructions",
    "include_environment_context",
)
# What the Codex process takes from YAAP's environment besides the system's basics: its sign-in folder. Nothing else
# of YAAP's reaches it; YAAP's tool token is passed per run.
INHERITED_ENVIRONMENT = frozenset({"CODEX_HOME"})


def codex_environment(token: str | None) -> dict[str, str]:
    environment = safe_subprocess_env({TOKEN_VARIABLE: token} if token is not None else None, only=INHERITED_ENVIRONMENT)
    environment.setdefault("CODEX_HOME", str(Path(tempfile.gettempdir()) / "yaap-codex"))
    Path(environment["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    return environment


def workspace_directory() -> Path:
    """An empty folder as working root, so no project files or AGENTS.md are picked up; YAAP gives the context."""
    base = Path(os.environ.get("CODEX_HOME") or Path(tempfile.gettempdir()) / "yaap-codex")
    workspace = base / "yaap-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def toml_string(value: str) -> str:
    """A TOML basic string; JSON's string escapes are valid TOML."""
    return json.dumps(value)


def session_id_of(message: HistoryMessage) -> str | None:
    for block in message.provider_raw or []:
        if block.get("type") == SESSION_BLOCK_TYPE and isinstance(block.get("thread_id"), str):
            return block["thread_id"]
    return None


@dataclass(frozen=True)
class CodexTurn:
    fork_session_id: str | None
    text: str
    # (media type, base64 data) of images in the new message; Codex takes them as files.
    images: list[tuple[str, str]]


def plan_turn(messages: Sequence[HistoryMessage]) -> CodexTurn:
    """Fork the session of the latest reply that has one; send everything after it as the new prompt."""
    resume_index = next((index for index in range(len(messages) - 1, -1, -1) if session_id_of(messages[index])), None)
    fork_session_id = session_id_of(messages[resume_index]) if resume_index is not None else None
    pending = list(messages[resume_index + 1 :] if resume_index is not None else messages)
    if pending and all(message.role == "user" for message in pending):
        blocks = to_messages_content([block for message in pending for block in message.content])
        introduction = ""
    else:
        # Replies made another way (an API key, another vendor) have no session: pass them along as a transcript.
        *before, latest = pending
        blocks = to_messages_content(latest.content)
        introduction = (
            "Earlier in this conversation, outside this session:\n\n"
            f"{render_transcript(before)}\n\n"
            "Continue the conversation. The user's new message:\n\n"
        )
    texts, images = [], []
    for block in blocks:
        if block.get("type") == "text":
            texts.append(block["text"])
        elif block.get("type") == "image":
            images.append((block["source"]["media_type"], block["source"]["data"]))
        elif block.get("type") == "document":
            texts.append("[A PDF is attached in YAAP, but Codex cannot read PDFs here.]")
    text = introduction + ("\n\n".join(texts) or "(empty message)")
    return CodexTurn(fork_session_id, text, images)


def tool_result_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    parts = []
    for part in result.get("content") or []:
        if isinstance(part, dict):
            parts.append(str(part.get("text", "")) if part.get("type") == "text" else f"[{part.get('type', 'content')}]")
    return "\n".join(parts)


def usage_from(usage: dict[str, Any]) -> TokenUsage:
    cached = int(usage.get("cached_input_tokens") or 0)
    return TokenUsage(
        input_tokens=max(int(usage.get("input_tokens") or 0) - cached, 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=cached,
        cache_write_tokens=int(usage.get("cache_write_input_tokens") or 0),
    )


def error_for(message: str) -> ProviderError:
    lowered = message.lower()
    if "401" in lowered or "unauthorized" in lowered or "not logged in" in lowered or "login" in lowered:
        return ProviderError(f"Codex is not signed in. Sign in under Settings › Connections › Providers, "
            f"or from the YAAP folder with: {SIGN_IN_COMMAND}")
    if "usage limit" in lowered or "rate limit" in lowered or "429" in lowered:
        return ProviderError(f"The usage limit of your ChatGPT plan was reached. {message}".strip())
    return ProviderError(f"Codex reported an error: {message or 'no details'}")


class CodexProvider:
    def __init__(self, command: Sequence[str] = DEFAULT_COMMAND) -> None:
        self.command = list(command)
        self.process: asyncio.subprocess.Process | None = None
        self.files: list[Path] = []

    def build_arguments(self, request: AgentRequest, turn: CodexTurn, instructions_file: Path, images: list[Path]) -> list[str]:
        settings = [
            f"model_instructions_file={toml_string(str(instructions_file))}",
            f"model_reasoning_effort={toml_string(request.effort)}",
            'model_reasoning_summary="auto"',
            'approval_policy="never"',
            # A fork takes no --sandbox option, so the sandbox is set as config for every run.
            'sandbox_mode="read-only"',
            'web_search="disabled"',
            'history.persistence="none"',
            "check_for_update_on_startup=false",
            # YAAP's system prompt is the only instruction: leave out Codex's own blocks about its skills, sandbox,
            # modes, apps and the empty working folder (checked with `codex debug prompt-input`).
            *(f"{key}=false" for key in CODEX_INSTRUCTION_BLOCKS),
        ]
        if request.tool_server is not None:
            prefix = f"mcp_servers.{TOOL_SERVER_NAME}"
            settings += [
                f"{prefix}.url={toml_string(request.tool_server.url)}",
                # The run's token travels in an environment variable, never on the command line.
                f"{prefix}.bearer_token_env_var={toml_string(TOKEN_VARIABLE)}",
                f"{prefix}.tool_timeout_sec={TOOL_TIMEOUT_SECONDS}",
                f"{prefix}.startup_timeout_sec=30",
                f"{prefix}.required=true",
            ]
        arguments = [*self.command, "exec"]
        if turn.fork_session_id:
            arguments += ["fork", turn.fork_session_id]
        arguments += [
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--model", request.model,
        ]  # fmt: skip
        for setting in settings:
            arguments += ["-c", setting]
        for feature in DISABLED_FEATURES:
            arguments += ["--disable", feature]
        for image in images:
            arguments += ["--image", str(image)]
        arguments.append("-")  # The prompt comes on stdin.
        return arguments

    def write_file(self, suffix: str, data: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(suffix=suffix, dir=workspace_directory())
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.chmod(name, 0o600)
        path = Path(name)
        self.files.append(path)
        return path

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        turn = plan_turn(request.messages)
        instructions = self.write_file(".md", "\n\n".join(part.text for part in request.system).encode())
        images = [
            self.write_file("." + media_type.split("/")[-1].replace("jpeg", "jpg"), base64.b64decode(data))
            for media_type, data in turn.images
        ]
        token = request.tool_server.token if request.tool_server is not None else None
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.build_arguments(request, turn, instructions, images),
                cwd=workspace_directory(),
                env=codex_environment(token),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LINE_LIMIT,
            )
        except FileNotFoundError as error:
            raise ProviderError(NOT_INSTALLED_MESSAGE) from error

        process = self.process
        assert process.stdin and process.stdout and process.stderr
        stderr_task = asyncio.create_task(process.stderr.read())
        process.stdin.write(turn.text.encode())
        await process.stdin.drain()
        process.stdin.close()

        thread_id: str | None = None
        usage = TokenUsage()
        completed = False
        failure: str | None = None
        last_error: str | None = None
        wrote_text = False
        tool_calls = 0
        async for raw_line in process.stdout:
            try:
                line = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            kind = line.get("type")
            item = line.get("item") if isinstance(line.get("item"), dict) else {}
            item_type = item.get("type")
            if kind == "thread.started":
                thread_id = line.get("thread_id")
            elif kind == "turn.completed":
                usage = usage_from(line.get("usage") or {})
                completed = True
            elif kind == "turn.failed":
                failure = str((line.get("error") or {}).get("message") or last_error or "")
            elif kind == "error":
                # Codex reports reconnects as errors too; only turn.failed ends the reply. A missing sign-in would
                # only be retried for seconds, so it ends the reply at once.
                last_error = str(line.get("message") or "")
                if "401 Unauthorized" in last_error:
                    failure = last_error
                    break
            elif kind == "item.started" and item_type == "mcp_tool_call":
                tool_calls += 1
                yield ToolCallStarted(str(item.get("id")), str(item.get("tool") or ""), item.get("arguments") or {})
            elif kind == "item.completed" and item_type == "mcp_tool_call":
                failed = item.get("status") == "failed" or item.get("error") is not None
                text = tool_result_text(item.get("result")) or str((item.get("error") or {}).get("message") or "")
                yield ToolCallFinished(str(item.get("id")), text, failed)
            elif kind == "item.completed" and item_type == "agent_message" and item.get("text"):
                yield TextDelta(("\n\n" if wrote_text else "") + str(item["text"]))
                wrote_text = True
            elif kind == "item.completed" and item_type == "reasoning" and item.get("text"):
                yield ReasoningDelta(str(item["text"]) + "\n\n")
            elif kind == "item.started" and item_type in ("command_execution", "file_change", "web_search"):
                # Should not happen with Codex's tools off; logged so a new Codex version that adds one is noticed.
                logger.warning("Codex used its own %s tool despite the configuration.", item_type)

        if failure is not None and process.returncode is None:
            await self.close_process()
        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace")[-STDERR_TAIL:]
        if failure is not None or not completed:
            message = failure or last_error or stderr.strip() or f"exit code {return_code}"
            logger.warning("Codex ended without a reply (exit code %s): %s", return_code, message)
            raise error_for(message)
        session_block: ContentBlock = {"type": SESSION_BLOCK_TYPE, "thread_id": thread_id}
        # Codex reports the turn's totals only, not how many model calls it made.
        yield ModelCallFinished(request.model, usage, None)
        yield Completed(
            stop_reason="end_turn",
            served_by_model=request.model,
            usage=usage,
            provider_raw=[session_block],
            billing="subscription",
            # Codex reports the whole turn's usage only; without tool calls that is the context.
            context_tokens=None if tool_calls == 0 else usage.input_tokens + usage.cache_read_tokens,
        )

    async def close(self) -> None:
        await self.close_process()
        for file in self.files:
            file.unlink(missing_ok=True)
        self.files.clear()

    async def close_process(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError, ValueError):
                process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


@dataclass(frozen=True)
class CodexStatus:
    installed: bool
    version: str | None
    logged_in: bool
    detail: str | None
    sign_in_command: str = SIGN_IN_COMMAND


async def read_codex_status(command: Sequence[str] = DEFAULT_COMMAND) -> CodexStatus:
    async def run(arguments: list[str]) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workspace_directory(),
            env=codex_environment(None),
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.wait()
            return -1, ""
        return process.returncode or 0, output.decode(errors="replace")

    try:
        _, version = await run([*command, "--version"])
        code, status = await run([*command, "login", "status"])
    except FileNotFoundError:
        return CodexStatus(False, None, False, None)
    lines = [line.strip() for line in status.splitlines() if line.strip() and not line.startswith("WARNING")]
    return CodexStatus(True, version.strip() or None, code == 0, lines[-1] if lines else None)


def codex_sign_in_spec(command: Sequence[str] = DEFAULT_COMMAND) -> SignInSpec:
    """Signing in from the browser: `codex login --device-auth` prints a link and a one-time code, then waits."""
    return SignInSpec(
        key="codex",
        display_name="Codex",
        command=(*command, "login", "--device-auth"),
        environment=lambda: codex_environment(None),
        working_directory=workspace_directory,
        allowed_hosts=SIGN_IN_HOSTS,
        paste_code=False,
        user_code_pattern=USER_CODE_PATTERN,
        signed_in=codex_signed_in,
        tool=TOOL,
    )


async def codex_signed_in() -> bool:
    """Whether Codex is installed and reports a sign-in: the provider spec's sign-in check."""
    return (await read_codex_status()).logged_in
