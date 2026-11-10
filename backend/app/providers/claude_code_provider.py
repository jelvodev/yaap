"""Claude through Claude Code: runs the unmodified `claude` CLI that the user signed in with their Claude subscription.

YAAP never sees or stores the subscription credentials; Claude Code keeps its own sign-in under CLAUDE_CONFIG_DIR.
Each reply is one `claude -p` run. Conversation history lives in Claude Code sessions: a reply resumes the session of
the previous reply and forks it, so every reply has its own session and branches stay independent.
"""

import asyncio
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

import anyio

from app.core.subprocess_env import safe_subprocess_env
from app.git.safety import UnsafeRepository, check_repository, git_variables
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
    ToolServer,
)
from app.providers.cli_sign_in import SignInSpec
from app.providers.messages_content import to_messages_content

logger = logging.getLogger(__name__)

DEFAULT_COMMAND = ("claude",)
SESSION_BLOCK_TYPE = "claude_code_session"
# What `claude auth login` prints before it asks for the code again.
REFUSED_CODE_PATTERN = re.compile(r"invalid code", re.IGNORECASE)
SIGN_IN_COMMAND = "docker compose exec -it yaap claude auth login"
# Where Claude Code's sign-in link may point: its own sites, nothing else.
SIGN_IN_HOSTS = frozenset({"claude.com", "claude.ai", "platform.claude.com"})
STREAM_LINE_LIMIT = 16 * 1024 * 1024
STDERR_TAIL = 2000
TOOL_SERVER_NAME = "yaap"
MAX_AGENT_TURNS = 60
# Without this, Claude Code's thinking blocks arrive empty; with it they stream as short summaries of what the model
# is working on. The flag is not listed in `claude --help`: when a version refuses it, YAAP retries without it.
THINKING_DISPLAY = ("--thinking-display", "summarized")
# Longer than YAAP's approval timeout, so a tool call that waits for the user is not cut off by Claude Code.
MCP_TOOL_TIMEOUT_MS = 35 * 60 * 1000
# What the Claude Code process takes from YAAP's environment besides the system's basics: its sign-in folder and the
# switch that keeps it from updating itself in the image. Nothing else of YAAP's reaches it.
INHERITED_ENVIRONMENT = frozenset({"CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER"})


def claude_code_environment(max_output_tokens: int | None = None, project_folder: Path | None = None) -> dict[str, str]:
    """Claude Code runs git in its working folder for its own context; in a project folder that git gets the same
    safe settings as YAAP's own (`app.git.safety`), since the project's .git can be written by the agent's commands."""
    extra = {"MCP_TOOL_TIMEOUT": str(MCP_TOOL_TIMEOUT_MS), **git_variables(project_folder)}
    if max_output_tokens:
        # The preset's reply length; Claude Code reads it per request it makes.
        extra["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    return safe_subprocess_env(extra, only=INHERITED_ENVIRONMENT)


def tool_name(name: str) -> str:
    """Claude Code names MCP tools mcp__<server>__<tool>; show YAAP's own name."""
    return name.removeprefix(f"mcp__{TOOL_SERVER_NAME}__")


def tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", "")) if part.get("type") != "image" else "[image]"
            for part in content
            if isinstance(part, dict)
        ]
        return "\n".join(parts)
    return ""


def context_tokens_of(usage: dict[str, Any] | None) -> int | None:
    if not usage:
        return None
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
    total = sum(int(usage.get(key) or 0) for key in keys)
    return total or None


def workspace_directory() -> Path:
    """An empty folder as working directory, so no project files, CLAUDE.md or .mcp.json are picked up."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path(tempfile.gettempdir()) / "yaap-claude-code")
    workspace = base / "yaap-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def session_id_of(message: HistoryMessage) -> str | None:
    for block in message.provider_raw or []:
        if block.get("type") == SESSION_BLOCK_TYPE and isinstance(block.get("session_id"), str):
            return block["session_id"]
    return None


def text_of(message: HistoryMessage) -> str:
    parts = []
    for block in message.content:
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
        elif block.get("type") in ("image", "document"):
            parts.append(f"[{block['type']} attached earlier]")
    return "\n\n".join(parts)


def render_transcript(messages: Sequence[HistoryMessage]) -> str:
    return "\n\n".join(f"<{message.role}>\n{text_of(message)}\n</{message.role}>" for message in messages)


@dataclass(frozen=True)
class TurnInput:
    resume_session_id: str | None
    content: list[dict[str, Any]]

    @property
    def prompt(self) -> str:
        """The text of the new turn, for logs and tests."""
        return "\n\n".join(block["text"] for block in self.content if block["type"] == "text")

    def input_line(self) -> str:
        """One user message on Claude Code's stream-json input; it carries images and PDFs as well as text."""
        message = {"type": "user", "message": {"role": "user", "content": self.content}}
        return json.dumps(message) + "\n"


def plan_turn(messages: Sequence[HistoryMessage]) -> TurnInput:
    """Resume the session of the latest reply that has one; send everything after it as the new prompt."""
    resume_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if session_id_of(messages[index])), None
    )
    earlier = messages[: resume_index + 1] if resume_index is not None else []
    pending = list(messages[resume_index + 1 :] if resume_index is not None else messages)
    resume_session_id = session_id_of(earlier[-1]) if earlier else None

    if pending and all(message.role == "user" for message in pending):
        content = to_messages_content([block for message in pending for block in message.content])
    else:
        # Replies that were not made through Claude Code (for example with an API key) have no session to resume:
        # pass them along as a transcript, followed by the latest user message.
        *before, latest = pending
        introduction = (
            "Earlier in this conversation, outside this session:\n\n"
            f"{render_transcript(before)}\n\n"
            "Continue the conversation. The user's new message:"
        )
        content = [{"type": "text", "text": introduction}, *to_messages_content(latest.content)]
    if not content:
        content = [{"type": "text", "text": "(empty message)"}]
    return TurnInput(resume_session_id, content)


def usage_from(result: dict[str, Any]) -> TokenUsage:
    usage = result.get("usage") or {}
    return TokenUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
    )


def error_for(result: dict[str, Any], api_error: str | None) -> ProviderError:
    detail = str(result.get("result") or "").strip()
    if api_error == "authentication_failed" or "/login" in detail:
        return ProviderError(f"Claude Code is not signed in. Sign in under Settings › Connections › Providers, "
            f"or from the YAAP folder with: {SIGN_IN_COMMAND}")
    if api_error == "rate_limit":
        return ProviderError(f"The usage limit of your Claude subscription was reached. {detail}".strip())
    return ProviderError(f"Claude Code reported an error: {detail or 'no details'}")


class ThinkingDisplayRefused(Exception):
    """This Claude Code version does not know --thinking-display."""


class ClaudeCodeProvider:
    # Shared by every run: once a version refuses the flag, it is no longer passed.
    thinking_display_supported = True

    def __init__(self, command: Sequence[str] = DEFAULT_COMMAND) -> None:
        self.command = list(command)
        self.process: asyncio.subprocess.Process | None = None
        self.system_prompt_file: Path | None = None
        self.mcp_config_file: Path | None = None

    def build_arguments(self, request: AgentRequest, turn: TurnInput, system_prompt_file: Path) -> list[str]:
        arguments = [
            *self.command,
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", request.model,
            "--effort", request.effort,
            "--system-prompt-file", str(system_prompt_file),
            # Claude Code's own tools stay off: a run has only YAAP's tools, through YAAP's MCP endpoint.
            # A message starting with "/" is text, not a command.
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--permission-mode", "dontAsk",
            # Only the settings in CLAUDE_CONFIG_DIR: a project's .claude/settings.json could name hooks, which would
            # run in YAAP's container with YAAP's rights.
            "--setting-sources", "user",
        ]  # fmt: skip
        if ClaudeCodeProvider.thinking_display_supported:
            arguments += THINKING_DISPLAY
        if self.mcp_config_file is not None:
            # YAAP's tools are allowed here; YAAP's own permission gate asks the user where needed.
            arguments += [
                "--mcp-config", str(self.mcp_config_file),
                "--allowedTools", f"mcp__{TOOL_SERVER_NAME}",
                "--max-turns", str(request.max_model_calls or MAX_AGENT_TURNS),
            ]  # fmt: skip
        if turn.resume_session_id:
            arguments += ["--resume", turn.resume_session_id, "--fork-session"]
        return arguments

    def write_mcp_config(self, tool_server: ToolServer, folder: Path) -> Path:
        """The run's token goes in a private file, never on the command line."""
        config = {
            "mcpServers": {
                TOOL_SERVER_NAME: {
                    "type": "http",
                    "url": tool_server.url,
                    "headers": {"Authorization": f"Bearer {tool_server.token}"},
                }
            }
        }
        descriptor, name = tempfile.mkstemp(suffix=".json", dir=folder)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(config, file)
        os.chmod(name, 0o600)
        return Path(name)

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        try:
            async for event in self.run_once(request):
                yield event
        except ThinkingDisplayRefused:
            logger.warning("Claude Code refused --thinking-display; continuing without thinking summaries.")
            ClaudeCodeProvider.thinking_display_supported = False
            await self.close()
            async for event in self.run_once(request):
                yield event

    async def run_once(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        if request.working_folder is not None:
            try:
                await anyio.to_thread.run_sync(check_repository, request.working_folder)
            except UnsafeRepository as error:
                raise ProviderError(str(error)) from error
        turn = plan_turn(request.messages)
        workspace = workspace_directory()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", dir=workspace, delete=False) as file:
            file.write("\n\n".join(part.text for part in request.system))
            self.system_prompt_file = Path(file.name)
        if request.tool_server is not None:
            self.mcp_config_file = self.write_mcp_config(request.tool_server, workspace)
        working_folder = request.working_folder or workspace

        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.build_arguments(request, turn, self.system_prompt_file),
                cwd=working_folder,
                env=claude_code_environment(request.max_tokens, request.working_folder),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LINE_LIMIT,
            )
        except FileNotFoundError as error:
            raise ProviderError(
                "Claude Code is not installed here. Rebuild the YAAP image with: docker compose up -d --build"
            ) from error

        process = self.process
        assert process.stdin and process.stdout and process.stderr
        stderr_task = asyncio.create_task(process.stderr.read())
        process.stdin.write(turn.input_line().encode())
        await process.stdin.drain()
        process.stdin.close()

        streamed_text = False
        api_error: str | None = None
        result: dict[str, Any] | None = None
        started: set[str] = set()
        context_tokens: int | None = None
        async for raw_line in process.stdout:
            try:
                line = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            kind = line.get("type")
            if line.get("parent_tool_use_id"):
                continue
            if kind == "stream_event":
                event = line.get("event") or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        streamed_text = True
                        yield TextDelta(delta["text"])
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                        yield ReasoningDelta(delta["thinking"])
            elif kind == "assistant":
                if line.get("error"):
                    api_error = str(line["error"])
                message = line.get("message") or {}
                context_tokens = context_tokens_of(message.get("usage")) or context_tokens
                for block in message.get("content") or []:
                    if block.get("type") == "tool_use" and block.get("id") not in started:
                        started.add(block["id"])
                        yield ToolCallStarted(block["id"], tool_name(block.get("name", "")), block.get("input") or {})
            elif kind == "user":
                for block in (line.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        yield ToolCallFinished(
                            str(block.get("tool_use_id")),
                            tool_result_text(block.get("content")),
                            bool(block.get("is_error")),
                        )
            elif kind == "result":
                result = line

        return_code = await process.wait()
        stderr = (await stderr_task).decode(errors="replace")[-STDERR_TAIL:]

        if result is None:
            if ClaudeCodeProvider.thinking_display_supported and THINKING_DISPLAY[0] in stderr:
                raise ThinkingDisplayRefused
            logger.warning("Claude Code exited with code %s without a result: %s", return_code, stderr)
            raise ProviderError(f"Claude Code stopped without a reply (exit code {return_code}). {stderr.strip()}".strip())
        if result.get("is_error"):
            logger.warning("Claude Code returned an error result: %s", result.get("result"))
            raise error_for(result, api_error)

        if not streamed_text and result.get("result"):
            yield TextDelta(str(result["result"]))
        served_by = next(iter(result.get("modelUsage") or {}), request.model)
        session_block: ContentBlock = {"type": SESSION_BLOCK_TYPE, "session_id": result.get("session_id")}
        # Claude Code reports its turn as a whole: the totals and how many model calls it made.
        turns = result.get("num_turns")
        yield ModelCallFinished(served_by, usage_from(result), turns if isinstance(turns, int) and turns > 0 else None)
        yield Completed(
            stop_reason=result.get("stop_reason"),
            served_by_model=served_by,
            usage=usage_from(result),
            provider_raw=[session_block],
            billing="subscription",
            context_tokens=context_tokens,
        )

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            # SIGINT lets Claude Code end the turn cleanly; kill it if it does not stop in time.
            # ValueError: Windows (local development) has no SIGINT for child processes; it falls through to kill.
            with contextlib.suppress(ProcessLookupError, ValueError):
                process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        for name in ("system_prompt_file", "mcp_config_file"):
            file = getattr(self, name)
            if file is not None:
                file.unlink(missing_ok=True)
                setattr(self, name, None)


@dataclass(frozen=True)
class ClaudeCodeStatus:
    installed: bool
    version: str | None
    logged_in: bool
    auth_method: str | None
    subscription_type: str | None
    email: str | None
    sign_in_command: str = SIGN_IN_COMMAND


async def run_command(arguments: Sequence[str], timeout: float = 20) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=workspace_directory(),
        env=claude_code_environment(),
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, ""
    return process.returncode or 0, stdout.decode(errors="replace")


async def read_claude_code_status(command: Sequence[str] = DEFAULT_COMMAND) -> ClaudeCodeStatus:
    try:
        _, version_output = await run_command([*command, "--version"])
        _, status_output = await run_command([*command, "auth", "status"])
    except FileNotFoundError:
        return ClaudeCodeStatus(False, None, False, None, None, None)
    try:
        status = json.loads(status_output)
    except json.JSONDecodeError:
        status = {}

    def text(key: str) -> str | None:
        value = status.get(key)
        return value if isinstance(value, str) and value else None

    return ClaudeCodeStatus(
        installed=True,
        version=version_output.strip() or None,
        logged_in=status.get("loggedIn") is True,
        auth_method=text("authMethod"),
        subscription_type=text("subscriptionType"),
        email=text("email"),
    )


def claude_code_sign_in_spec(command: Sequence[str] = DEFAULT_COMMAND) -> SignInSpec:
    """Signing in from the browser: `claude auth login` prints a link and waits for the code the user pastes."""
    return SignInSpec(
        key="claude-code",
        display_name="Claude Code",
        command=(*command, "auth", "login"),
        environment=claude_code_environment,
        working_directory=workspace_directory,
        allowed_hosts=SIGN_IN_HOSTS,
        paste_code=True,
        refused_code_pattern=REFUSED_CODE_PATTERN,
        user_code_pattern=None,
        signed_in=claude_code_signed_in,
    )


async def claude_code_signed_in() -> bool:
    """Whether Claude Code is installed and reports a sign-in: the provider spec's sign-in check."""
    return (await read_claude_code_status()).logged_in
