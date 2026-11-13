"""Search models: hits across messages, memory and Blueprint files."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SearchHitOut(BaseModel):
    """A message, a project's memory or a Blueprint file. Message fields are set for messages only; path and level
    for Blueprint files."""

    kind: Literal["message", "memory", "knowledge", "blueprint"] = "message"
    conversation_id: int | None = None
    conversation_title: str | None = None
    project_id: int | None = None
    message_id: int | None = None
    role: str | None = None
    path: str | None = None
    level: Literal["global", "environment", "project"] | None = None
    # Matched words are between  and .
    snippet: str
    created_at: datetime | None = None
