"""Building blocks shared by YAAP's tools: the tool description, errors and output helpers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.agent.runs import AgentRun

# auto: runs without asking; ask: always waits for approval; conditional: asks when needs_approval says so.
Permission = Literal["auto", "ask", "conditional"]
# Where a tool can be used: in a project, in a conversation with attachments, or in every agent run.
Scope = Literal["project", "attachments", "project_attachments", "always"]

MAX_OUTPUT_CHARS = 30_000


class ToolError(Exception):
    """A tool failed in a way the model can act on; the message goes back to the model."""


class ToolOutput(str):
    """A tool's text output. Images can go along for models that can see them."""

    images: tuple[tuple[str, str], ...] = ()

    @classmethod
    def with_images(cls, text: str, images: list[tuple[str, str]]) -> "ToolOutput":
        """Images as (media type, base64 data)."""
        output = cls(text)
        output.images = tuple(images)
        return output

    @classmethod
    def of(cls, output: str) -> "ToolOutput":
        """The output as a ToolOutput (a plain string carries no images)."""
        return output if isinstance(output, ToolOutput) else cls(output)


def with_text(output: str, text: str) -> ToolOutput:
    """The output with new text: a ToolOutput that keeps the images the original output carried, if any."""
    return ToolOutput.with_images(text, list(output.images) if isinstance(output, ToolOutput) else [])


Handler = Callable[[AgentRun, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: Permission
    handler: Handler
    # For tools that ask: a one-line description of what will happen, shown on the approval card.
    describe: Callable[[dict[str, Any]], str] | None = None
    scope: Scope = "project"
    # For conditional tools: whether this call needs the user's approval.
    needs_approval: Callable[[AgentRun, dict[str, Any]], bool] | None = None
    # Only reads: never asks because of the injection guard (reading cannot act on outside instructions).
    read_only: bool = False
    # The handler shows its own approval card (install_skill), so the gate never asks for it.
    asks_itself: bool = False
    # Secret placeholders may be in any of its text arguments; the handler fills them in (the browser's typing).
    takes_secrets: bool = False


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n\n[Output truncated: {len(text) - limit} more characters.]"


def text_argument(arguments: dict[str, Any], name: str, required: bool = True) -> str:
    value = arguments.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and value == ""):
        raise ToolError(f"'{name}' must be a non-empty string.")
    return value


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


STRING = {"type": "string"}
