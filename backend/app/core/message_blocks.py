"""The kinds of block a message is made of: validated when stored, published per kind, read leniently.

Stored blocks may carry internal keys the API does not show (a repo map's key, a command's read_only flag); the models
ignore them and validate_blocks leaves the stored dicts as they are.
"""

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)


class TextBlockOut(BaseModel):
    type: Literal["text"]
    text: str = ""


class ReasoningBlockOut(BaseModel):
    type: Literal["reasoning"]
    text: str = ""


class ToolCallBlockOut(BaseModel):
    """What the agent did and what came back."""

    type: Literal["tool_call"]
    id: str
    name: str
    input: dict[str, Any]
    output: str | None = None
    is_error: bool = False
    # A call of an imported session: shown as it was, never run by YAAP.
    imported: bool = False


class AttachmentBlockOut(BaseModel):
    type: Literal["attachment"]
    attachment_id: int
    name: str
    kind: str | None = None
    media_type: str | None = None
    size: int | None = None


class CommandBlockOut(BaseModel):
    """What a /command expanded to; a /card command also names the card it made."""

    type: Literal["command"]
    name: str
    text: str
    card_id: int | None = None


class SkillBlockOut(BaseModel):
    """A pinned skill's full text."""

    type: Literal["skill"]
    name: str
    text: str = ""


class SkillRemovedBlockOut(BaseModel):
    type: Literal["skill_removed"]
    name: str


class GoalBlockOut(BaseModel):
    """The marker of the goal turn a user message starts."""

    type: Literal["goal"]
    text: str
    turn: int
    max_turns: int


class SummaryBlockOut(BaseModel):
    """On the last message a summary covers; the model gets it instead of what it covers."""

    type: Literal["summary"]
    text: str
    covered: int
    focus: str | None = None


class TrimBlockOut(BaseModel):
    """The model gets the long tool results up to this message shortened."""

    type: Literal["trim"]


class SteeringBlockOut(BaseModel):
    type: Literal["steering"]
    text: str


class NoteBlockOut(BaseModel):
    """A line YAAP added to the conversation, such as a move to another project."""

    type: Literal["note"]
    text: str


class RepoMapBlockOut(BaseModel):
    """The repository map kept for the conversation; its store key is internal and not shown."""

    type: Literal["repo_map"]


class PlanBlockOut(BaseModel):
    """A plan the agent handed over with submit_plan; the card reads the plan itself."""

    type: Literal["plan"]
    plan_id: int


ContentBlockOut = Annotated[
    TextBlockOut
    | ReasoningBlockOut
    | ToolCallBlockOut
    | AttachmentBlockOut
    | CommandBlockOut
    | SkillBlockOut
    | SkillRemovedBlockOut
    | GoalBlockOut
    | SummaryBlockOut
    | TrimBlockOut
    | SteeringBlockOut
    | NoteBlockOut
    | RepoMapBlockOut
    | PlanBlockOut,
    Field(discriminator="type"),
]

BLOCK_ADAPTER: TypeAdapter[ContentBlockOut] = TypeAdapter(ContentBlockOut)

# Reading a conversation runs on every load: one warning per message and process keeps the log readable. The set is
# cleared when it grows large so a long-running process does not hold every message id it ever saw.
WARNED_MESSAGES: set[int] = set()
MAX_WARNED_MESSAGES = 10_000


class InvalidBlock(ValueError):
    """A block of a message to store does not fit its kind."""


def validate_blocks(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check every block against its kind and return the content unchanged. The error names the block and the
    fields, never the block's text."""
    for index, block in enumerate(content):
        try:
            BLOCK_ADAPTER.validate_python(block)
        except ValidationError as error:
            kind = block.get("type") if isinstance(block, dict) else None
            places = {".".join(str(part) for part in item["loc"][1:]) or "-" for item in error.errors()}
            fields = ", ".join(sorted(places))
            raise InvalidBlock(f"Block {index} (kind {kind!r}) is not valid: check {fields}.") from error
    return content


def legacy_tool_call(message_id: int, index: int, block: dict[str, Any]) -> dict[str, Any]:
    """A tool call stored before id, name and input were required, with the missing parts filled in."""
    call_id = block.get("id")
    name = block.get("name")
    tool_input = block.get("input")
    return {
        **block,
        "id": call_id if isinstance(call_id, str) and call_id else f"legacy-{message_id}-{index}",
        "name": name if isinstance(name, str) and name else "tool",
        "input": tool_input if isinstance(tool_input, dict) else {},
    }


def block_out(message_id: int, index: int, block: dict[str, Any]) -> ContentBlockOut | None:
    """The block as the API shows it, a legacy tool call normalized; None when it fits no kind."""
    try:
        return BLOCK_ADAPTER.validate_python(block)
    except ValidationError:
        pass
    if isinstance(block, dict) and block.get("type") == "tool_call":
        try:
            return BLOCK_ADAPTER.validate_python(legacy_tool_call(message_id, index, block))
        except ValidationError:
            pass
    return None


def blocks_out(message_id: int, content: list[dict[str, Any]]) -> list[ContentBlockOut]:
    """The message's blocks for the API; a block of an old row that fits no kind is left out, never an error."""
    shown: list[ContentBlockOut] = []
    normalized = 0
    left_out = 0
    for index, block in enumerate(content):
        out = block_out(message_id, index, block)
        if out is None:
            left_out += 1
            continue
        if isinstance(out, ToolCallBlockOut) and (block.get("id"), block.get("name"), block.get("input")) != (
            out.id,
            out.name,
            out.input,
        ):
            normalized += 1
        shown.append(out)
    if (normalized or left_out) and message_id not in WARNED_MESSAGES:
        if len(WARNED_MESSAGES) >= MAX_WARNED_MESSAGES:
            WARNED_MESSAGES.clear()
        WARNED_MESSAGES.add(message_id)
        logger.warning("Message %s: %s blocks normalized, %s left out.", message_id, normalized, left_out)
    return shown
