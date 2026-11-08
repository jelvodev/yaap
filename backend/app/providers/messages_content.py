"""Neutral content blocks in the Anthropic Messages format, shared by the API adapter and the Claude Code adapter."""

from collections.abc import Sequence
from typing import Any

from app.providers.base import ContentBlock


def to_messages_block(block: ContentBlock) -> dict[str, Any] | None:
    kind = block.get("type")
    if kind == "text":
        # The Messages API refuses empty text blocks.
        return {"type": "text", "text": block["text"]} if block.get("text") else None
    if kind in ("image", "document"):
        converted: dict[str, Any] = {
            "type": kind,
            "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]},
        }
        if kind == "document" and block.get("title"):
            converted["title"] = block["title"]
        return converted
    return None


def to_messages_content(blocks: Sequence[ContentBlock]) -> list[dict[str, Any]]:
    return [converted for block in blocks if (converted := to_messages_block(block)) is not None]
